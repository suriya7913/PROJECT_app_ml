#!/usr/bin/env python3
"""
LegalKGent — Step 6: GraphRAG Query Agent
============================================
Hybrid query agent: semantic search (FAISS) + graph traversal (Neo4j).
ReAct loop with strict guardrails against hallucination.

Usage:
    python 6_query_agent.py

Requires:
    - data/faiss_index/ (built by 5_build_index.py)
    - Neo4j running with ingested data (from 4_ingest_neo4j.py)
"""

import json
import os
import re
import numpy as np

from neo4j import GraphDatabase
from sentence_transformers import SentenceTransformer
import faiss

from config import (
    INDEX_FILE, IDMAP_FILE, CORPUS_FILE,
    EMBED_MODEL, MIN_SIM_THRESHOLD,
    MISTRAL_MODEL,
)
from utils.neo4j_client import get_driver, get_schema
from llm.client import get_mistral_client


# ─────────────────────────────────────────────
# GLOBALS (populated at module load)
# ─────────────────────────────────────────────
_tracer = None
_embed_model = None
_faiss_index = None
_id_map = None
_corpus_lookup = None
_mistral_client = None
_driver = None


def _init():
    """Initialize all clients and data stores."""
    global _tracer, _embed_model, _faiss_index, _id_map, _corpus_lookup, _mistral_client, _driver

    # Phoenix tracing (optional)
    try:
        from phoenix.otel import register
        from opentelemetry import trace
        register(project_name="legalkgent-graphrag", auto_instrument=True)
        _tracer = trace.get_tracer("legalkgent.graphrag")
        print("✅ Phoenix tracer registered")
    except Exception:
        from opentelemetry import trace
        _tracer = trace.get_tracer("legalkgent.graphrag")
        print("ℹ️  Phoenix not available, tracing disabled")

    # FAISS
    if not os.path.exists(INDEX_FILE):
        raise FileNotFoundError(f"FAISS index not found at {INDEX_FILE}. Run 5_build_index.py first.")

    print(f"📦 Loading embedding model: {EMBED_MODEL}...")
    _embed_model = SentenceTransformer(EMBED_MODEL)

    print(f"🗂️  Loading FAISS index...")
    _faiss_index = faiss.read_index(INDEX_FILE)

    with open(IDMAP_FILE) as f:
        _id_map = json.load(f)
    print(f"✅ FAISS ready — {_faiss_index.ntotal} vectors")

    # Corpus lookup
    print(f"📂 Loading corpus...")
    with open(CORPUS_FILE, "r", encoding="utf-8") as f:
        _corpus_data = json.load(f)
    _corpus_lookup = {c["id"]: c for c in _corpus_data if c.get("id")}
    print(f"   ✅ {len(_corpus_lookup)} chunks loaded")

    # Neo4j + Mistral
    _driver = get_driver()
    _mistral_client = get_mistral_client()
    print("✅ Connected to Neo4j and Mistral")


# ─────────────────────────────────────────────
# TOOL 1: SEMANTIC SEARCH
# ─────────────────────────────────────────────

def semantic_search(query: str, top_k: int = 5) -> str:
    """Search FAISS for chunks most semantically similar to query."""
    print(f"\n🔧 [TOOL] semantic_search({query!r}, top_k={top_k})")

    vec = _embed_model.encode([query], normalize_embeddings=True).astype("float32")
    scores, indices = _faiss_index.search(vec, k=top_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0 or idx >= len(_id_map):
            continue
        rec = _id_map[idx]
        chunk_meta = _corpus_lookup.get(rec["node_id"], {})
        heading = chunk_meta.get("heading", "")
        if heading and re.match(r'^[\s.]+$', heading):
            heading = ""
        results.append({
            "node_id":    rec["node_id"],
            "doc_title":  rec["doc_title"],
            "section":    rec.get("section", ""),
            "heading":    heading,
            "part":       chunk_meta.get("part", ""),
            "similarity": round(float(score), 4),
            "snippet":    rec["text"][:600],
        })

    relevant = [r for r in results if r["similarity"] >= MIN_SIM_THRESHOLD]

    if not relevant:
        msg = (f"⚠️ NO_MATCHES: No corpus chunks found with similarity ≥ "
               f"{MIN_SIM_THRESHOLD}. This topic may not be in the knowledge graph. "
               "You MUST inform the user: 'I do not have this information in my graph.'")
        print(f"   ⚠️ {msg}")
        return msg

    output = json.dumps(relevant, indent=2)
    print(f"   ✅ {len(relevant)} matches")
    return output


# ─────────────────────────────────────────────
# TOOL 2: RUN CYPHER
# ─────────────────────────────────────────────

def run_cypher(query: str) -> str:
    """Execute a Cypher query against Neo4j."""
    print(f"\n🔧 [TOOL] run_cypher({query!r})")

    with _driver.session() as session:
        try:
            result = session.run(query)
            records = [dict(r) for r in result]

            if not records:
                msg = ("⚠️ ZERO_RESULTS: This Cypher query returned 0 rows. "
                       "Do NOT fabricate data. Try broader WHERE clause or "
                       "respond with ANSWER stating the graph lacks this info.")
                print(f"   ⚠️ Zero results")
                return msg

            output = json.dumps(records, indent=2, default=str)
            print(f"   ✅ {len(records)} records")
            return output

        except Exception as e:
            err = f"CYPHER ERROR: {e}"
            print(f"   ❌ {err}")
            return err


# ─────────────────────────────────────────────
# TOOL 3: LOOKUP CORPUS
# ─────────────────────────────────────────────

def lookup_corpus(node_ids_str: str) -> str:
    """Retrieve full content for given node IDs."""
    print(f"\n🔧 [TOOL] lookup_corpus({node_ids_str!r})")

    node_ids = [nid.strip().strip('"\'') for nid in node_ids_str.split(",")]
    node_ids = [nid for nid in node_ids if nid]
    if not node_ids:
        return "⚠️ NO_IDS: No valid node IDs provided."

    results = []
    for nid in node_ids[:10]:
        chunk = _corpus_lookup.get(nid)
        if chunk:
            text = chunk.get("content", "")
            if "| TEXT:" in text:
                text = text.split("| TEXT:", 1)[-1].strip()
            heading = chunk.get("heading", "")
            if heading and re.match(r'^[\s.]+$', heading):
                heading = ""
            results.append({
                "node_id":   nid,
                "doc_title": chunk.get("doc_title", ""),
                "section":   chunk.get("section", ""),
                "heading":   heading,
                "part":      chunk.get("part", ""),
                "content":   text[:800],
            })

    if not results:
        return "⚠️ NO_CONTENT_FOUND: None of the provided node IDs were found."

    output = json.dumps(results, indent=2)
    print(f"   ✅ {len(results)} chunks")
    return output


# ─────────────────────────────────────────────
# SYSTEM PROMPT BUILDER
# ─────────────────────────────────────────────

def build_system_prompt() -> str:
    """Build the system prompt with live graph schema."""
    schema = get_schema()
    concepts_list = "\n".join(f"  - {c}" for c in schema["concepts"])
    action_types = ", ".join(schema["action_types"].keys())

    return f"""You are a UK Legal Knowledge Graph agent. You answer legal questions by using THREE tools in a hybrid (GraphRAG) workflow.

## Your Three Tools

### Tool 1 — semantic_search(query, top_k=5)
- Searches the vector index for corpus chunks semantically similar to your query
- Returns: node_id, doc_title, section, heading, part, similarity score, text snippet
- **Always call this FIRST** to find relevant anchor nodes

### Tool 2 — run_cypher(query)
- Executes Cypher against Neo4j and returns JSON results
- **Call this SECOND** using node_id values from semantic_search as anchors
- Returns "⚠️ ZERO_RESULTS" if query matches nothing

### Tool 3 — lookup_corpus(node_ids)
- Retrieves full section text, heading, and part for given node IDs
- Input: comma-separated node IDs: lookup_corpus("id1,id2,id3")

---

## ⚠️ CRITICAL GRAPH SCHEMA

- Node label: `:LegalDoc` with properties: `id`, `citation`, `title`, `heading`, `part`, `content_snippet`
- Edge type: `:LEGAL_RELATIONSHIP` with property `action_type`
- Valid action_type values: {action_types}
- Edge properties: `r.detail`, `r.confidence`, `r.date`, `r.provenance`
- Concept nodes: `:Concept` linked via `(n:LegalDoc)-[:REGULATES]->(c:Concept)`

## Legal Domain Concepts
{concepts_list}

## Graph Stats
- Nodes: {schema['node_count']} | Edges: {schema['edge_count']}
- Sample edge:
{json.dumps(schema['sample_edges'][:2], indent=2, default=str)}

---

## Recommended Cypher Patterns

```cypher
// Find what an anchor Act DOES (outgoing)
MATCH (s:LegalDoc)-[r:LEGAL_RELATIONSHIP]->(t:LegalDoc)
WHERE s.id STARTS WITH 'ukpga_2023_36'
RETURN s.id, s.heading, r.action_type, r.detail, t.citation
LIMIT 25
```

```cypher
// Find what AFFECTS an anchor Act (incoming)
MATCH (s:LegalDoc)-[r:LEGAL_RELATIONSHIP]->(t:LegalDoc)
WHERE t.citation CONTAINS 'Housing Act 1985'
RETURN s.id, s.title, r.action_type, r.detail, t.citation
LIMIT 25
```

---

## ⚠️ OUTPUT FORMATTING — MANDATORY

1. **NEVER output raw node IDs** like "ukpga_2023_55.xml_63". Convert to standard UK legal citations.
2. Use `doc_title` and `section` from tool results to build readable citations.
3. When listing amendments, use proper legal citation format throughout.

## ⚠️ HANDLING INCOMPLETE DATA

1. Answer ONLY the parts you have evidence for.
2. State clearly: "The specific textual amendments for [topic] are not available in the current knowledge graph."
3. **NEVER use hedging**: "likely targeted", "suggests", "probably". If you don't have data, SAY SO.

## POLICY CONTEXT SYNTHESIS

When presenting amendments, explain the "So What?":
1. **Group** related amendments by theme
2. **Explain** practical impact: What changed → Why it matters → Who is affected

## ⚠️ ABSOLUTE GUARDRAILS

1. **NEVER answer from general knowledge.** Only cite data returned by tools.
2. **If a tool returns ZERO_RESULTS or NO_MATCHES**, inform the user.
3. **NEVER invent section numbers, Act names, or statutory provisions.**
4. **You must call semantic_search at least once BEFORE answering.**

## Response Format (one action per turn)

THOUGHT: <your reasoning>
ACTION: semantic_search("<query>", top_k=5)

OR:
THOUGHT: <reasoning about node IDs>
ACTION: run_cypher("<Cypher>")

OR:
THOUGHT: <need full text>
ACTION: lookup_corpus("node_id_1,node_id_2")

OR (only after at least one successful tool call):
THOUGHT: <interpretation>
ANSWER: <answer using standard UK legal citations>

Output ONLY ONE ACTION per turn.
"""


# ─────────────────────────────────────────────
# ANSWER CLEANING
# ─────────────────────────────────────────────

def _clean_answer(answer: str) -> str:
    """Remove leaked internals from the answer."""
    answer = re.sub(r'THOUGHT:.*?(?=\n\n|\Z)', '', answer, flags=re.DOTALL).strip()
    answer = re.sub(r'\((?:ukpga|uksi|ewca|uksc|ewhc|ukut)_\d{4}_\d+\.xml_\w+\)', '', answer)
    answer = re.sub(r'(?:ukpga|uksi|ewca|uksc|ewhc|ukut)_[\w]+_\d{4}_?\d*\.xml_[\w]+', '', answer)
    answer = re.sub(r'\(\s*\)', '', answer)
    answer = re.sub(r'  +', ' ', answer)
    answer = re.sub(r'\n{3,}', '\n\n', answer)
    return answer.strip()


def _extract_arg(action_line: str, tool_name: str) -> str:
    """Extract the first string argument from a tool call."""
    start = action_line.index(tool_name + "(") + len(tool_name) + 1
    for quote in ('"', "'", '`'):
        if action_line[start:].lstrip().startswith(quote):
            s = action_line.index(quote, start)
            e = action_line.index(quote, s + 1)
            return action_line[s + 1:e]
    depth = 1; i = start
    while i < len(action_line) and depth > 0:
        if action_line[i] == '(':  depth += 1
        elif action_line[i] == ')': depth -= 1
        i += 1
    return action_line[start:i - 1].strip().strip('"\'`')


# ─────────────────────────────────────────────
# AGENT LOOP
# ─────────────────────────────────────────────

def agent_ask(question: str, max_steps: int = 10) -> str:
    """GraphRAG ReAct agent loop."""
    from opentelemetry import trace

    with _tracer.start_as_current_span("agent_ask", attributes={
        "question": question, "max_steps": max_steps,
    }):
        print(f"\n{'#'*60}")
        print(f"  🔍 USER QUESTION: {question}")
        print(f"{'#'*60}")

        system_prompt = build_system_prompt()
        conversation = question
        has_nonempty_result = False
        semantic_done = False

        for step in range(1, max_steps + 1):
            print(f"\n--- Agent Step {step} ---")

            response = _mistral_client.chat.complete(
                model=MISTRAL_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": conversation},
                ],
                temperature=0.1,
            )
            llm_output = response.choices[0].message.content.strip()
            print(f"📥 [LLM]\n{llm_output}")

            # Parse ANSWER
            if "ANSWER:" in llm_output:
                if not semantic_done:
                    conversation += (f"\n\n{llm_output}\n\n"
                                     "You MUST call semantic_search before answering.")
                    continue

                answer = llm_output.split("ANSWER:")[-1].strip()
                answer = _clean_answer(answer)

                if not has_nonempty_result:
                    answer = ("⚠️ The knowledge graph tools did not return relevant data.\n\n" + answer)

                trace.get_current_span().set_attribute("answer", answer[:1000])
                print(f"\n{'='*60}\n  ✅ FINAL ANSWER (step {step})\n{'='*60}\n{answer}")
                return answer

            # Parse ACTION
            if "ACTION:" not in llm_output:
                conversation += (f"\n\n{llm_output}\n\n"
                                 "You must respond with ACTION: or ANSWER:")
                continue

            action_line = llm_output.split("ACTION:")[1].strip()
            if "ACTION:" in action_line:
                action_line = action_line.split("ACTION:")[0].strip()

            tool_result = None

            if "semantic_search(" in action_line:
                try:
                    query_text = _extract_arg(action_line, "semantic_search")
                    top_k = 5
                    m = re.search(r"top_k\s*=\s*(\d+)", action_line)
                    if m:
                        top_k = int(m.group(1))
                    tool_result = semantic_search(query_text, top_k=top_k)
                    semantic_done = True
                except Exception as e:
                    tool_result = f"TOOL ERROR (semantic_search): {e}"

            elif "run_cypher(" in action_line:
                try:
                    cypher = _extract_arg(action_line, "run_cypher")
                    tool_result = run_cypher(cypher)
                except Exception as e:
                    tool_result = f"TOOL ERROR (run_cypher): {e}"

            elif "lookup_corpus(" in action_line:
                try:
                    ids_arg = _extract_arg(action_line, "lookup_corpus")
                    tool_result = lookup_corpus(ids_arg)
                except Exception as e:
                    tool_result = f"TOOL ERROR (lookup_corpus): {e}"

            else:
                tool_result = "UNKNOWN TOOL. Use: semantic_search, run_cypher, or lookup_corpus"

            # Track real data
            if tool_result and not tool_result.startswith("⚠️"):
                try:
                    parsed = json.loads(tool_result)
                    if isinstance(parsed, (list, dict)) and parsed:
                        has_nonempty_result = True
                except json.JSONDecodeError:
                    pass

            conversation += (
                f"\n\n{llm_output}\n\nOBSERVATION (tool result):\n{tool_result}\n\n"
                "Continue reasoning. Use another ACTION or respond with ANSWER:"
            )

        return f"❌ Agent did not reach an answer within {max_steps} steps."


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    _init()

    # Self-test
    print("\n🔬 Self-test: semantic_search...")
    result = semantic_search("transport road traffic law", top_k=3)
    if not result.startswith("⚠️"):
        hits = json.loads(result)
        print(f"   ✅ Top hit: {hits[0]['doc_title']} (sim={hits[0]['similarity']})")

    # Test questions
    TEST_QUESTIONS = [
        "Which 2023 Acts modify the Employment Rights Act 1996, and what type of changes does each make?",
    ]
    for q in TEST_QUESTIONS:
        agent_ask(q)
