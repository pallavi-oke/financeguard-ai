# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0
"""Generate live dashboard data from the pilot's audit packets.

Reads the newest audit packet per journal entry in ./audit_packets, parses the
investigator's markdown report into structured fields, reconstructs the RAG evidence
(corporate policy + historical memos) from the same deterministic sources the agent
used, and emits a JavaScript data file the control dashboard consumes:

    ../dashboard_data.js   ->  window.DASHBOARD_DATA / window.DASHBOARD_META

The dashboard falls back to its built-in sample data when this file is absent, so
it always renders; when present, it shows the real run. Run after run_pilot.py:

    uv run python run_pilot.py
    uv run python build_dashboard_data.py
"""

import glob
import json
import os
import re
from datetime import datetime, timezone

from app.mock_data import MOCK_LEDGER, get_policy
from app.tools import run_deterministic_rules, search_historical_explanations

HERE = os.path.dirname(os.path.abspath(__file__))
PACKET_DIR = os.path.join(HERE, "audit_packets")
OUT_PATH = os.path.abspath(os.path.join(HERE, "..", "dashboard_data.js"))

# Human-readable names for the GL accounts used in the synthetic ledger.
ACCOUNT_NAMES = {
    "180200": "Prepaid Expenses",
    "300090": "Retained Earnings",
    "400500": "Suspense / Clearing",
    "610200": "Spares Parts Expense",
    "610300": "Professional Services",
    "610500": "Spares Logistics",
    "620100": "Operating Expense",
    "620500": "Travel & Entertainment",
}

# Map a deterministic rule name to the corporate policy key it enforces.
RULE_TO_POLICY = {
    "segregation": "segregation_of_duties",
    "materiality": "materiality_limits",
    "cutoff": "period_cutoff",
    "sensitive": "sensitive_accounts",
}

REPORT_LABELS = [
    "Observed Facts",
    "Hypothesis",
    "Evidence Retrieved",
    "Confidence Score",
    "Recommended Action",
    "Escalation Narrative",
]


def _account_label(code: str) -> str:
    name = ACCOUNT_NAMES.get(code)
    return f"{code} ({name})" if name else code


def parse_report(report: str) -> dict:
    """Parse the investigator's markdown report into structured sections.

    Tolerant of bullet/inline formatting. Returns empty/best-effort fields when the
    report is an error string (e.g. the model call failed), so the dashboard can show
    an explicit 'report unavailable' state instead of breaking.
    """
    out = {
        "facts": [],
        "hypothesis": "",
        "evidence_retrieved": "",
        "confidence": "Unknown",
        "recommended_action": "",
        "escalation_narrative": "",
        "report_ok": False,
    }
    if not report or report.strip().lower().startswith("investigation halted") \
            or report.strip().lower().startswith("investigation produced no"):
        out["hypothesis"] = report.strip()
        return out

    # Locate each labeled section by its bold header and slice to the next header.
    label_alt = "|".join(re.escape(x) for x in REPORT_LABELS)
    pattern = re.compile(rf"\*\*\s*({label_alt})\s*\*\*\s*:?\s*", re.IGNORECASE)
    matches = list(pattern.finditer(report))
    sections = {}
    for i, m in enumerate(matches):
        label = m.group(1).title()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(report)
        sections[label] = report[start:end].strip()

    if not sections:
        # No recognizable structure; keep the whole text as the hypothesis blob.
        out["hypothesis"] = report.strip()
        return out

    out["report_ok"] = True

    facts_blob = sections.get("Observed Facts", "")
    out["facts"] = _split_bullets(facts_blob)
    out["hypothesis"] = _clean(sections.get("Hypothesis", ""))
    out["evidence_retrieved"] = _clean(sections.get("Evidence Retrieved", ""))
    out["recommended_action"] = _clean(sections.get("Recommended Action", ""))
    out["escalation_narrative"] = _clean(sections.get("Escalation Narrative", ""))

    conf_blob = sections.get("Confidence Score", "")
    conf_match = re.search(r"\b(High|Medium|Low)\b", conf_blob, re.IGNORECASE)
    out["confidence"] = conf_match.group(1).title() if conf_match else "Unknown"
    return out


def _split_bullets(blob: str) -> list:
    """Split a section into list items on bullet/numbered markers, else sentences."""
    blob = blob.strip()
    if not blob:
        return []
    lines = [ln.strip() for ln in blob.splitlines() if ln.strip()]
    bullets = []
    for ln in lines:
        cleaned = re.sub(r"^[\-\*•]\s+", "", ln)
        cleaned = re.sub(r"^\d+[\.\)]\s+", "", cleaned)
        bullets.append(cleaned.strip())
    # If it collapsed to a single blob with no real bullets, keep as one item.
    return [b for b in bullets if b]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("*", "").strip())


def reconstruct_rag_sources(je: dict, rule_results: dict) -> dict:
    """Rebuild the policy text and historical memos the agent's tools would return."""
    policy_keys = []
    for failure in rule_results["failures"]:
        rn = failure["rule"].lower()
        for token, key in RULE_TO_POLICY.items():
            if token in rn and key not in policy_keys:
                policy_keys.append(key)
    if policy_keys:
        policy_text = "\n\n".join(f"[{k}] {get_policy(k)}" for k in policy_keys)
    else:
        policy_text = "No specific control policy retrieved for this exception."
    history = search_historical_explanations(je["account"])
    return {"policy": policy_text, "history": history}


def latest_packets() -> dict:
    """Return the newest audit packet per journal_id."""
    by_id = {}
    for path in sorted(glob.glob(os.path.join(PACKET_DIR, "audit_packet_*.json"))):
        try:
            packet = json.load(open(path))
        except Exception:
            continue
        jid = packet.get("metadata", {}).get("journal_id") \
            or packet.get("transaction_payload", {}).get("journal_id")
        if not jid:
            continue
        # Filenames carry a sortable timestamp; last one wins.
        by_id[jid] = packet
    return by_id


def build_entry(packet: dict) -> dict:
    je = packet.get("transaction_payload", {})
    checks = packet.get("deterministic_control_checks", {})
    review = packet.get("ai_quality_review", {}) or {}
    disposition = packet.get("human_in_the_loop_disposition", {}) or {}

    rule_results = run_deterministic_rules(je) if je.get("journal_id") else {"failures": []}
    rag = reconstruct_rag_sources(je, rule_results) if je.get("journal_id") else {"policy": "", "history": ""}
    parsed = parse_report(packet.get("ai_investigation_report", ""))

    amount = je.get("amount", 0) or 0
    failures = checks.get("failures", []) or []
    return {
        "journal_id": je.get("journal_id", "?"),
        "description": je.get("description", ""),
        "account": _account_label(je.get("account", "")),
        "cost_center": je.get("cost_center", ""),
        "entity": je.get("entity", ""),
        "amount": f"${amount:,.2f}",
        "amount_value": amount,
        "created_by": je.get("created_by", ""),
        "approved_by": je.get("approved_by", ""),
        "period": je.get("period", ""),
        "risk_score": checks.get("risk_score", 0),
        "triage_action": checks.get("triage_decision", ""),
        "failures": [f"{f.get('rule','')}: {f.get('error','')}" for f in failures],
        "policy": rag["policy"],
        "history": rag["history"],
        "confidence": parsed["confidence"],
        "facts": parsed["facts"],
        "hypothesis": parsed["hypothesis"],
        "recommended_action": parsed["recommended_action"],
        "escalation_narrative": parsed["escalation_narrative"],
        "report_ok": parsed["report_ok"],
        "quality_review": {
            "verdict": review.get("verdict", "NOT_REVIEWED"),
            "grounding_score": review.get("grounding_score"),
            "issues": review.get("issues", []) or [],
            "summary": review.get("summary", ""),
        },
        "human_disposition": {
            "status": disposition.get("status", ""),
            "reviewer": disposition.get("reviewer", ""),
            "override_reason": disposition.get("override_reason", ""),
        },
    }


def main() -> None:
    packets = latest_packets()
    data = {}
    for jid, packet in packets.items():
        data[jid] = build_entry(packet)

    # Whole-ledger triage summary for the metric cards (deterministic, always real).
    total = len(MOCK_LEDGER)
    escalated = 0
    for je in MOCK_LEDGER:
        if run_deterministic_rules(je)["action"] == "ESCALATE_TO_HUMAN":
            escalated += 1
    auto_approved = total - escalated

    signed = sum(1 for e in data.values() if e["human_disposition"]["status"])
    reports_ok = sum(1 for e in data.values() if e["report_ok"])

    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "live_audit_packets",
        "ledger_size": total,
        "auto_approved": auto_approved,
        "escalated": escalated,
        "investigated": len(data),
        "reports_ok": reports_ok,
        "signed_packets": signed,
    }

    # Order entries by journal_id for a stable queue.
    ordered = {k: data[k] for k in sorted(data.keys())}

    js = (
        "// AUTO-GENERATED by build_dashboard_data.py — do not edit by hand.\n"
        "// Regenerate: uv run python close-investigator/build_dashboard_data.py\n"
        f"window.DASHBOARD_META = {json.dumps(meta, indent=2)};\n"
        f"window.DASHBOARD_DATA = {json.dumps(ordered, indent=2)};\n"
    )
    with open(OUT_PATH, "w") as f:
        f.write(js)

    print(f"Wrote {OUT_PATH}")
    print(f"  Entries: {len(ordered)} | reports_ok: {reports_ok} | signed: {signed}")
    print(f"  Ledger: {total} | auto-approved: {auto_approved} | escalated: {escalated}")
    for jid, e in ordered.items():
        print(f"  - {jid}: score={e['risk_score']:>3} conf={e['confidence']:<7} "
              f"review={e['quality_review']['verdict']:<12} report_ok={e['report_ok']}")


if __name__ == "__main__":
    main()
