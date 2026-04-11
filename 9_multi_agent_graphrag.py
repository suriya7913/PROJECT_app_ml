#!/usr/bin/env python3
# ruff: noqa: E402
"""
LegalKGent — Step 8: Multi-Agent GraphRAG Pipeline
=====================================================
A 4-agent pipeline with MCP-style tools for precise legal knowledge graph traversal.

Agent 1: Retriever        — finds candidate chunk IDs via semantic search
Agent 2: Graph Engineer   — expands those IDs into a structured subgraph via Neo4j
Agent 3: Context Aggregator — fetches the raw legal text for confirmed node IDs
Agent 4: Senior Counsel   — synthesizes a structured legal answer from verified context

Usage:
    source /home/suriya/pytorch-envs/bin/activate
    python 9_multi_agent_graphrag.py
"""

import json
import os
import re
import time
from typing import Any

import faiss
import numpy as np
import requests
from sentence_transformers import SentenceTransformer

from config import (
    CORPUS_FILE, EMBED_MODEL, INDEX_FILE, IDMAP_FILE,
    MIN_SIM_THRESHOLD, OPEN_ROUTER,
)
from utils.mcp_tools import (
    AGENT_TOOL_REGISTRY,
    mcp_semantic_search,
    mcp_search_nodes_by_title,
    mcp_get_schema,
    mcp_execute_cypher,
    mcp_get_node_subgraph,
    mcp_find_relationships,
    mcp_find_case_interpretations,
    mcp_find_amendments_timeline,
    mcp_read_document_text,
    mcp_submit_final_answer,
)
from utils.neo4j_client import get_driver

# ─────────────────────────────────────────────
# GLOBALS
# ─────────────────────────────────────────────
_embed_model = None
_faiss_index = None
_id_map = None
_corpus_lookup = None

OPENROUTER_MODEL = "qwen/qwen3.6-plus:free"
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"


def _init():
    global _embed_model, _faiss_index, _id_map, _corpus_lookup

    print(f"📦 Loading embedding model: {EMBED_MODEL}...")
    _embed_model = SentenceTransformer(EMBED_MODEL)

    print("🗂️  Loading FAISS index...")
    _faiss_index = faiss.read_index(INDEX_FILE)
    with open(IDMAP_FILE) as f:
        _id_map = json.load(f)
    print(f"✅ FAISS ready — {_faiss_index.ntotal} vectors")

    print("📂 Loading corpus...")
    with open(CORPUS_FILE, "r", encoding="utf-8") as f:
        corpus_data = json.load(f)
    _corpus_lookup = {c["chunk_id"]: c for c in corpus_data if c.get("chunk_id")}
    print(f"   ✅ {len(_corpus_lookup)} chunks loaded")

    get_driver()  # test Neo4j connection
    print("✅ Neo4j connected")


# ─────────────────────────────────────────────
# LLM CLIENT (OpenRouter)
# ─────────────────────────────────────────────

def _llm_call(messages: list[dict], max_tokens: int = 2048) -> str:
    """Call OpenRouter with retry logic for rate limits."""
    for attempt in range(5):
        try:
            resp = requests.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPEN_ROUTER}",
                    "Content-Type": "application/json",
                },
                data=json.dumps({
                    "model": OPENROUTER_MODEL,
                    "messages": messages,
                    "max_tokens": max_tokens,
                }),
                timeout=120,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content", "") or ""
            return content.strip()
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower():
                wait = 5 * (2 ** attempt)
                print(f"⚠️  Rate limit — waiting {wait}s (attempt {attempt+1}/5)")
                time.sleep(wait)
            else:
                raise e
    raise RuntimeError("LLM call failed after 5 retries")


# ─────────────────────────────────────────────
# AGENT 1: The Retriever
# ─────────────────────────────────────────────

AGENT1_SYSTEM = """You are a Legal Information Retriever. Your ONLY job is to translate the user's legal question into semantic search queries that locate relevant statutory sections and case law.

Output ONLY valid JSON in this exact format:
{"queries": ["<query1>", "<query2>", "<query3>"]}

Rules:
- Generate 2-3 diverse queries covering different angles of the question.
- Include the Act name/section if mentioned.
- Include the legal concept being searched.
- Do NOT attempt to answer the legal question."""


def run_agent1_retriever(question: str) -> list[dict]:
    """Agent 1: Generate semantic search queries and retrieve candidate node IDs."""
    print("\n" + "="*60)
    print("🔍 AGENT 1: RETRIEVER")
    print("="*60)

    messages = [
        {"role": "system", "content": AGENT1_SYSTEM},
        {"role": "user", "content": f"Legal question: {question}"}
    ]
    response = _llm_call(messages, max_tokens=1000)
    print(f"📥 Agent 1 queries: {response}")

    # Parse JSON queries
    try:
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            queries = data.get("queries", [])
        else:
            queries = [question]
    except Exception:
        queries = [question]

    # Run semantic search for each query and merge results
    all_results: dict[str, dict] = {}
    for q in queries:
        hits = mcp_semantic_search(
            q,
            embed_model=_embed_model,
            faiss_index=_faiss_index,
            id_map=_id_map,
            corpus_lookup=_corpus_lookup,
            top_k=10,
            min_sim=MIN_SIM_THRESHOLD,
        )
        for hit in hits:
            nid = hit["node_id"]
            if nid not in all_results or hit["score"] > all_results[nid]["score"]:
                all_results[nid] = hit

    results = sorted(all_results.values(), key=lambda x: x["score"], reverse=True)[:30]
    print(f"   ✅ {len(results)} candidate nodes found")
    for r in results[:30]:
        print(f"      [{r['score']:.3f}] {r['doc_title']} (id={r['node_id']})")

    return results


# ─────────────────────────────────────────────
# AGENT 2: The Graph Engineer
# ─────────────────────────────────────────────

AGENT2_SYSTEM = """You are a Knowledge Graph Specialist with access to a legal Neo4j database. You receive a list of seed node IDs from a semantic search and your goal is to expand them into a structured subgraph that can answer the user's legal question.

## Your MCP Tools:
- `mcp_search_nodes_by_title(keyword)` — find nodes exactly/partially matching a title (e.g. "Road Safety Act 2006"). Use this if the provided seed nodes are irrelevant.
- `mcp_get_node_subgraph(node_id, depth)` — expand a node to its 1-2 hop neighbourhood
- `mcp_find_relationships(node_id, action_type, direction)` — find typed edges (AMENDS, INTERPRETS, OVERRULES, CITES, EMPOWERS)
- `mcp_find_case_interpretations(legislation_id)` — find cases that INTERPRETS/OVERRULES a law section
- `mcp_find_amendments_timeline(act_title)` — find all amendments to an Act chronologically

## Schema:
- Node: :LegalDoc with properties: id, title, heading, type
- Edge: :LEGAL_RELATIONSHIP with action_type: AMENDS, INTERPRETS, OVERRULES, CITES, EMPOWERS, REQUIRES, REPEALS

## Rules:
- Use the node IDs exactly as given — do not guess or modify them.
- Choose 2-4 most relevant seed nodes from the list provided.
- Do NOT attempt to answer the user's question.
- Output ONE tool call per turn in this format:
  TOOL: <tool_name>(<arg1>, <arg2>)
- When you have enough graph data, output:
  DONE: {"nodes": [...], "edges": [...]}"""


def _parse_agent2_action(line: str, seed_ids: list[str]) -> tuple[str, Any] | None:
    """Parse an agent 2 TOOL call and execute the matching MCP function."""
    line = line.strip()

    if "mcp_search_nodes_by_title(" in line:
        m = re.search(r'mcp_search_nodes_by_title\(["\']?([^"\']+)["\']?\)', line)
        if m:
            result = mcp_search_nodes_by_title(m.group(1))
            return ("mcp_search_nodes_by_title", result)

    elif "mcp_get_node_subgraph(" in line:
        m = re.search(r'mcp_get_node_subgraph\(["\']?([\w./()]+)["\']?,?\s*(\d?)', line)
        if m:
            nid, depth = m.group(1), int(m.group(2) or 1)
            result = mcp_get_node_subgraph(nid, depth)
            return ("mcp_get_node_subgraph", result)

    elif "mcp_find_relationships(" in line:
        m = re.search(r'mcp_find_relationships\(["\']?([\w./()]+)["\']?(?:,\s*["\']?(\w+)["\']?)?(?:,\s*["\']?(\w+)["\']?)?\)', line)
        if m:
            nid = m.group(1)
            action = m.group(2) if m.group(2) else None
            direction = m.group(3) if m.group(3) else "both"
            result = mcp_find_relationships(nid, action, direction)
            return ("mcp_find_relationships", result)

    elif "mcp_find_case_interpretations(" in line:
        m = re.search(r'mcp_find_case_interpretations\(["\']?([\w./()]+)["\']?\)', line)
        if m:
            result = mcp_find_case_interpretations(m.group(1))
            return ("mcp_find_case_interpretations", result)

    elif "mcp_find_amendments_timeline(" in line:
        m = re.search(r'mcp_find_amendments_timeline\(["\'](.+?)["\']?\)', line)
        if m:
            result = mcp_find_amendments_timeline(m.group(1))
            return ("mcp_find_amendments_timeline", result)

    elif "mcp_execute_cypher(" in line:
        m = re.search(r'mcp_execute_cypher\(["\'](.+?)["\']\)', line, re.DOTALL)
        if m:
            result = mcp_execute_cypher(m.group(1))
            return ("mcp_execute_cypher", result)

    return None


def run_agent2_graph_engineer(question: str, seed_nodes: list[dict]) -> dict:
    """Agent 2: Expand seed node IDs into a structured subgraph."""
    print("\n" + "="*60)
    print("🕸️  AGENT 2: GRAPH ENGINEER")
    print("="*60)

    seed_ids = [n["node_id"] for n in seed_nodes[:10]]
    seed_summary = json.dumps([{
        "node_id": n["node_id"],
        "doc_title": n["doc_title"],
        "score": n["score"]
    } for n in seed_nodes[:10]], indent=2)

    messages = [
        {"role": "system", "content": AGENT2_SYSTEM},
        {"role": "user", "content": (
            f"User question: {question}\n\n"
            f"Seed nodes from semantic search:\n{seed_summary}\n\n"
            "Use MCP tools to explore the graph around these nodes and find relevant relationships."
        )}
    ]

    collected_nodes: dict = {}
    collected_edges: list = []

    for step in range(8):  # max 8 graph exploration steps
        response = _llm_call(messages, max_tokens=512)
        print(f"\n--- Graph Engineer Step {step+1} ---")
        print(f"📥 {response}")

        # Check if done
        if "DONE:" in response:
            done_match = re.search(r'DONE:\s*(\{.*\})', response, re.DOTALL)
            if done_match:
                try:
                    data = json.loads(done_match.group(1))
                    collected_nodes.update(data.get("nodes", {}))
                    collected_edges.extend(data.get("edges", []))
                except Exception:
                    pass
            break

        # Parse and execute tool call
        tool_result = None
        tool_name = None
        for line in response.split("\n"):
            if "TOOL:" in line:
                tool_line = line.split("TOOL:", 1)[1].strip()
                parsed = _parse_agent2_action(tool_line, seed_ids)
                if parsed:
                    tool_name, tool_result = parsed
                    break

        if tool_result is None:
            # Try parsing directly from response
            for line in response.split("\n"):
                parsed = _parse_agent2_action(line, seed_ids)
                if parsed:
                    tool_name, tool_result = parsed
                    break

        if tool_result is None:
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": "Please output a TOOL: call or DONE: with the subgraph JSON."})
            continue

        # Accumulate graph data
        if isinstance(tool_result, dict) and "nodes" in tool_result:
            collected_nodes.update(tool_result.get("nodes", {}))
            collected_edges.extend(tool_result.get("edges", []))
        elif isinstance(tool_result, list):
            # flatten list results into edges
            for rec in tool_result:
                if "case_id" in rec:
                    collected_nodes[rec["case_id"]] = rec.get("case_title", "")
                    collected_edges.append({
                        "from": rec["case_id"],
                        "to": None,
                        "action_type": rec.get("action"),
                        "detail": rec.get("detail"),
                    })
                elif "source" in rec:
                    collected_edges.append({
                        "from": rec.get("source"),
                        "to": rec.get("target_id"),
                        "action_type": rec.get("action"),
                        "detail": rec.get("detail"),
                    })

        result_str = json.dumps(tool_result, default=str)[:1500]
        print(f"   ✅ [{tool_name}] → {result_str[:200]}...")

        messages.append({"role": "assistant", "content": response})
        messages.append({
            "role": "user",
            "content": (
                f"Tool result:\n{result_str}\n\n"
                "Continue expanding the graph with more TOOL: calls, or output DONE: with collected data."
            )
        })

    print(f"\n   ✅ Graph subgraph: {len(collected_nodes)} nodes, {len(collected_edges)} edges")
    return {"nodes": collected_nodes, "edges": collected_edges, "seed_ids": seed_ids}


# ─────────────────────────────────────────────
# AGENT 3: The Context Aggregator
# ─────────────────────────────────────────────

AGENT3_SYSTEM = """You are a Legal Document Aggregator. You receive a structured subgraph and a list of seed node IDs. Your job is to:

1. Identify up to 15 most relevant node IDs that can answer the user's question.
2. Output them as JSON — DO NOT call any tools yourself, just identify the IDs.

Output format (JSON only):
{"relevant_ids": ["id1", "id2", "id3"]}"""


def run_agent3_aggregator(question: str, subgraph: dict) -> list[dict]:
    """Agent 3: Select the most relevant nodes and retrieve their text from corpus."""
    print("\n" + "="*60)
    print("📋 AGENT 3: CONTEXT AGGREGATOR")
    print("="*60)

    # Build node list for the LLM to prioritize
    all_ids = list(subgraph.get("nodes", {}).keys())
    seed_ids = subgraph.get("seed_ids", [])
    # cap at 50 to avoid massive prompt blowout if the subgraph is extraordinarily large
    candidate_ids = (seed_ids + [i for i in all_ids if i not in seed_ids])[:50]

    node_summary = [
        {"node_id": nid, "title": subgraph["nodes"].get(nid, "")}
        for nid in candidate_ids
    ]

    messages = [
        {"role": "system", "content": AGENT3_SYSTEM},
        {"role": "user", "content": (
            f"User question: {question}\n\n"
            f"Available nodes:\n{json.dumps(node_summary, indent=2)}\n\n"
            f"Graph edges (relationships):\n{json.dumps(subgraph.get('edges', []), indent=2)}\n\n"
            "Identify the 15 most relevant node IDs to read."
        )}
    ]

    response = _llm_call(messages, max_tokens=256)
    print(f"📥 Agent 3 selection: {response}")

    # Parse selected IDs
    try:
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        data = json.loads(json_match.group()) if json_match else {}
        selected_ids = data.get("relevant_ids", candidate_ids[:15])
    except Exception:
        selected_ids = candidate_ids[:15]

    # Fetch the text without heavy truncation
    context_chunks = mcp_read_document_text(selected_ids, _corpus_lookup, max_chars=8000)
    valid = [c for c in context_chunks if "⚠️" not in c.get("content", "")]
    print(f"   ✅ Retrieved text for {len(valid)}/{len(selected_ids)} nodes")
    return context_chunks


# ─────────────────────────────────────────────
# AGENT 4: Senior Counsel
# ─────────────────────────────────────────────

AGENT4_SYSTEM = """You are a Senior Barrister at a leading UK law firm. Your research team has assembled verified legal context from the knowledge graph and case law corpus. Answer the client's question using ONLY the provided context.

Rules:
- Cite every legal fact with [node_id] at the end of the relevant sentence.
- Structure your answer with these headers:
  1. Applicable Statute
  2. Relevant Case Law
  3. Legal Principle
  4. Conclusion for the Client
- If the context does not contain sufficient information, state:
  "The available case law and legislation in this knowledge base does not contain sufficient information to answer this question definitively."
- Do NOT use general legal knowledge not present in the provided context.
- If graph relationships (edges) are provided, you MUST use them to understand how different legal documents or sections interact (e.g., amendments, interpretations)."""


def run_agent4_counsel(question: str, context_chunks: list[dict], subgraph: dict) -> str:
    """Agent 4: Synthesize verified legal context into a structured answer."""
    print("\n" + "="*60)
    print("⚖️  AGENT 4: SENIOR COUNSEL")
    print("="*60)

    # Format text context for the LLM
    context_str = "=== RAW LEGAL TEXT ===\n"
    for chunk in context_chunks:
        nid = chunk.get("node_id", "")
        title = chunk.get("doc_title", "")
        section = chunk.get("section", "")
        heading = chunk.get("heading", "")
        content = chunk.get("content", "")
        context_str += f"\n---\n[{nid}] {title}"
        if section:
            context_str += f" | Section {section}"
        if heading:
            context_str += f" | {heading}"
        context_str += f"\n{content}\n"

    # Format graph edges for the LLM
    context_str += "\n=== GRAPH RELATIONSHIPS (STRUCTURAL CONTEXT) ===\n"
    edges = subgraph.get("edges", [])
    if edges:
        for e in edges:
            frm = e.get("from", "Unknown")
            to = e.get("to", "Unknown")
            act = e.get("action_type", "RELATES")
            det = e.get("detail", "")
            context_str += f"Graph Edge: [{frm}] --({act})--> [{to}] | Details: {det}\n"
    else:
        context_str += "No structural graph edges were identified.\n"

    messages = [
        {"role": "system", "content": AGENT4_SYSTEM},
        {"role": "user", "content": (
            f"CLIENT QUESTION:\n{question}\n\n"
            f"VERIFIED LEGAL CONTEXT FROM KNOWLEDGE GRAPH:\n{context_str}"
        )}
    ]

    answer = _llm_call(messages, max_tokens=4096)
    return mcp_submit_final_answer(answer)


# ─────────────────────────────────────────────
# PIPELINE ORCHESTRATOR
# ─────────────────────────────────────────────

def run_pipeline(question: str) -> str:
    """
    Run the full 4-agent pipeline for a legal question.
    Returns the final answer from Senior Counsel.
    """
    print("\n" + "█"*60)
    print(f"  ⚖️  LEGAL QUERY PIPELINE")
    print(f"  Question: {question[:100]}...")
    print("█"*60)

    # Agent 1: Find candidate nodes
    seed_nodes = run_agent1_retriever(question)
    if not seed_nodes:
        return "❌ No relevant documents found. Please check the FAISS index and try rephrasing."

    # Agent 2: Expand into subgraph
    subgraph = run_agent2_graph_engineer(question, seed_nodes)

    # Agent 3: Retrieve text
    context_chunks = run_agent3_aggregator(question, subgraph)

    # Agent 4: Synthesize answer using both text and graph relationships
    answer = run_agent4_counsel(question, context_chunks, subgraph)

    print("\n" + "="*60)
    print("✅ FINAL ANSWER FROM SENIOR COUNSEL")
    print("="*60)
    print(answer)
    return answer


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    _init()

    TEST_QUESTIONS = [
        # Tests case law discovery (Campbell v R interprets RTA 1988 s.3)
        "Trace the legislative amendments made by the National Security Act 2023. What specific statutory definitions or provisions were amended by this Act, and have any subsequent tribunals or courts cited these newly amended sections?",

        # Tests AMENDS traversal (Road Safety Act 2006 → Road Traffic Act 1988)
        # "What changes does the Road Safety Act 2006 make to the Road Traffic Act 1988?",
    ]

    for question in TEST_QUESTIONS:
        run_pipeline(question)
