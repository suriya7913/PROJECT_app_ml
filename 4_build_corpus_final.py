#!/usr/bin/env python3
"""
LegalKGent — Step 2b: Build Final Unified Corpus
================================================
Parses all raw XML files (legislation, SIs, case law) into a unified
corpus with CLML structural metadata. Also loads effects triples.

Usage:
    python 2b_build_corpus_final.py

Reads:
    data/raw_legislation/*.xml
    data/raw_caselaw/*.xml
    data/raw_statutory_instruments/*.xml
    data/raw_statutory_instruments/*.xml
    data/amendments/*.xml

Writes:
    data/legal_corpus_final.json
    data/effects_triples.json
"""

import json
from config import CORPUS_FILE, EFFECTS_TRIPLES_FILE
from utils.xml_parser import build_smart_corpus, load_effects_triples
from utils.normalizers import build_abbreviation_table, build_id_to_title_map


def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║  LegalKGent — Step 2b: Build Final Unified Corpus       ║
╚══════════════════════════════════════════════════════════╝
    """)

    # 1. Build smart corpus from all raw XML
    corpus = build_smart_corpus(types=['legislation', 'si', 'caselaw'])

    # 2. Save corpus
    with open(CORPUS_FILE, "w", encoding="utf-8") as f:
        json.dump(corpus, f, indent=2, ensure_ascii=False)
    print(f"💾 Saved corpus to {CORPUS_FILE}")

    # 3. Build metadata
    abbrev_table = build_abbreviation_table(corpus)
    id_to_title = build_id_to_title_map(corpus)

    print(f"\n📊 Corpus Summary:")
    print(f"   Total chunks:     {len(corpus)}")
    print(f"   Abbreviations:    {len(abbrev_table)}")
    print(f"   Source documents: {len(id_to_title)}")

    # Count by source type
    sources = {}
    for c in corpus:
        s = c.get("source", "unknown")
        sources[s] = sources.get(s, 0) + 1
    for src, count in sorted(sources.items()):
        print(f"   {src}: {count} chunks")



    # 4. Load effects triples (ground-truth from API)
    effects = load_effects_triples()
    if effects:
        with open(EFFECTS_TRIPLES_FILE, "w", encoding="utf-8") as f:
            json.dump(effects, f, indent=2, ensure_ascii=False)
        print(f"💾 Saved {len(effects)} effects triples to {EFFECTS_TRIPLES_FILE}")

    print("\n✅ Corpus building complete!")


if __name__ == "__main__":
    main()
