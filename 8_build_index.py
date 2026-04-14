#!/usr/bin/env python3
"""
LegalKGent — Step 5: Build FAISS Index
=========================================
Generates embeddings for all corpus chunks using sentence-transformers
and builds a FAISS index for fast semantic search.

Usage:
    python 8_build_index.py

Reads:
    data/legal_corpus_final.json

Writes:
    data/faiss_index/index.faiss
    data/faiss_index/id_map.json
"""

import json
import os
import numpy as np
from sentence_transformers import SentenceTransformer
import faiss

from config import (
    CORPUS_FILE, INDEX_DIR, INDEX_FILE, IDMAP_FILE,
    EMBED_MODEL, EMBED_DIM, EMBED_BATCH_SIZE, MAX_TEXT_LEN,
)


def main():
    print("""
║  LegalKGent — Step 5: Build FAISS Index                 ║
    """)
    os.makedirs(INDEX_DIR, exist_ok=True)

    # 1. Load corpus
    print(f"Loading corpus from {CORPUS_FILE}...")
    with open(CORPUS_FILE, "r", encoding="utf-8") as f:
        corpus = json.load(f)
    print(f"   Loaded {len(corpus)} chunks")

    # 2. Prepare texts and metadata
    texts = []
    id_map = []
    for chunk in corpus:
        content = chunk.get("content", "")
        if not content or len(content) < 20:
            continue
        texts.append(content[:MAX_TEXT_LEN])
        id_map.append({
            "node_id":   chunk.get("chunk_id", chunk.get("id", "UNKNOWN")),
            "doc_title": chunk.get("doc_title", ""),
            "section":   chunk.get("section", ""),
            "source":    chunk.get("source", ""),
            "text":      content[:600],
        })

    print(f"   Indexing {len(texts)} chunks (skipped {len(corpus) - len(texts)} short chunks)")

    # 3. Encode
    print(f" Loading embedding model: {EMBED_MODEL}...")
    model = SentenceTransformer(EMBED_MODEL)

    print(f"Encoding {len(texts)} chunks (batch_size={EMBED_BATCH_SIZE})...")
    embeddings = model.encode(
        texts,
        batch_size=EMBED_BATCH_SIZE,
        show_progress_bar=True,
        normalize_embeddings=True,
    ).astype("float32")

    print(f"   Embeddings shape: {embeddings.shape}")

    # 4. Build FAISS index
    print("Building FAISS index (Inner Product = cosine on L2-normalized vectors)...")
    index = faiss.IndexFlatIP(EMBED_DIM)
    index.add(embeddings)
    print(f"   Index size: {index.ntotal} vectors")

    # 5. Save
    faiss.write_index(index, INDEX_FILE)
    print(f"aved FAISS index to {INDEX_FILE}")

    with open(IDMAP_FILE, "w") as f:
        json.dump(id_map, f, indent=2, ensure_ascii=False)
    print(f"Saved ID map to {IDMAP_FILE} ({len(id_map)} entries)")

    # 6. Sanity check
    print("\nSanity check: querying 'transport road traffic'...")
    query_vec = model.encode(["transport road traffic law"], normalize_embeddings=True).astype("float32")
    scores, indices = index.search(query_vec, k=3)
    for s, i in zip(scores[0], indices[0]):
        if i >= 0:
            print(f"   [{s:.4f}] {id_map[i]['doc_title']} — {id_map[i]['section']}")

    print("\nFAISS index built!")


if __name__ == "__main__":
    main()
