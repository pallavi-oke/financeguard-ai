# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import json
from datetime import datetime
from .mock_data import get_mixed_feed, get_contract, get_policy, get_user_info, EVIDENCE_REGISTRY

# Expanded Historical Memos Database mapped to stable source IDs
HISTORICAL_MEMOS = [
    {
        "id": "MEMO-WAR-229",
        "key": "610200",
        "description": "Warranty reserve adjustment for spares",
        "reason": "Approved exception warranty journal entries: physical spares logs verified by VP Ops Finance on 2026-06-30."
    },
    {
        "id": "MEMO-WAR-229",
        "key": "ConsultingCorp",
        "description": "Warranty reserve adjustment for spares",
        "reason": "Approved exception warranty journal entries: physical spares logs verified by VP Ops Finance on 2026-06-30."
    },
    {
        "id": "MEMO-MEAL-CAP-WU",
        "key": "Meals",
        "description": "Per-diem travel meal overages for David Wu",
        "reason": "Pre-authorization approved for David Wu for supplier dinner overage up to $250.00."
    },
    {
        "id": "MEMO-MIGRATION-RATE-2025",
        "key": "ConsultingCorp",
        "description": "Emergency weekend migration support rate approval",
        "reason": "A temporary rate of $180/hour was approved for emergency weekend migration support during Q4 2025."
    }
]

# --- Deterministic Rule Checks ---

# 1. Close Rules
def check_sod(je: dict) -> dict:
    creator = je.get("created_by")
    approver = je.get("approved_by")
    if creator != "SYS_AUTO" and creator == approver:
        return {"passed": False, "detail": f"SoD Violation: Entry created and approved by same user ID ({creator})."}
    return {"passed": True, "detail": "Passed."}

def check_materiality(je: dict) -> dict:
    amount = je.get("amount", 0.0)
    auth_ref = je.get("authorization_ref")
    if amount > 250000.00 and not auth_ref:
        return {"passed": False, "detail": f"Materiality Violation: Manual entry (${amount:,.2f}) > $250K lacks authorization reference."}
    return {"passed": True, "detail": "Passed."}

def check_cutoff(je: dict) -> dict:
    cutoff_time = datetime(2026, 7, 2, 17, 0, 0)
    posting_date = datetime.fromisoformat(je.get("posting_date"))
    if je.get("period") == "06-2026" and posting_date > cutoff_time:
        return {"passed": False, "detail": f"Cutoff Violation: June period entry posted late on {je.get('posting_date')}."}
    return {"passed": True, "detail": "Passed."}

def check_sensitive_accounts(je: dict) -> dict:
    account = je.get("account", "")
    if account.startswith("3000"):
        return {"passed": False, "detail": f"Sensitive Account Violation: Manual posting directly to sensitive account '{account}'."}
    return {"passed": True, "detail": "Passed."}

# 2. Billing Rules
def check_invoice_rate(inv: dict) -> dict:
    vendor = inv.get("vendor")
    unit_price = inv.get("unit_price", 0.0)
    contract = get_contract(vendor)
    contract_rate = contract.get("contracted_rate_usd", 0.0)
    if contract_rate > 0.0 and unit_price > contract_rate:
        return {"passed": False, "detail": f"Pricing Violation: Billed rate (${unit_price:,.2f}) exceeds contracted rate (${contract_rate:,.2f}) for vendor '{vendor}'."}
    return {"passed": True, "detail": "Passed."}

def check_duplicate_invoice(inv: dict) -> dict:
    feed = get_mixed_feed()
    curr_id = inv.get("invoice_id")
    amount = inv.get("amount")
    vendor = inv.get("vendor")
    curr_date = datetime.fromisoformat(inv.get("posting_date"))
    
    for other in feed:
        if other.get("tx_type") == "BILLING_INVOICE" and other.get("invoice_id") != curr_id:
            if other.get("amount") == amount and other.get("vendor") == vendor:
                other_date = datetime.fromisoformat(other.get("posting_date"))
                if curr_date > other_date and abs((curr_date - other_date).total_seconds()) <= 86400: # 24 hrs
                    return {"passed": False, "detail": f"Duplicate Violation: Duplicate billing amount (${amount:,.2f}) within 24 hours (vs {other.get('invoice_id')})."}
    return {"passed": True, "detail": "Passed."}

# 3. Expense Rules
def check_expense_limit(exp: dict) -> dict:
    amount = exp.get("amount", 0.0)
    category = exp.get("category")
    if category == "Meals" and amount > 150.00:
        return {"passed": False, "detail": f"Policy Limit Violation: Meal expense (${amount:,.2f}) exceeds daily cap of $150.00."}
    return {"passed": True, "detail": "Passed."}

def check_expense_receipt(exp: dict) -> dict:
    amount = exp.get("amount", 0.0)
    status = exp.get("receipt_status")
    if amount > 25.00 and status == "MISSING":
        return {"passed": False, "detail": f"Receipt Violation: Expense amount (${amount:,.2f}) > $25.00 is missing receipt verification."}
    return {"passed": True, "detail": "Passed."}


# --- Unified Rules Engine & Risk Triage ---

def run_deterministic_rules(tx: dict) -> dict:
    """Stage 1: Core deterministic rules checks."""
    tx_type = tx.get("tx_type")
    failures = []
    score = 0
    
    if tx_type == "JOURNAL_ENTRY":
        sod = check_sod(tx)
        if not sod["passed"]:
            failures.append({"rule": "Segregation of Duties Check", "error": sod["detail"]})
            score += 35
        mat = check_materiality(tx)
        if not mat["passed"]:
            failures.append({"rule": "Materiality Check", "error": mat["detail"]})
            score += 40
        cut = check_cutoff(tx)
        if not cut["passed"]:
            failures.append({"rule": "Period Cutoff Check", "error": cut["detail"]})
            score += 25
        sensitive = check_sensitive_accounts(tx)
        if not sensitive["passed"]:
            failures.append({"rule": "Sensitive Account Check", "error": sensitive["detail"]})
            score += 30
            
    elif tx_type == "BILLING_INVOICE":
        rate = check_invoice_rate(tx)
        if not rate["passed"]:
            failures.append({"rule": "Invoice Pricing Check", "error": rate["detail"]})
            score += 40
        dup = check_duplicate_invoice(tx)
        if not dup["passed"]:
            failures.append({"rule": "Duplicate Invoice Check", "error": dup["detail"]})
            score += 30
            
    elif tx_type == "EXPENSE_REPORT":
        lim = check_expense_limit(tx)
        if not lim["passed"]:
            failures.append({"rule": "Per-Diem Cap Check", "error": lim["detail"]})
            score += 30
        rec = check_expense_receipt(tx)
        if not rec["passed"]:
            failures.append({"rule": "Receipt Verification Check", "error": rec["detail"]})
            score += 35

    # Scale score based on size
    amount = tx.get("amount", 0.0)
    if amount > 200000.00:
        score += 20
    elif amount > 50000.00:
        score += 10
        
    score = min(score, 100)
    action = "AUTO_APPROVE" if (score < 30 and len(failures) == 0) else "ESCALATE_TO_HUMAN"
    
    return {
        "id": tx.get("journal_id") or tx.get("invoice_id") or tx.get("report_id"),
        "tx_type": tx_type,
        "passed": len(failures) == 0,
        "failures": failures,
        "risk_score": score,
        "action": action
    }


# --- Evidence Retrieval Tools (RAG) ---

def query_vendor_contract(vendor_name: str) -> str:
    """Tool: Retrieve pricing contract details for a given vendor name."""
    return f"Contract Details:\n{json.dumps(get_contract(vendor_name), indent=2)}"

def query_related_transactions(query_val: str) -> str:
    """Tool: Search other historical bookings matching an account or vendor code."""
    feed = get_mixed_feed()
    related = []
    for tx in feed:
        if tx.get("account") == query_val or tx.get("vendor") == query_val or tx.get("employee") == query_val:
            related.append({
                "id": tx.get("journal_id") or tx.get("invoice_id") or tx.get("report_id"),
                "amount": tx.get("amount"),
                "posting_date": tx.get("posting_date"),
                "description": tx.get("description")
            })
    return f"Found {len(related)} related records:\n" + json.dumps(related[:5], indent=2)

def read_finance_policy(policy_name: str) -> str:
    """Tool: Retrieve text of specific finance policy."""
    return f"Corporate Policy Text for {policy_name}:\n{get_policy(policy_name)}"

def search_historical_memos(key_val: str) -> str:
    """Tool: Search historical accounting explanation memos matching an account or vendor name."""
    matches = []
    for memo in HISTORICAL_MEMOS:
        if memo["key"] == key_val:
            matches.append(memo)
    if not matches:
        return "No historical explanation memos found."
    return "Historical memo matches:\n" + json.dumps(matches, indent=2)

def get_user_profile(user_id: str) -> str:
    """Tool: Fetch hierarchy profile and roles for a corporate user ID."""
    return f"User metadata:\n{get_user_info(user_id)}"


def validate_specialist_evidence_refs(
    specialist: dict,
    evidence_registry: dict,
) -> list[str]:
    """Validates that all cited evidence sources exist in the snapshotted RAG snapshot."""
    invalid_refs = []

    for fact in specialist.get("facts", []):
        for ref in fact.get("evidence_refs", []):
            source_id = ref.get("source_id")
            if source_id not in evidence_registry:
                invalid_refs.append(source_id)

    for hypothesis in specialist.get("hypotheses", []):
        for source_id in hypothesis.get("basis_source_ids", []):
            if source_id not in evidence_registry:
                invalid_refs.append(source_id)

    return sorted(set(invalid_refs))


def validate_critic_claim_coverage(
    specialist_res: dict,
    critic_res: dict
) -> dict:
    """Verifies that the critic evaluated every specialist fact exactly once.
    
    Returns a dict with key 'valid' and list of any coverage failures.
    """
    specialist_claim_ids = {fact["claim_id"] for fact in specialist_res.get("facts", [])}
    critic_claim_ids = [check["claim_id"] for check in critic_res.get("claim_checks", [])]
    
    missing_claims = specialist_claim_ids - set(critic_claim_ids)
    unknown_claims = set(critic_claim_ids) - specialist_claim_ids
    duplicate_claims = {cid for cid in critic_claim_ids if critic_claim_ids.count(cid) > 1}
    
    valid = not (missing_claims or unknown_claims or duplicate_claims)
    return {
        "valid": valid,
        "missing_claims": sorted(list(missing_claims)),
        "unknown_claims": sorted(list(unknown_claims)),
        "duplicate_claims": sorted(list(duplicate_claims))
    }
