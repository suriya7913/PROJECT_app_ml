"""
LegalKGent — Centralised Configuration
========================================
Single source of truth for paths, credentials, model settings, and constants.
Import this file in every pipeline script instead of hardcoding values.
"""

import os

# ─────────────────────────────────────────────
# DATA PATHS
# ─────────────────────────────────────────────
DATA_DIR             = "data"
RAW_LEGISLATION_DIR  = os.path.join(DATA_DIR, "raw_legislation")
RAW_CASELAW_DIR      = os.path.join(DATA_DIR, "raw_caselaw")
RAW_SI_DIR           = os.path.join(DATA_DIR, "raw_statutory_instruments")
AMENDMENTS_DIR       = os.path.join(DATA_DIR, "amendments")
NOTES_DIR            = os.path.join(DATA_DIR, "explanatory_notes")

CORPUS_FILE          = os.path.join(DATA_DIR, "legal_corpus_final.json")
SMART_CORPUS_FILE    = os.path.join(DATA_DIR, "smart_corpus.json")
TRIPLES_FILE         = os.path.join(DATA_DIR, "extracted_triples.json")
CASELAW_TRIPLES_FILE = os.path.join(DATA_DIR, "extracted_triples_caselaw.json")
EFFECTS_TRIPLES_FILE = os.path.join(DATA_DIR, "effects_triples.json")
MANIFEST_FILE        = os.path.join(DATA_DIR, "download_manifest.json")

INDEX_DIR            = os.path.join(DATA_DIR, "faiss_index")
INDEX_FILE           = os.path.join(INDEX_DIR, "index.faiss")
IDMAP_FILE           = os.path.join(INDEX_DIR, "id_map.json")

RESULTS_DIR          = "results"

# ─────────────────────────────────────────────
# NEO4J
# ─────────────────────────────────────────────
NEO4J_URI      = os.environ.get("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.environ.get("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "LegalPassword123")

# ─────────────────────────────────────────────
# LLM — vLLM (local, for triple extraction)
# ─────────────────────────────────────────────
VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "http://localhost:8000/v1")
VLLM_MODEL    = os.environ.get("VLLM_MODEL",    "Qwen/Qwen2.5-7B-Instruct")

# ─────────────────────────────────────────────
# LLM — Mistral (cloud, for query agent)
# ─────────────────────────────────────────────
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "wTPmklMivGd5WB14RsLnms5DUw1pOeHh")
MISTRAL_MODEL   = os.environ.get("MISTRAL_MODEL",   "mistral-large-latest")

# ─────────────────────────────────────────────
# EMBEDDINGS
# ─────────────────────────────────────────────
EMBED_MODEL      = "all-MiniLM-L6-v2"
EMBED_DIM        = 384
EMBED_BATCH_SIZE = 512
MAX_TEXT_LEN     = 512

# ─────────────────────────────────────────────
# PROCESSING
# ─────────────────────────────────────────────
NUM_WORKERS = 2        # parallel workers — keep low for single GPU (Colab)
BATCH_SIZE  = 5000     # max corpus chunks per extraction run
SAVE_EVERY  = 20       # checkpoint interval
MAX_RETRIES = 3        # per-chunk LLM retry limit
VLLM_TIMEOUT = 120     # seconds per vLLM request (Colab needs long timeout)

# ─────────────────────────────────────────────
# FAISS
# ─────────────────────────────────────────────
MIN_SIM_THRESHOLD = 0.25  # below this cosine similarity = "no match"

# ─────────────────────────────────────────────
# CASE LAW PREFIXES
# ─────────────────────────────────────────────
CASE_PREFIXES = ["uksc_", "ewca_", "ewhc_", "ukut_"]

def is_caselaw(id_str: str) -> bool:
    """Check if a chunk ID belongs to case law."""
    return any(p in id_str for p in CASE_PREFIXES)

# ─────────────────────────────────────────────
# CANONICAL ACTIONS — the ONLY valid relationship types in the KG
# ─────────────────────────────────────────────
CANONICAL_ACTIONS = [
    # Legislative modification
    "AMENDS", "REPEALS", "SUBSTITUTES", "INSERTS",
    "COMMENCES", "REVOKES", "APPLIES", "CITES", "OVERRULES",
    # Semantic
    "DEFINES", "INTERPRETS", "DELEGATES", "IMPLEMENTS",
    # Power & obligation
    "CREATES", "EMPOWERS", "REQUIRES", "PROHIBITS", "EXTENDS",
]

# Map any LLM variation to canonical form
ACTION_NORMALIZER = {
    # --- AMENDS ---
    "AMEND": "AMENDS", "AMENDED": "AMENDS", "AMENDS": "AMENDS", "AMENDING": "AMENDS",
    # --- REPEALS ---
    "REPEAL": "REPEALS", "REPEALED": "REPEALS", "REPEALS": "REPEALS", "REPEALING": "REPEALS",
    "OMIT": "REPEALS", "OMITS": "REPEALS", "OMITTED": "REPEALS",
    # --- SUBSTITUTES ---
    "SUBSTITUTE": "SUBSTITUTES", "SUBSTITUTED": "SUBSTITUTES", "SUBSTITUTES": "SUBSTITUTES",
    "REPLACE": "SUBSTITUTES", "REPLACES": "SUBSTITUTES", "REPLACED": "SUBSTITUTES",
    # --- INSERTS ---
    "INSERT": "INSERTS", "INSERTED": "INSERTS", "INSERTS": "INSERTS", "INSERTING": "INSERTS",
    # --- COMMENCES ---
    "COMMENCE": "COMMENCES", "COMMENCED": "COMMENCES", "COMMENCES": "COMMENCES",
    # --- REVOKES ---
    "REVOKE": "REVOKES", "REVOKED": "REVOKES", "REVOKES": "REVOKES",
    # --- APPLIES ---
    "APPLY": "APPLIES", "APPLIED": "APPLIES", "APPLIES": "APPLIES",
    # --- CITES ---
    "CITE": "CITES", "CITED": "CITES", "CITES": "CITES", "CITING": "CITES",
    "REFER": "CITES", "REFERS": "CITES", "REFERRED": "CITES", "REFERENCES": "CITES",
    "MENTION": "CITES", "MENTIONS": "CITES", "MENTIONED": "CITES",
    "RELATES_TO": "CITES",
    # --- OVERRULES ---
    "OVERRULE": "OVERRULES", "OVERRULED": "OVERRULES", "OVERRULES": "OVERRULES",
    "DEPART": "OVERRULES", "DEPARTS": "OVERRULES", "DEPARTED": "OVERRULES",
    "DISAPPROVE": "OVERRULES", "DISAPPROVES": "OVERRULES", "DISAPPROVED": "OVERRULES",
    "REVERSE": "OVERRULES", "REVERSES": "OVERRULES", "REVERSED": "OVERRULES",
    "QUASH": "OVERRULES", "QUASHES": "OVERRULES", "QUASHED": "OVERRULES",
    "SET_ASIDE": "OVERRULES",
    # --- DEFINES ---
    "DEFINE": "DEFINES", "DEFINED": "DEFINES", "DEFINES": "DEFINES", "DEFINING": "DEFINES",
    # --- INTERPRETS ---
    "INTERPRET": "INTERPRETS", "INTERPRETED": "INTERPRETS", "INTERPRETS": "INTERPRETS",
    "CONSTRUE": "INTERPRETS", "CONSTRUES": "INTERPRETS", "CONSTRUED": "INTERPRETS",
    "DISTINGUISH": "INTERPRETS", "DISTINGUISHES": "INTERPRETS", "DISTINGUISHED": "INTERPRETS",
    # --- DELEGATES ---
    "DELEGATE": "DELEGATES", "DELEGATED": "DELEGATES", "DELEGATES": "DELEGATES",
    # --- IMPLEMENTS ---
    "IMPLEMENT": "IMPLEMENTS", "IMPLEMENTED": "IMPLEMENTS", "IMPLEMENTS": "IMPLEMENTS",
    "TRANSPOSES": "IMPLEMENTS", "TRANSPOSED": "IMPLEMENTS",
    # --- CREATES ---
    "CREATE": "CREATES", "CREATED": "CREATES", "CREATES": "CREATES", "CREATING": "CREATES",
    "ESTABLISH": "CREATES", "ESTABLISHES": "CREATES", "ESTABLISHED": "CREATES",
    # --- EMPOWERS ---
    "EMPOWER": "EMPOWERS", "EMPOWERED": "EMPOWERS", "EMPOWERS": "EMPOWERS",
    "AUTHORISE": "EMPOWERS", "AUTHORISES": "EMPOWERS", "AUTHORIZE": "EMPOWERS",
    "CONFER": "EMPOWERS", "CONFERS": "EMPOWERS", "CONFERRED": "EMPOWERS",
    # --- REQUIRES ---
    "REQUIRE": "REQUIRES", "REQUIRED": "REQUIRES", "REQUIRES": "REQUIRES",
    "MANDATE": "REQUIRES", "MANDATES": "REQUIRES", "OBLIGATE": "REQUIRES",
    "IMPOSE": "REQUIRES", "IMPOSES": "REQUIRES",
    # --- PROHIBITS ---
    "PROHIBIT": "PROHIBITS", "PROHIBITED": "PROHIBITS", "PROHIBITS": "PROHIBITS",
    "RESTRICT": "PROHIBITS", "RESTRICTS": "PROHIBITS", "FORBID": "PROHIBITS",
    "BAN": "PROHIBITS", "BANS": "PROHIBITS",
    # --- EXTENDS ---
    "EXTEND": "EXTENDS", "EXTENDED": "EXTENDS", "EXTENDS": "EXTENDS", "EXTENDING": "EXTENDS",
    "RENEW": "EXTENDS", "RENEWS": "EXTENDS", "RENEWED": "EXTENDS",
    "PROLONG": "EXTENDS", "PROLONGS": "EXTENDS",
    # --- Judicial extras → CITES ---
    "FOLLOW": "CITES", "FOLLOWS": "CITES", "FOLLOWED": "CITES",
    "APPROVE": "CITES", "APPROVES": "CITES", "APPROVED": "CITES",
    "CONSIDER": "CITES", "CONSIDERS": "CITES", "CONSIDERED": "CITES",
    "UPHELD": "CITES", "UPHOLD": "CITES", "UPHOLDS": "CITES",
    "AFFIRM": "CITES", "AFFIRMS": "CITES", "AFFIRMED": "CITES",
}

# ─────────────────────────────────────────────
# LEGAL DOMAIN CONCEPTS (for 4_ingest_neo4j.py)
# ─────────────────────────────────────────────
CONCEPTS = [
    {
        "name":        "Transport and Infrastructure",
        "description": "Legislation covering road, rail, aviation, maritime transport, and autonomous vehicles.",
        "keywords":    ["transport", "road traffic", "highway", "motor vehicle", "driving",
                        "railway", "aviation", "shipping", "tachograph", "traffic sign",
                        "automated vehicle", "electric vehicle", "taxi", "pedicab",
                        "road safety", "vehicle registration", "air traffic"],
    },
    {
        "name":        "Criminal Justice",
        "description": "Legislation covering criminal law, policing, sentencing, and the courts.",
        "keywords":    ["criminal", "police", "offence", "court", "sentencing", "prison",
                        "prosecution", "justice", "penalty"],
    },
    {
        "name":        "Energy and Environment",
        "description": "Legislation covering energy provision, climate change, and environmental protection.",
        "keywords":    ["energy", "electricity", "gas", "climate", "environment", "carbon",
                        "renewable", "nuclear", "oil", "petroleum"],
    },
]

# ─────────────────────────────────────────────
# XML NAMESPACES
# ─────────────────────────────────────────────
LEG_NS  = "http://www.legislation.gov.uk/namespaces/legislation"
META_NS = "http://www.legislation.gov.uk/namespaces/metadata"
DC_NS   = "http://purl.org/dc/elements/1.1/"
AKN_NS  = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
TNA_NS  = "https://caselaw.nationalarchives.gov.uk/akn"

NAMESPACES = {
    "leg": LEG_NS,
    "ukm": META_NS,
    "dc":  DC_NS,
    "akn": AKN_NS,
    "tna": TNA_NS,
}
