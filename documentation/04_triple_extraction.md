# Step 3 — Triple Extraction (`3_extract_triples.py`)

> **362 lines** · LLM-based knowledge triple extraction using vLLM with parallel workers, resumable checkpointing, and post-processing.

---

## Complete Execution Flow

```mermaid
flowchart TD
    START(["main()"]) --> LOAD["📂 Load legal_corpus_final.json"]
    LOAD --> META["Build metadata:<br/>• abbreviation table<br/>• id_to_title map"]
    META --> VLLM["🔌 Connect to vLLM<br/>(connection test: 'Say OK')"]
    VLLM -->|"❌ fail"| ABORT(["Abort — vLLM not running"])
    VLLM -->|"✅ pass"| BATCH["build_smart_batch()<br/>Prioritize diverse chunks"]
    BATCH --> RESUME{"Existing triples<br/>file exists?"}

    RESUME -->|yes| LOAD_EX["Load existing triples<br/>Track already_done set"]
    RESUME -->|no| INIT["Initialize empty results"]
    LOAD_EX --> FILTER
    INIT --> FILTER["Filter to unprocessed<br/>chunks only"]
    FILTER -->|"none left"| POST
    FILTER -->|"chunks remain"| PARALLEL

    subgraph "⚡ Parallel Extraction"
        PARALLEL["ThreadPoolExecutor<br/>(NUM_WORKERS threads)"]
        PARALLEL --> WORKER1["Thread 1:<br/>extract_triples(chunk)"]
        PARALLEL --> WORKER2["Thread 2:<br/>extract_triples(chunk)"]
        PARALLEL --> WORKERN["Thread N:<br/>extract_triples(chunk)"]
    end

    WORKER1 & WORKER2 & WORKERN --> COLLECT["Collect results<br/>(thread-safe with Lock)"]
    COLLECT --> CHECK{"Every SAVE_EVERY<br/>(20) chunks?"}
    CHECK -->|yes| CHECKPOINT["💾 Save checkpoint<br/>+ print speed/ETA"]
    CHECK -->|no| NEXT["Next chunk"]
    CHECKPOINT --> NEXT
    NEXT --> PARALLEL

    COLLECT --> POST["📊 post_process()<br/>1. Deduplicate<br/>2. Re-normalize actions<br/>3. Re-normalize citations"]
    POST --> SAVE["💾 Save extracted_triples.json"]
    SAVE --> REPORT["print_quality_report()"]
    REPORT --> DONE(["✅ Done"])
```

---

## `extract_triples()` — Per-Chunk LLM Extraction

```mermaid
flowchart TD
    INPUT["chunk dict"] --> CHOOSE{"chunk source?"}
    CHOOSE -->|"legislation"| LEG_PROMPT["Use LEGISLATION_PROMPT"]
    CHOOSE -->|"judgment"| CASE_PROMPT["Use CASELAW_PROMPT"]

    LEG_PROMPT & CASE_PROMPT --> BUILD["Build user content message:<br/>• DOCUMENT ID<br/>• TITLE<br/>• SOURCE TYPE<br/>• PART, HEADING<br/>• IN_FORCE_DATE, EXTENT<br/>• DEFINED_TERMS<br/>• PRE-MARKED AMENDMENTS (first 5)<br/>• EXPLANATORY NOTE<br/>• Full TEXT content"]

    BUILD --> RETRY["Retry loop<br/>(up to MAX_RETRIES = 3)"]
    RETRY --> CALL["vLLM chat.completions.create()<br/>model: Qwen2.5-7B-Instruct<br/>temperature: 0.1<br/>max_tokens: 2048"]
    CALL -->|"timeout/error"| BACKOFF["Exponential backoff<br/>5s, 10s, 15s"]
    BACKOFF --> RETRY
    CALL -->|"success"| RAW["Get raw text response"]

    RAW --> PARSE["parse_llm_json(raw_text)"]
    PARSE -->|"None"| EMPTY["Return []"]
    PARSE -->|"list"| VALIDATE

    subgraph "🔍 Validate Each Item"
        VALIDATE["For each item in list"] --> HAS{"Has 'action' +<br/>'target_citation'?"}
        HAS -->|no| SKIP["Skip"]
        HAS -->|yes| NORM_A["normalize_action(action)"]
        NORM_A -->|None| SKIP
        NORM_A -->|canonical| NORM_C["normalize_citation(target,<br/>abbrev_table)"]
        NORM_C -->|"< 3 chars"| SKIP
        NORM_C -->|"valid"| ACT["extract_act_name(citation)"]
        ACT --> SELF["Detect self-amendment:<br/>source_title ∈ act_name?"]
        SELF --> ENRICH["Enrich triple with:<br/>source_id, source_title,<br/>source_section, in_force_date,<br/>extent, is_self_amendment"]
    end

    ENRICH --> RETURN["Return results list"]
```

---

## `build_smart_batch()` — Intelligent Chunk Selection

```mermaid
flowchart LR
    CORPUS["Full corpus"] --> CLASSIFY

    subgraph "Classify Chunks"
        CLASSIFY --> R["🔴 Repeal chunks<br/>'repeal' or 'omit' in text"]
        CLASSIFY --> A["🟡 Amendment chunks<br/>'amend' or 'substitut' in text"]
        CLASSIFY --> C["🔵 Case law chunks<br/>source == 'judgment'"]
        CLASSIFY --> L["⚪ Legislation chunks<br/>source == 'legislation'"]
    end

    R & A & C --> SELECTED["Union of all<br/>priority chunks"]
    L -->|"fill remaining<br/>up to BATCH_SIZE"| SELECTED
    SELECTED --> SORTED["Sort indices<br/>Limit to BATCH_SIZE"]
```

---

## `post_process()` — Deduplication & Re-normalization

```mermaid
flowchart TD
    INPUT["Raw triples list"] --> DEDUP["1️⃣ Deduplicate<br/>Key = (source_id, action, target_citation)"]
    DEDUP --> NORM_A["2️⃣ Re-normalize actions<br/>Ensure all actions are canonical"]
    NORM_A --> NORM_C["3️⃣ Re-normalize citations<br/>Expand abbreviations<br/>Standardize section refs"]
    NORM_C --> OUTPUT["Cleaned triples list"]
```

---

## System Prompts (`llm/prompts.py`)

### `LEGISLATION_PROMPT`

Instructs the LLM to extract relationships from legislation text. Defines 19 relationship types with trigger patterns:

| Action | Pattern Examples |
|--------|-----------------|
| `AMENDS` | "is amended", "for X substitute Y" |
| `REPEALS` | "is repealed", "shall cease to have effect", "is omitted" |
| `SUBSTITUTES` | "for 'X' substitute 'Y'" |
| `INSERTS` | "after section X insert" |
| `COMMENCES` | "comes into force on" |
| `DEFINES` | "'automated vehicle' means..." |
| `EMPOWERS` | "The Secretary of State may by regulations..." |
| `REQUIRES` | "the operator must...", "shall notify" |
| `PROHIBITS` | "no person shall...", "it is an offence to..." |

### `CASELAW_PROMPT`

Emphasizes judicial actions most common in case law:
- **CITES** — court refers to, considers, follows, or applies
- **OVERRULES** — court overrules, reverses, departs from
- **INTERPRETS** — court interprets or construes
- **APPLIES** — court applies a statute to the facts

### Required LLM Output Format

```json
[
  {
    "action":           "AMENDS",
    "target_citation":  "Road Traffic Act 1988 s.5",
    "detail_text":      "for 'prescribed limit' substitute 'specified limit'",
    "effective_date":   "2024-01-01"
  }
]
```

---

## LLM JSON Parser (`llm/client.py`)

### `parse_llm_json(raw_text) → list[dict] | None`

```mermaid
flowchart TD
    IN["Raw LLM text"] --> TRY1["Try json.loads(text)"]
    TRY1 -->|"✅ success"| TYPE
    TRY1 -->|"❌ fail"| REGEX["Regex search for<br/>[...] array in text"]
    REGEX -->|"found"| TRY2["Try json.loads(match)"]
    TRY2 -->|"✅ success"| TYPE
    TRY2 -->|"❌ fail"| NONE["Return None"]
    REGEX -->|"not found"| NONE

    TYPE{"Type?"}
    TYPE -->|"dict"| UNWRAP["Try keys:<br/>'mutations', 'relationships',<br/>'results', 'data'<br/>or if 'action' in dict → [dict]"]
    TYPE -->|"list"| RETURN["Return list"]
    TYPE -->|"other"| NONE

    UNWRAP --> RETURN
```

---

## Output Data Structure — `extracted_triples.json`

```json
{
  "action":              "AMENDS",
  "target_citation":     "Road Traffic Act 1988 s.5",
  "target_act_name":     "Road Traffic Act 1988",
  "detail_text":         "for 'prescribed limit' substitute 'specified limit'",
  "effective_date":      "2024-01-01",
  "source_id":           "ukpga_2024_3.xml_1",
  "source_title":        "Automated Vehicles Act 2024",
  "source_section":      "1",
  "in_force_date":       "2024-05-20",
  "extent":              "E+W+S+NI",
  "is_self_amendment":   false,
  "chunk_id":            "ukpga_2024_3.xml_1"
}
```

---

## Quality Report — `print_quality_report()`

Prints after extraction:
- **Action distribution** — count per canonical action (✅ / ❌ marker for valid/invalid)
- **Self-amendments** — count of triples where source modifies itself
- **With effective_date** — count of triples with dates
- **With detail_text** — count of triples with detail
