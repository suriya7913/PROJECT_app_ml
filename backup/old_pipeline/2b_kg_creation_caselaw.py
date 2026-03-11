# -*- coding: utf-8 -*-
"""
LegalKGent — Case Law Triple Extraction Pipeline
==================================================
Extracts legal relationships from CASE LAW chunks only.
Designed for judicial text patterns (CITES, OVERRULES, APPLIES, INTERPRETS).

Reads:  data/legal_corpus_final.json  (filters to source == 'judgment')
Writes: data/extracted_triples_caselaw.json

Usage:
  1. Start vLLM server:
     vllm serve Qwen/Qwen2.5-7B-Instruct \
       --dtype auto --max-model-len 4096 \
       --gpu-memory-utilization 0.90 --enable-prefix-caching \
       --max-num-seqs 16 --port 8000

  2. Run this script:
     python3 2b_kg_creation_caselaw.py
"""

################################################################
# CELL 1 — Imports & Configuration
################################################################

import os
import json
import re
import time
import threading
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

# --- PATHS ---
CORPUS_FILE = "data/legal_corpus_final.json"
OUTPUT_FILE = "data/extracted_triples_caselaw.json"

# --- vLLM CONFIG ---
VLLM_BASE_URL = "http://localhost:8000/v1"
VLLM_MODEL = "Qwen/Qwen2.5-7B-Instruct"  # Must match the model served by vLLM

# --- PARALLELISM ---
NUM_WORKERS = 16    # Match vLLM --max-num-seqs
SAVE_EVERY = 20     # Save checkpoint every N chunks
MAX_RETRIES = 2

# --- CANONICAL ACTIONS ---
CANONICAL_ACTIONS = [
    "AMENDS", "REPEALS", "SUBSTITUTES", "INSERTS",
    "COMMENCES", "REVOKES", "APPLIES", "CITES", "OVERRULES",
    "DEFINES", "INTERPRETS", "DELEGATES", "IMPLEMENTS",
    "CREATES", "EMPOWERS", "REQUIRES", "PROHIBITS", "EXTENDS",
]

ACTION_NORMALIZER = {
    "AMEND": "AMENDS", "AMENDED": "AMENDS", "AMENDS": "AMENDS", "AMENDING": "AMENDS",
    "REPEAL": "REPEALS", "REPEALED": "REPEALS", "REPEALS": "REPEALS", "REPEALING": "REPEALS",
    "OMIT": "REPEALS", "OMITS": "REPEALS", "OMITTED": "REPEALS",
    "SUBSTITUTE": "SUBSTITUTES", "SUBSTITUTED": "SUBSTITUTES", "SUBSTITUTES": "SUBSTITUTES",
    "REPLACE": "SUBSTITUTES", "REPLACES": "SUBSTITUTES", "REPLACED": "SUBSTITUTES",
    "INSERT": "INSERTS", "INSERTED": "INSERTS", "INSERTS": "INSERTS", "INSERTING": "INSERTS",
    "COMMENCE": "COMMENCES", "COMMENCED": "COMMENCES", "COMMENCES": "COMMENCES",
    "REVOKE": "REVOKES", "REVOKED": "REVOKES", "REVOKES": "REVOKES",
    "APPLY": "APPLIES", "APPLIED": "APPLIES", "APPLIES": "APPLIES",
    "CITE": "CITES", "CITED": "CITES", "CITES": "CITES", "CITING": "CITES",
    "REFER": "CITES", "REFERS": "CITES", "REFERRED": "CITES", "REFERENCES": "CITES",
    "MENTION": "CITES", "MENTIONS": "CITES", "MENTIONED": "CITES",
    "OVERRULE": "OVERRULES", "OVERRULED": "OVERRULES", "OVERRULES": "OVERRULES",
    "DEPART": "OVERRULES", "DEPARTS": "OVERRULES", "DEPARTED": "OVERRULES",
    "DISAPPROVE": "OVERRULES", "DISAPPROVES": "OVERRULES", "DISAPPROVED": "OVERRULES",
    "DISTINGUISH": "INTERPRETS", "DISTINGUISHES": "INTERPRETS", "DISTINGUISHED": "INTERPRETS",
    "RELATES_TO": "CITES",
    "DEFINE": "DEFINES", "DEFINED": "DEFINES", "DEFINES": "DEFINES", "DEFINING": "DEFINES",
    "INTERPRET": "INTERPRETS", "INTERPRETED": "INTERPRETS", "INTERPRETS": "INTERPRETS",
    "CONSTRUE": "INTERPRETS", "CONSTRUES": "INTERPRETS", "CONSTRUED": "INTERPRETS",
    "DELEGATE": "DELEGATES", "DELEGATED": "DELEGATES", "DELEGATES": "DELEGATES",
    "IMPLEMENT": "IMPLEMENTS", "IMPLEMENTED": "IMPLEMENTS", "IMPLEMENTS": "IMPLEMENTS",
    "TRANSPOSES": "IMPLEMENTS", "TRANSPOSED": "IMPLEMENTS",
    "CREATE": "CREATES", "CREATED": "CREATES", "CREATES": "CREATES", "CREATING": "CREATES",
    "ESTABLISH": "CREATES", "ESTABLISHES": "CREATES", "ESTABLISHED": "CREATES",
    "EMPOWER": "EMPOWERS", "EMPOWERED": "EMPOWERS", "EMPOWERS": "EMPOWERS",
    "AUTHORISE": "EMPOWERS", "AUTHORISES": "EMPOWERS", "AUTHORIZE": "EMPOWERS",
    "CONFER": "EMPOWERS", "CONFERS": "EMPOWERS", "CONFERRED": "EMPOWERS",
    "REQUIRE": "REQUIRES", "REQUIRED": "REQUIRES", "REQUIRES": "REQUIRES",
    "MANDATE": "REQUIRES", "MANDATES": "REQUIRES", "OBLIGATE": "REQUIRES",
    "IMPOSE": "REQUIRES", "IMPOSES": "REQUIRES",
    "PROHIBIT": "PROHIBITS", "PROHIBITED": "PROHIBITS", "PROHIBITS": "PROHIBITS",
    "RESTRICT": "PROHIBITS", "RESTRICTS": "PROHIBITS", "FORBID": "PROHIBITS",
    "BAN": "PROHIBITS", "BANS": "PROHIBITS",
    "EXTEND": "EXTENDS", "EXTENDED": "EXTENDS", "EXTENDS": "EXTENDS", "EXTENDING": "EXTENDS",
    "RENEW": "EXTENDS", "RENEWS": "EXTENDS", "RENEWED": "EXTENDS",
    "PROLONG": "EXTENDS", "PROLONGS": "EXTENDS",
    # Judicial-specific extras
    "FOLLOW": "CITES", "FOLLOWS": "CITES", "FOLLOWED": "CITES",
    "APPROVE": "CITES", "APPROVES": "CITES", "APPROVED": "CITES",
    "CONSIDER": "CITES", "CONSIDERS": "CITES", "CONSIDERED": "CITES",
    "UPHELD": "CITES", "UPHOLD": "CITES", "UPHOLDS": "CITES",
    "AFFIRM": "CITES", "AFFIRMS": "CITES", "AFFIRMED": "CITES",
    "REVERSE": "OVERRULES", "REVERSES": "OVERRULES", "REVERSED": "OVERRULES",
    "QUASH": "OVERRULES", "QUASHES": "OVERRULES", "QUASHED": "OVERRULES",
    "SET_ASIDE": "OVERRULES",
}

print("✅ Config loaded")


################################################################
# CELL 2 — Normalization Functions
################################################################

def normalize_action(raw_action):
    """Map any LLM action string to canonical form. Returns None if unrecognized."""
    if not raw_action:
        return None
    cleaned = raw_action.upper().strip()
    # Direct lookup
    normalized = ACTION_NORMALIZER.get(cleaned)
    if normalized:
        return normalized
    # Fuzzy fallback: check if any canonical action is a substring match
    for canon in CANONICAL_ACTIONS:
        if canon in cleaned or cleaned in canon:
            return canon
    return None


def normalize_citation(raw_citation):
    """Normalize a citation to consistent format."""
    if not raw_citation:
        return None
    citation = raw_citation.strip()

    # Normalize section references after the year
    year_match = re.search(r'\d{4}', citation)
    if year_match:
        pos = year_match.end()
        act_part = citation[:pos]
        ref_part = citation[pos:]

        ref_part = re.sub(r'\bsection\s+', 's.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bsections\s+', 'ss.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bSchedule\s+', 'Sch.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bSCHEDULE\s+', 'Sch.', ref_part)
        ref_part = re.sub(r'\bparagraph\s+', 'para.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bparagraphs\s+', 'paras.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bregulation\s+', 'reg.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bregulations\s+', 'regs.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\barticle\s+', 'art.', ref_part, flags=re.IGNORECASE)
        citation = act_part + ref_part

    return citation.strip()


def extract_act_name(citation):
    """Pull out just the Act/Case name without section reference.
    'Housing Act 1996 s.122' → 'Housing Act 1996'
    '[2023] UKSC 1' → '[2023] UKSC 1'
    """
    if not citation:
        return None
    # Match standard Act pattern: words + Act/Bill/Order + year
    match = re.match(r'(.*?(?:Act|Bill|Order|Regulations?|Rules?)\s+\d{4})', citation)
    if match:
        return match.group(1).strip()
    # Match case citation: [YYYY] COURT NUM
    case_match = re.match(r'(\[\d{4}\]\s+\w+\s+\d+)', citation)
    if case_match:
        return case_match.group(1).strip()
    return citation.strip()


print("✅ Normalization functions loaded")


################################################################
# CELL 3 — Case Law System Prompt
################################################################

SYSTEM_PROMPT_CASELAW = """You are a UK Legal Knowledge Engineer specialising in case law analysis.
Extract ALL legal relationships from the given JUDGMENT text.

IMPORTANT: Judgments discuss legislation and prior cases differently from statute text.
Look for these patterns in judicial language:

RELATIONSHIP TYPES (use EXACTLY these names):

== Judicial Actions (MOST COMMON in case law) ==
- CITES: court refers to, considers, follows, or applies a statute or prior case
  Triggers: "pursuant to section X", "under the provisions of", "as provided by",
  "the court considered", "as held in [Case]", "following [Case]", "applying [Case]"
- OVERRULES: court overrules, reverses, departs from, or disapproves a prior case
  Triggers: "overruled", "departed from", "reversed", "set aside", "disapproved",
  "no longer good law", "wrongly decided"
- INTERPRETS: court interprets or construes a statutory provision
  Triggers: "construed as meaning", "interpreted to mean", "the meaning of section X",
  "section X should be read as", "the proper construction of"
- APPLIES: court applies a statute to the facts
  Triggers: "applying section X", "section X applies to", "under section X the court must"

== Legislative References (less common but present when discussing statute text) ==
- AMENDS: if judgment notes a provision was amended
- REPEALS: if judgment notes a provision was repealed/omitted
- SUBSTITUTES: if judgment notes text was substituted
- INSERTS: if judgment notes text was inserted
- COMMENCES: if judgment notes a law came into force
- DEFINES: if judgment defines a legal term
- CREATES: if judgment notes creation of body/right/offence
- EMPOWERS: if judgment notes a power was granted
- REQUIRES: if judgment discusses a statutory obligation/duty
- PROHIBITS: if judgment discusses a statutory prohibition
- DELEGATES: if judgment discusses delegated powers
- EXTENDS: if judgment notes temporal/territorial extension
- REVOKES: if judgment notes revocation of secondary legislation
- IMPLEMENTS: if judgment discusses EU law implementation

RULES:
1. Use FORMAL FULL citations:
   - For Acts: "Children Act 1989 s.31" (NOT "CA 1989" or "the Act")
   - For cases: "[2023] UKSC 1" or "Smith v Jones [2020] EWCA Civ 123"
2. NEVER use abbreviations like "CA 1989", "the Act", "the 1996 Act"
   Always expand to the full Act name with year
3. The source document is the JUDGMENT itself — extract what the judgment CITES/INTERPRETS/APPLIES
4. For CITES, include in detail_text what the court said about the cited provision
5. For OVERRULES, include the specific point that was overruled
6. For INTERPRETS, include the court's interpretation in detail_text
7. Return empty array [] if NO legal relationships found
8. Do NOT hallucinate relationships not explicitly stated in the text
9. Each relationship should have exactly ONE target
10. If the judgment refers to "sections 5, 6 and 7", create THREE separate CITES relationships

BAD EXAMPLES (do NOT produce these):
- {"action": "CITE", ...}  ← wrong, use "CITES"
- {"action": "REFERENCES", ...}  ← wrong, use "CITES"
- {"action": "CONSIDERS", ...}  ← wrong, use "CITES"
- {"target_citation": "the Act"}  ← wrong, use full name
- {"target_citation": "CA 1989"}  ← wrong, use "Children Act 1989"
- {"target_citation": "s.31"}  ← wrong, include Act name: "Children Act 1989 s.31"

Respond with ONLY a JSON array. Each object must have:
{"action": "...", "target_citation": "...", "detail_text": "..." or null, "effective_date": "YYYY-MM-DD" or null}

Example output for a judgment:
[{"action": "CITES", "target_citation": "Children Act 1989 s.31", "detail_text": "The court applied the threshold criteria under s.31 to determine whether the child had suffered significant harm", "effective_date": null},
 {"action": "INTERPRETS", "target_citation": "Children Act 1989 s.20", "detail_text": "The court held that s.20 accommodation requires informed and voluntary consent from the parent", "effective_date": null},
 {"action": "OVERRULES", "target_citation": "Williams v London Borough of Hackney [2018] EWCA Civ 1111", "detail_text": "The court departed from the approach in Williams regarding the test for voluntary accommodation", "effective_date": null},
 {"action": "CITES", "target_citation": "Human Rights Act 1998 s.6", "detail_text": "The local authority's actions engaged Article 8 rights under the HRA 1998", "effective_date": null}]"""

print("✅ Case law system prompt defined")


################################################################
# CELL 4 — Extraction Function
################################################################

# Initialize vLLM client
vllm_client = OpenAI(base_url=VLLM_BASE_URL, api_key="not-needed")


def extract_triples_caselaw(chunk, max_retries=MAX_RETRIES):
    """Extract legal triples from a case law chunk using vLLM."""

    # Build user prompt with all available metadata
    user_content = "Extract all legal relationships from this JUDGMENT text:\n\n"
    user_content += f"DOCUMENT ID: {chunk['id']}\n"
    user_content += f"CASE NAME: {chunk.get('doc_title', 'Unknown')}\n"
    user_content += f"YEAR: {chunk.get('year', 'Unknown')}\n"

    court_level = chunk.get('court_level', '')
    if not court_level:
        # Infer court from ID
        cid = chunk.get('id', '')
        if 'uksc_' in cid:
            court_level = "Supreme Court"
        elif 'ewca_' in cid:
            court_level = "Court of Appeal"
        elif 'ewhc_' in cid:
            court_level = "High Court"
        elif 'ukut_' in cid:
            court_level = "Upper Tribunal"
    if court_level:
        user_content += f"COURT: {court_level}\n"

    if chunk.get('section'):
        user_content += f"PARAGRAPH: {chunk['section']}\n"

    content = chunk.get('content', '')
    if not content or len(content.strip()) < 30:
        return []

    user_content += f"\nTEXT:\n{content}\n\n"
    user_content += "Respond with ONLY a JSON array of relationships. If none found, respond with []"

    for attempt in range(max_retries):
        try:
            response = vllm_client.chat.completions.create(
                model=VLLM_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_CASELAW},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.1,
                max_tokens=2048,
            )

            raw_text = response.choices[0].message.content.strip()

            # Parse JSON — handle common LLM output quirks
            parsed = None
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError:
                # Try to extract JSON array from markdown/text wrapper
                match = re.search(r'\[.*\]', raw_text, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group())
                    except json.JSONDecodeError:
                        pass

            if parsed is None:
                print(f"    ⚠️ Could not parse JSON (attempt {attempt+1}): {raw_text[:100]}...")
                continue

            # Handle wrapped formats (e.g., {"relationships": [...]})
            if isinstance(parsed, dict):
                items = (parsed.get("mutations") or parsed.get("relationships")
                         or parsed.get("results") or parsed.get("data") or [])
                if not items and "action" in parsed:
                    items = [parsed]
            elif isinstance(parsed, list):
                items = parsed
            else:
                return []

            # Validate, normalize, and clean each triple
            results = []
            for item in items:
                if not isinstance(item, dict):
                    continue

                # Must have both action and target_citation
                raw_action = item.get("action")
                raw_citation = item.get("target_citation")
                if not raw_action or not raw_citation:
                    continue

                # Skip if target_citation is just "the Act" or similar vague ref
                if raw_citation.strip().lower() in ("the act", "this act", "the 1989 act",
                                                      "the 1996 act", "the act of 1989",
                                                      "the statute", "the provision"):
                    continue

                # Normalize action
                action = normalize_action(raw_action)
                if not action:
                    print(f"    ⚠️ Unknown action '{raw_action}', skipping")
                    continue

                # Normalize citation
                citation = normalize_citation(raw_citation)
                if not citation or len(citation) < 3:
                    continue

                # Skip if citation is just a section number without an Act name
                # e.g., "s.31" alone is useless — we need "Children Act 1989 s.31"
                if re.match(r'^s\.\d+', citation) and not re.search(r'Act|Order|Rules?\s+\d{4}', citation):
                    continue

                # Extract parent Act/case name
                act_name = extract_act_name(citation)

                # Build source metadata
                source_id = chunk['id']
                source_title = chunk.get('doc_title', '')

                # Detect self-citation (judgment citing itself — rare but skip)
                is_self = False
                if source_title and act_name:
                    # Case self-citation: same case name
                    if source_title.lower().strip() == act_name.lower().strip():
                        is_self = True

                # detail_text cleanup: strip if it's just the citation repeated
                detail_text = item.get("detail_text")
                if detail_text and isinstance(detail_text, str):
                    detail_text = detail_text.strip()
                    # If detail_text is just the citation or "null", clear it
                    if detail_text.lower() in ("null", "none", "n/a", ""):
                        detail_text = None
                    elif len(detail_text) < 5:
                        detail_text = None

                # effective_date cleanup
                effective_date = item.get("effective_date")
                if effective_date and isinstance(effective_date, str):
                    effective_date = effective_date.strip()
                    # Validate date format
                    if not re.match(r'^\d{4}-\d{2}-\d{2}$', effective_date):
                        effective_date = None
                    if effective_date in ("null", "None", "N/A", ""):
                        effective_date = None

                results.append({
                    "action": action,
                    "target_citation": citation,
                    "target_act_name": act_name,
                    "detail_text": detail_text,
                    "effective_date": effective_date,
                    "source_id": source_id,
                    "source_title": source_title,
                    "source_section": chunk.get('section'),
                    "in_force_date": chunk.get('in_force_date'),
                    "extent": chunk.get('extent'),
                    "is_self_amendment": is_self,
                    "chunk_id": source_id,
                })

            return results

        except Exception as e:
            error_str = str(e).lower()
            if "timeout" in error_str or "connection" in error_str:
                print(f"    ⏳ Timeout (attempt {attempt+1}/{max_retries}), retrying...")
                time.sleep(2)
            else:
                print(f"    ❌ Error: {e}")
                return []

    print(f"    ❌ Failed after {max_retries} retries")
    return []


print("✅ Extraction function loaded")


################################################################
# CELL 5 — Load Corpus & Filter Case Law
################################################################

print(f"\n📂 Loading corpus from {CORPUS_FILE}...")
with open(CORPUS_FILE, "r", encoding="utf-8") as f:
    full_corpus = json.load(f)
print(f"   Total corpus: {len(full_corpus)} chunks")

# Filter to case law only
case_chunks = [c for c in full_corpus if c.get('source') == 'judgment']
print(f"   Case law chunks: {len(case_chunks)}")

# Skip chunks with very short content (< 50 chars of actual text)
processable = []
for c in case_chunks:
    content = c.get('content', '')
    # Extract text portion (after "TEXT:" prefix in the formatted content)
    text_body = content
    if '| TEXT:' in text_body:
        text_body = text_body.split('| TEXT:')[-1].strip()
    if len(text_body) >= 50:
        processable.append(c)

print(f"   Processable (content >= 50 chars): {len(processable)}")


################################################################
# CELL 6 — Resume Support & Run Extraction
################################################################

# Load existing results (resume support)
if os.path.exists(OUTPUT_FILE):
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        all_results = json.load(f)
    already_done = set(r.get('source_id', '') for r in all_results)
    print(f"\n📂 Loaded {len(all_results)} existing triples from {OUTPUT_FILE}")
else:
    all_results = []
    already_done = set()

# Filter to chunks not yet processed
chunks_to_process = [c for c in processable if c['id'] not in already_done]
print(f"🚀 Chunks remaining: {len(chunks_to_process)} (of {len(processable)})")

if not chunks_to_process:
    print("✅ All case law chunks already processed!")
else:
    lock = threading.Lock()
    stats = {"processed": 0, "triples_found": 0, "errors": 0}
    start_time = time.time()

    print(f"\n🚀 Processing {len(chunks_to_process)} case law chunks "
          f"with {VLLM_MODEL} ({NUM_WORKERS} workers)\n")

    def process_chunk(chunk):
        """Extract triples from one chunk (thread-safe)."""
        try:
            triples = extract_triples_caselaw(chunk)
            return chunk, triples, None
        except Exception as e:
            return chunk, [], str(e)

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {executor.submit(process_chunk, c): c for c in chunks_to_process}

        for future in as_completed(futures):
            chunk, triples, error = future.result()

            with lock:
                stats["processed"] += 1

                if error:
                    stats["errors"] += 1
                    print(f"[{stats['processed']}/{len(chunks_to_process)}] "
                          f"❌ {chunk['id']}: {error}")
                elif triples:
                    for t in triples:
                        all_results.append(t)
                        stats["triples_found"] += 1

                    self_count = sum(1 for t in triples if t.get('is_self_amendment'))
                    print(f"[{stats['processed']}/{len(chunks_to_process)}] "
                          f"{chunk['id']}: {len(triples)} triples "
                          f"({self_count} self-cite)")
                else:
                    print(f"[{stats['processed']}/{len(chunks_to_process)}] "
                          f"{chunk['id']}: (none)")

                # Periodic save
                if stats['processed'] % SAVE_EVERY == 0:
                    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                        json.dump(all_results, f, indent=2, ensure_ascii=False)
                    elapsed_so_far = time.time() - start_time
                    speed = stats['processed'] / max(elapsed_so_far, 0.01)
                    remaining = (len(chunks_to_process) - stats['processed']) / max(speed, 0.01)
                    print(f"   💾 Saved ({len(all_results)} triples) | "
                          f"{speed:.1f} chunks/sec | ~{remaining:.0f}s remaining")

    # Final save
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    elapsed = time.time() - start_time
    print(f"\n{'='*50}")
    print(f"✅ EXTRACTION DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"   Processed: {stats['processed']}")
    print(f"   Triples found: {stats['triples_found']}")
    print(f"   Errors: {stats['errors']}")
    print(f"   Total in file: {len(all_results)}")
    print(f"   Speed: {stats['processed']/max(elapsed,1):.1f} chunks/sec")


################################################################
# CELL 7 — Post-Processing: Dedup & Quality Report
################################################################

print(f"\n{'='*60}")
print(f"POST-PROCESSING")
print(f"{'='*60}")

with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
    all_triples = json.load(f)
print(f"   Loaded {len(all_triples)} triples for post-processing")

# 1. Deduplicate (same source_id + action + target_citation)
seen = set()
deduped = []
dup_count = 0
for t in all_triples:
    key = (t.get('source_id', ''), t.get('action', ''), t.get('target_citation', ''))
    if key not in seen:
        seen.add(key)
        deduped.append(t)
    else:
        dup_count += 1
print(f"   Duplicates removed: {dup_count}")

# 2. Re-normalize actions
fixed_actions = 0
for t in deduped:
    canonical = normalize_action(t.get('action', ''))
    if canonical and canonical != t.get('action'):
        t['action'] = canonical
        fixed_actions += 1
print(f"   Actions re-normalized: {fixed_actions}")

# 3. Re-normalize citations
fixed_citations = 0
for t in deduped:
    old = t.get('target_citation', '')
    new = normalize_citation(old)
    if new and new != old:
        t['target_citation'] = new
        t['target_act_name'] = extract_act_name(new)
        fixed_citations += 1
print(f"   Citations re-normalized: {fixed_citations}")

# Save cleaned version
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(deduped, f, indent=2, ensure_ascii=False)
print(f"\n✅ Cleaned: {len(deduped)} triples saved (was {len(all_triples)})")

# Quality report
print(f"\n{'='*50}")
print(f"📊 QUALITY REPORT")
print(f"{'='*50}")

actions = defaultdict(int)
for t in deduped:
    actions[t.get('action', 'UNKNOWN')] += 1
print(f"\n  Action distribution:")
for a, c in sorted(actions.items(), key=lambda x: -x[1]):
    marker = "✅" if a in CANONICAL_ACTIONS else "❌"
    print(f"    {marker} {a}: {c}")

with_detail = sum(1 for t in deduped if t.get('detail_text'))
with_date = sum(1 for t in deduped if t.get('effective_date'))
self_cites = sum(1 for t in deduped if t.get('is_self_amendment'))
print(f"\n  With detail_text: {with_detail}/{len(deduped)}")
print(f"  With effective_date: {with_date}/{len(deduped)}")
print(f"  Self-citations: {self_cites}/{len(deduped)}")

# Vague citations warning
vague = [t for t in deduped if t.get('target_citation') and len(t['target_citation']) < 15]
if vague:
    print(f"\n  ⚠️ Vague citations (<15 chars): {len(vague)}")
    for v in vague[:5]:
        print(f"      {v['source_id']} -> \"{v['target_citation']}\"")

# Samples
print(f"\n📝 Samples (one per action type):")
shown = set()
for t in deduped:
    if t['action'] not in shown:
        shown.add(t['action'])
        print(f"  [{t['action']}] {t.get('source_title','?')[:50]} -> {t['target_citation']}")
        if t.get('detail_text'):
            detail_preview = t['detail_text'][:120]
            print(f"           detail: {detail_preview}...")

print(f"\n✅ Case law extraction complete — {OUTPUT_FILE}")
