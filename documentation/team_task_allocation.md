# LegalKGent — 3-Member Team Task Allocation

> All 3 members contribute at **every stage**. Data Collection & Processing are combined into a single stage.

| Member | Name / Role |
|--------|------------|
| **M1** | Member 1 |
| **M2** | Member 2 |
| **M3** | Member 3 |

---

## Stage 1: Data Collection & Processing (M1 + M2 only)

*Scripts: [1_download_legislation.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/1_download_legislation.py), [2_build_corpus_legislation.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/2_build_corpus_legislation.py), [3_download_caselaw.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/3_download_caselaw.py), [4_build_corpus_final.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/4_build_corpus_final.py), [5_build_glossary_summaries.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/5_build_glossary_summaries.py)*
*Utilities: [xml_parser.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/utils/xml_parser.py), [normalizers.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/utils/normalizers.py), [config.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/config.py)*

> **Sequential flow:** M1 completes data collection first → M2 then processes the collected data.

### M1 — Data Collection (Download Legislation & Case Law)

*Downloads all raw data from government APIs and saves to disk.*

| # | File | Lines | What |
|---|------|-------|------|
| 1 | `config.py` | 10–29 | Define data directory paths: `RAW_LEGISLATION_DIR`, `RAW_SI_DIR`, `RAW_CASELAW_DIR`, `AMENDMENTS_DIR` |
| 2 | `config.py` | 197–209 | Define XML namespaces: `LEG_NS`, `AKN_NS`, `META_NS` for all parsers |
| 3 | `1_download_legislation.py` | 70–155 | Define `TRANSPORT_SUBJECTS`, `TITLE_KEYWORDS`, `SEED_ACTS` — configure what legislation to discover |
| 4 | `1_download_legislation.py` | 165–222 | Implement `URIRegistry` class — deduplicated store for discovered legislation URIs |
| 5 | `1_download_legislation.py` | 230–288 | Implement `parse_atom_feed()` — parse Atom XML feeds, extract entries and pagination links |
| 6 | `1_download_legislation.py` | 293–341 | Implement `build_session()`, `get_bytes()`, `save_file()` — HTTP client with retry and rate limiting |
| 7 | `1_download_legislation.py` | 348–444 | Implement `discover_by_subject()`, `discover_by_title()`, `discover_by_year_enum()` — 3-layer URI discovery |
| 8 | `1_download_legislation.py` | 449–531 | Implement `download_legislation()`, `download_effects()` — fetch and save XML files to disk |
| 9 | `1_download_legislation.py` | 537–563 | Implement `save_manifest()` — record download metadata as JSON |
| 10 | `1_download_legislation.py` | 567–640 | Implement `run()` — main orchestrator chaining discovery → filter → download → manifest |
| 11 | `3_download_caselaw.py` | 54–86 | Implement `build_session()`, `get_bytes()` — HTTP helpers for National Archives API |
| 12 | `3_download_caselaw.py` | 93–118 | Implement `generate_queries()` — read legislation corpus titles to create search queries |
| 13 | `3_download_caselaw.py` | 125–172 | Implement `search_caselaw()`, `find_all_judgment_links()` — search Atom feed and collect judgment URLs |
| 14 | `3_download_caselaw.py` | 179–245 | Implement `download_judgments()`, `run()` — download judgment XML files with skip-existing checks |

### M2 — Data Processing (Parse XML, Build Corpus & Glossary)

*Parses all downloaded XML into a unified corpus and generates glossary summaries.*

| # | File | Lines | What |
|---|------|-------|------|
| 1 | `utils/xml_parser.py` | 25–79 | Implement namespace helpers: `_leg()`, `_akn()`, `_meta()`, `extract_text_recursive()`, `_extract_own_text()` |
| 2 | `utils/xml_parser.py` | 86–565 | Implement `CLMLParser` class — recursive CLML walker for UK legislation XML (Parts → Chapters → Sections) |
| 3 | `utils/xml_parser.py` | 148–186 | Implement `CLMLParser._extract_defined_terms()` — parse `<Term>` elements into `defined_terms` dict |
| 4 | `utils/xml_parser.py` | 338–450 | Implement `CLMLParser._process_section()` — extract section text, subsections, commentary refs, block amendments |
| 5 | `utils/xml_parser.py` | 582–682 | Implement `parse_caselaw_xml()` — AKN namespace parser for court judgment XML |
| 6 | `utils/xml_parser.py` | 707–778 | Implement `_ref_to_section_id()`, `_extract_section_refs()` — map Section Ref attributes to chunk IDs |
| 7 | `utils/xml_parser.py` | 781–944 | Implement `parse_effects_xml()`, `_map_effect_type()` — parse effects feed into ground-truth amendment triples |
| 8 | `utils/xml_parser.py` | 954–1004 | Implement `build_smart_corpus()` — unified corpus builder combining legislation, SI, and case law parsers |
| 9 | `utils/xml_parser.py` | 1007–1023 | Implement `load_effects_triples()` — load all effects triples from amendments directory |
| 10 | `utils/normalizers.py` | 12–83 | Implement `normalize_action()`, `normalize_citation()`, `extract_act_name()` — normalisation utilities |
| 11 | `utils/normalizers.py` | 86–122 | Implement `build_abbreviation_table()`, `build_id_to_title_map()` — auto-extract abbreviations and ID-to-title map |
| 12 | `utils/normalizers.py` | 125–144 | Implement `extract_matched_glossary()` — match glossary terms in text via regex |
| 13 | `2_build_corpus_legislation.py` | 1–62 | Implement `main()` — build intermediate legislation-only corpus from raw XML |
| 14 | `4_build_corpus_final.py` | 1–48 | Implement `main()` — merge legislation + case law + effects into final unified corpus |
| 15 | `5_build_glossary_summaries.py` | 1–38 | Define `GLOSSARY_FILE` path and `SYSTEM_PROMPT` for LLM-based summarisation |
| 16 | `5_build_glossary_summaries.py` | 39–73 | Implement `summarize_term()` — call vLLM to summarise a single defined term with retry logic |
| 17 | `5_build_glossary_summaries.py` | 75–120 | Implement corpus loading, term extraction, and existing summary resume logic |
| 18 | `5_build_glossary_summaries.py` | 121–171 | Implement multi-threaded summarisation loop with `ThreadPoolExecutor`, checkpoint saves, and progress logging |

### Stage 1 Outputs
- `data/raw_legislation/*.xml`, `data/raw_statutory_instruments/*.xml`, `data/raw_caselaw/*.xml`
- `data/legal_corpus_final.json`, `data/glossary_summaries.json`, `data/effects_triples.json`

---

## Stage 2: Knowledge Graph Creation (M3 only)

*Scripts: [6_extract_triples.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/6_extract_triples.py), [7_ingest_neo4j.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/7_ingest_neo4j.py)*
*Utilities: [llm/prompts.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/llm/prompts.py), [llm/client.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/llm/client.py), [neo4j_client.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/utils/neo4j_client.py), [config.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/config.py)*

> **Ownership:** M3 has complete end-to-end responsibility for Knowledge Graph generation via vLLM and Neo4j ingestion.

### M3 — LLM Extraction, Knowledge Graph Prompts & Neo4j Ingestion

*Extracts triples using local vLLM, processes JSON, and ingests into Neo4j.*

| # | File | Lines | What |
|---|------|-------|------|
| 1 | `config.py` | 85–97 | Define `CANONICAL_ACTIONS` list — 17 valid relationship types |
| 2 | `config.py` | 98–165 | Define `ACTION_NORMALIZER` — map ~100+ verb variations |
| 3 | `config.py` | 171–195 | Define `CONCEPTS` list — domain concept definitions |
| 4 | `llm/prompts.py` | 1–49 | `LEGISLATION_PROMPT` — extraction rules and JSON schema |
| 5 | `llm/prompts.py` | 51–82 | `CASELAW_PROMPT` — judicial action types and citation rules |
| 6 | `llm/client.py` | 12–23 | `get_vllm_client()` |
| 7 | `llm/client.py` | 26–32 | `get_query_client()` |
| 8 | `llm/client.py` | 35–72 | `parse_llm_json()` — robust LLM output parsing |
| 9 | `6_extract_triples.py` | 1–42 | Script setup, imports, config |
| 10 | `6_extract_triples.py` | 43–183 | `extract_triples()` — per-chunk prompt + glossary injection + vLLM call |
| 11 | `6_extract_triples.py` | 190–246 | `post_process()`, `print_quality_report()` |
| 12 | `6_extract_triples.py` | 253–357 | `main()` — batch extraction with ThreadPoolExecutor + checkpoints |
| 13 | `utils/neo4j_client.py` | 1–73 | `get_driver()` — Neo4j driver wrapper |
| 14 | `7_ingest_neo4j.py` | 41–102 | `load_all_triples()`, `load_corpus_lookup()` |
| 15 | `7_ingest_neo4j.py` | 109–210 | `ingest_triples()` — `:LegalDoc` nodes, `:LEGAL_RELATIONSHIP` edges, confidence scoring |
| 16 | `7_ingest_neo4j.py` | 217–246 | `create_concept_nodes()` |
| 17 | `7_ingest_neo4j.py` | 253–295 | `verify_graph()` — node/edge counts, relationship distribution |
| 18 | `7_ingest_neo4j.py` | 302–351 | `ingest_graph_edges()` — deterministic structural edges |
| 19 | `7_ingest_neo4j.py` | 358–394 | `main()` — orchestrate ingestion pipeline |

### Stage 2 Outputs
- `data/triples_legislation.json`, `data/triples_caselaw.json`
- Neo4j graph with `:LegalDoc`, `:Concept` nodes and `:LEGAL_RELATIONSHIP` edges

---

## Stage 3: Multi-Agent Framework & Evaluation

*Scripts: [8_build_index.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/8_build_index.py), [9_multi_agent_graphrag.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/9_multi_agent_graphrag.py), [10_evaluate.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/10_evaluate.py)*
*Utilities: [mcp_tools.py](file:///home/suriya/Documents/ARU_AIML/PROJECT_app_ml/utils/mcp_tools.py)*

### M1 — FAISS Setup, Agent 1 (Retriever) & Agent 4 (Senior Counsel)

| File | Lines | What |
|------|-------|------|
| `8_build_index.py` | 1–30 | Script setup and config imports |
| `8_build_index.py` | 69–104 | Build `IndexFlatIP`, save `index.faiss` + `id_map.json`, sanity check |
| `utils/mcp_tools.py` | 1–97 | `AGENT_TOOL_REGISTRY`, `_is_write_query()` sandbox, `mcp_semantic_search()` |
| `utils/mcp_tools.py` | 265–311 | `mcp_submit_final_answer()` |
| `9_multi_agent_graphrag.py` | 114–178 | Agent 1 — `run_agent1_retriever()`: Generates semantic queries for FAISS |
| `9_multi_agent_graphrag.py` | 416–480 | Agent 4 — `run_agent4_counsel()`: Synthesises final answer with citations |

### M2 — Embeddings, Evaluations & Pipeline Orchestrator

| File | Lines | What |
|------|-------|------|
| `8_build_index.py` | 31–68 | Corpus loading, SentenceTransformer encoding |
| `9_multi_agent_graphrag.py` | 1–59 | Imports, globals, model config for multi-agent |
| `9_multi_agent_graphrag.py` | 60–113 | `_init()`, `_llm_call()` — lazy loader + OpenRouter client |
| `9_multi_agent_graphrag.py` | 487–515 | `run_pipeline()` — chain all 4 agents |
| `9_multi_agent_graphrap.py` | 516–535 | `main()` + test questions |
| `10_evaluate.py` | 1–29 | Eval setup, Phoenix tracer |
| `10_evaluate.py` | 30–62 | `TEST_QUESTIONS` — 5 test cases with expected actions |
| `10_evaluate.py` | 64–120 | `evaluate()` scoring loop — `has_answer`, `has_grounding`, `actions_found` |
| `10_evaluate.py` | 121–148 | Summary stats and results output |

### M3 — Agent 2 (Graph Engineer) & Agent 3 (Aggregator)

| File | Lines | What |
|------|-------|------|
| `utils/mcp_tools.py` | 104–188 | `mcp_get_schema()`, `mcp_search_nodes_by_title()`, `mcp_execute_cypher()`, `mcp_get_node_subgraph()` |
| `utils/mcp_tools.py` | 191–258 | `mcp_find_relationships()`, `mcp_find_case_interpretations()`, `mcp_find_amendments_timeline()` |
| `utils/mcp_tools.py` | 288–311 | `mcp_read_document_text()` |
| `9_multi_agent_graphrag.py` | 180–357 | Agent 2 — `_parse_agent2_action()`, `run_agent2_graph_engineer()` |
| `9_multi_agent_graphrag.py` | 358–415 | Agent 3 — `run_agent3_aggregator()` |

### Stage 3 Outputs
- `data/faiss_index/index.faiss`, `data/faiss_index/id_map.json`
- `results/evaluation_results.json`

---

## Summary: Contribution Matrix

| Stage | M1 | M2 | M3 |
|-------|----|----|-----|
| **1. Data Collection & Processing** | Data collection: API discovery, HTTP client, download legislation + case law | Data processing: CLML/AKN parsers, corpus assembly, glossary summarisation | — |
| **2. KG Creation** | — | — | Full KG Pipeline: Prompts, vLLM extraction, JSON parsing, Neo4j ingestion, concept nodes |
| **3. Multi-Agent Framework** | FAISS Index build, Agent 1 (Retriever), Agent 4 (Senior Counsel) | Embeddings, Pipeline Orchestration, full 10_evaluate.py | Agent 2 (Graph Engineer) w/ Neo4j traversal tools, Agent 3 (Aggregator) |
