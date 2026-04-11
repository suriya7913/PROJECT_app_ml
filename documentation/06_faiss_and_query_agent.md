# Step 5: FAISS Index Build (`8_build_index.py`)

> Generates dense vector embeddings for every corpus chunk and stores them in a FAISS inner-product index for fast semantic search used by the Multi-Agent GraphRAG pipeline (Step 6).

---

## Execution Flow

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

---

## Embedding Configuration

| Parameter | Value | Description |
|-----------|-------|-------------|
| Model | `all-MiniLM-L6-v2` | Sentence-transformers model |
| Dimension | 384 | Vector size |
| Batch Size | 512 | Encoding batch |
| Max Text Length | 512 chars | Input truncation |
| Index Type | `IndexFlatIP` | Exact inner product search |
| Normalization | L2-normalized → IP = cosine similarity | |

---

## ID Map Entry Format

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

## Outputs

| File | Description |
|------|-------------|
| `data/faiss_index/index.faiss` | Binary FAISS index (N × 384 vectors) |
| `data/faiss_index/id_map.json` | Metadata array mapping FAISS row → `{node_id, doc_title, section, source, text}` |

> These outputs are consumed by **Agent 1 (Retriever)** in the [Multi-Agent GraphRAG pipeline](06_multi_agent_graphrag.md).
