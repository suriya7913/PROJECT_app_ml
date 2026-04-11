#!/usr/bin/env python3
"""
LegalKGent — Step 7: Evaluate GraphRAG
=========================================
Runs test questions and scores the agent's answers.

Usage:
    python 10_evaluate.py
"""

import json
import importlib.util
import time

# Import run_legal_pipeline from 6_multi_agent_graphrag
spec = importlib.util.spec_from_file_location("multi_agent", "9_multi_agent_graphrag.py")
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)

agent_ask = _mod.run_legal_pipeline

# Load Phoenix tracer
from opentelemetry import trace
_tracer = trace.get_tracer("legalkgent.evaluation")


# ─────────────────────────────────────────────
# TEST QUESTIONS
# ─────────────────────────────────────────────

TEST_QUESTIONS = [
    {
        "question": "Which 2023 Acts modify the Employment Rights Act 1996, and what type of changes does each make?",
        "expected_actions": ["AMENDS", "INSERTS", "SUBSTITUTES", "REPEALS"],
        "domain": "Employment",
    },
    {
        "question": "What legislation regulates autonomous vehicles and self-driving car data in the UK?",
        "expected_actions": ["DEFINES", "CREATES", "REQUIRES"],
        "domain": "Transport",
    },
    {
        "question": "What changes did the Finance Act 2023 make to dividend allowances?",
        "expected_actions": ["AMENDS", "SUBSTITUTES"],
        "domain": "Finance",
    },
    {
        "question": "What are the rules for cryptocurrency regulation under UK law?",
        "expected_actions": [],  # Should trigger guardrail
        "domain": "Crypto (negative test)",
    },
    {
        "question": "What safety requirements does the Automated Vehicles Act 2024 create?",
        "expected_actions": ["REQUIRES", "CREATES", "DEFINES"],
        "domain": "Transport",
    },
]


# ─────────────────────────────────────────────
# EVALUATION
# ─────────────────────────────────────────────

def evaluate():
    """Run all test questions and collect results."""
    print("""
╔══════════════════════════════════════════════════════════╗
║  LegalKGent — Step 7: Evaluate GraphRAG                 ║
╚══════════════════════════════════════════════════════════╝
    """)

    results = []

    for i, test in enumerate(TEST_QUESTIONS):
        print(f"\n{'='*60}")
        print(f"  QUESTION {i+1}/{len(TEST_QUESTIONS)}: {test['question'][:80]}...")
        print(f"  Domain: {test['domain']}")
        print(f"{'='*60}")

        with _tracer.start_as_current_span(f"eval_q{i+1}", attributes={
            "question": test["question"],
            "domain": test["domain"],
        }):
            start = time.time()
            try:
                answer = agent_ask(test["question"])
                elapsed = time.time() - start
                error = None
            except Exception as e:
                answer = f"ERROR: {e}"
                elapsed = time.time() - start
                error = str(e)

            # Score
            has_answer = bool(answer and not answer.startswith("❌"))
            has_grounding = "⚠️" not in answer[:50] if answer else False
            is_negative = len(test["expected_actions"]) == 0

            # Check if expected actions are mentioned (case-insensitive)
            answer_lower = answer.lower() if answer else ""
            actions_found = [a for a in test["expected_actions"]
                             if a.lower() in answer_lower]

            result = {
                "question": test["question"],
                "domain": test["domain"],
                "answer_length": len(answer) if answer else 0,
                "elapsed_seconds": round(elapsed, 1),
                "has_answer": has_answer,
                "has_grounding": has_grounding,
                "expected_actions": test["expected_actions"],
                "actions_found": actions_found,
                "is_negative_test": is_negative,
                "error": error,
            }
            results.append(result)

            print(f"\n  ⏱️  {elapsed:.1f}s | Answer length: {len(answer)} chars")
            if not is_negative:
                print(f"  📊 Actions found: {actions_found}/{test['expected_actions']}")

    # Summary
    print(f"\n\n{'='*60}")
    print(f"  📊 EVALUATION SUMMARY")
    print(f"{'='*60}")

    total = len(results)
    answered = sum(1 for r in results if r["has_answer"])
    grounded = sum(1 for r in results if r["has_grounding"])
    avg_time = sum(r["elapsed_seconds"] for r in results) / total

    print(f"  Questions: {total}")
    print(f"  Answered:  {answered}/{total}")
    print(f"  Grounded:  {grounded}/{total}")
    print(f"  Avg time:  {avg_time:.1f}s")

    # Save results
    output_file = "results/evaluation_results.json"
    import os
    os.makedirs("results", exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  💾 Results saved to {output_file}")


if __name__ == "__main__":
    evaluate()
