"""
LegalKGent — Improved Neo4j Ingestion Script
=============================================
Ingests extracted triples into Neo4j with:
  - Single :LEGAL_RELATIONSHIP edge type with action_type property
  - Confidence accumulation: s = 1 - (1-s)(1-s') when same triple re-appears
  - Provenance tracking: source_ids list on edges
  - Compatible with kg_query.py ReAct agent

Usage: Copy cells into Jupyter notebook or run as standalone script.
"""

from neo4j import GraphDatabase
import json

# ⚠️ UPDATE THESE with your Neo4j credentials
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "LegalPassword123"

# --- Connect ---
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
driver.verify_connectivity()
print(f"✅ Connected to Neo4j at {NEO4J_URI}")

# --- Load triples ---
TRIPLES_FILE = "extracted_triples (5).json"
with open(TRIPLES_FILE, "r") as f:
    triples = json.load(f)
print(f"📂 Loaded {len(triples)} triples from {TRIPLES_FILE}")

# --- Clear existing graph (fresh start) ---
with driver.session() as session:
    session.run("MATCH (n) DETACH DELETE n")
print("🗑️  Cleared existing graph")

# --- Create indexes for performance ---
with driver.session() as session:
    session.run("CREATE INDEX IF NOT EXISTS FOR (n:LegalDoc) ON (n.id)")
    session.run("CREATE INDEX IF NOT EXISTS FOR (n:LegalDoc) ON (n.citation)")
print("📇 Created indexes on LegalDoc.id and LegalDoc.citation")


# ============================================================
# Confidence accumulation formula (from MedKGent paper)
# s_new = 1 - (1 - s_existing) * (1 - s_incoming)
# 
# This means: if a triple appears multiple times, its confidence
# increases monotonically. E.g.:
#   First time:  0.8
#   Second time:  1 - (1-0.8)*(1-0.6) = 1 - 0.2*0.4 = 0.92
#   Third time:   1 - (1-0.92)*(1-0.7) = 1 - 0.08*0.3 = 0.976
# ============================================================

# --- Ingest triples with confidence accumulation ---
loaded = 0
skipped = 0
accumulated = 0

with driver.session() as session:
    for t in triples:
        source_id = t.get("source_id", "UNKNOWN")
        target = t.get("target_citation")
        action = t.get("action", "RELATES_TO")
        confidence = t.get("confidence", 1.0)  # Default 1.0 for legacy triples

        # Skip if target is null
        if not target:
            skipped += 1
            continue

        # Determine source type from the ID
        if any(prefix in source_id for prefix in ["uksc_", "ewca_", "ewhc_", "ukut_"]):
            source_type = "CaseLaw"
        else:
            source_type = "Legislation"

        # Cypher: create nodes and relationship with confidence accumulation
        # MERGE ON CREATE/ON MATCH handles dedup + confidence accumulation
        # using MedKGent formula: s = 1 - (1-s)(1-s')
        query = """
        MERGE (s:LegalDoc {id: $source_id})
        SET s.type = $source_type,
            s.title = COALESCE(s.title, $source_title)
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

        if loaded % 200 == 0:
            print(f"   ... loaded {loaded} triples")

print(f"\n{'='*50}")
print(f"✅ NEO4J INGESTION COMPLETE")
print(f"   Loaded: {loaded} triples")
print(f"   Skipped (null targets/errors): {skipped}")

# --- Verify: print graph stats ---
with driver.session() as session:
    nodes = session.run("MATCH (n:LegalDoc) RETURN count(n) as cnt").single()["cnt"]
    edges = session.run("MATCH ()-[r]->() RETURN count(r) as cnt").single()["cnt"]
    actions = session.run("""
        MATCH ()-[r:LEGAL_RELATIONSHIP]->()
        RETURN r.action_type AS action, count(*) AS count
        ORDER BY count DESC
    """)
    action_counts = {r["action"]: r["count"] for r in actions}

    # Check confidence stats (MedKGent-style)
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
print(f"   Actions: {action_counts}")

if conf_stats and conf_stats["accumulated_edges"]:
    print(f"\n📈 Confidence Accumulation Stats:")
    print(f"   Edges seen >1 time: {conf_stats['accumulated_edges']}")
    print(f"   Avg confidence (accumulated): {conf_stats['avg_confidence']:.4f}")
    print(f"   Max times seen: {conf_stats['max_times_seen']}")

driver.close()
print("\n✅ Neo4j connection closed")
