"""
LegalKGent — Neo4j Client Utilities
=====================================
Shared Neo4j connection management and schema queries.
"""

from neo4j import GraphDatabase
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

_driver = None


def get_driver():
    """Get or create a singleton Neo4j driver."""
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        _driver.verify_connectivity()
        print(f"✅ Connected to Neo4j at {NEO4J_URI}")
    return _driver


def close_driver():
    """Close the Neo4j driver if open."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
        print("✅ Neo4j connection closed")


def clear_graph():
    """Delete all nodes and relationships from the graph."""
    driver = get_driver()
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    print("🗑️  Cleared existing graph")


def create_indexes():
    """Create indexes for faster lookups."""
    driver = get_driver()
    with driver.session() as session:
        session.run("CREATE INDEX IF NOT EXISTS FOR (n:LegalDoc) ON (n.id)")
        session.run("CREATE INDEX IF NOT EXISTS FOR (n:LegalDoc) ON (n.citation)")
    print("📇 Created indexes on LegalDoc.id and LegalDoc.citation")


def get_schema() -> dict:
    """Fetch graph schema for the system prompt."""
    driver = get_driver()
    with driver.session() as s:
        node_count = s.run("MATCH (n) RETURN count(n) AS cnt").single()["cnt"]
        edge_count = s.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()["cnt"]
        actions = {r["action"]: r["count"] for r in s.run(
            "MATCH ()-[r:LEGAL_RELATIONSHIP]->() "
            "RETURN r.action_type AS action, count(*) AS count ORDER BY count DESC"
        )}
        concepts = [r["name"] for r in s.run(
            "MATCH (c:Concept) RETURN c.name AS name ORDER BY name"
        )]
        sample_edges = [dict(r) for r in s.run(
            "MATCH (s)-[r:LEGAL_RELATIONSHIP]->(t) "
            "RETURN s.id AS source_id, r.action_type AS action, "
            "r.detail AS detail, t.citation AS target LIMIT 5"
        )]
    return {
        "node_count":   node_count,
        "edge_count":   edge_count,
        "action_types": actions,
        "concepts":     concepts,
        "sample_edges": sample_edges,
    }
