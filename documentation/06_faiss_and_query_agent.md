# Steps 5 & 6 — FAISS Index & Query Agent

> **`5_build_index.py`** (104 lines) — Builds a FAISS vector index for semantic search.  
> **`6_query_agent.py`** (477 lines) — Hybrid GraphRAG ReAct agent combining FAISS + Neo4j + Mistral.

---

## Part A: FAISS Index (`5_build_index.py`)

### Execution Flow

```mermaid
flowchart TD
    START(["main()"]) --> LOAD["📂 Load legal_corpus_final.json"]
    LOAD --> PREP["Prepare texts:<br/>• Skip chunks with < 20 chars<br/>• Truncate content to 512 chars"]
    PREP --> MODEL["📦 Load SentenceTransformer<br/>(all-MiniLM-L6-v2)"]
    MODEL --> ENCODE["🔢 Encode all texts<br/>batch_size = 512<br/>normalize_embeddings = True<br/>→ float32 array [N × 384]"]
    ENCODE --> BUILD["🗂️ Build FAISS IndexFlatIP<br/>(Inner Product = cosine<br/>on L2-normalized vectors)"]
    BUILD --> SAVE_IDX["💾 Save index.faiss"]
    SAVE_IDX --> SAVE_MAP["💾 Save id_map.json"]
    SAVE_MAP --> SANITY["🔬 Sanity check:<br/>Query 'transport road traffic'<br/>Show top-3 results"]
    SANITY --> DONE(["✅ Done"])
```

### Embedding Configuration

| Parameter | Value | Description |
|-----------|-------|-------------|
| Model | `all-MiniLM-L6-v2` | Sentence-transformers model |
| Dimension | 384 | Vector size |
| Batch Size | 512 | Encoding batch |
| Max Text Length | 512 chars | Input truncation |
| Index Type | `IndexFlatIP` | Exact inner product search |
| Normalization | L2-normalized → IP = cosine similarity | |

### ID Map Entry Format

```json
{
  "node_id":   "ukpga_2024_3.xml_1",
  "doc_title": "Automated Vehicles Act 2024",
  "section":   "1",
  "source":    "legislation",
  "text":      "ACT: Automated Vehicles Act 2024... [first 600 chars]"
}
```

---

## Part B: Query Agent (`6_query_agent.py`)

### Initialization Flow

```mermaid
flowchart LR
    TRACE["Optional Phoenix<br/>tracing (OTEL)"] --> EMBED["Load embedding model<br/>(all-MiniLM-L6-v2)"]
    EMBED --> FAISS["Load FAISS index<br/>+ id_map.json"]
    FAISS --> CORPUS["Load corpus into<br/>{id → chunk} lookup"]
    CORPUS --> NEO4J["Connect to Neo4j"]
    NEO4J --> MISTRAL["Create Mistral client"]
```

---

### ReAct Agent Loop (`agent_ask`)

```mermaid
flowchart TD
    QUESTION["User question"] --> SYSTEM["Build system prompt<br/>with live graph schema"]
    SYSTEM --> LOOP["ReAct Loop<br/>(max 10 steps)"]

    LOOP --> LLM["Mistral chat.complete()<br/>system + conversation"]
    LLM --> OUTPUT["Parse LLM output"]

    OUTPUT --> PARSE{"Contains?"}

    PARSE -->|"ANSWER:"| SEM{"semantic_search<br/>called at least once?"}
    SEM -->|no| FORCE["Inject: 'You MUST call<br/>semantic_search first'"]
    FORCE --> LOOP
    SEM -->|yes| CLEAN["_clean_answer()<br/>Remove leaked IDs and<br/>internal THOUGHT: blocks"]
    CLEAN --> GROUNDED{"Has any non-empty<br/>tool result?"}
    GROUNDED -->|no| WARN["Prepend ⚠️ warning:<br/>'tools did not return<br/>relevant data'"]
    GROUNDED -->|yes| FINAL["Return final answer"]
    WARN --> FINAL

    PARSE -->|"ACTION:"| TOOL{"Which tool?"}
    TOOL -->|"semantic_search()"| T1["🔍 semantic_search()"]
    TOOL -->|"run_cypher()"| T2["🔧 run_cypher()"]
    TOOL -->|"lookup_corpus()"| T3["📖 lookup_corpus()"]
    TOOL -->|"unknown"| ERR["'UNKNOWN TOOL'"]

    T1 & T2 & T3 & ERR --> OBS["Append OBSERVATION<br/>to conversation"]
    OBS --> LOOP

    PARSE -->|"neither"| REMIND["'Respond with<br/>ACTION: or ANSWER:'"]
    REMIND --> LOOP
```

---

### The Three Tools

```mermaid
graph TB
    subgraph "🔍 Tool 1: semantic_search"
        SS_IN["query, top_k=5"] --> SS_ENC["Encode query → 384d vector"]
        SS_ENC --> SS_FAISS["FAISS search → top-k indices + scores"]
        SS_FAISS --> SS_FILTER["Filter by MIN_SIM_THRESHOLD ≥ 0.25"]
        SS_FILTER --> SS_ENRICH["Enrich with heading, part from corpus"]
        SS_ENRICH --> SS_OUT["JSON array of matches"]
    end

    subgraph "🔧 Tool 2: run_cypher"
        RC_IN["Cypher query string"] --> RC_EXEC["Execute against Neo4j"]
        RC_EXEC --> RC_CHECK{"Results?"}
        RC_CHECK -->|"0 rows"| RC_WARN["⚠️ ZERO_RESULTS"]
        RC_CHECK -->|"rows"| RC_OUT["JSON array of records"]
    end

    subgraph "📖 Tool 3: lookup_corpus"
        LC_IN["Comma-separated node IDs"] --> LC_PARSE["Parse IDs (max 10)"]
        LC_PARSE --> LC_LOOKUP["Look up each in corpus dict"]
        LC_LOOKUP --> LC_OUT["JSON: node_id, doc_title,<br/>section, heading, part,<br/>content (800 chars)"]
    end
```

### `semantic_search()` Return Format

```json
[
  {
    "node_id":    "ukpga_2024_3.xml_1",
    "doc_title":  "Automated Vehicles Act 2024",
    "section":    "1",
    "heading":    "Meaning of 'self-driving'",
    "part":       "Part 1 — Automated vehicles",
    "similarity": 0.8234,
    "snippet":    "ACT: Automated Vehicles Act 2024..."
  }
]
```

---

### System Prompt Construction (`build_system_prompt`)

```mermaid
flowchart TD
    SCHEMA["get_schema() from Neo4j"] --> BUILD["Build system prompt"]

    BUILD --> S1["1️⃣ Tool definitions<br/>(semantic_search, run_cypher,<br/>lookup_corpus)"]
    S1 --> S2["2️⃣ Graph schema<br/>(node labels, edge types,<br/>properties, action_type values)"]
    S2 --> S3["3️⃣ Legal domain concepts<br/>(from :Concept nodes)"]
    S3 --> S4["4️⃣ Graph stats<br/>(node count, edge count,<br/>sample edges)"]
    S4 --> S5["5️⃣ Recommended Cypher patterns<br/>(outgoing + incoming queries)"]
    S5 --> S6["6️⃣ Output formatting rules<br/>(never expose raw IDs)"]
    S6 --> S7["7️⃣ Handling incomplete data<br/>(state clearly when data missing)"]
    S7 --> S8["8️⃣ Absolute guardrails<br/>(never answer from general knowledge,<br/>must call semantic_search first)"]
```

### Anti-Hallucination Guardrails

| Guardrail | Mechanism |
|-----------|-----------|
| **Must use tools** | Agent cannot answer without calling `semantic_search` at least once |
| **No fabrication** | If tool returns ZERO_RESULTS or NO_MATCHES, agent must inform user |
| **No general knowledge** | Only cite data returned by tools |
| **No invented citations** | Never invent section numbers, Act names, or provisions |
| **Clean output** | `_clean_answer()` strips leaked internal IDs from final answer |
| **Grounding check** | If no tool returned real data, answer is prefixed with ⚠️ warning |

---

### Answer Cleaning (`_clean_answer`)

Removes from final answer:
1. `THOUGHT: ...` blocks
2. Internal IDs like `ukpga_2023_55.xml_63` (regex patterns for all court prefixes)
3. Empty parentheses `(  )`
4. Excess whitespace and blank lines
