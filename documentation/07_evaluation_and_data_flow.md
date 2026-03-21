# Step 7 — Evaluation & End-to-End Data Flow

> **`7_evaluate.py`** (148 lines) — Runs 5 test questions against the agent and scores accuracy.  
> This document also contains the **complete end-to-end data flow summary** across all 7 steps.

---

## Part A: Evaluation (`7_evaluate.py`)

### Execution Flow

```mermaid
flowchart TD
    START(["evaluate()"]) --> IMPORT["Import agent_ask from<br/>6_query_agent.py<br/>(via importlib.util)"]
    IMPORT --> INIT["Call _init()<br/>Load FAISS, Neo4j, Mistral"]
    INIT --> LOOP["For each TEST_QUESTION<br/>(5 questions)"]

    LOOP --> TRACE["Start Phoenix trace span<br/>(eval_q1, eval_q2, ...)"]
    TRACE --> TIMER["Start timer"]
    TIMER --> ASK["agent_ask(question)"]
    ASK --> SCORE

    subgraph "📊 Scoring"
        SCORE["Calculate metrics:"]
        SCORE --> M1["has_answer?<br/><i>Not empty, not ❌</i>"]
        SCORE --> M2["has_grounding?<br/><i>No ⚠️ in first 50 chars</i>"]
        SCORE --> M3["actions_found?<br/><i>Which expected actions<br/>appear in answer</i>"]
        SCORE --> M4["is_negative_test?<br/><i>Expected actions = empty</i>"]
    end

    M1 & M2 & M3 & M4 --> COLLECT["Append result dict"]
    COLLECT --> NEXT["Next question"]
    NEXT --> LOOP

    LOOP -->|"all done"| SUMMARY

    subgraph "📋 Summary"
        SUMMARY["Print:<br/>• Questions answered / total<br/>• Grounded answers<br/>• Average response time"]
    end

    SUMMARY --> SAVE["💾 Save evaluation_results.json"]
    SAVE --> DONE(["✅ Done"])
```

---

### Test Questions

```mermaid
graph TB
    subgraph "✅ Positive Tests"
        Q1["Q1: Employment<br/>'Which 2023 Acts modify<br/>Employment Rights Act 1996?'<br/>Expected: AMENDS, INSERTS,<br/>SUBSTITUTES, REPEALS"]
        Q2["Q2: Transport<br/>'Legislation regulating<br/>autonomous vehicles?'<br/>Expected: DEFINES,<br/>CREATES, REQUIRES"]
        Q3["Q3: Finance<br/>'Finance Act 2023<br/>dividend allowances?'<br/>Expected: AMENDS,<br/>SUBSTITUTES"]
        Q5["Q5: Transport<br/>'Safety requirements in<br/>Automated Vehicles Act 2024?'<br/>Expected: REQUIRES,<br/>CREATES, DEFINES"]
    end

    subgraph "🚫 Negative Test"
        Q4["Q4: Crypto<br/>'Cryptocurrency regulation<br/>under UK law?'<br/>Expected: NONE<br/><i>Should trigger guardrail</i>"]
    end
```

| # | Domain | Purpose | Expected Behavior |
|---|--------|---------|-------------------|
| Q1 | Employment | Cross-referencing amendments | Find specific amendment actions |
| Q2 | Transport | Broad legislation discovery | Find defining/creating legislation |
| Q3 | Finance | Specific provision changes | Find amendment details |
| Q4 | Crypto | **Negative test** | Should say "not in graph" |
| Q5 | Transport | Specific Act analysis | Find requirements/creations |

---

### Output Format — `results/evaluation_results.json`

```json
[
  {
    "question":          "Which 2023 Acts modify the Employment Rights Act 1996?",
    "domain":            "Employment",
    "answer_length":     1234,
    "elapsed_seconds":   15.3,
    "has_answer":        true,
    "has_grounding":     true,
    "expected_actions":  ["AMENDS", "INSERTS", "SUBSTITUTES", "REPEALS"],
    "actions_found":     ["AMENDS", "SUBSTITUTES"],
    "is_negative_test":  false,
    "error":             null
  },
  {
    "question":          "What are the rules for cryptocurrency regulation under UK law?",
    "domain":            "Crypto (negative test)",
    "answer_length":     320,
    "elapsed_seconds":   8.1,
    "has_answer":        true,
    "has_grounding":     false,
    "expected_actions":  [],
    "actions_found":     [],
    "is_negative_test":  true,
    "error":             null
  }
]
```

---

## Part B: End-to-End Data Flow

### Complete Pipeline Visualisation

```mermaid
flowchart TB
    subgraph "🌐 Step 1 — Download"
        API["legislation.gov.uk<br/>National Archives"] -->|"HTTP GET"| RAW["Raw XML Files<br/><b>300+ .xml files</b><br/>5 directories"]
    end

    subgraph "⚙️ Step 2 — Build Corpus"
        RAW -->|"parse_legislation_xml()<br/>parse_caselaw_xml()"| CORPUS["📄 legal_corpus_final.json<br/><b>3000-5000 chunks</b><br/>Each: id, source, doc_title,<br/>section, content, heading, part"]
        RAW -->|"parse_effects_xml()"| EFFECTS["📄 effects_triples.json<br/><b>500-2000 triples</b><br/>confidence=1.0, provenance=api"]
    end

    subgraph "🤖 Step 3 — Extract Triples"
        CORPUS -->|"vLLM (Qwen2.5-7B)<br/>+ system prompts"| TRIPLES["📄 extracted_triples.json<br/><b>2000-10000 triples</b><br/>action, target_citation,<br/>detail_text, effective_date"]
    end

    subgraph "🧠 Step 4 — Ingest"
        TRIPLES -->|"Cypher MERGE"| NEO["🗄️ Neo4j Graph<br/><b>1000-5000 nodes</b><br/><b>5000-15000 edges</b><br/>+ 3 Concept nodes"]
        EFFECTS -->|"Cypher MERGE<br/>(confidence=1.0)"| NEO
        CORPUS -->|"Corpus enrichment"| NEO
    end

    subgraph "🗂️ Step 5 — Build Index"
        CORPUS -->|"all-MiniLM-L6-v2"| FAISS["🗂️ FAISS Index<br/><b>N × 384 vectors</b><br/>+ id_map.json"]
    end

    subgraph "🔍 Step 6 — Query Agent"
        FAISS --> AGENT["🤖 ReAct Agent<br/>(Mistral large)"]
        NEO --> AGENT
        CORPUS --> AGENT
        AGENT --> ANSWER["💬 Grounded<br/>legal answer"]
    end

    subgraph "📊 Step 7 — Evaluate"
        AGENT --> EVAL["📄 evaluation_results.json<br/><b>5 test results</b><br/>has_answer, has_grounding,<br/>actions_found, elapsed_seconds"]
    end
```

---

### Data Transformation Summary Table

| Step | Input | Process | Output | Key Format |
|------|-------|---------|--------|------------|
| **1. Download** | legislation.gov.uk API | Async HTTP GET + Atom feed parsing | `data/raw_*/*.xml` (300+ files) | CLML/AKN XML |
| **2. Corpus** | XML files (5 dirs) | `parse_*_xml()` → chunks + notes enrichment | `legal_corpus_final.json` | `{id, source, doc_title, section, content, heading, part, ...}` |
| **2b. Effects** | `data/amendments/*.xml` | `parse_effects_xml()` | `effects_triples.json` | `{source_title, action, target_citation, confidence: 1.0}` |
| **3. Extract** | Corpus JSON + vLLM | LLM inference → JSON parse → normalize | `extracted_triples.json` | `{action, target_citation, detail_text, source_id, ...}` |
| **4. Ingest** | Triples + Effects + Corpus | MERGE Cypher + confidence accumulation | Neo4j `:LegalDoc` / `:LEGAL_RELATIONSHIP` / `:Concept` | Property graph |
| **5. Index** | Corpus JSON | SentenceTransformer → FAISS IndexFlatIP | `index.faiss` + `id_map.json` | Binary + `{node_id, doc_title, section, text}` |
| **6. Query** | User question | ReAct loop: semantic_search → run_cypher → lookup_corpus | Natural language answer | Grounded legal citation text |
| **7. Evaluate** | 5 test questions | `agent_ask()` per question → score | `evaluation_results.json` | `{has_answer, has_grounding, actions_found, elapsed_seconds}` |

---

### Technology Stack Summary

```mermaid
mindmap
  root((LegalKGent))
    📥 Data
      legislation.gov.uk OpenAPI
      National Archives API
      XML: CLML, AKN, Atom
    ⚙️ Processing
      Python 3.10+
      xml.etree.ElementTree
      requests + ThreadPoolExecutor
      asyncio
    🤖 LLMs
      vLLM + Qwen2.5-7B-Instruct
      Mistral large-latest
      OpenAI-compatible API
    🧠 Knowledge Graph
      Neo4j (Bolt protocol)
      Cypher queries
      Noisy-OR confidence
    🗂️ Vector Search
      FAISS IndexFlatIP
      SentenceTransformers
      all-MiniLM-L6-v2
    📊 Observability
      Arize Phoenix
      OpenTelemetry
      Trace spans per query
```
