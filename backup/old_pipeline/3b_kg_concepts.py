"""
LegalKGent — Phase 2: Legal Ontology — Concept Nodes
=====================================================
Creates higher-level Concept nodes in Neo4j and links existing LegalDoc nodes
via [:REGULATES] edges. This enables concept-level graph traversal.

New schema additions:
  (:Concept {name, description})          — domain concept node
  (:LegalDoc)-[:REGULATES]->(:Concept)   — statute regulates a legal concept

Usage:
  python3 3b_kg_concepts.py
  (Run AFTER 3_kg_ingest.py)
"""

from neo4j import GraphDatabase
import re

# ── CONFIG ─────────────────────────────────────────────────────────────────
NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "LegalPassword123"

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
driver.verify_connectivity()
print("✅ Connected to Neo4j")

# ── CONCEPT TAXONOMY ──────────────────────────────────────────────────────────
# Each concept: (name, description, keyword triggers)
# Keywords matched against LegalDoc.title / LegalDoc.citation / LegalDoc.act_name
# Case-insensitive substring match.
CONCEPTS = [
    {
        "name":        "Housing and Regeneration",
        "description": "Legislation governing housing provision, social housing regulation, planning, and urban regeneration.",
        "keywords":    ["housing", "regeneration", "planning", "dwelling", "leasehold", "rent", "landlord", "tenant"],
    },
    {
        "name":        "Employment and Labour",
        "description": "Legislation governing employment rights, working conditions, trade unions, and redundancy.",
        "keywords":    ["employment", "worker", "labour", "redundancy", "dismissal", "trade union", "wages", "national insurance"],
    },
    {
        "name":        "Finance and Taxation",
        "description": "Legislation covering taxation, financial services, banking, and fiscal policy.",
        "keywords":    ["finance", "tax", "taxation", "revenue", "income tax", "capital gains", "vat", "budget",
                        "corporation", "levy", "duty", "stamp", "energy profits"],
    },
    {
        "name":        "Social Security and Welfare",
        "description": "Legislation governing welfare benefits, social security payments, and universal credit.",
        "keywords":    ["social security", "welfare", "benefit", "universal credit", "pension", "disability",
                        "child tax credit", "working tax credit", "jobseeker", "support allowance"],
    },
    {
        "name":        "Transport and Infrastructure",
        "description": "Legislation covering road, rail, aviation, maritime transport, and autonomous vehicles.",
        "keywords":    ["transport", "road", "railway", "aviation", "maritime", "vehicle", "driver", "highway",
                        "autonomous", "self-driving", "pedicab", "taxi", "private hire"],
    },
    {
        "name":        "Energy and Environment",
        "description": "Legislation covering energy production, climate change, environment protection, and net zero.",
        "keywords":    ["energy", "environment", "climate", "carbon", "emissions", "nuclear", "renewable",
                        "oil", "gas", "offshore", "petroleum", "net zero"],
    },
    {
        "name":        "Building Safety and Standards",
        "description": "Legislation governing building regulations, fire safety, and structural standards.",
        "keywords":    ["building", "fire safety", "construction", "structure", "regulation", "safety"],
    },
    {
        "name":        "Financial Services and Banking",
        "description": "Legislation covering banks, building societies, insurance, and financial regulation.",
        "keywords":    ["bank", "building society", "insurance", "financial services", "pra", "fca", "prudential",
                        "credit", "mortgage", "securities"],
    },
    {
        "name":        "Public Health and NHS",
        "description": "Legislation covering public health, the National Health Service, and medical regulation.",
        "keywords":    ["health", "nhs", "medical", "medicine", "mental health", "clinical", "care", "patient",
                        "hospital", "pharmaceutical"],
    },
    {
        "name":        "Education",
        "description": "Legislation governing schools, universities, and higher education.",
        "keywords":    ["education", "school", "university", "student", "apprenticeship", "ofsted", "higher education"],
    },
    {
        "name":        "Local Government and Rates",
        "description": "Legislation governing local authorities, council tax, non-domestic rates, and governance.",
        "keywords":    ["local government", "council", "rates", "non-domestic", "authority", "municipality",
                        "unitary", "combined authority"],
    },
    {
        "name":        "Criminal Justice and Policing",
        "description": "Legislation covering crime, police powers, courts, sentencing, and prisons.",
        "keywords":    ["criminal", "police", "offence", "court", "sentencing", "prison", "prosecution",
                        "justice", "penalty"],
    },
    {
        "name":        "Immigration and Asylum",
        "description": "Legislation governing immigration, nationality, and asylum seekers.",
        "keywords":    ["immigration", "asylum", "nationality", "visa", "border", "migration", "refugee"],
    },
    {
        "name":        "Data Protection and Digital",
        "description": "Legislation covering data protection, privacy, digital infrastructure, and cyber security.",
        "keywords":    ["data protection", "digital", "cyber", "online", "internet", "gdpr", "privacy", "information"],
    },
    {
        "name":        "Intellectual Property",
        "description": "Legislation covering patents, trademarks, copyright, and trade secrets.",
        "keywords":    ["intellectual property", "patent", "trademark", "copyright", "trade secret", "design right"],
    },
    {
        "name":        "Agriculture and Food",
        "description": "Legislation governing agriculture, food safety, farming, and rural matters.",
        "keywords":    ["agriculture", "farm", "food", "fisheries", "rural", "livestock", "crop"],
    },
    {
        "name":        "Competition and Consumer",
        "description": "Legislation covering competition law, consumer protection, and market regulation.",
        "keywords":    ["competition", "consumer", "market", "monopoly", "merger", "antitrust", "unfair"],
    },
    {
        "name":        "Constitutional and Administrative",
        "description": "Legislation governing constitutional arrangements, devolution, and public administration.",
        "keywords":    ["constitutional", "devolution", "parliament", "prerogative", "public administration",
                        "westminster", "statutory instrument", "subordinate legislation"],
    },
]

# ── HELPER: keyword match ──────────────────────────────────────────────────────
def concepts_for_text(text: str) -> list[str]:
    """Return all concept names whose keywords appear in text."""
    text_lower = text.lower()
    matched = []
    for c in CONCEPTS:
        for kw in c["keywords"]:
            if kw in text_lower:
                matched.append(c["name"])
                break   # one keyword sufficient per concept
    return matched

# ── STEP 1: Create Concept nodes ──────────────────────────────────────────────
print("\n📐 Creating Concept nodes ...")
with driver.session() as s:
    for c in CONCEPTS:
        s.run(
            """
            MERGE (c:Concept {name: $name})
            SET c.description = $description
            """,
            name=c["name"],
            description=c["description"],
        )
print(f"   ✅ Created/updated {len(CONCEPTS)} Concept nodes")

# ── STEP 2: Pull all LegalDoc nodes ──────────────────────────────────────────
print("\n🔍 Fetching all LegalDoc nodes for classification ...")
with driver.session() as s:
    result = s.run("""
        MATCH (n:LegalDoc)
        RETURN n.id AS id, n.citation AS citation, n.title AS title, n.act_name AS act_name
    """)
    docs = [dict(r) for r in result]
print(f"   Found {len(docs)} LegalDoc nodes")

# ── STEP 3: Classify and link ─────────────────────────────────────────────────
print("\n🔗 Classifying nodes and creating [:REGULATES] edges ...")
linked_count   = 0
unlinked_count = 0

with driver.session() as s:
    for doc in docs:
        # Build a combined text blob for matching
        combined = " ".join(filter(None, [
            doc.get("title") or "",
            doc.get("citation") or "",
            doc.get("act_name") or "",
            doc.get("id") or "",
        ]))

        matched_concepts = concepts_for_text(combined)

        if not matched_concepts:
            unlinked_count += 1
            continue

        # Create [:REGULATES] edge for each matched concept
        # Using the node ID if available, otherwise citation
        node_id   = doc.get("id")
        citation  = doc.get("citation")

        for concept_name in matched_concepts:
            if node_id:
                s.run("""
                    MATCH (n:LegalDoc {id: $id})
                    MATCH (c:Concept {name: $concept})
                    MERGE (n)-[:REGULATES]->(c)
                """, id=node_id, concept=concept_name)
            elif citation:
                s.run("""
                    MATCH (n:LegalDoc {citation: $citation})
                    MATCH (c:Concept {name: $concept})
                    MERGE (n)-[:REGULATES]->(c)
                """, citation=citation, concept=concept_name)

        linked_count += 1

print(f"   ✅ Linked: {linked_count} nodes  |  Unlinked (no match): {unlinked_count}")

# ── STEP 4: Verify ───────────────────────────────────────────────────────────
print("\n📊 Concept node summary:")
with driver.session() as s:
    results = s.run("""
        MATCH (c:Concept)
        OPTIONAL MATCH (n:LegalDoc)-[:REGULATES]->(c)
        RETURN c.name AS concept, count(n) AS linked_docs
        ORDER BY linked_docs DESC
    """)
    for r in results:
        bar = "█" * min(r["linked_docs"], 40)
        print(f"   {r['concept']:<40} {r['linked_docs']:>4}  {bar}")

# ── STEP 5: Add structural summary edges ─────────────────────────────────────
# Concept-level summary: within each concept, which action types are most used?
print("\n🔗 Adding concept-level relationship summary (action_type stats per concept) ...")
with driver.session() as s:
    s.run("""
        MATCH (n:LegalDoc)-[:REGULATES]->(c:Concept)
        MATCH (n)-[r:LEGAL_RELATIONSHIP]->(t:LegalDoc)
        WITH c, r.action_type AS action, count(*) AS cnt
        SET c.top_actions = COALESCE(c.top_actions, '') + action + ':' + toString(cnt) + ' '
    """)
print("   ✅ Top-action summaries written to Concept nodes")

driver.close()
print("\n✅ Phase 2 complete — Concept ontology ready")
print("   You can now traverse: (LegalDoc)-[:REGULATES]->(Concept)")
print("   and filter by concept in Cypher: MATCH (n)-[:REGULATES]->(c:Concept {name: 'Housing and Regeneration'})")
