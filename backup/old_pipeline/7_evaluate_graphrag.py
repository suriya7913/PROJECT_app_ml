"""
LegalKGent — GraphRAG Evaluation: 5 Lawyer-Grade Questions
===========================================================
Imports agent_ask directly from 6_graphrag_query.py — no duplication.

5 questions grounded in actual downloaded Acts:
  Q1 Finance & Taxation    — Finance (No.2) Act 2023         [ukpga_2023_30]
  Q2 Energy & Utilities    — Energy Act 2023                 [ukpga_2023_52]
  Q3 Social Housing        — Social Housing (Reg.) Act 2023  [ukpga_2023_36]
  Q4 Employment Rights     — Carer's Leave + Redundancy 2023
  Q5 Planning              — Levelling-up Act 2023           [ukpga_2023_55]

Usage:
  python3 7_evaluate_graphrag.py
"""

import json
import os
import time
import datetime
import importlib.util

# ── IMPORT FROM 6_graphrag_query.py ──────────────────────────────────────────
_MODULE_PATH = os.path.join(os.path.dirname(__file__), "6_graphrag_query.py")
_spec   = importlib.util.spec_from_file_location("graphrag_query", _MODULE_PATH)
_mod    = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

agent_ask       = _mod.agent_ask
semantic_search = _mod.semantic_search
run_cypher      = _mod.run_cypher
SYSTEM_PROMPT   = _mod.SYSTEM_PROMPT

# --- Phoenix Tracing (tracer already registered by 6_graphrag_query.py) ---
from opentelemetry import trace
_tracer = trace.get_tracer("legalkgent.evaluation")

print("\n✅ Imported agent_ask, semantic_search, run_cypher from 6_graphrag_query.py")
print("✅ Phoenix tracing active for evaluation runs")

_run_question = agent_ask
# ── OUTPUT DIR ────────────────────────────────────────────────────────────────
RESULTS_DIR  = "results"
RESULTS_FILE = os.path.join(RESULTS_DIR, "graphrag_eval_results.json")
os.makedirs(RESULTS_DIR, exist_ok=True)

# ── 5 LAWYER-GRADE TEST QUESTIONS ────────────────────────────────────────────
QUESTIONS = [
    {
        "id":       "Q1",
        "domain":   "Finance & Taxation",
        "act_hint": "Finance (No. 2) Act 2023  [ukpga_2023_30] — 575 KG relationships",
        "question": (
            "What specific amendments did the Finance (No. 2) Act 2023 make to "
            "dividend allowances and the annual exempt amount for Capital Gains Tax "
            "under the Income Tax Act 2007 or Taxation of Chargeable Gains Act 1992?"
        ),
        "legal_rationale": (
            "Tests whether the KG can trace specific Finance Act substitutions into "
            "primary tax legislation — a core task for tax practitioners advising "
            "clients on investment income after the 2023 changes."
        ),
    },
    {
        "id":       "Q2",
        "domain":   "Energy & Utilities",
        "act_hint": "Energy Act 2023  [ukpga_2023_52] — 407 KG relationships",
        "question": (
            "Which sections of the Electricity Act 1989 or the Gas Act 1986 were "
            "amended by the Energy Act 2023, and what is the nature of each amendment?"
        ),
        "legal_rationale": (
            "Tests multi-target graph traversal: an energy lawyer needs to know "
            "exactly which legacy provisions were modified to advise on network "
            "access and licensing obligations."
        ),
    },
    {
        "id":       "Q3",
        "domain":   "Social Housing",
        "act_hint": "Social Housing (Regulation) Act 2023  [ukpga_2023_36] — 205 KG relationships",
        "question": (
            "What amendments did the Social Housing (Regulation) Act 2023 make to "
            "the Housing and Regeneration Act 2008, particularly regarding registered "
            "provider regulation and consumer standards?"
        ),
        "legal_rationale": (
            "Tests detail-extraction: housing solicitors need exact provision wording "
            "to advise registered providers on their obligations post-2023 reform."
        ),
    },
    {
        "id":       "Q4",
        "domain":   "Employment Rights",
        "act_hint": "Carer's Leave Act 2023 [ukpga_2023_18] + Protection from Redundancy Act 2023 [ukpga_2023_17]",
        "question": (
            "Which 2023 Acts introduced new statutory rights into the Employment "
            "Rights Act 1996, and what are the specific sections inserted or amended "
            "by each Act?"
        ),
        "legal_rationale": (
            "Tests multi-source aggregation: an employment barrister must identify "
            "all 2023 legislative changes to ERA 1996 to advise on flexible working, "
            "carer's leave, and redundancy protection in a single brief."
        ),
    },
    {
        "id":       "Q5",
        "domain":   "Planning & Regeneration",
        "act_hint": "Levelling-up and Regeneration Act 2023  [ukpga_2023_55] — 535 KG relationships",
        "question": (
            "What amendments did the Levelling-up and Regeneration Act 2023 make to "
            "the Local Democracy, Economic Development and Construction Act 2009, "
            "and how did it modify planning-related provisions in the Housing and "
            "Planning Act 2016?"
        ),
        "legal_rationale": (
            "Tests cross-Act traversal: a planning solicitor needs to know how the "
            "Levelling-up Act altered both local infrastructure levy powers (2009 Act)"
            " and existing planning enforcement (2016 Act)."
        ),
    },
]

# ── RUN EVALUATION ────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  LegalKGent GraphRAG Evaluation — 5 Lawyer-Grade Questions")
print("=" * 70)

eval_results = {
    "metadata": {
        "timestamp":      datetime.datetime.now(datetime.UTC).isoformat(),
        "model":          _mod.MISTRAL_MODEL,
        "embed_model":    _mod.EMBED_MODEL,
        "faiss_vectors":  _mod.faiss_index.ntotal,
        "description": (
            "5 lawyer-grade questions grounded in actual Acts in the KG, "
            "tested via GraphRAG hybrid workflow (semantic_search + run_cypher)."
        ),
    },
    "questions": [],
}

for q_item in QUESTIONS:
    q_id   = q_item["id"]
    q_text = q_item["question"]

    print(f"\n{'─'*70}")
    print(f"  {q_id}: [{q_item['domain']}]")
    print(f"  Act : {q_item['act_hint']}")
    print(f"  Q   : {q_text}")
    print(f"{'─'*70}")

    with _tracer.start_as_current_span(f"eval_{q_id}", attributes={
        "question_id": q_id,
        "domain": q_item["domain"],
        "question": q_text,
    }):
        t0     = time.time()
        answer = _run_question(q_text, max_steps=10)   # traced or plain
        elapsed = round(time.time() - t0, 2)

        grounded = not answer.startswith(("❌", "⚠️ UNGROUNDED"))
        trace.get_current_span().set_attribute("grounded", grounded)
        trace.get_current_span().set_attribute("elapsed_sec", elapsed)

    print(f"\n  ✅ Answer ({elapsed}s, grounded={grounded}):")
    print(f"  {answer[:1000]}")

    eval_results["questions"].append({
        "id":              q_id,
        "domain":          q_item["domain"],
        "act_hint":        q_item["act_hint"],
        "legal_rationale": q_item["legal_rationale"],
        "question":        q_text,
        "answer":          answer,
        "elapsed_sec":     elapsed,
        "grounded":        grounded,
    })

    time.sleep(2)

# ── SAVE RESULTS ──────────────────────────────────────────────────────────────
with open(RESULTS_FILE, "w") as f:
    json.dump(eval_results, f, indent=2, ensure_ascii=False)

print(f"\n{'='*70}")
print(f"  ✅ Results saved → {RESULTS_FILE}")
print(f"{'='*70}")

# ── SUMMARY TABLE ─────────────────────────────────────────────────────────────
print(f"\n  {'ID':<4}  {'Domain':<28}  {'Time(s)':>7}  {'Grounded':>8}")
print(f"  {'─'*54}")
for q in eval_results["questions"]:
    g = "✅ YES" if q["grounded"] else "⚠️  NO"
    print(f"  {q['id']:<4}  {q['domain']:<28}  {q['elapsed_sec']:>7}  {g}")

print()
