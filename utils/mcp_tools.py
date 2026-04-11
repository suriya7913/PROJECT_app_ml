"""
LegalKGent — MCP Tools Layer
============================
Typed, sandboxed tool functions for each agent in the Multi-Agent GraphRAG pipeline.

Each agent is granted access to a specific subset of tools:
- Agent 1 (Retriever):         mcp_semantic_search
- Agent 2 (Graph Engineer):    mcp_get_schema, mcp_execute_cypher, mcp_get_node_subgraph,
                               mcp_find_relationships, mcp_find_case_interpretations,
                               mcp_find_amendments_timeline
- Agent 3 (Aggregator):        mcp_read_document_text
- Agent 4 (Senior Counsel):    mcp_submit_final_answer
"""

from __future__ import annotations
import json
import re
from typing import Any

# ─────────────────────────────────────────────
# TOOL REGISTRY
# ─────────────────────────────────────────────

# Tools available to each agent (enforced by the orchestrator)
AGENT_TOOL_REGISTRY = {
    "retriever":    ["mcp_semantic_search"],
    "graph_engineer": [
        "mcp_search_nodes_by_title",
        "mcp_get_schema",
        "mcp_execute_cypher",
        "mcp_get_node_subgraph",
        "mcp_find_relationships",
        "mcp_find_case_interpretations",
        "mcp_find_amendments_timeline",
    ],
    "aggregator":   ["mcp_read_document_text"],
    "counsel":      ["mcp_submit_final_answer"],
}

# Cypher keywords that mutate the graph — blocked in the sandbox
_WRITE_KEYWORDS = ["CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE", "DROP", "CALL"]


def _is_write_query(cypher: str) -> bool:
    """Detect if a Cypher statement tries to mutate the database."""
    upper = cypher.upper()
    for kw in _WRITE_KEYWORDS:
        # match as a whole word to avoid false positives (e.g. "CREATED")
        if re.search(rf'\b{kw}\b', upper):
            return True
    return False


def _driver_session():
    """Lazy import of the Neo4j driver to avoid circular imports."""
    from utils.neo4j_client import get_driver
    return get_driver().session()


# ─────────────────────────────────────────────
# AGENT 1 TOOLS: Retriever
# ─────────────────────────────────────────────

def mcp_semantic_search(
    query: str,
    *,
    embed_model,
    faiss_index,
    id_map: list[dict],
    corpus_lookup: dict[str, dict],
    top_k: int = 10,
    min_sim: float = 0.40,
) -> list[dict]:
    """
    Search the FAISS vector index for chunks semantically similar to the query.

    Returns a minimal list of {node_id, doc_title, section, score} dicts.
    Does NOT return any corpus text — that is Agent 3's job.
    """
    import numpy as np
    vec = embed_model.encode([query], normalize_embeddings=True).astype("float32")
    scores, indices = faiss_index.search(vec, k=top_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0 or idx >= len(id_map):
            continue
        if float(score) < min_sim:
            continue
        rec = id_map[idx]
        results.append({
            "node_id":   rec["node_id"],
            "doc_title": rec.get("doc_title", ""),
            "section":   rec.get("section", ""),
            "score":     round(float(score), 4),
        })
    return results


# ─────────────────────────────────────────────
# AGENT 2 TOOLS: Graph Engineer
# ─────────────────────────────────────────────

def mcp_get_schema() -> dict:
    """
    Return the live Neo4j schema: node labels, relationship types, and their properties.
    Helps the Graph Engineer write correct Cypher without hallucinating property names.
    """
    with _driver_session() as s:
        labels = [r["label"] for r in s.run("CALL db.labels() YIELD label RETURN label")]
        rel_types = [r["relationshipType"] for r in
                     s.run("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType")]
        props_raw = s.run("CALL db.schema.nodeTypeProperties() YIELD nodeType, propertyName RETURN nodeType, propertyName LIMIT 50").data()
        action_types = [r["action"] for r in
                        s.run("MATCH ()-[r:LEGAL_RELATIONSHIP]->() RETURN DISTINCT r.action_type AS action LIMIT 30").data()]
    return {
        "node_labels": labels,
        "relationship_types": rel_types,
        "node_properties": props_raw,
        "legal_action_types": action_types,
    }


def mcp_search_nodes_by_title(keyword: str, court_level: str | None = None) -> list[dict]:
    """
    Find nodes exactly or partially matching a title or citation (bypasses vector semantic search).
    Provides a deterministic way to find a specific Act or Case by name.
    """
    where_clause = "WHERE toLower(n.title) CONTAINS toLower($kw) OR toLower(n.citation) CONTAINS toLower($kw)"
    if court_level:
        where_clause += " AND toLower(n.court_level) = toLower($cl)"

    cypher = f"""
    MATCH (n:LegalDoc)
    {where_clause}
    RETURN n.id AS node_id, n.title AS title, n.court_level AS court_level
    LIMIT 20
    """
    with _driver_session() as s:
        return s.run(cypher, kw=keyword, cl=court_level).data()


def mcp_execute_cypher(cypher: str) -> list[dict]:
    """
    Execute a READ-ONLY Cypher query against Neo4j.
    Blocks any write operations (CREATE, MERGE, DELETE, SET, REMOVE, DROP).
    Returns up to 30 records.
    """
    if _is_write_query(cypher):
        raise PermissionError(
            "mcp_execute_cypher: WRITE operations are blocked. "
            "Only read queries are permitted."
        )
    with _driver_session() as s:
        result = s.run(cypher)
        return result.data()[:100]


def mcp_get_node_subgraph(node_id: str, depth: int = 1) -> dict:
    """
    Expand a node into its local subgraph up to N hops.
    Returns nodes and edges as structured JSON for Agent 2 to analyse.
    depth: 1 = immediate neighbours, 2 = two hops (use carefully — can be large).
    """
    depth = max(1, min(depth, 2))  # cap at 2 hops
    cypher = f"""
    MATCH path = (n {{id: $nid}})-[r:LEGAL_RELATIONSHIP*1..{depth}]-(m)
    RETURN n.id AS source_id, n.title AS source_title,
           [rel in relationships(path) | {{type: rel.action_type, detail: rel.detail}}] AS rels,
           m.id AS target_id, m.title AS target_title
    LIMIT 150
    """
    with _driver_session() as s:
        records = s.run(cypher, nid=node_id).data()

    nodes, edges = {}, []
    for r in records:
        nodes[r["source_id"]] = r["source_title"]
        nodes[r.get("target_id", "")] = r.get("target_title", "")
        for rel in (r.get("rels") or []):
            edges.append({
                "from": r["source_id"],
                "to":   r.get("target_id"),
                "action_type": rel.get("type"),
                "detail": rel.get("detail"),
            })

    return {"nodes": nodes, "edges": edges}


def mcp_find_relationships(
    node_id: str,
    action_type: str | None = None,
    direction: str = "both",
) -> list[dict]:
    """
    Find all LEGAL_RELATIONSHIP edges connected to a node.
    action_type: filter by a specific action (e.g. "AMENDS", "INTERPRETS") or None for all.
    direction: "out" | "in" | "both"
    """
    if direction == "out":
        pattern = "(n)-[r:LEGAL_RELATIONSHIP]->(m)"
    elif direction == "in":
        pattern = "(n)<-[r:LEGAL_RELATIONSHIP]-(m)"
    else:
        pattern = "(n)-[r:LEGAL_RELATIONSHIP]-(m)"

    where = "WHERE n.id = $nid"
    if action_type:
        where += " AND r.action_type = $action_type"

    cypher = f"""
    MATCH {pattern}
    {where}
    RETURN n.id AS source, r.action_type AS action, r.detail AS detail,
           r.confidence AS confidence, r.date AS date,
           m.id AS target_id, m.title AS target_title
    LIMIT 150
    """
    with _driver_session() as s:
        return s.run(cypher, nid=node_id, action_type=action_type).data()


def mcp_find_case_interpretations(legislation_id: str) -> list[dict]:
    """
    Find all case law nodes that INTERPRETS or OVERRULES a given legislation chunk.
    Anchors on the legislation node ID and traverses incoming case law edges.
    """
    cypher = """
    MATCH (case:LegalDoc)-[r:LEGAL_RELATIONSHIP]->(leg:LegalDoc)
    WHERE leg.id = $lid
      AND r.action_type IN ['INTERPRETS', 'OVERRULES', 'APPLIES', 'DISTINGUISHES']
    RETURN case.id AS case_id, case.title AS case_title,
           r.action_type AS action, r.detail AS detail, r.confidence AS confidence
    ORDER BY r.confidence DESC
    LIMIT 100
    """
    with _driver_session() as s:
        return s.run(cypher, lid=legislation_id).data()


def mcp_find_amendments_timeline(act_title: str) -> list[dict]:
    """
    Return a chronological list of AMENDS edges pointing to an Act.
    Shows what legislation changed it and when.
    """
    cypher = """
    MATCH (amending:LegalDoc)-[r:LEGAL_RELATIONSHIP {action_type: 'AMENDS'}]->(target:LegalDoc)
    WHERE toLower(target.title) CONTAINS toLower($act_title)
       OR toLower(target.act_name) CONTAINS toLower($act_title)
       OR toLower(target.citation) CONTAINS toLower($act_title)
    RETURN amending.id AS amending_id, amending.title AS amending_title,
           r.detail AS detail, r.date AS date, r.confidence AS confidence
    ORDER BY r.date ASC
    LIMIT 200
    """
    with _driver_session() as s:
        return s.run(cypher, act_title=act_title).data()


# ─────────────────────────────────────────────
# AGENT 3 TOOLS: Context Aggregator
# ─────────────────────────────────────────────

def mcp_read_document_text(
    node_ids: list[str],
    corpus_lookup: dict[str, dict],
    max_chars: int = 1200,
) -> list[dict]:
    """
    Retrieve the full text (vector_text) from the local corpus JSON file for a list of chunk IDs.
    Returns [{node_id, doc_title, section, heading, content}] per matched ID.
    Does NOT query Neo4j — uses the local JSON corpus.
    """
    results = []
    for nid in node_ids[:10]:
        chunk = corpus_lookup.get(nid)
        if not chunk:
            results.append({"node_id": nid, "content": "⚠️ No text available in corpus."})
            continue

        text = chunk.get("content", chunk.get("vector_text", "")) or ""
        if "| TEXT:" in text:
            text = text.split("| TEXT:", 1)[-1].strip()

        heading = chunk.get("heading", "") or ""
        if re.match(r'^[\s.]+$', heading):
            heading = ""

        results.append({
            "node_id":   nid,
            "doc_title": chunk.get("doc_title", ""),
            "section":   chunk.get("section", ""),
            "heading":   heading,
            "content":   text[:max_chars],
        })
    return results


# ─────────────────────────────────────────────
# AGENT 4 TOOLS: Senior Counsel
# ─────────────────────────────────────────────

def mcp_submit_final_answer(answer: str) -> str:
    """
    Validate and submit the final answer from Agent 4.
    Checks that the answer cites at least one source node.
    """
    if not answer or len(answer.strip()) < 50:
        raise ValueError("Answer is too short or empty.")
    return answer.strip()
