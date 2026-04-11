#!/usr/bin/env python3
"""
LegalKGent — Step 2.5: Build Glossary Summaries
=================================================
Extracts all <Term> definitions from the corpus and uses a local vLLM 
server to summarize them into single sentences. This allows for dynamic,
low-token RAG injection during triple extraction.

Usage:
    python 5_build_glossary_summaries.py

Reads:
    data/legal_corpus_final.json

Writes:
    data/glossary_summaries.json
"""

import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    CORPUS_FILE, NUM_WORKERS, SAVE_EVERY, MAX_RETRIES,
    VLLM_MODEL, GLOSSARY_MODEL, VLLM_TIMEOUT
)
from llm.client import get_vllm_client

GLOSSARY_FILE = "data/glossary_summaries.json"

SYSTEM_PROMPT = """You are a highly analytical UK Legal Knowledge Engineer. 
Summarize the provided legal definition into a single, concise sentence identifying the entity, concept, or scope.
- DO NOT start with "The definition means...". Start directly with the summarized fact.
- Keep it under 150 characters if possible.
- Respond ONLY with the summary sentence and nothing else."""

def summarize_term(act_id: str, term: str, raw_text: str, vllm_client, max_retries: int = MAX_RETRIES) -> dict:
    user_content = f"TERM: {term}\nRAW DEFINITION:\n{raw_text}\n\nSUMMARY:"
    
    for attempt in range(max_retries):
        try:
            response = vllm_client.chat.completions.create(
                model=GLOSSARY_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.1,
                max_tokens=64, # summaries should be tiny
                timeout=VLLM_TIMEOUT,
                extra_body={
                    "chat_template_kwargs": {"enable_thinking": False}
                },
            )
            summary = response.choices[0].message.content.strip()
            
            return {
                "act_id": act_id,
                "term": term,
                "summary": summary
            }
        except Exception as e:
            error_str = str(e).lower()
            if "timeout" in error_str or "connection" in error_str:
                import random
                wait = 3 * (attempt + 1) + random.uniform(0, 2)
                time.sleep(wait)
            else:
                print(f"    ❌ Error on {term}: {e}")
                return None
    return None

def main():
    print("╔══════════════════════════════════════════════════════════╗")
    print("║  LegalKGent — Step 2.5: Build Glossary Summaries        ║")
    print("╚══════════════════════════════════════════════════════════╝\n")
    
    if not os.path.exists(CORPUS_FILE):
        print(f"❌ Corpus file not found: {CORPUS_FILE}")
        return
        
    # 1. Load Corpus to extract definitions
    with open(CORPUS_FILE, "r", encoding="utf-8") as f:
        corpus = json.load(f)
        
    # Act ID -> { Term -> Raw Definition }
    raw_glossaries = {}
    for chunk in corpus:
        chunk_id = chunk.get("chunk_id", "")
        act_id = chunk_id.rsplit(".xml_", 1)[0] if ".xml_" in chunk_id else chunk_id
        
        terms = chunk.get("defined_terms")
        if terms and isinstance(terms, dict):
            if act_id not in raw_glossaries:
                raw_glossaries[act_id] = {}
            for term, definition in terms.items():
                if term and definition:
                    # Clean up random whitespace/newlines from XML extraction
                    clean_def = " ".join(str(definition).split())
                    raw_glossaries[act_id][term] = clean_def
                    
    total_terms = sum(len(terms) for terms in raw_glossaries.values())
    print(f"📂 Found {total_terms} unique defined terms across {len(raw_glossaries)} Acts.")
    
    # 2. Load existing summaries (resume support)
    existing_summaries = {}
    if os.path.exists(GLOSSARY_FILE):
        with open(GLOSSARY_FILE, "r", encoding="utf-8") as f:
            existing_summaries = json.load(f)
            
    existing_count = sum(len(terms) for terms in existing_summaries.values())
    print(f"✅ Loaded {existing_count} existing summaries from {GLOSSARY_FILE}.")
    
    # 3. Queue remaining terms
    tasks = []
    for act_id, terms in raw_glossaries.items():
        for term, raw_text in terms.items():
            if act_id in existing_summaries and term in existing_summaries[act_id]:
                continue
            tasks.append((act_id, term, raw_text))
            
    if not tasks:
        print("✅ All glossaries summarized! Pipeline ready.")
        return
        
    print(f"🔌 Connecting to vLLM server (Extract: {VLLM_MODEL} | Glossary: {GLOSSARY_MODEL})...")
    vllm_client = get_vllm_client()
    
    print(f"🚀 Summarizing {len(tasks)} remaining definitions with {NUM_WORKERS} workers\n")
    
    total_processed = 0
    save_lock = threading.Lock()
    
    def _save_progress():
        with open(GLOSSARY_FILE, "w", encoding="utf-8") as f:
            json.dump(existing_summaries, f, indent=2)
            
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        future_map = {
            executor.submit(summarize_term, act, term, text, vllm_client): (act, term)
            for act, term, text in tasks
        }
        
        for future in as_completed(future_map):
            act_id, term = future_map[future]
            try:
                res = future.result()
                if res and res["summary"]:
                    with save_lock:
                        if act_id not in existing_summaries:
                            existing_summaries[act_id] = {}
                        existing_summaries[act_id][term] = res["summary"]
                        total_processed += 1
                        
                        clean_string = res['summary'].replace('\n', ' ')
                        print(f"[{total_processed}/{len(tasks)}] {act_id} '{term[:15]}...' -> {clean_string[:60]}...")
                        
                        if total_processed % SAVE_EVERY == 0:
                            _save_progress()
            except Exception as e:
                print(f"❌ Unhandled exception on {act_id} '{term}': {e}")
                
    # Final save
    _save_progress()
    print(f"\n✅ Finished. Saved {(existing_count + total_processed)} total summaries to {GLOSSARY_FILE}.")

if __name__ == "__main__":
    main()
