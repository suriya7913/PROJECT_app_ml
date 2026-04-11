# LegalKGent — Pipeline Overview & Architecture

> A 9-step GraphRAG pipeline that builds a UK Legal Knowledge Graph from raw legislation XML and provides a hybrid query agent.

---

## High-Level Architecture

```mermaid
flowchart TD
    subgraph " 🌐 Data Collection"
        A1["<b>Step 1a</b><br/>1_download_legislation.py<br/><i>Async legislation downloader</i>"]
        A2["<b>Step 1b</b><br/>3_download_caselaw.py<br/><i>Case law downloader</i>"]
    end

    subgraph " ⚙️ Data Processing"
        B1["<b>Step 2a</b><br/>2_build_corpus_legislation.py<br/><i>Legislation → JSON corpus</i>"]
        B2["<b>Step 2b</b><br/>4_build_corpus_final.py<br/><i>Unified corpus + effects</i>"]
        B3["<b>Step 2.5</b><br/>5_build_glossary_summaries.py<br/><i>LLM glossary summaries</i>"]
        C["<b>Step 3</b><br/>6_extract_triples.py<br/><i>LLM triple extraction</i>"]
    end

    subgraph " 🧠 Knowledge Graph"
        D["<b>Step 4</b><br/>7_ingest_neo4j.py<br/><i>Graph construction</i>"]
        E["<b>Step 5</b><br/>8_build_index.py<br/><i>FAISS vector index</i>"]
    end

    subgraph " 🔍 Querying & Evaluation"
        F["<b>Step 6</b><br/>9_multi_agent_graphrag.py<br/><i>Multi-Agent Pipeline (4 Agents)</i>"]
        G["<b>Step 7</b><br/>10_evaluate.py<br/><i>Accuracy scoring</i>"]
    end

    subgraph " ☁️ External Services"
        LegAPI["legislation.gov.uk<br/>OpenAPI"]
        CaseLawAPI["National Archives<br/>Case Law API"]
        vLLM["vLLM Server<br/>(Qwen3-8B)"]
        Neo4j["Neo4j<br/>Graph Database"]
        LightningAI["Lightning AI<br/>(GPT-5.4)"]
    end

    LegAPI -->|"Atom feeds + XML"| A1
    CaseLawAPI -->|"AKN XML"| A2
    A1 -->|"raw XML files"| B1
    B1 -->|"legislation corpus"| A2
    A2 -->|"raw caselaw XML"| B2
    B2 -->|"legal_corpus_final.json"| B3
    B2 -->|"effects_triples.json"| D
    B3 -->|"glossary_summaries.json"| C
    B2 -->|"legal_corpus_final.json"| C
    B2 -->|"legal_corpus_final.json"| E
    C -->|"extracted_triples.json"| D
    vLLM -.->|"LLM inference"| C
    vLLM -.->|"Glossary summarization"| B3
    D -->|"Cypher MERGE"| Neo4j
    E -->|"FAISS index"| F
    Neo4j -.->|"Graph traversal (MCP)"| F
    LightningAI -.->|"4-Agent reasoning pipeline"| F
    F -->|"synthesised answers"| G
```

---

## Module Dependency Map

```mermaid
graph TB
    subgraph "Pipeline Scripts (numbered steps)"
        S1a["1_download_legislation.py"]
        S1b["3_download_caselaw.py"]
        S2a["2_build_corpus_legislation.py"]
        S2b["4_build_corpus_final.py"]
        S25["5_build_glossary_summaries.py"]
        S3["6_extract_triples.py"]
        S4["7_ingest_neo4j.py"]
        S5["8_build_index.py"]
        S6["9_multi_agent_graphrag.py"]
        S7["10_evaluate.py"]
    end

    subgraph "config.py"
        CFG["Paths · Credentials · Models<br/>Canonical Actions · XML Namespaces"]
    end

    subgraph "utils/"
        XP["xml_parser.py<br/><i>CLML, AKN, Effects, Notes parsers</i>"]
        NM["normalizers.py<br/><i>Action/citation normalization</i>"]
        NC["neo4j_client.py<br/><i>Driver, schema, indexes</i>"]
        MCP["mcp_tools.py<br/><i>Sandboxed graph traversal functions</i>"]
    end

    subgraph "llm/"
        LC["client.py<br/><i>vLLM + Lightning AI clients</i>"]
        LP["prompts.py<br/><i>System prompts for extraction</i>"]
    end

    S1a --> CFG
    S1b --> CFG
    S2a --> CFG & XP & NM
    S2b --> CFG & XP & NM
    S25 --> CFG & LC
    S3 --> CFG & NM & LC & LP
    S4 --> CFG & NM & NC
    S5 --> CFG
    S6 --> CFG & NC & LC & MCP
    S7 --> S6
    XP --> CFG
    NM --> CFG
    NC --> CFG
    LC --> CFG
```

---

## File Map

| File | Location | Purpose |
|------|----------|---------|
| `config.py` | Root | Centralised configuration — paths, credentials, models, canonical actions |
| `1_download_legislation.py` | Root | Async parallel downloader for UK legislation & SI XML |
| `3_download_caselaw.py` | Root | Case law downloader from National Archives API |
| `2_build_corpus_legislation.py` | Root | Parses legislation/SI XML into intermediate corpus |
| `4_build_corpus_final.py` | Root | Builds unified corpus (legislation + caselaw) + effects triples |
| `5_build_glossary_summaries.py` | Root | LLM-powered glossary term summarization |
| `6_extract_triples.py` | Root | LLM-based knowledge triple extraction with checkpointing |
| `7_ingest_neo4j.py` | Root | Ingests triples into Neo4j with confidence accumulation |
| `8_build_index.py` | Root | Builds FAISS vector index from corpus embeddings |
| `6_query_agent.py` | Root | *(Deprecated)* Legacy GraphRAG ReAct agent |
| `9_multi_agent_graphrag.py` | Root | Standardised 4-Agent Orchestration (Retriever, Graph Engineer, Context Aggregator, Senior Counsel) |
| `10_evaluate.py` | Root | Runs test questions and scores agent accuracy |
| `validate.py` | Root | Combined validation script for corpus, triples, and graph |
| `utils/xml_parser.py` | utils/ | All XML parsers — legislation, case law, effects, notes |
| `utils/normalizers.py` | utils/ | Action & citation normalization, abbreviation expansion |
| `utils/neo4j_client.py` | utils/ | Neo4j connection management and schema queries |
| `utils/mcp_tools.py` | utils/ | Tool catalogue for Graph Engineer / Aggregator containing sandboxed Cypher macros |
| `llm/client.py` | llm/ | LLM client wrappers (vLLM + Lightning AI) and JSON parsing |
| `llm/prompts.py` | llm/ | System prompts for triple extraction |

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
      RAW_SI_DIR
      AMENDMENTS_DIR
      CORPUS_FILE
      TRIPLES_FILE
      INDEX_DIR
    🗄️ Neo4j
      NEO4J_URI
      NEO4J_USER
      NEO4J_PASSWORD
    🤖 LLMs
      vLLM (Qwen3-8B)
      Glossary (Qwen2.5-3B-Instruct)
      Lightning AI (GPT-5.4)
    📐 Embeddings
      all-MiniLM-L6-v2
      384 dimensions
      batch=200, max_len=512
    ⚡ Processing
      NUM_WORKERS=4
      BATCH_SIZE=5000
      SAVE_EVERY=20
      MAX_RETRIES=3
    ⚖️ Canonical Actions
      20 valid types
      120+ normalizer mappings
    🏷️ Concepts
      Transport & Infrastructure
      Criminal Justice
      Energy & Environment
```

### The 20 Canonical Actions

| Category | Actions |
|----------|---------|
| **Legislative Modification** | `AMENDS`, `REPEALS`, `SUBSTITUTES`, `INSERTS`, `COMMENCES`, `REVOKES`, `APPLIES`, `CITES`, `OVERRULES` |
| **Semantic** | `DEFINES`, `INTERPRETS`, `DELEGATES`, `IMPLEMENTS` |
| **Power & Obligation** | `CREATES`, `EMPOWERS`, `REQUIRES`, `PROHIBITS`, `EXTENDS` |
| **Judicial** | `AFFIRMS` |

The `ACTION_NORMALIZER` dictionary maps **120+ LLM output variations** to these 20 canonical forms:

```
"AMEND" → "AMENDS"       "OMIT" → "REPEALS"        "REPLACE" → "SUBSTITUTES"
"AUTHORISE" → "EMPOWERS"  "FOLLOWS" → "CITES"       "SET_ASIDE" → "OVERRULES"
"MANDATE" → "REQUIRES"    "RESTRICT" → "PROHIBITS"   "UPHELD" → "AFFIRMS"
```

### `is_caselaw(id_str: str) → bool`

Checks if a chunk ID belongs to case law by presence of prefixes: `uksc_`, `ewca_`, `ewhc_`, `ukut_`, `ukftt_`, `eat_`, `ewfc_`.
