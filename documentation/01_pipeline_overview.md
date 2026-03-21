# LegalKGent — Pipeline Overview & Architecture

> A 7-step GraphRAG pipeline that builds a UK Legal Knowledge Graph from raw legislation XML and provides a hybrid query agent.

---

## High-Level Architecture

```mermaid
flowchart TD
    subgraph " 🌐 Data Collection"
        A["<b>Step 1</b><br/>1_download_data.py<br/><i>Async parallel downloader</i>"]
    end

    subgraph " ⚙️ Data Processing"
        B["<b>Step 2</b><br/>2_build_corpus.py<br/><i>XML → JSON corpus</i>"]
        C["<b>Step 3</b><br/>3_extract_triples.py<br/><i>LLM triple extraction</i>"]
    end

    subgraph " 🧠 Knowledge Graph"
        D["<b>Step 4</b><br/>4_ingest_neo4j.py<br/><i>Graph construction</i>"]
        E["<b>Step 5</b><br/>5_build_index.py<br/><i>FAISS vector index</i>"]
    end

    subgraph " 🔍 Querying & Evaluation"
        F["<b>Step 6</b><br/>6_query_agent.py<br/><i>GraphRAG ReAct agent</i>"]
        G["<b>Step 7</b><br/>7_evaluate.py<br/><i>Accuracy scoring</i>"]
    end

    subgraph " ☁️ External Services"
        LegAPI["legislation.gov.uk<br/>OpenAPI"]
        CaseLawAPI["National Archives<br/>Case Law API"]
        vLLM["vLLM Server<br/>(Qwen2.5-7B-Instruct)"]
        Neo4j["Neo4j<br/>Graph Database"]
        Mistral["Mistral API<br/>(mistral-large-latest)"]
    end

    LegAPI -->|"Atom feeds + XML"| A
    CaseLawAPI -->|"AKN XML"| A
    A -->|"raw XML files"| B
    B -->|"legal_corpus_final.json"| C
    B -->|"effects_triples.json"| D
    B -->|"legal_corpus_final.json"| E
    C -->|"extracted_triples.json"| D
    vLLM -.->|"LLM inference"| C
    D -->|"Cypher MERGE"| Neo4j
    E -->|"FAISS index"| F
    Neo4j -.->|"Graph queries"| F
    Mistral -.->|"ReAct reasoning"| F
    F -->|"agent answers"| G
```

---

## Module Dependency Map

```mermaid
graph TB
    subgraph "Pipeline Scripts (numbered steps)"
        S1["1_download_data.py"]
        S2["2_build_corpus.py"]
        S3["3_extract_triples.py"]
        S4["4_ingest_neo4j.py"]
        S5["5_build_index.py"]
        S6["6_query_agent.py"]
        S7["7_evaluate.py"]
    end

    subgraph "config.py"
        CFG["Paths · Credentials · Models<br/>Canonical Actions · XML Namespaces"]
    end

    subgraph "utils/"
        XP["xml_parser.py<br/><i>CLML, AKN, Effects, Notes parsers</i>"]
        NM["normalizers.py<br/><i>Action/citation normalization</i>"]
        NC["neo4j_client.py<br/><i>Driver, schema, indexes</i>"]
    end

    subgraph "llm/"
        LC["client.py<br/><i>vLLM + Mistral clients</i>"]
        LP["prompts.py<br/><i>System prompts for extraction</i>"]
    end

    S1 --> CFG
    S2 --> CFG & XP & NM
    S3 --> CFG & NM & LC & LP
    S4 --> CFG & NM & NC
    S5 --> CFG
    S6 --> CFG & NC & LC
    S7 --> S6
    XP --> CFG
    NM --> CFG
    NC --> CFG
    LC --> CFG
```

---

## File Map

| File | Location | Lines | Purpose |
|------|----------|-------|---------|
| `config.py` | Root | 206 | Centralised configuration — paths, credentials, models, canonical actions |
| `1_download_data.py` | Root | 796 | Async parallel downloader for UK legislation & case law XML |
| `2_build_corpus.py` | Root | 78 | Orchestrates XML parsing into unified JSON corpus |
| `3_extract_triples.py` | Root | 362 | LLM-based knowledge triple extraction with checkpointing |
| `4_ingest_neo4j.py` | Root | 314 | Ingests triples into Neo4j with confidence accumulation |
| `5_build_index.py` | Root | 104 | Builds FAISS vector index from corpus embeddings |
| `6_query_agent.py` | Root | 477 | Hybrid GraphRAG ReAct agent (FAISS + Neo4j + Mistral) |
| `7_evaluate.py` | Root | 148 | Runs test questions and scores agent accuracy |
| `utils/xml_parser.py` | utils/ | 628 | All XML parsers — legislation, case law, effects, notes |
| `utils/normalizers.py` | utils/ | 124 | Action & citation normalization, abbreviation expansion |
| `utils/neo4j_client.py` | utils/ | 74 | Neo4j connection management and schema queries |
| `llm/client.py` | llm/ | 71 | LLM client wrappers (vLLM + Mistral) and JSON parsing |
| `llm/prompts.py` | llm/ | 85 | System prompts for triple extraction |

---

## Configuration — `config.py`

### Key Configuration Sections

```mermaid
mindmap
  root((config.py))
    📁 Data Paths
      DATA_DIR = "data"
      RAW_LEGISLATION_DIR
      RAW_CASELAW_DIR
      CORPUS_FILE
      TRIPLES_FILE
      INDEX_DIR
    🗄️ Neo4j
      NEO4J_URI
      NEO4J_USER
      NEO4J_PASSWORD
    🤖 LLMs
      vLLM (Qwen2.5-7B)
      Mistral (large-latest)
    📐 Embeddings
      all-MiniLM-L6-v2
      384 dimensions
      batch=512, max_len=512
    ⚡ Processing
      NUM_WORKERS=2
      BATCH_SIZE=5000
      SAVE_EVERY=20
      MAX_RETRIES=3
    ⚖️ Canonical Actions
      19 valid types
      120+ normalizer mappings
    🏷️ Concepts
      Transport & Infrastructure
      Criminal Justice
      Energy & Environment
```

### The 19 Canonical Actions

| Category | Actions |
|----------|---------|
| **Legislative Modification** | `AMENDS`, `REPEALS`, `SUBSTITUTES`, `INSERTS`, `COMMENCES`, `REVOKES`, `APPLIES`, `CITES`, `OVERRULES` |
| **Semantic** | `DEFINES`, `INTERPRETS`, `DELEGATES`, `IMPLEMENTS` |
| **Power & Obligation** | `CREATES`, `EMPOWERS`, `REQUIRES`, `PROHIBITS`, `EXTENDS` |

The `ACTION_NORMALIZER` dictionary maps **120+ LLM output variations** to these 19 canonical forms:

```
"AMEND" → "AMENDS"       "OMIT" → "REPEALS"        "REPLACE" → "SUBSTITUTES"
"AUTHORISE" → "EMPOWERS"  "FOLLOWS" → "CITES"       "SET_ASIDE" → "OVERRULES"
"MANDATE" → "REQUIRES"    "RESTRICT" → "PROHIBITS"   "RENEW" → "EXTENDS"
```

### `is_caselaw(id_str: str) → bool`

Checks if a chunk ID belongs to case law by presence of prefixes: `uksc_`, `ewca_`, `ewhc_`, `ukut_`.
