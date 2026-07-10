# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0
"""Generate the golden evaluation dataset for the Close Exception Investigator.

The dataset is built directly from the synthetic ledger and the SAME deterministic
rule engine the pilot uses, so every eval prompt is a faithful reproduction of what
`run_pilot.py` sends the investigator agent. This keeps the golden set reproducible:
re-running this script regenerates the dataset when the ledger or rules change.

Usage:
    uv run python build_eval_dataset.py
Output:
    tests/eval/datasets/close_investigations.json  (agents-cli eval `Shape A` format)
"""

import json
import os

from app.mock_data import get_journal_entry_by_id
from app.tools import run_deterministic_rules

# Curated coverage set: one entry per control failure mode, plus clean controls.
# `expected` fields are documentation of ground truth for reviewers of the dataset;
# the LLM-as-judge metrics score the live response, but these annotations let a human
# audit what "correct" means for each case.
CASES = [
    {
        "id": "sod_violation_JE-1001",
        "journal_id": "JE-1001",
        "expected_primary_control": "Segregation of Duties",
        "expected_routing": "Escalate to Controller",
        "notes": "Creator == approver. History memo exists, so confidence should be Medium/High, not Low.",
    },
    {
        "id": "materiality_missing_auth_JE-1002",
        "journal_id": "JE-1002",
        "expected_primary_control": "Materiality",
        "expected_routing": "Request Supporting Invoice",
        "notes": "Manual >$250K with null authorization_ref. Must NOT recommend plain Approve.",
    },
    {
        "id": "period_cutoff_JE-1003",
        "journal_id": "JE-1003",
        "expected_primary_control": "Period Cutoff",
        "expected_routing": "Escalate to Controller",
        "notes": "Posted to closed June period after the July-2 cutoff without a cutoff token.",
    },
    {
        "id": "sensitive_retained_earnings_JE-1004",
        "journal_id": "JE-1004",
        "expected_primary_control": "Sensitive Account",
        "expected_routing": "Escalate to Controller",
        "notes": "Manual posting to Retained Earnings 300090. No historical memo -> Low confidence.",
    },
    {
        "id": "duplicate_freight_JE-1006",
        "journal_id": "JE-1006",
        "expected_primary_control": "Duplicate Entry",
        "expected_routing": "Escalate to Controller",
        "notes": "Exact dup of JE-1005 (amount/account/cc/entity) within 45 minutes.",
    },
    {
        "id": "sensitive_suspense_roundnum_JE-1007",
        "journal_id": "JE-1007",
        "expected_primary_control": "Sensitive Account",
        "expected_routing": "Escalate to Controller",
        "notes": "$500,000.00 round number to suspense/clearing 400500, null auth.",
    },
    {
        "id": "clean_automated_JE-1000",
        "journal_id": "JE-1000",
        "expected_primary_control": "None",
        "expected_routing": "Approve (with note)",
        "notes": "System-automated amortization. No violations -> agent must NOT invent one.",
    },
]


def build_prompt(je: dict, rule_results: dict) -> str:
    """Mirror the exact investigation prompt used in run_pilot.run_agent_investigation."""
    return (
        f"Please perform a close exception investigation for journal entry {je['journal_id']}.\n"
        f"Ledger details:\n{json.dumps(je, indent=2)}\n\n"
        f"Deterministic rule failures:\n{json.dumps(rule_results['failures'], indent=2)}"
    )


def main() -> None:
    eval_cases = []
    for case in CASES:
        je = get_journal_entry_by_id(case["journal_id"])
        if je is None:
            raise ValueError(f"Journal entry {case['journal_id']} not found in mock ledger.")
        rule_results = run_deterministic_rules(je)

        eval_cases.append({
            "eval_case_id": case["id"],
            "prompt": {
                "role": "user",
                "parts": [{"text": build_prompt(je, rule_results)}],
            },
            # Non-schema annotation block: ground truth for human dataset review.
            # agents-cli ignores unknown keys; the LLM judges score the live response.
            "ground_truth": {
                "expected_primary_control": case["expected_primary_control"],
                "expected_routing": case["expected_routing"],
                "risk_score": rule_results["risk_score"],
                "triage_action": rule_results["action"],
                "num_failures": len(rule_results["failures"]),
                "notes": case["notes"],
            },
        })

    dataset = {"eval_cases": eval_cases}

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "eval", "datasets")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "close_investigations.json")
    with open(out_path, "w") as f:
        json.dump(dataset, f, indent=2)

    print(f"Wrote {len(eval_cases)} golden eval cases to {out_path}")
    for c in eval_cases:
        gt = c["ground_truth"]
        print(f"  - {c['eval_case_id']:<38} control={gt['expected_primary_control']:<22} "
              f"score={gt['risk_score']:>3} route='{gt['expected_routing']}'")


if __name__ == "__main__":
    main()
