"""
LegalKGent — Phase 1: GraphRAG Embedding Builder
=================================================
Generates embeddings for all LegalDoc corpus chunks using sentence-transformers
(all-MiniLM-L6-v2) and builds a FAISS index for fast semantic search.

Output:
  data/faiss_index/index.faiss  — FAISS inner-product index (L2-normalized = cosine)
  data/faiss_index/id_map.json  — maps FAISS vector position → {node_id, text, doc_title, source}

Usage:
  python3 5_graphrag_embeddings.py

Takes ~1-3 minutes on CPU for 17K corpus chunks.
"""

import json
import os
import numpy as np

from sentence_transformers import SentenceTransformer
import faiss

# ── CONFIG ──────────────────────────────────────────────────────────────────
CORPUS_FILE  = "data/legal_corpus_final.json"
TRIPLES_FILE = "data/extracted_triples.json"
INDEX_DIR    = "data/faiss_index"
INDEX_FILE   = os.path.join(INDEX_DIR, "index.faiss")
IDMAP_FILE   = os.path.join(INDEX_DIR, "id_map.json")

EMBED_MODEL  = "all-MiniLM-L6-v2"   # 384-dim, fast, good for legal text
BATCH_SIZE   = 512                   # encode batch size
MAX_TEXT_LEN = 512                   # truncate characters (not tokens) for speed

os.makedirs(INDEX_DIR, exist_ok=True)

# ── LOAD MODEL ───────────────────────────────────────────────────────────────
print(f"📦 Loading embedding model: {EMBED_MODEL} ...")
model = SentenceTransformer(EMBED_MODEL)
DIM = model.get_sentence_embedding_dimension()
print(f"   Embedding dimension: {DIM}")

# ── LOAD CORPUS ───────────────────────────────────────────────────────────────
print(f"\n📂 Loading corpus from {CORPUS_FILE} ...")
with open(CORPUS_FILE, "r") as f:
    corpus = json.load(f)
print(f"   {len(corpus)} chunks loaded")

# ── BUILD TEXT RECORDS ────────────────────────────────────────────────────────
# Each record: {node_id, text_to_embed, doc_title, source, section, year}
# We embed a short, informative string that captures:
#   "<Act Title> | Section <N> | <section text first 400 chars>"
records = []
seen_ids = set()

for chunk in corpus:
    node_id   = chunk.get("id", "")
    doc_title = chunk.get("doc_title", "")
    section   = chunk.get("section", "")
    content   = chunk.get("content", "")
    source    = chunk.get("source", "legislation")
    year      = chunk.get("year", "")

    if not node_id:
        continue
    if node_id in seen_ids:
        continue
    seen_ids.add(node_id)

    # Build embedding text: concise summary of this chunk
    # Format: "TITLE | SECTION N | TEXT..."
    embed_text = f"{doc_title}"
    if section:
        embed_text += f" | Section {section}"
    if content:
        # Strip the "ACT: ... | TEXT:" prefix if present (from kg_creation format)
        text_body = content
        if "| TEXT:" in text_body:
            text_body = text_body.split("| TEXT:")[-1].strip()
        embed_text += f" | {text_body[:MAX_TEXT_LEN]}"

    records.append({
        "node_id":   node_id,
        "text":      embed_text,
        "doc_title": doc_title,
        "section":   section,
        "year":      year,
        "source":    source,
    })

print(f"\n📝 {len(records)} unique node records prepared for embedding")

# ── GENERATE EMBEDDINGS ───────────────────────────────────────────────────────
texts = [r["text"] for r in records]
n     = len(texts)
print(f"\n🔄 Encoding {n} texts in batches of {BATCH_SIZE} ...")
print("   (This may take 1-3 minutes on CPU)")

all_embeddings = []
for start in range(0, n, BATCH_SIZE):
    batch = texts[start : start + BATCH_SIZE]
    vecs  = model.encode(batch, show_progress_bar=False, normalize_embeddings=True)
    all_embeddings.append(vecs)
    done = min(start + BATCH_SIZE, n)
    print(f"   ... {done}/{n} encoded")

embeddings = np.vstack(all_embeddings).astype("float32")
print(f"   ✅ Embeddings shape: {embeddings.shape}")

# ── BUILD FAISS INDEX ─────────────────────────────────────────────────────────
# IndexFlatIP = inner-product search; since vectors are L2-normalized, this = cosine similarity
print(f"\n🗂️  Building FAISS IndexFlatIP (cosine) ...")
index = faiss.IndexFlatIP(DIM)
index.add(embeddings)
print(f"   ✅ Index size: {index.ntotal} vectors")

# ── SAVE INDEX ────────────────────────────────────────────────────────────────
faiss.write_index(index, INDEX_FILE)
print(f"   💾 Saved FAISS index → {INDEX_FILE}")

# ── SAVE ID MAP ───────────────────────────────────────────────────────────────
# id_map[i] = record for the i-th vector in the FAISS index
id_map = records  # already in the same order as embeddings
with open(IDMAP_FILE, "w") as f:
    json.dump(id_map, f, indent=2)
print(f"   💾 Saved ID map ({len(id_map)} entries) → {IDMAP_FILE}")

# ── QUICK SMOKE TEST ─────────────────────────────────────────────────────────
print("\n🔍 Smoke test — searching for 'self-driving vehicles regulations' ...")
test_vec = model.encode(["self-driving vehicles regulations"], normalize_embeddings=True).astype("float32")
scores, indices = index.search(test_vec, k=5)
print("   Top-5 matches:")
for rank, (score, idx) in enumerate(zip(scores[0], indices[0]), 1):
    rec = id_map[idx]
    print(f"   {rank}. [{score:.4f}] {rec['doc_title']} § {rec['section']}  (id={rec['node_id']})")

print("\n✅ Phase 1 complete — FAISS index ready for hybrid GraphRAG queries")
print(f"   Index file : {INDEX_FILE}")
print(f"   ID map file: {IDMAP_FILE}")
