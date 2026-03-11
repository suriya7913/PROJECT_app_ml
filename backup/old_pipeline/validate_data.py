"""
LegalKGent — Data Cross-Verification Script
=============================================
Validates alignment between:
  1. Corpus (data/legal_corpus_final.json)
  2. Extracted triples (data/extracted_triples.json)
  3. FAISS index (data/faiss_index/id_map.json)

Run: python3 validate_data.py
"""

import json
from collections import Counter

CORPUS_FILE  = "data/legal_corpus_final.json"
TRIPLES_FILE = "data/extracted_triples.json"
IDMAP_FILE   = "data/faiss_index/id_map.json"

CASE_PREFIXES = ['uksc_', 'ewca_', 'ewhc_', 'ukut_']

def is_caselaw(id_str):
    return any(p in id_str for p in CASE_PREFIXES)

# ── LOAD ──────────────────────────────────────────────────────
print("Loading files...")

with open(CORPUS_FILE) as f:
    corpus = json.load(f)
print(f"  ✅ Corpus: {len(corpus)} chunks")

with open(TRIPLES_FILE) as f:
    triples = json.load(f)
print(f"  ✅ Triples: {len(triples)} triples")

with open(IDMAP_FILE) as f:
    id_map = json.load(f)
print(f"  ✅ FAISS id_map: {len(id_map)} records")


# ── 1. CORPUS ANALYSIS ───────────────────────────────────────
print(f"\n{'='*60}")
print("1. CORPUS ANALYSIS")
print(f"{'='*60}")

corpus_ids  = {c['id'] for c in corpus if c.get('id')}
corpus_leg  = {c['id'] for c in corpus if c.get('source') == 'legislation'}
corpus_case = {c['id'] for c in corpus if c.get('source') == 'judgment'}

print(f"   Total chunks:  {len(corpus)}")
print(f"   Unique IDs:    {len(corpus_ids)}")
print(f"   Legislation:   {len(corpus_leg)}")
print(f"   Case law:      {len(corpus_case)}")

# Chunk fields available
sample = corpus[0]
print(f"   Fields: {list(sample.keys())}")

with_heading    = sum(1 for c in corpus if c.get('heading'))
with_part       = sum(1 for c in corpus if c.get('part'))
with_content    = sum(1 for c in corpus if c.get('content'))
with_amendments = sum(1 for c in corpus if c.get('inline_amendments'))
with_terms      = sum(1 for c in corpus if c.get('defined_terms'))

print(f"\n   Data richness:")
print(f"     With heading:           {with_heading}/{len(corpus)}")
print(f"     With part:              {with_part}/{len(corpus)}")
print(f"     With content:           {with_content}/{len(corpus)}")
print(f"     With inline_amendments: {with_amendments}/{len(corpus)}")
print(f"     With defined_terms:     {with_terms}/{len(corpus)}")


# ── 2. TRIPLES ANALYSIS ──────────────────────────────────────
print(f"\n{'='*60}")
print("2. TRIPLES ANALYSIS")
print(f"{'='*60}")

triple_src     = {t['source_id'] for t in triples if t.get('source_id')}
triple_case    = {s for s in triple_src if is_caselaw(s)}
triple_leg     = triple_src - triple_case
with_detail    = sum(1 for t in triples if t.get('detail_text'))
with_conf      = sum(1 for t in triples if t.get('confidence') is not None)
with_date      = sum(1 for t in triples if t.get('effective_date'))

print(f"   Total triples:     {len(triples)}")
print(f"   Unique source_ids: {len(triple_src)}")
print(f"   Legislation srcs:  {len(triple_leg)}")
print(f"   Case law srcs:     {len(triple_case)}")
print(f"\n   Triple fields: {list(triples[0].keys())}")
print(f"   With detail_text:    {with_detail}/{len(triples)}")
print(f"   With confidence:     {with_conf}/{len(triples)}")
print(f"   With effective_date: {with_date}/{len(triples)}")

# Action type distribution
actions = Counter(t.get('action', '?') for t in triples)
print(f"\n   Action types:")
for a, c in actions.most_common():
    print(f"     {a}: {c}")


# ── 3. FAISS INDEX ANALYSIS ──────────────────────────────────
print(f"\n{'='*60}")
print("3. FAISS INDEX ANALYSIS")
print(f"{'='*60}")

faiss_ids  = {r['node_id'] for r in id_map}
faiss_case = {n for n in faiss_ids if is_caselaw(n)}
faiss_leg  = faiss_ids - faiss_case

print(f"   Total vectors:  {len(id_map)}")
print(f"   Unique node_ids: {len(faiss_ids)}")
print(f"   Legislation:    {len(faiss_leg)}")
print(f"   Case law:       {len(faiss_case)}")
print(f"   FAISS fields:   {list(id_map[0].keys())}")


# ── 4. CROSS-CHECK ───────────────────────────────────────────
print(f"\n{'='*60}")
print("4. CROSS-CHECK: ID ALIGNMENT")
print(f"{'='*60}")

print(f"\n   Corpus ↔ Triples:")
print(f"     Corpus IDs in triples:      {len(corpus_ids & triple_src)}")
print(f"     Corpus IDs NOT in triples:  {len(corpus_ids - triple_src)}")
print(f"     Triple IDs NOT in corpus:   {len(triple_src - corpus_ids)}")

print(f"\n   Corpus ↔ FAISS:")
print(f"     Corpus IDs in FAISS:        {len(corpus_ids & faiss_ids)}")
print(f"     Corpus IDs NOT in FAISS:    {len(corpus_ids - faiss_ids)}")
print(f"     FAISS IDs NOT in corpus:    {len(faiss_ids - corpus_ids)}")

print(f"\n   Triples ↔ FAISS:")
print(f"     Triple source IDs in FAISS: {len(triple_src & faiss_ids)}")
print(f"     Triple IDs NOT in FAISS:    {len(triple_src - faiss_ids)}")


# ── 5. CASE LAW GAP ──────────────────────────────────────────
print(f"\n{'='*60}")
print("5. CASE LAW GAP ANALYSIS")
print(f"{'='*60}")

print(f"   Case law chunks in corpus:       {len(corpus_case)}")
print(f"   Case law vectors in FAISS:       {len(faiss_case)}")
print(f"   Case law source_ids in triples:  {len(triple_case)}")
print(f"   Case law chunks with NO triples: {len(corpus_case - triple_src)} ← GAP")


# ── 6. TOP SOURCE DOCUMENTS ──────────────────────────────────
print(f"\n{'='*60}")
print("6. TOP 10 SOURCE DOCUMENTS (by triple count)")
print(f"{'='*60}")

prefixes = Counter()
for t in triples:
    sid = t.get('source_id', '')
    if '.xml_' in sid:
        prefixes[sid.rsplit('.xml_', 1)[0]] += 1

for p, c in prefixes.most_common(10):
    print(f"   {p}: {c} triples")


# ── 7. SAMPLES ───────────────────────────────────────────────
print(f"\n{'='*60}")
print("7. SAMPLE DATA")
print(f"{'='*60}")

# Sample legislation corpus chunk
leg_chunks = [c for c in corpus if c.get('source') == 'legislation']
if leg_chunks:
    c = leg_chunks[0]
    print(f"\n   [LEGISLATION CHUNK]")
    print(f"   ID: {c['id']}")
    print(f"   Title: {c.get('doc_title', '')}")
    print(f"   Section: {c.get('section', '')}")
    print(f"   Heading: {c.get('heading', '')}")
    print(f"   Part: {c.get('part', '')}")
    print(f"   Content[:250]: {c.get('content', '')[:250]}")

# Sample case law corpus chunk
case_chunks = [c for c in corpus if c.get('source') == 'judgment']
if case_chunks:
    c = case_chunks[0]
    print(f"\n   [CASE LAW CHUNK]")
    print(f"   ID: {c['id']}")
    print(f"   Title: {c.get('doc_title', '')}")
    print(f"   Section: {c.get('section', '')}")
    print(f"   Content[:250]: {c.get('content', '')[:250]}")

# Sample triple
print(f"\n   [SAMPLE TRIPLE]")
t = triples[0]
for k, v in t.items():
    print(f"   {k}: {v}")

print(f"\n{'='*60}")
print("DONE — Paste this output back to continue with the plan.")
print(f"{'='*60}")
