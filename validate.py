#!/usr/bin/env python3
"""
LegalKGent — Validate Pipeline Data
======================================
Combined validation script for corpus, triples, and graph integrity.

Usage:
    python validate.py
"""

import json
import os
import xml.etree.ElementTree as ET
from collections import Counter

from config import (
    DATA_DIR, RAW_LEGISLATION_DIR, RAW_CASELAW_DIR, RAW_SI_DIR,
    AMENDMENTS_DIR, NOTES_DIR, CORPUS_FILE, TRIPLES_FILE,
    CASELAW_TRIPLES_FILE, EFFECTS_TRIPLES_FILE,
    INDEX_DIR, IDMAP_FILE, CANONICAL_ACTIONS, CASE_PREFIXES,
    is_caselaw,
)


def validate_raw_xml():
    """Validate downloaded XML files."""
    print(f"\n{'='*60}")
    print("📥 RAW XML VALIDATION")
    print(f"{'='*60}")

    for name, dirpath in [
        ("Legislation",  RAW_LEGISLATION_DIR),
        ("Case Law",     RAW_CASELAW_DIR),
        ("SIs",          RAW_SI_DIR),
        ("Amendments",   AMENDMENTS_DIR),
        ("Notes",        NOTES_DIR),
    ]:
        if not os.path.exists(dirpath):
            print(f"  ℹ️  {name}: directory not found ({dirpath})")
            continue

        xml_files = [f for f in os.listdir(dirpath) if f.endswith('.xml')]
        valid = 0
        invalid = 0
        total_bytes = 0

        for f in xml_files:
            fp = os.path.join(dirpath, f)
            try:
                ET.parse(fp)
                valid += 1
                total_bytes += os.path.getsize(fp)
            except ET.ParseError:
                invalid += 1
                print(f"    ❌ {f}: XML parse error")

        mb = total_bytes / (1024 * 1024)
        print(f"  {name}: {valid} valid, {invalid} invalid, {mb:.1f} MB total")


def validate_corpus():
    """Validate the built corpus."""
    print(f"\n{'='*60}")
    print("📄 CORPUS VALIDATION")
    print(f"{'='*60}")

    if not os.path.exists(CORPUS_FILE):
        print(f"  ❌ Corpus file not found: {CORPUS_FILE}")
        return

    with open(CORPUS_FILE, "r", encoding="utf-8") as f:
        corpus = json.load(f)

    print(f"  Total chunks: {len(corpus)}")

    # Source distribution
    sources = Counter(c.get("source", "unknown") for c in corpus)
    for src, cnt in sources.most_common():
        print(f"    {src}: {cnt}")

    # Field completeness
    fields = ["doc_title", "year", "section", "content", "heading", "part",
              "extent", "in_force_date", "notes_text"]
    for field in fields:
        has = sum(1 for c in corpus if c.get(field))
        pct = (has / len(corpus) * 100) if corpus else 0
        print(f"    {field}: {has}/{len(corpus)} ({pct:.0f}%)")

    # Empty chunks
    empty = sum(1 for c in corpus if len(c.get("content", "")) < 30)
    if empty:
        print(f"  ⚠️ {empty} chunks with <30 chars content")

    # Duplicate IDs
    ids = [c["id"] for c in corpus if c.get("id")]
    dupes = len(ids) - len(set(ids))
    if dupes:
        print(f"  ⚠️ {dupes} duplicate chunk IDs")
    else:
        print(f"  ✅ All chunk IDs unique")


def validate_triples():
    """Validate extracted triples."""
    print(f"\n{'='*60}")
    print("🔗 TRIPLES VALIDATION")
    print(f"{'='*60}")

    for name, filepath in [
        ("Legislation",  TRIPLES_FILE),
        ("Case Law",     CASELAW_TRIPLES_FILE),
        ("Effects",      EFFECTS_TRIPLES_FILE),
    ]:
        if not os.path.exists(filepath):
            print(f"  ℹ️  {name}: not found ({filepath})")
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            triples = json.load(f)

        print(f"\n  {name}: {len(triples)} triples")

        # Action distribution
        actions = Counter(t.get("action", "?") for t in triples)
        canonical = sum(cnt for act, cnt in actions.items() if act in CANONICAL_ACTIONS)
        non_canonical = len(triples) - canonical
        print(f"    Canonical: {canonical}, Non-canonical: {non_canonical}")

        # Top actions
        for act, cnt in actions.most_common(5):
            marker = "✅" if act in CANONICAL_ACTIONS else "❌"
            print(f"      {marker} {act}: {cnt}")

        # Missing fields
        no_target = sum(1 for t in triples if not t.get("target_citation"))
        no_source = sum(1 for t in triples if not t.get("source_id") and not t.get("source_title"))
        if no_target:
            print(f"    ⚠️ {no_target} triples with no target_citation")
        if no_source:
            print(f"    ⚠️ {no_source} triples with no source_id")


def validate_index():
    """Validate the FAISS index."""
    print(f"\n{'='*60}")
    print("🗂️  FAISS INDEX VALIDATION")
    print(f"{'='*60}")

    if not os.path.exists(IDMAP_FILE):
        print(f"  ❌ ID map not found: {IDMAP_FILE}")
        return

    with open(IDMAP_FILE) as f:
        id_map = json.load(f)

    print(f"  ID map entries: {len(id_map)}")

    # Check alignment with corpus
    if os.path.exists(CORPUS_FILE):
        with open(CORPUS_FILE, "r", encoding="utf-8") as f:
            corpus = json.load(f)
        corpus_ids = set(c["id"] for c in corpus if c.get("id"))
        indexed_ids = set(r["node_id"] for r in id_map if r.get("node_id"))
        not_indexed = corpus_ids - indexed_ids
        if not_indexed:
            print(f"  ⚠️ {len(not_indexed)} corpus chunks not in index")
        else:
            print(f"  ✅ All corpus chunks indexed")


def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║  LegalKGent — Data Validation                           ║
╚══════════════════════════════════════════════════════════╝
    """)

    validate_raw_xml()
    validate_corpus()
    validate_triples()
    validate_index()

    print(f"\n{'='*60}")
    print("✅ Validation complete!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
