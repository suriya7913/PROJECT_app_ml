# Step 4 — Neo4j Ingestion (`4_ingest_neo4j.py`)

> **314 lines** · Ingests extracted triples into Neo4j with confidence accumulation, corpus enrichment, concept nodes, and provenance tracking.

---

## Complete Execution Flow

```mermaid
flowchart TD
    START(["main()"]) --> LOAD_T["load_all_triples()<br/>Merge 3 sources:<br/>• extracted_triples.json<br/>• extracted_triples_caselaw.json<br/>• effects_triples.json"]
    LOAD_T --> LOAD_C["load_corpus_lookup()<br/>Build {id → chunk} dict<br/>from legal_corpus_final.json"]
    LOAD_C --> CLEAR["🗑️ clear_graph()<br/>MATCH (n) DETACH DELETE n"]
    CLEAR --> INDEX["📇 create_indexes()<br/>• LegalDoc.id<br/>• LegalDoc.citation"]
    INDEX --> INGEST["ingest_triples(all_triples, corpus_lookup)"]
    INGEST --> CONCEPTS["🧠 create_concept_nodes()<br/>3 domain concepts"]
    CONCEPTS --> VERIFY["📊 verify_graph()<br/>Print statistics"]
    VERIFY --> CLOSE["close_driver()"]
    CLOSE --> DONE(["✅ Done"])
```

---

## Per-Triple Ingestion Flow

```mermaid
flowchart TD
    TRIPLE["Input triple dict"] --> TARGET_CHECK{"target exists<br/>and non-empty?"}
    TARGET_CHECK -->|no| SKIP["Skip"]
    TARGET_CHECK -->|yes| ACTION["Validate action is canonical<br/>normalize_action() fallback → CITES"]
    ACTION --> CONF["Set confidence<br/>default: 1.0"]
    CONF --> TYPE["Determine source_type:<br/>is_caselaw(source_id)?<br/>→ 'CaseLaw' or 'Legislation'"]

    TYPE --> ENRICH["Corpus enrichment:<br/>Look up chunk by source_id"]

    subgraph "📚 Enrichment"
        ENRICH --> HEADING["heading<br/>(skip whitespace-only)"]
        ENRICH --> PART["part"]
        ENRICH --> CONTENT["content_snippet<br/>(strip prefix, limit 500 chars)"]
    end

    HEADING & PART & CONTENT --> CYPHER["Execute MERGE Cypher"]
```

---

## Cypher MERGE Pattern

```mermaid
flowchart LR
    subgraph "Source Node"
        S[":LegalDoc<br/>{id: source_id}"]
    end

    subgraph "Edge"
        R[":LEGAL_RELATIONSHIP<br/>{action_type: action}"]
    end

    subgraph "Target Node"
        T[":LegalDoc<br/>{citation: target}"]
    end

    S -->|"MERGE"| R
    R -->|"MERGE"| T
```

### ON CREATE vs ON MATCH Behavior

| Property | ON CREATE (new edge) | ON MATCH (existing edge) |
|----------|---------------------|--------------------------|
| `confidence` | Set to initial value | **Accumulated**: `1 - (1-old) × (1-new)` |
| `source_ids` | `[source_id]` | Append if not already present |
| `times_seen` | `1` | Increment by 1 |
| `detail` | Set from triple | COALESCE (keep existing or set new) |
| `date` | Set from triple | COALESCE (keep existing or set new) |
| `provenance` | Set from triple | Not updated on match |

### Confidence Accumulation Formula (Noisy-OR)

```
new_confidence = 1.0 − (1.0 − existing_confidence) × (1.0 − incoming_confidence)
```

```mermaid
graph LR
    subgraph "Example: Edge seen 3 times"
        T1["Observation 1<br/>conf = 0.7"] --> C1["Running: 0.70"]
        T2["Observation 2<br/>conf = 0.8"] --> C2["Running: 0.94<br/><i>1-(1-0.7)(1-0.8)</i>"]
        T3["Observation 3<br/>conf = 0.6"] --> C3["Running: 0.976<br/><i>1-(1-0.94)(1-0.6)</i>"]
    end
```

---

## Neo4j Graph Schema

```mermaid
graph LR
    subgraph "Node Types"
        LD[":LegalDoc<br/>id, citation, title,<br/>type, heading, part,<br/>act_name, content_snippet"]
        CO[":Concept<br/>name, description"]
    end

    LD -->|":LEGAL_RELATIONSHIP<br/>{action_type, detail,<br/>confidence, date,<br/>source_ids, times_seen,<br/>provenance}"| LD
    LD -->|":REGULATES"| CO
```

### `:LegalDoc` Node Properties

| Property | Type | Source | Example |
|----------|------|--------|---------|
| `id` | string | Chunk ID | `ukpga_2024_3.xml_1` |
| `type` | string | Computed | `"Legislation"` or `"CaseLaw"` |
| `title` | string | Triple / corpus | `"Automated Vehicles Act 2024"` |
| `citation` | string | Triple target | `"Road Traffic Act 1988 s.5"` |
| `act_name` | string | Extracted | `"Road Traffic Act 1988"` |
| `heading` | string | Corpus chunk | `"Meaning of 'self-driving'"` |
| `part` | string | Corpus chunk | `"Part 1 — Automated vehicles"` |
| `content_snippet` | string | Corpus (500 chars) | Section text |

### `:LEGAL_RELATIONSHIP` Edge Properties

| Property | Type | Description |
|----------|------|-------------|
| `action_type` | string | One of 19 canonical actions |
| `detail` | string | Detail text (e.g., textual substitution) |
| `confidence` | float | Accumulated (1.0 for API ground-truth) |
| `date` | string | Effective date (YYYY-MM-DD) |
| `source_ids` | list[string] | All chunk IDs that contributed |
| `times_seen` | int | Observation count |
| `provenance` | string | `"llm_extracted"` or `"effects_api"` |

### `:Concept` Nodes

| Property | Type | Description |
|----------|------|-------------|
| `name` | string | Concept name |
| `description` | string | What this concept covers |

---

## Concept Node Creation

```mermaid
flowchart TD
    CONCEPTS["3 Legal Domain Concepts"] --> LOOP["For each concept"]

    subgraph "Per Concept"
        LOOP --> CREATE["MERGE :Concept node<br/>SET name, description"]
        CREATE --> KEYWORDS["For each keyword<br/>in concept.keywords"]
        KEYWORDS --> MATCH["MATCH :LegalDoc<br/>WHERE title/citation/act_name<br/>CONTAINS keyword"]
        MATCH --> LINK["MERGE (:LegalDoc)-[:REGULATES]→(:Concept)"]
    end
```

| Concept | Keywords (sample) | Description |
|---------|-------------------|-------------|
| **Transport and Infrastructure** | transport, road traffic, highway, motor vehicle, railway, aviation, shipping, automated vehicle, taxi | Legislation covering road, rail, aviation, maritime transport |
| **Criminal Justice** | criminal, police, offence, court, sentencing, prison, prosecution | Legislation covering criminal law, policing, sentencing |
| **Energy and Environment** | energy, electricity, gas, climate, environment, carbon, renewable, nuclear | Legislation covering energy, climate change, environment |

---

## Graph Verification (`verify_graph()`)

Prints comprehensive statistics:

```
📊 Graph Stats:
   Nodes: 1234
   Edges: 5678
   Types: {'Legislation': 1000, 'CaseLaw': 234}
   Actions: {'AMENDS': 500, 'CITES': 300, ...}
   Provenance: {'llm_extracted': 4000, 'effects_api': 1678}

📈 Confidence Accumulation:
   Edges seen >1 time: 456
   Avg confidence: 0.9234
   Max times seen: 12
```

---

## Neo4j Client (`utils/neo4j_client.py`)

```mermaid
classDiagram
    class neo4j_client {
        -_driver: Neo4j Driver (singleton)
        +get_driver(): Driver
        +close_driver(): void
        +clear_graph(): void
        +create_indexes(): void
        +get_schema(): dict
    }
```

### `get_schema()` Return Structure

```json
{
  "node_count":   1234,
  "edge_count":   5678,
  "action_types": { "AMENDS": 500, "CITES": 300 },
  "concepts":     ["Criminal Justice", "Energy and Environment", "Transport and Infrastructure"],
  "sample_edges": [
    { "source_id": "ukpga_2024_3.xml_1", "action": "AMENDS", "detail": "...", "target": "Road Traffic Act 1988 s.5" }
  ]
}
```
