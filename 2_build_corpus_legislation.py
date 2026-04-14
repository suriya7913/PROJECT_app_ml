#!/usr/bin/env python3
"""
LegalKGent — Step 2a: Build Legislation Corpus
==============================================
Parses ONLY primary legislation and SIs into a unified corpus.
This is required BEFORE downloading case law, as the case law
downloader depends on the parsed legislation titles to generate queries.

Usage:
    python 2_build_corpus_legislation.py

Reads:
    data/raw_legislation/*.xml
    data/raw_statutory_instruments/*.xml

Writes:
    data/legal_corpus_final.json
"""

import json
from config import CORPUS_FILE
from utils.xml_parser import build_smart_corpus
from utils.normalizers import build_abbreviation_table, build_id_to_title_map


def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║  LegalKGent — Step 2a: Build Legislation Corpus          ║
╚══════════════════════════════════════════════════════════╝
    """)

    # 1. Build smart corpus from legislation and SIs ONLY
    corpus = build_smart_corpus(types=['legislation', 'si'])

    # 2. Save corpus
    with open(CORPUS_FILE, "w", encoding="utf-8") as f:
        json.dump(corpus, f, indent=2, ensure_ascii=False)
    print(f"Saved intermediate legislation corpus to {CORPUS_FILE}")

    # 3. Build metadata
    abbrev_table = build_abbreviation_table(corpus)
    id_to_title = build_id_to_title_map(corpus)

    print(f"\nIntermediate Corpus Summary:")
    print(f"   Total chunks:     {len(corpus)}")
    print(f"   Abbreviations:    {len(abbrev_table)}")
    print(f"   Source documents: {len(id_to_title)}")

    sources = {}
    for c in corpus:
        s = c.get("source", "unknown")
        sources[s] = sources.get(s, 0) + 1
    for src, count in sorted(sources.items()):
        print(f"   {src}: {count} chunks")

    print("\nLegislation Corpus building complete! You may now download Case Law.")


if __name__ == "__main__":
    main()
