#!/usr/bin/env python3
"""
LegalKGent — Step 3: Extract Triples
=======================================
Unified triple extraction for both legislation and case law.
Uses a local vLLM server for high-throughput parallel inference.

Usage:
    python 6_extract_triples.py

Reads:
    data/legal_corpus_final.json

Writes:
    data/extracted_triples.json
"""

import json
import os
import re
import time
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    CORPUS_FILE, TRIPLES_FILE, NUM_WORKERS, SAVE_EVERY, MAX_RETRIES,
    CANONICAL_ACTIONS, VLLM_MODEL, VLLM_TIMEOUT
)
from utils.normalizers import (
    normalize_action, normalize_citation, extract_act_name,
    build_abbreviation_table, build_id_to_title_map,
    extract_matched_glossary
)
from llm.client import get_vllm_client, parse_llm_json
from llm.prompts import LEGISLATION_PROMPT, CASELAW_PROMPT


# ─────────────────────────────────────────────
# EXTRACT FROM ONE CHUNK
# ─────────────────────────────────────────────

def extract_triples(
    chunk: dict,
    vllm_client,
    abbrev_table: dict,
    id_to_title: dict,
    glossaries: dict,
    max_retries: int = MAX_RETRIES,
) -> list[dict]:
    """Extract legal triples from a single chunk using vLLM."""

    # Choose prompt by source type
    is_judgment = chunk.get("source") == "judgment"
    system_prompt = CASELAW_PROMPT if is_judgment else LEGISLATION_PROMPT

    # Build user prompt
    user_content = "Extract all legal relationships from this text:\n\n"
    user_content += f"DOCUMENT ID: {chunk['chunk_id']}\n"
    user_content += f"TITLE: {chunk['doc_title']}\n"
    user_content += f"SOURCE TYPE: {chunk.get('source', 'unknown')}\n"

    if chunk.get('part'):
        user_content += f"PART: {chunk['part']}\n"
    if chunk.get('heading'):
        user_content += f"HEADING: {chunk['heading']}\n"
    if chunk.get('in_force_date'):
        user_content += f"IN FORCE DATE: {chunk['in_force_date']}\n"
    if chunk.get('extent'):
        user_content += f"EXTENT: {chunk['extent']}\n"
        
    text_content = chunk.get('content') or chunk.get('vector_text', '')
    
    # --- DYNAMIC GLOSSARY RAG INJECTION ---
    chunk_id = chunk['chunk_id']
    act_id = chunk_id.rsplit('.xml_', 1)[0] if '.xml_' in chunk_id else chunk_id
    act_glossary = glossaries.get(act_id, {})
    
    # Only find definitions that actually appear in the chunk text
    matched_glossary = extract_matched_glossary(text_content, act_glossary)
    
    if matched_glossary:
        user_content += "DEFINED TERMS:\n"
        for term, summary in matched_glossary.items():
            user_content += f" - {term}: {summary}\n"
    # --------------------------------------

    # --- PREVIOUS CODE (BUG) ---
    # if chunk.get('inline_amendments'):
    #     user_content += f"PRE-MARKED AMENDMENTS: {json.dumps(chunk['inline_amendments'][:5])}\n"
    
    # --- NEW CODE (FIXED) ---
    amendments = chunk.get('inline_amendments') or chunk.get('graph_edges', {}).get('inline_amendments', [])
    if amendments:
        user_content += f"PRE-MARKED AMENDMENTS: {json.dumps(amendments[:5])}\n"

    user_content += f"\nTEXT:\n{text_content}\n\n"
    user_content += "Respond with ONLY a JSON array of relationships. If none found, respond with []"

    # Retry loop
    for attempt in range(max_retries):
        try:
            response = vllm_client.chat.completions.create(
                model=VLLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.1,
                max_tokens=2048,
                timeout=VLLM_TIMEOUT,
                extra_body={
                    "chat_template_kwargs": {"enable_thinking": False}
                },
            )
            raw_text = response.choices[0].message.content.strip()
            items = parse_llm_json(raw_text)
            
            if items is None:
                # LLM failed to return structured JSON
                return []

            # Normalize and validate each triple
            results = []
            source_prefix = chunk['chunk_id'].rsplit('.xml_', 1)[0] if '.xml_' in chunk['chunk_id'] else chunk['chunk_id']
            source_title = id_to_title.get(source_prefix, "")

            for item in items:
                if not isinstance(item, dict):
                    continue
                if "action" not in item or "target_citation" not in item:
                    continue
                if not item["target_citation"]:
                    continue

                action = normalize_action(item["action"])
                if not action:
                    continue

                citation = normalize_citation(item["target_citation"], abbrev_table)
                if not citation or len(citation) < 3:
                    continue

                act_name = extract_act_name(citation)
                is_self = False
                if source_title and act_name:
                    s_lower = source_title.lower()
                    a_lower = act_name.lower()
                    if s_lower in a_lower or a_lower in s_lower:
                        is_self = True
                    else:
                        # Fallback for hallucinated text: if generic terms match
                        s_tokens = set(re.findall(r'\b\w+\b', s_lower))
                        a_tokens = set(re.findall(r'\b\w+\b', a_lower))
                        # If the Act name and Year both exist in the hallucinated string
                        if len(s_tokens) > 2 and s_tokens.issubset(a_tokens):
                            is_self = True

                results.append({
                    "action": action,
                    "target_citation": citation,
                    "target_act_name": act_name,
                    "detail_text": item.get("detail_text"),
                    "effective_date": item.get("effective_date"),
                    "source_id": chunk['chunk_id'],
                    "source_title": chunk.get('doc_title'),
                    "source_section": chunk.get('section') or chunk.get('section_number'),
                    "in_force_date": chunk.get('in_force_date'),
                    "extent": chunk.get('extent'),
                    "is_self_amendment": is_self,
                    "chunk_id": chunk['chunk_id'],
                })
            return results

        except Exception as e:
            error_str = str(e).lower()
            if "timeout" in error_str or "connection" in error_str:
                import random
                base_wait = 3 * (attempt + 1)
                jitter = random.uniform(0, 2)
                wait = base_wait + jitter
                print(f"    ⏳ Timeout (attempt {attempt+1}/{max_retries}), retrying in {wait:.0f}s...")
                time.sleep(wait)
            else:
                print(f"   Error: {e}")
                return []

    print(f"   Failed after {max_retries} retries")
    return []


# ─────────────────────────────────────────────
# POST-PROCESSING
# ─────────────────────────────────────────────

def post_process(triples: list[dict], abbrev_table: dict) -> list[dict]:
    """Deduplicate and re-normalize triples."""
    # 1. Deduplicate
    seen = set()
    deduped = []
    for t in triples:
        key = (t.get('source_id', ''), t.get('action', ''), t.get('target_citation', ''))
        if key not in seen:
            seen.add(key)
            deduped.append(t)

    dup_count = len(triples) - len(deduped)
    print(f"   Duplicates removed: {dup_count}")

    # 2. Re-normalize actions
    fixed = 0
    for t in deduped:
        canonical = normalize_action(t.get('action', ''))
        if canonical and canonical != t.get('action'):
            t['action'] = canonical
            fixed += 1
    print(f"   Actions re-normalized: {fixed}")

    # 3. Re-normalize citations
    fixed_cit = 0
    for t in deduped:
        old = t.get('target_citation', '')
        new = normalize_citation(old, abbrev_table)
        if new and new != old:
            t['target_citation'] = new
            t['target_act_name'] = extract_act_name(new)
            fixed_cit += 1
    print(f"   Citations re-normalized: {fixed_cit}")

    return deduped


def print_quality_report(triples: list[dict]):
    """Print a summary quality report."""
    print(f"\n{'='*50}")
    print(f"QUALITY REPORT")
    print(f"{'='*50}")

    actions = defaultdict(int)
    for t in triples:
        actions[t.get('action', 'UNKNOWN')] += 1
    print(f"\n  Action distribution:")
    for a, c in sorted(actions.items(), key=lambda x: -x[1]):
        marker = "done" if a in CANONICAL_ACTIONS else "not done"
        print(f"    {marker} {a}: {c}")

    self_amendments = sum(1 for t in triples if t.get('is_self_amendment'))
    with_date = sum(1 for t in triples if t.get('effective_date'))
    with_detail = sum(1 for t in triples if t.get('detail_text'))
    print(f"\n  Self-amendments: {self_amendments}/{len(triples)}")
    print(f"  With effective_date: {with_date}/{len(triples)}")
    print(f"  With detail_text: {with_detail}/{len(triples)}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("""
║  LegalKGent — Step 3: Extract Triples (vLLM Edition)    ║
    """)

    # 1. Load corpus
    print(f"Loading corpus from {CORPUS_FILE}...")
    with open(CORPUS_FILE, "r", encoding="utf-8") as f:
        corpus = json.load(f)
    print(f"   Loaded {len(corpus)} chunks")

    # 2. Build metadata
    abbrev_table = build_abbreviation_table(corpus)
    id_to_title = build_id_to_title_map(corpus)
    print(f"   Abbreviations: {len(abbrev_table)}")
    print(f"   Source docs: {len(id_to_title)}")

    glossaries = {}
    if os.path.exists("data/glossary_summaries.json"):
        with open("data/glossary_summaries.json", "r", encoding="utf-8") as f:
            glossaries = json.load(f)
        print(f"   Glossaries: {len(glossaries)} Acts loaded")
    else:
        print("   No glossary summaries found. Run 5_build_glossary_summaries.py for optimal extraction.")

    # 3. Connect to vLLM
    vllm_client = get_vllm_client()
    print(f" Connected to vLLM server ({VLLM_MODEL})")

    # 4. Load existing results (resume support)
    if os.path.exists(TRIPLES_FILE):
        with open(TRIPLES_FILE, "r", encoding="utf-8") as f:
            all_results = json.load(f)
        already_done = set(r['source_id'] for r in all_results if 'source_id' in r)
        print(f"Loaded {len(all_results)} existing triples, {len(already_done)} chunks done")
    else:
        all_results = []
        already_done = set()

    # 5. Filter to unprocessed chunks sequentially
    chunks_to_process = [c for c in corpus if c['chunk_id'] not in already_done]
    print(f"\nProcessing remaining {len(chunks_to_process)} chunks with {NUM_WORKERS} workers\n")

    if not chunks_to_process:
        print("All chunks already processed!")
        post_process(all_results, abbrev_table)
        print_quality_report(all_results)
        return

    # 6. Parallel extraction ( local vLLM)
    lock = threading.Lock()
    stats = {"processed": 0, "triples_found": 0}
    start_time = time.time()

    def process_one(chunk):
        return chunk, extract_triples(chunk, vllm_client, abbrev_table, id_to_title, glossaries)

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {executor.submit(process_one, c): c for c in chunks_to_process}

        for future in as_completed(futures):
            chunk, triples = future.result()
            with lock:
                stats["processed"] += 1
                if triples:
                    all_results.extend(triples)
                    stats["triples_found"] += len(triples)
                    self_count = sum(1 for t in triples if t.get('is_self_amendment'))
                    print(f"[{stats['processed']}/{len(chunks_to_process)}] {chunk['chunk_id']}: "
                          f"{len(triples)} triples ({self_count} self-amend)")
                else:
                    print(f"[{stats['processed']}/{len(chunks_to_process)}] {chunk['chunk_id']}: (none)")

                # Checkpoint
                if stats['processed'] % SAVE_EVERY == 0:
                    with open(TRIPLES_FILE, "w", encoding="utf-8") as f:
                        json.dump(all_results, f, indent=2, ensure_ascii=False)
                    elapsed_so_far = time.time() - start_time
                    speed = stats['processed'] / elapsed_so_far
                    remaining = (len(chunks_to_process) - stats['processed']) / max(speed, 0.01)
                    print(f"   Saved ({len(all_results)} triples) | "
                          f"{speed:.1f} chunks/sec | ~{remaining:.0f}s remaining")

    # 7. Post-process
    print(f"\nPost-Processing {len(all_results)} triples...")
    all_results = post_process(all_results, abbrev_table)

    # 8. Final save
    with open(TRIPLES_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    elapsed = time.time() - start_time
    print(f"\n{'='*50}")
    print(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"   Processed: {stats['processed']}")
    print(f"   Triples: {len(all_results)}")
    print(f"   Speed: {stats['processed']/max(elapsed,1):.1f} chunks/sec")

    print_quality_report(all_results)


if __name__ == "__main__":
    main()