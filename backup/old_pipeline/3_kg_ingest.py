# -*- coding: utf-8 -*-
"""
LegalKGent — Enriched Neo4j Ingestion Script
=============================================
Ingests extracted triples into Neo4j with:
  - Corpus enrichment: heading, part, content_snippet from legal_corpus_final.json
  - Single :LEGAL_RELATIONSHIP edge type with action_type property
  - Confidence accumulation: s = 1 - (1-s)(1-s') when same triple re-appears
  - Provenance tracking: source_ids list on edges
  - Case law support: merges caselaw triples from separate file
  - Compatible with 6_graphrag_query.py

Usage:
  python3 3_kg_ingest.py

Reads:
  data/extracted_triples.json         (legislation triples)
  data/extracted_triples_caselaw.json (case law triples, if exists)
  data/legal_corpus_final.json        (corpus for enrichment)

Writes to Neo4j:
  :LegalDoc nodes with heading, part, content_snippet
  :LEGAL_RELATIONSHIP edges with action_type, detail, confidence
"""

from neo4j import GraphDatabase
import json
import os
import re

# ⚠️ UPDATE THESE with your Neo4j credentials
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "LegalPassword123"

# --- FILES ---
TRIPLES_FILE         = "data/extracted_triples.json"
CASELAW_TRIPLES_FILE = "data/extracted_triples_caselaw.json"
CORPUS_FILE          = "data/legal_corpus_final.json"

# --- CANONICAL ACTIONS (for validation) ---
CANONICAL_ACTIONS = [
    "AMENDS", "REPEALS", "SUBSTITUTES", "INSERTS",
    "COMMENCES", "REVOKES", "APPLIES", "CITES", "OVERRULES",
    "DEFINES", "INTERPRETS", "DELEGATES", "IMPLEMENTS",
    "CREATES", "EMPOWERS", "REQUIRES", "PROHIBITS", "EXTENDS",
]

CASE_PREFIXES = ["uksc_", "ewca_", "ewhc_", "ukut_"]

# ============================================================
# STEP 1: Connect to Neo4j
# ============================================================
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
driver.verify_connectivity()
print(f"✅ Connected to Neo4j at {NEO4J_URI}")


# ============================================================
# STEP 2: Load all data
# ============================================================

# Load legislation triples
with open(TRIPLES_FILE, "r", encoding="utf-8") as f:
    leg_triples = json.load(f)
print(f"📂 Loaded {len(leg_triples)} legislation triples from {TRIPLES_FILE}")

# Load case law triples (if file exists)
case_triples = []
if os.path.exists(CASELAW_TRIPLES_FILE):
    with open(CASELAW_TRIPLES_FILE, "r", encoding="utf-8") as f:
        case_triples = json.load(f)
    print(f"📂 Loaded {len(case_triples)} case law triples from {CASELAW_TRIPLES_FILE}")
else:
    print(f"ℹ️  No case law triples file found at {CASELAW_TRIPLES_FILE}")

# Merge all triples
all_triples = leg_triples + case_triples
print(f"📋 Total triples to ingest: {len(all_triples)}")

# Load corpus for enrichment (heading, part, content_snippet)
corpus_lookup = {}
with open(CORPUS_FILE, "r", encoding="utf-8") as f:
    corpus_data = json.load(f)
for c in corpus_data:
    cid = c.get("id")
    if cid:
        corpus_lookup[cid] = c
print(f"📂 Loaded {len(corpus_lookup)} corpus chunks for enrichment")


# ============================================================
# STEP 3: Clear existing graph (fresh start)
# ============================================================
with driver.session() as session:
    session.run("MATCH (n) DETACH DELETE n")
print("🗑️  Cleared existing graph")


# ============================================================
# STEP 4: Create indexes for performance
# ============================================================
with driver.session() as session:
    session.run("CREATE INDEX IF NOT EXISTS FOR (n:LegalDoc) ON (n.id)")
    session.run("CREATE INDEX IF NOT EXISTS FOR (n:LegalDoc) ON (n.citation)")
print("📇 Created indexes on LegalDoc.id and LegalDoc.citation")


# ============================================================
# STEP 5: Ingest triples with confidence accumulation
# ============================================================
#
# Confidence accumulation formula (from MedKGent paper):
#   s_new = 1 - (1 - s_existing) * (1 - s_incoming)
#
# If a triple appears in multiple chunks, confidence increases:
#   First:  0.8
#   Second: 1 - (1-0.8)*(1-0.6) = 0.92
#   Third:  1 - (1-0.92)*(1-0.7) = 0.976

loaded = 0
skipped = 0

with driver.session() as session:
    for t in all_triples:
        source_id = t.get("source_id", "UNKNOWN")
        target = t.get("target_citation")
        action = t.get("action", "CITES")

        # Validate action is canonical
        if action not in CANONICAL_ACTIONS:
            # Try uppercase
            if action.upper() in CANONICAL_ACTIONS:
                action = action.upper()
            else:
                action = "CITES"  # Fallback

        confidence = t.get("confidence", 1.0)
        if confidence is None:
            confidence = 1.0

        # Skip if target is null or empty
        if not target or not target.strip():
            skipped += 1
            continue

        # Determine source type from ID
        is_case = any(prefix in source_id for prefix in CASE_PREFIXES)
        source_type = "CaseLaw" if is_case else "Legislation"

        # --- Corpus enrichment ---
        # Look up the chunk in corpus to get heading, part, content_snippet
        chunk = corpus_lookup.get(source_id, {})
        heading = chunk.get("heading")
        part = chunk.get("part")
        content = chunk.get("content", "")

        # Extract clean content snippet (strip the formatted prefix)
        content_snippet = content
        if "| TEXT:" in content_snippet:
            content_snippet = content_snippet.split("| TEXT:", 1)[-1].strip()
        elif "| PARA:" in content_snippet:
            # Case law format: "CASE: ... | COURT: ... | PARA: N | TEXT: ..."
            parts = content_snippet.split("| TEXT:", 1)
            content_snippet = parts[-1].strip() if len(parts) > 1 else content_snippet
        # Truncate to 500 chars for Neo4j property size
        content_snippet = content_snippet[:500] if content_snippet else None

        # Clean heading: some have dots placeholder ". . . . ."
        if heading and re.match(r'^[\s.]+$', heading):
            heading = None

        # Cypher: create nodes and relationship with confidence accumulation
        query = """
        MERGE (s:LegalDoc {id: $source_id})
        SET s.type = $source_type,
            s.title = COALESCE(s.title, $source_title),
            s.heading = COALESCE($heading, s.heading),
            s.part = COALESCE($part, s.part),
            s.content_snippet = COALESCE($content_snippet, s.content_snippet)
        MERGE (t:LegalDoc {citation: $target})
        SET t.act_name = COALESCE(t.act_name, $target_act)

        MERGE (s)-[r:LEGAL_RELATIONSHIP {action_type: $action}]->(t)
        ON CREATE SET
            r.detail = $detail,
            r.date = $date,
            r.confidence = $confidence,
            r.source_ids = [$source_id],
            r.times_seen = 1
        ON MATCH SET
            r.confidence = 1.0 - (1.0 - r.confidence) * (1.0 - $confidence),
            r.source_ids = CASE
                WHEN NOT $source_id IN COALESCE(r.source_ids, [])
                THEN COALESCE(r.source_ids, []) + $source_id
                ELSE r.source_ids
            END,
            r.times_seen = COALESCE(r.times_seen, 1) + 1,
            r.detail = COALESCE($detail, r.detail),
            r.date = COALESCE($date, r.date)
        """
        try:
            session.run(query,
                source_id=source_id,
                source_type=source_type,
                source_title=t.get("source_title", source_id),
                heading=heading,
                part=part,
                content_snippet=content_snippet,
                target=target,
                target_act=t.get("target_act_name", target),
                action=action,
                detail=t.get("detail_text"),
                date=t.get("effective_date"),
                confidence=confidence,
            )
        except Exception as e:
            print(f"   ❌ Error on triple {loaded}: {e}")
            skipped += 1
            continue

        loaded += 1

        if loaded % 500 == 0:
            print(f"   ... loaded {loaded} triples")

print(f"\n{'='*50}")
print(f"✅ NEO4J INGESTION COMPLETE")
print(f"   Loaded: {loaded} triples")
print(f"   Skipped (null targets/errors): {skipped}")


# ============================================================
# STEP 6: Verify — print graph stats
# ============================================================
with driver.session() as session:
    nodes = session.run("MATCH (n:LegalDoc) RETURN count(n) as cnt").single()["cnt"]
    edges = session.run("MATCH ()-[r]->() RETURN count(r) as cnt").single()["cnt"]
    actions = session.run("""
        MATCH ()-[r:LEGAL_RELATIONSHIP]->()
        RETURN r.action_type AS action, count(*) AS count
        ORDER BY count DESC
    """)
    action_counts = {r["action"]: r["count"] for r in actions}

    # Source type distribution
    type_dist = session.run("""
        MATCH (n:LegalDoc)
        WHERE n.type IS NOT NULL
        RETURN n.type AS type, count(n) AS cnt
    """)
    type_counts = {r["type"]: r["cnt"] for r in type_dist}

    # Enrichment stats
    enrichment = session.run("""
        MATCH (n:LegalDoc)
        WHERE n.id IS NOT NULL
        RETURN
            count(n) AS total,
            count(n.heading) AS with_heading,
            count(n.part) AS with_part,
            count(n.content_snippet) AS with_content
    """).single()

    # Confidence stats
    conf_stats = session.run("""
        MATCH ()-[r:LEGAL_RELATIONSHIP]->()
        WHERE r.times_seen > 1
        RETURN count(r) AS accumulated_edges,
               avg(r.confidence) AS avg_confidence,
               max(r.times_seen) AS max_times_seen
    """).single()

print(f"\n📊 Graph Stats:")
print(f"   Nodes: {nodes}")
print(f"   Edges: {edges}")
print(f"   Node types: {type_counts}")
print(f"   Actions: {action_counts}")

print(f"\n📝 Enrichment Stats:")
print(f"   Source nodes total:    {enrichment['total']}")
print(f"   With heading:         {enrichment['with_heading']}")
print(f"   With part:            {enrichment['with_part']}")
print(f"   With content_snippet: {enrichment['with_content']}")

if conf_stats and conf_stats["accumulated_edges"]:
    print(f"\n📈 Confidence Accumulation:")
    print(f"   Edges seen >1 time: {conf_stats['accumulated_edges']}")
    print(f"   Avg confidence:     {conf_stats['avg_confidence']:.4f}")
    print(f"   Max times seen:     {conf_stats['max_times_seen']}")

driver.close()
print("\n✅ Neo4j connection closed")
