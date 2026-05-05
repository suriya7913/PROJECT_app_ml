# LegalKGent

**Autonomous Legal Knowledge Graph Construction and Multi-Agent GraphRAG**

> MSc Artificial Intelligence & Machine Learning — Anglia Ruskin University  
> A 9-step pipeline that builds a UK legal knowledge graph from raw legislation and case law, then answers complex legal queries through a four-agent retrieval-augmented generation framework.

---

## Overview

LegalKGent ingests UK primary legislation, statutory instruments, and court judgments from government APIs, parses them into a structured corpus, extracts semantic triples using a local Small Language Model (SLM), stores the resulting graph in Neo4j, and exposes a hybrid FAISS + graph query interface served by four specialised AI agents.

```
legislation.gov.uk  ──┐
                       ├─► Corpus  ─►  Triples  ─►  Neo4j  ─►  Multi-Agent GraphRAG  ─►  Answer
National Archives   ──┘                                 └─►  FAISS Index ──────────────────────┘
```

---

## Key Features

- **Automated data collection** — async parallel downloads from the legislation.gov.uk OpenAPI and the National Archives Case Law API
- **Structured XML parsing** — full CLML (legislation) and AKN (judgments) namespace-aware parsers with defined-term extraction
- **SLM-powered triple extraction** — local vLLM (Qwen3-8B) extracts `(subject, action, object)` triples mapped to 19 canonical relation types
- **Confidence-accumulating knowledge graph** — Neo4j graph where repeated evidence strengthens edge confidence scores
- **Hybrid retrieval** — FAISS semantic search seeds Neo4j subgraph expansion for grounded, citation-backed answers
- **Four-agent orchestration** — Retriever → Graph Engineer → Context Aggregator → Senior Counsel, each with sandboxed MCP tools
- **Judge-LLM evaluation** — automated accuracy scoring against ground-truth test cases

---

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full technical design, data flow diagrams, agent specifications, and knowledge graph schema.

---

## Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | 3.10+ |
| Neo4j | 5.x |
| vLLM server | 0.8+ with Qwen3-8B |
| CUDA GPU | Recommended for vLLM |

---

## Installation

```bash
git clone <repo-url>
cd PROJECT_app_ml
pip install -r requirements.txt
```

---

## Configuration

All settings live in [config.py](config.py). Credentials are loaded from a `.env` file (see [.env.example](.env.example)):

```bash
cp .env.example .env
# then edit .env with your values
```

Key variables:

```ini
# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password

# vLLM (local inference server)
VLLM_BASE_URL=http://localhost:8000/v1
VLLM_MODEL=Qwen/Qwen3-8B

# OpenRouter (cloud LLM for the query pipeline)
OPEN_ROUTER=sk-or-v1-...
```

Start a local vLLM server before running Steps 3–7:

```bash
vllm serve Qwen/Qwen3-8B --port 8000
```

Start Neo4j before running Steps 4–9:

```bash
neo4j start
```

---

## Pipeline

Run each step in order. Each step depends on the outputs of the previous one.

```
Step 1  ──►  Step 2  ──►  Step 3  ──►  Step 4  ──►  Step 5  ──►  Step 6  ──►  Step 7
 Data         Data        Glossary     Triples       Neo4j      FAISS Index    Query &
Collection  Processing  Summaries    Extraction    Ingestion      Build        Evaluate
```

| Step | Script | Description | Output |
|------|--------|-------------|--------|
| 1a | `1_download_legislation.py` | Download UK legislation + SI XML from legislation.gov.uk | `data/raw_legislation/`, `data/raw_statutory_instruments/` |
| 2a | `2_build_corpus_legislation.py` | Parse legislation XML into intermediate corpus | `data/legal_corpus_final.json` (legislation only) |
| 1b | `3_download_caselaw.py` | Download case law judgments (requires Step 2a output) | `data/raw_caselaw/` |
| 2b | `4_build_corpus_final.py` | Merge legislation + case law into unified corpus | `data/legal_corpus_final.json`, `data/effects_triples.json` |
| 2c | `5_build_glossary_summaries.py` | LLM-summarise defined legal terms | `data/glossary_summaries.json` |
| 3 | `6_extract_triples.py` | Extract `(subject, action, object)` triples via vLLM | `data/extracted_triples.json` |
| 4 | `7_ingest_neo4j.py` | Build knowledge graph in Neo4j | Graph: `:LegalDoc`, `:Concept` nodes + `:LEGAL_RELATIONSHIP` edges |
| 5 | `8_build_index.py` | Build FAISS semantic vector index | `data/faiss_index/index.faiss`, `data/faiss_index/id_map.json` |
| 6 | `9_multi_agent_graphrag.py` | Run 4-agent GraphRAG query pipeline | Console answers with citations |

### Running the full pipeline

```bash
python 1_download_legislation.py
python 2_build_corpus_legislation.py
python 3_download_caselaw.py
python 4_build_corpus_final.py
python 5_build_glossary_summaries.py
python 6_extract_triples.py
python 7_ingest_neo4j.py
python 8_build_index.py
```

### Running a query

```bash
python 9_multi_agent_graphrag.py
```

---

## Project Structure

```
PROJECT_app_ml/
├── config.py                        # Centralised configuration — paths, models, constants
│
├── 1_download_legislation.py        # Step 1a — Legislation + SI downloader
├── 2_build_corpus_legislation.py    # Step 2a — Legislation corpus builder
├── 3_download_caselaw.py            # Step 1b — Case law downloader
├── 4_build_corpus_final.py          # Step 2b — Unified corpus assembler
├── 5_build_glossary_summaries.py    # Step 2c — LLM glossary summariser
├── 6_extract_triples.py             # Step 3  — Triple extraction (vLLM)
├── 7_ingest_neo4j.py                # Step 4  — Neo4j ingestion
├── 8_build_index.py                 # Step 5  — FAISS index builder
├── 9_multi_agent_graphrag.py        # Step 6  — Multi-agent query pipeline
│
├── utils/
│   ├── xml_parser.py                # CLML + AKN XML parsers, effects parser
│   ├── normalizers.py               # Action/citation normalisation utilities
│   ├── neo4j_client.py              # Neo4j driver wrapper + schema helpers
│   └── mcp_tools.py                 # Sandboxed MCP tool registry for agents
│
├── llm/
│   ├── client.py                    # vLLM + Lightning AI client wrappers
│   └── prompts.py                   # System prompts for triple extraction
│
├── data/                            # Generated data (git-ignored)
│   ├── raw_legislation/
│   ├── raw_statutory_instruments/
│   ├── raw_caselaw/
│   ├── amendments/
│   ├── faiss_index/
│   └── *.json
│
├── docs/
│   └── ARCHITECTURE.md              # Full technical architecture reference
│
└── requirements.txt
```

## Dependencies

```
openai>=1.30.0          # vLLM OpenAI-compatible client
vllm>=0.8.0             # Local LLM inference server
neo4j>=5.0.0,<6.2.0     # Graph database driver
sentence-transformers   # all-MiniLM-L6-v2 embeddings
faiss-cpu>=1.7.4        # Vector similarity search
lxml>=5.0.0             # XML parsing
requests>=2.28.0        # HTTP downloads
mistralai>=1.0.0        # Mistral cloud client
```

Full list: [requirements.txt](requirements.txt)

---

## License

Academic project — Anglia Ruskin University, 2025–2026.
