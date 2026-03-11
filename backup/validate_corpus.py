#!/usr/bin/env python3
"""
LegalKGent — Corpus Validator
==============================
Validates downloaded data for quality, completeness, and stats.
Run after download_transport_data.py.

Usage:
    python validate_corpus.py
"""

import os
import json
import statistics
import xml.etree.ElementTree as ET
from collections import Counter


# ============================================================
# CONFIG
# ============================================================

DATA_DIR = "data"
LEGISLATION_DIR = os.path.join(DATA_DIR, "raw_legislation")
CASELAW_DIR = os.path.join(DATA_DIR, "raw_caselaw")
SI_DIR = os.path.join(DATA_DIR, "raw_statutory_instruments")
NOTES_DIR = os.path.join(DATA_DIR, "explanatory_notes")
AMENDMENTS_DIR = os.path.join(DATA_DIR, "amendments")

# Namespace helpers
def strip_ns(tag):
    """Strip XML namespace prefix."""
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


# ============================================================
# VALIDATE XML FILES
# ============================================================

def validate_xml_dir(dirpath, label):
    """Validate all XML files in a directory."""
    print(f"\n{'='*50}")
    print(f"📂 {label}: {dirpath}")
    print(f"{'='*50}")
    
    if not os.path.exists(dirpath):
        print(f"  [WARN] Directory does not exist")
        return {}
    
    files = [f for f in os.listdir(dirpath) if f.endswith('.xml')]
    if not files:
        print(f"  [WARN] No XML files found")
        return {}
    
    stats = {
        'total_files': len(files),
        'valid_xml': 0,
        'parse_errors': 0,
        'total_sections': 0,
        'total_size_kb': 0,
        'file_sizes': [],
        'titles': [],
        'errors': [],
    }
    
    for filename in sorted(files):
        filepath = os.path.join(dirpath, filename)
        file_size = os.path.getsize(filepath) / 1024
        stats['total_size_kb'] += file_size
        stats['file_sizes'].append(file_size)
        
        try:
            tree = ET.parse(filepath)
            root = tree.getroot()
            stats['valid_xml'] += 1
            
            # Count sections (P1 for legislation, paragraph for caselaw)
            section_count = 0
            title = "Unknown"
            
            for elem in root.iter():
                tag = strip_ns(elem.tag)
                if tag == 'P1':
                    section_count += 1
                elif tag == 'paragraph':
                    section_count += 1
                elif tag == 'title' and title == "Unknown":
                    if elem.text:
                        title = elem.text.strip()[:80]
                elif tag == 'FRBRname':
                    title = elem.get('value', title)[:80]
            
            stats['total_sections'] += section_count
            stats['titles'].append(f"  {filename}: {title} ({section_count} sections, {file_size:.0f} KB)")
            
        except ET.ParseError as e:
            stats['parse_errors'] += 1
            stats['errors'].append(f"  [{filename}] XML Parse Error: {e}")
    
    # Print report
    print(f"\n  Files: {stats['total_files']}")
    print(f"  Valid XML: {stats['valid_xml']}")
    if stats['parse_errors']:
        print(f"  ❌ Parse Errors: {stats['parse_errors']}")
    print(f"  Total Sections: {stats['total_sections']}")
    print(f"  Total Size: {stats['total_size_kb']:.0f} KB ({stats['total_size_kb']/1024:.1f} MB)")
    
    if stats['file_sizes']:
        print(f"\n  File Size Stats:")
        print(f"    Min: {min(stats['file_sizes']):.0f} KB")
        print(f"    Max: {max(stats['file_sizes']):.0f} KB")
        print(f"    Avg: {statistics.mean(stats['file_sizes']):.0f} KB")
    
    print(f"\n  Documents:")
    for t in stats['titles'][:20]:
        print(t)
    if len(stats['titles']) > 20:
        print(f"  ... and {len(stats['titles'])-20} more")
    
    if stats['errors']:
        print(f"\n  Errors:")
        for e in stats['errors']:
            print(e)
    
    return stats


# ============================================================
# VALIDATE PARSED CORPUS (if exists)
# ============================================================

def validate_parsed_corpus(filepath):
    """Validate a parsed JSON corpus file."""
    print(f"\n{'='*50}")
    print(f"📊 PARSED CORPUS: {filepath}")
    print(f"{'='*50}")
    
    if not os.path.exists(filepath):
        print(f"  [INFO] Not yet created (run kg_creation.py first)")
        return
    
    with open(filepath, "r") as f:
        data = json.load(f)
    
    total = len(data)
    print(f"\n  Total Chunks: {total}")
    
    # Source distribution
    sources = Counter(item.get('source', 'unknown') for item in data)
    print(f"\n  Source Distribution:")
    for src, count in sources.most_common():
        print(f"    {src}: {count} ({count/total:.1%})")
    
    # Content length stats
    lengths = [len(item.get('content', '')) for item in data]
    if lengths:
        print(f"\n  Content Length (chars):")
        print(f"    Min: {min(lengths)}")
        print(f"    Max: {max(lengths)}")
        print(f"    Avg: {int(statistics.mean(lengths))}")
        print(f"    Median: {int(statistics.median(lengths))}")
    
    # Empty content check
    empty = sum(1 for l in lengths if l < 10)
    if empty:
        print(f"\n  ⚠️  Empty chunks (<10 chars): {empty}")
    
    # Missing fields
    required = ['id', 'source', 'content', 'doc_title']
    for field in required:
        missing = sum(1 for item in data if not item.get(field))
        if missing:
            print(f"  ⚠️  Missing '{field}': {missing}")
    
    # Chunk budget check
    print(f"\n  📏 Chunk Budget:")
    print(f"    Current: {total}")
    print(f"    Limit: 5,000")
    print(f"    Remaining: {5000-total}")
    if total > 5000:
        print(f"    ❌ OVER BUDGET by {total-5000} chunks!")
    else:
        print(f"    ✅ Within budget")


# ============================================================
# MAIN
# ============================================================

def main():
    print(f"""
╔══════════════════════════════════════════════════╗
║  LegalKGent — Data Validation Report            ║
╚══════════════════════════════════════════════════╝
    """)
    
    all_stats = {}
    
    # Validate each data source
    all_stats['legislation'] = validate_xml_dir(LEGISLATION_DIR, "PRIMARY LEGISLATION")
    all_stats['caselaw'] = validate_xml_dir(CASELAW_DIR, "CASE LAW")
    all_stats['sis'] = validate_xml_dir(SI_DIR, "STATUTORY INSTRUMENTS")
    all_stats['notes'] = validate_xml_dir(NOTES_DIR, "EXPLANATORY NOTES")
    all_stats['amendments'] = validate_xml_dir(AMENDMENTS_DIR, "AMENDMENTS TABLES")
    
    # Validate parsed corpus if exists
    for corpus_file in ["data/smart_corpus.json", "data/legal_corpus_clean.json"]:
        validate_parsed_corpus(corpus_file)
    
    # Summary
    print(f"\n{'='*50}")
    print(f"📊 OVERALL SUMMARY")
    print(f"{'='*50}")
    
    total_files = sum(s.get('total_files', 0) for s in all_stats.values())
    total_sections = sum(s.get('total_sections', 0) for s in all_stats.values())
    total_size = sum(s.get('total_size_kb', 0) for s in all_stats.values())
    
    print(f"  Total XML files: {total_files}")
    print(f"  Total sections: {total_sections}")
    print(f"  Total disk size: {total_size/1024:.1f} MB")
    print(f"  Est. chunks (sections): ~{total_sections}")
    print(f"  Budget remaining: ~{5000-total_sections} chunks")


if __name__ == "__main__":
    main()
