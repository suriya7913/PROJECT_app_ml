#!/usr/bin/env python3
"""
LegalKGent — Step 4: Ingest into Neo4j
=========================================
Ingests extracted triples into Neo4j with:
  - Confidence accumulation (MedKGent formula)
  - Corpus enrichment (heading, part, content_snippet)
  - Effects triples (ground-truth from API, confidence=1.0)
  - Concept node creation

Usage:
    python 7_ingest_neo4j.py

Reads:
    data/extracted_triples.json
    data/extracted_triples_caselaw.json  (if exists)
    data/effects_triples.json            (if exists)
    data/legal_corpus_final.json

Writes to Neo4j:
    :LegalDoc nodes, :Concept nodes, :LEGAL_RELATIONSHIP edges
"""

import json
import os
import re

from config import (
    TRIPLES_FILE, CASELAW_TRIPLES_FILE, EFFECTS_TRIPLES_FILE,
    CORPUS_FILE, CANONICAL_ACTIONS, CASE_PREFIXES, CONCEPTS,
    is_caselaw,
)
from utils.normalizers import normalize_action
from utils.neo4j_client import get_driver, close_driver, clear_graph, create_indexes


# ─────────────────────────────────────────────
# LOAD ALL DATA
# ─────────────────────────────────────────────

def load_all_triples() -> list[dict]:
    """Load and merge legislation, caselaw, and effects triples."""
    all_triples = []

    # Legislation triples
    if os.path.exists(TRIPLES_FILE):
        with open(TRIPLES_FILE, "r", encoding="utf-8") as f:
            leg = json.load(f)
        print(f" Loaded {len(leg)} legislation triples")
        all_triples.extend(leg)
    else:
        print(f" No legislation triples: {TRIPLES_FILE}")

    # Case law triples
    if os.path.exists(CASELAW_TRIPLES_FILE):
        with open(CASELAW_TRIPLES_FILE, "r", encoding="utf-8") as f:
            case = json.load(f)
        print(f" Loaded {len(case)} case law triples")
        all_triples.extend(case)

    # Effects triples (ground-truth from API) — flatten source_chunk_ids
    if os.path.exists(EFFECTS_TRIPLES_FILE):
        with open(EFFECTS_TRIPLES_FILE, "r", encoding="utf-8") as f:
            effects_raw = json.load(f)
        effects_count = 0
        for eff in effects_raw:
            # Each effect may map to multiple source chunks
            source_ids = eff.get("source_chunk_ids", [])
            if not source_ids:
                # Fallback: use source_title as node ID
                source_ids = [eff.get("source_title", "UNKNOWN")]
            for sid in source_ids:
                all_triples.append({
                    "source_id": sid,
                    "source_title": eff.get("source_title"),
                    "source_section": eff.get("source_section"),
                    "action": eff.get("action", "CITES"),
                    "target_citation": eff.get("target_citation"),
                    "target_act_name": eff.get("target_act_name"),
                    "detail_text": eff.get("effect_type_raw"),
                    "effective_date": eff.get("effective_date"),
                    "confidence": eff.get("confidence", 1.0),
                    "provenance": "effects_api",
                })
                effects_count += 1
        print(f" Loaded {len(effects_raw)} effects → {effects_count} flattened edges (ground-truth)")

    print(f" Total triples to ingest: {len(all_triples)}")
    return all_triples


def load_corpus_lookup() -> dict:
    """Load corpus for enrichment."""
    lookup = {}
    with open(CORPUS_FILE, "r", encoding="utf-8") as f:
        corpus = json.load(f)
    for c in corpus:
        cid = c.get("chunk_id")
        if cid:
            lookup[cid] = c
    print(f" Loaded {len(lookup)} corpus chunks for enrichment")
    return lookup


# ─────────────────────────────────────────────
# INGEST TRIPLES
# ─────────────────────────────────────────────

def ingest_triples(all_triples: list[dict], corpus_lookup: dict):
    """Ingest triples into Neo4j with confidence accumulation."""
    driver = get_driver()
    loaded = 0
    skipped = 0

    with driver.session() as session:
        for t in all_triples:
            source_id = t.get("source_id", t.get("source_title", "UNKNOWN"))
            target = t.get("target_citation")
            action = t.get("action", "CITES")

            # Validate action is canonical
            if action not in CANONICAL_ACTIONS:
                action = normalize_action(action) or "CITES"

            confidence = t.get("confidence", 1.0)
            if confidence is None:
                confidence = 1.0

            if not target or not target.strip():
                skipped += 1
                continue

            # Determine source type
            source_type = "CaseLaw" if is_caselaw(source_id) else "Legislation"

            # Corpus enrichment
            chunk = corpus_lookup.get(source_id, {})
            heading = chunk.get("heading")
            part = chunk.get("part")
            content = chunk.get("content", "")

            # Clean content snippet
            content_snippet = content
            if "| TEXT:" in content_snippet:
                content_snippet = content_snippet.split("| TEXT:", 1)[-1].strip()
            content_snippet = content_snippet[:500] if content_snippet else None

            # Clean heading
            if heading and re.match(r'^[\s.]+$', heading):
                heading = None

            # Cypher: create nodes + relationship with confidence accumulation
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
                r.times_seen = 1,
                r.provenance = $provenance
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
                    provenance=t.get("provenance", "llm_extracted"),
                )
            except Exception as e:
                print(f"    Error on triple {loaded}: {e}")
                skipped += 1
                continue

            loaded += 1
            if loaded % 500 == 0:
                print(f"   ... loaded {loaded} triples")

    print(f"\n{'='*50}")
    print(f" NEO4J INGESTION COMPLETE")
    print(f"   Loaded: {loaded} triples")
    print(f"   Skipped: {skipped}")


# ─────────────────────────────────────────────
# CONCEPT NODES
# ─────────────────────────────────────────────

def create_concept_nodes():
    """Create higher-level Concept nodes and link LegalDoc nodes."""
    driver = get_driver()

    with driver.session() as session:
        for concept in CONCEPTS:
            # Create Concept node
            session.run(
                "MERGE (c:Concept {name: $name}) SET c.description = $desc",
                name=concept["name"], desc=concept["description"]
            )

            # Link LegalDocs by keyword matching
            for keyword in concept["keywords"]:
                session.run("""
                    MATCH (n:LegalDoc)
                    WHERE toLower(COALESCE(n.title, '')) CONTAINS toLower($kw)
                       OR toLower(COALESCE(n.citation, '')) CONTAINS toLower($kw)
                       OR toLower(COALESCE(n.act_name, '')) CONTAINS toLower($kw)
                    MERGE (n)-[:REGULATES]->(c:Concept {name: $concept_name})
                """, kw=keyword, concept_name=concept["name"])

            # Count links
            result = session.run(
                "MATCH (n:LegalDoc)-[:REGULATES]->(c:Concept {name: $name}) RETURN count(n) AS cnt",
                name=concept["name"]
            ).single()
            print(f"   {concept['name']}: {result['cnt']} docs linked")

    print(" Concept nodes created")


# ─────────────────────────────────────────────
# VERIFY
# ─────────────────────────────────────────────

def verify_graph():
    """Print graph statistics."""
    driver = get_driver()
    with driver.session() as session:
        nodes = session.run("MATCH (n:LegalDoc) RETURN count(n) as cnt").single()["cnt"]
        edges = session.run("MATCH ()-[r]->() RETURN count(r) as cnt").single()["cnt"]
        actions = {r["action"]: r["count"] for r in session.run("""
            MATCH ()-[r:LEGAL_RELATIONSHIP]->()
            RETURN r.action_type AS action, count(*) AS count ORDER BY count DESC
        """)}
        type_dist = {r["type"]: r["cnt"] for r in session.run("""
            MATCH (n:LegalDoc) WHERE n.type IS NOT NULL
            RETURN n.type AS type, count(n) AS cnt
        """)}

        # Confidence stats
        conf = session.run("""
            MATCH ()-[r:LEGAL_RELATIONSHIP]->()
            WHERE r.times_seen > 1
            RETURN count(r) AS accumulated_edges,
                   avg(r.confidence) AS avg_confidence,
                   max(r.times_seen) AS max_times_seen
        """).single()

        # Provenance stats
        prov = {r["prov"]: r["cnt"] for r in session.run("""
            MATCH ()-[r:LEGAL_RELATIONSHIP]->()
            RETURN COALESCE(r.provenance, 'llm_extracted') AS prov, count(*) AS cnt
            ORDER BY cnt DESC
        """)}

    print(f"\n Graph Stats:")
    print(f"   Nodes: {nodes}")
    print(f"   Edges: {edges}")
    print(f"   Types: {type_dist}")
    print(f"   Actions: {actions}")
    print(f"   Provenance: {prov}")

    if conf and conf["accumulated_edges"]:
        print(f"\n Confidence Accumulation:")
        print(f"   Edges seen >1 time: {conf['accumulated_edges']}")
        print(f"   Avg confidence: {conf['avg_confidence']:.4f}")
        print(f"   Max times seen: {conf['max_times_seen']}")


# ─────────────────────────────────────────────
# INGEST GRAPH_EDGES FROM LEGISLATION CHUNKS
# ─────────────────────────────────────────────

def ingest_graph_edges(corpus_lookup: dict):
    """Create deterministic structural edges from legislation chunks' graph_edges metadata."""
    driver = get_driver()
    loaded = 0

    with driver.session() as session:
        for cid, chunk in corpus_lookup.items():
            ge = chunk.get("graph_edges")
            if not ge:
                continue

            source_title = chunk.get("doc_title", cid)

            # 1. HAS_SUBSECTION edges
            for sub in ge.get("has_subsection", []):
                session.run("""
                    MERGE (s:LegalDoc {id: $source_id})
                    SET s.type = 'Legislation', s.title = COALESCE(s.title, $title)
                    MERGE (t:LegalDoc {id: $sub_id})
                    SET t.type = 'Legislation', t.title = COALESCE(t.title, $title)
                    MERGE (s)-[r:HAS_SUBSECTION]->(t)
                """, source_id=cid, title=source_title,
                   sub_id=cid.rsplit('_', 1)[0] + '_' + sub if '_' in cid else sub)
                loaded += 1

            # 2. AMENDED_BY edges from commentary refs
            for ab in ge.get("amended_by", []):
                affecting_act = ab.get("affecting_act", "")
                commentary_text = ab.get("text", "")
                if affecting_act:
                    session.run("""
                        MERGE (s:LegalDoc {id: $source_id})
                        SET s.type = 'Legislation', s.title = COALESCE(s.title, $title)
                        MERGE (t:LegalDoc {citation: $affecting})
                        SET t.act_name = COALESCE(t.act_name, $affecting)
                        MERGE (t)-[r:LEGAL_RELATIONSHIP {action_type: 'AMENDS'}]->(s)
                        ON CREATE SET
                            r.detail = $detail,
                            r.confidence = 0.95,
                            r.provenance = 'xml_commentary',
                            r.source_ids = [$source_id],
                            r.times_seen = 1
                        ON MATCH SET
                            r.times_seen = COALESCE(r.times_seen, 1) + 1,
                            r.confidence = 1.0 - (1.0 - r.confidence) * (1.0 - 0.95)
                    """, source_id=cid, title=source_title,
                       affecting=affecting_act, detail=commentary_text[:500])
                    loaded += 1

    print(f"\n Graph edges ingested: {loaded} structural edges from legislation metadata")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║  LegalKGent — Step 4: Ingest into Neo4j                 ║
╚══════════════════════════════════════════════════════════╝
    """)

    # 1. Load data
    all_triples = load_all_triples()
    corpus_lookup = load_corpus_lookup()

    # 2. Clear and set up graph
    clear_graph()
    create_indexes()

    # 3. Ingest triples
    ingest_triples(all_triples, corpus_lookup)

    # 3b. Ingest graph_edges from legislation chunks
    print("\nIngesting graph_edges from legislation metadata...")
    ingest_graph_edges(corpus_lookup)

    # 4. Create concept nodes
    print("\nCreating Concept nodes...")
    create_concept_nodes()

    # 5. Verify
    verify_graph()

    # 6. Cleanup
    close_driver()
    print("\n Done!")


if __name__ == "__main__":
    main()
