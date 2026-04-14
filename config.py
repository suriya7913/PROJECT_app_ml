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
VLLM_BASE_URL  = os.environ.get("VLLM_BASE_URL", "http://localhost:8000/v1")
VLLM_MODEL     = os.environ.get("VLLM_MODEL",    "Qwen/Qwen3-8B")
# Optional: Use a smaller, faster model just for summarizing definitions (e.g., 1.5B or 7B)
GLOSSARY_MODEL = os.environ.get("GLOSSARY_MODEL", "Qwen/Qwen2.5-3B-Instruct")

# ─────────────────────────────────────────────
# LLM — Mistral (cloud, for query agent)
# ─────────────────────────────────────────────
LIGHTNING_API_KEY = os.environ.get("LIGHTNING_API_KEY", "e5f2bb78-88e0-4607-8e18-f4c86f6604d9/subburaj2927/fraud-model")
QUERY_MODEL   = "openai/gpt-5.4-2026-03-05"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")
OPEN_ROUTER= os.environ.get("OPEN_ROUTER", "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")
# ─────────────────────────────────────────────
# EMBEDDINGS
# ─────────────────────────────────────────────
EMBED_MODEL      = "all-MiniLM-L6-v2"
EMBED_DIM        = 384
EMBED_BATCH_SIZE = 200
MAX_TEXT_LEN     = 512

# ─────────────────────────────────────────────
# PROCESSING
# ─────────────────────────────────────────────
NUM_WORKERS = 4        # parallel workers — local vLLM, no rate limits
BATCH_SIZE  = 5000     # max corpus chunks per extraction run
SAVE_EVERY  = 20       # checkpoint interval
MAX_RETRIES = 3        # per-chunk retry limit
DOWNLOAD_RATE_LIMIT_WAIT = 0.2  # wait time between requests to avoid rate limits
MAX_PAGES_PER_FEED = 300       # safety cap per atom feed
VLLM_TIMEOUT = 300     # seconds per vLLM request

# ─────────────────────────────────────────────
# FAISS
# ─────────────────────────────────────────────
MIN_SIM_THRESHOLD = 0.25  # below this cosine similarity = "no match"

# ─────────────────────────────────────────────
# CASE LAW PREFIXES
# ─────────────────────────────────────────────
CASE_PREFIXES = ["uksc_", "ewca_", "ewhc_", "ukut_", "ukftt_", "eat_", "ewfc_"]

def is_caselaw(id_str: str) -> bool:
    """Check if a chunk ID belongs to case law."""
    return any(p in id_str for p in CASE_PREFIXES)

# ─────────────────────────────────────────────
# CANONICAL ACTIONS — the ONLY valid relationship types in the KG
# ─────────────────────────────────────────────
CANONICAL_ACTIONS = [
    # Structural modifications (LLM acts as safety net fallback for API)
    "AMENDS", "REPEALS", "SUBSTITUTES", "INSERTS", "COMMENCES", "REVOKES",
    # Semantic
    "APPLIES", "CITES", "OVERRULES", "DEFINES", "INTERPRETS", "DELEGATES", "IMPLEMENTS",
    # Power & obligation
    "CREATES", "EMPOWERS", "REQUIRES", "PROHIBITS", "EXTENDS", "AFFIRMS"
]

# Map any LLM variation to canonical form
ACTION_NORMALIZER = {
    # --- AMENDS ---
    "AMEND": "AMENDS", "AMENDED": "AMENDS", "AMENDS": "AMENDS", "AMENDING": "AMENDS",
    "MODIFY": "AMENDS", "MODIFIES": "AMENDS", "MODIFIED": "AMENDS",
    "CHANGE": "AMENDS", "CHANGES": "AMENDS", "CHANGED": "AMENDS",
    "VARY": "AMENDS", "VARIES": "AMENDS", "VARIED": "AMENDS",
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
    # --- Judicial extras ---
    "FOLLOW": "CITES", "FOLLOWS": "CITES", "FOLLOWED": "CITES",
    "APPROVE": "AFFIRMS", "APPROVES": "AFFIRMS", "APPROVED": "AFFIRMS",
    "CONSIDER": "CITES", "CONSIDERS": "CITES", "CONSIDERED": "CITES",
    "UPHELD": "AFFIRMS", "UPHOLD": "AFFIRMS", "UPHOLDS": "AFFIRMS",
    "AFFIRM": "AFFIRMS", "AFFIRMS": "AFFIRMS", "AFFIRMED": "AFFIRMS",
}

# ─────────────────────────────────────────────
# LEGAL DOMAIN CONCEPTS (for 7_ingest_neo4j.py)
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