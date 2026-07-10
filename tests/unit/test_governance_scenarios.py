# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import pytest
from run_pilot import check_evidence_sufficiency, run_governance_gate

def test_billing_rate_mismatch_sufficiency():
    tx = {
        "tx_type": "BILLING_INVOICE",
        "invoice_id": "INV-2001",
        "vendor": "ConsultingCorp",
        "amount": 18000.00,
        "po_number": "PO-9002",
        "unit_price": 180.00,
        "posting_date": "2026-07-02T13:45:00"
    }
    
    # Snapshot contains active contract and historical exception, but no current override
    snapshot = {
        "POL-BILLING": {
            "source_type": "policy",
            "title": "Procurement BI-201 Billing matching",
            "content": "Billed invoice unit rates must match contract rate agreements exactly."
        },
        "CON-CONSULTINGCORP-2026": {
            "source_type": "contract",
            "vendor": "ConsultingCorp",
            "contracted_rate_usd": 150.00
        },
        "MEMO-MIGRATION-RATE-2025": {
            "source_type": "historical_memo",
            "content": "A temporary rate of $180/hour was approved for emergency weekend migration support during Q4 2025."
        }
    }
    
    missing = check_evidence_sufficiency(tx, snapshot)
    assert "Invoice rate exceeds active contract rate." in missing
    assert "Historical exception does not establish current authorization." in missing
    assert "Current amendment or invoice-specific override is missing." in missing


def test_governance_request_documents_action():
    tx = {
        "tx_type": "BILLING_INVOICE",
        "invoice_id": "INV-2001",
        "vendor": "ConsultingCorp",
        "amount": 18000.00,
        "po_number": "PO-9002",
        "unit_price": 180.00,
        "posting_date": "2026-07-02T13:45:00"
    }
    
    snapshot = {
        "CON-CONSULTINGCORP-2026": {
            "source_type": "contract",
            "vendor": "ConsultingCorp",
            "contracted_rate_usd": 150.00
        }
    }
    
    rule_results = {
        "passed": False,
        "failures": [{"rule": "Pricing Violation", "error": "rate mismatch"}]
    }
    
    coordinator_res = {"confidence_score": 0.95}
    
    # Specialist correctly recommends requesting documents
    specialist_res = {
        "confidence": "MEDIUM",
        "recommendation": "REQUEST_DOCUMENTS",
        "missing_evidence": ["Current contract amendment authorizing $180 per hour"]
    }
    
    # Critic passes grounded claims
    critic_res = {
        "verdict": "PASS",
        "claim_checks": [{"claim_id": "FACT-01", "status": "SUPPORTED"}]
    }
    
    gov = run_governance_gate(tx, rule_results, coordinator_res, specialist_res, critic_res, snapshot)
    
    assert gov["passed"] is False
    assert gov["action"] == "REQUEST_DOCUMENTS"
    assert "CONTRACT_RATE_VARIANCE" in gov["triggered_controls"]
    assert "MISSING_CURRENT_RATE_AUTHORIZATION" in gov["triggered_controls"]


def test_adversarial_critic_rejects_ungrounded_claims():
    tx = {
        "tx_type": "BILLING_INVOICE",
        "invoice_id": "INV-2001",
        "vendor": "ConsultingCorp",
        "amount": 18000.00,
        "po_number": "PO-9002",
        "unit_price": 180.00,
        "posting_date": "2026-07-02T13:45:00"
    }
    
    snapshot = {
        "CON-CONSULTINGCORP-2026": {
            "source_type": "contract",
            "vendor": "ConsultingCorp",
            "contracted_rate_usd": 150.00
        }
    }
    
    rule_results = {
        "passed": False,
        "failures": [{"rule": "Pricing Violation", "error": "rate mismatch"}]
    }
    
    coordinator_res = {"confidence_score": 0.95}
    specialist_res = {
        "confidence": "HIGH",
        "recommendation": "APPROVE_WITH_EXCEPTION"
    }
    
    # Adversarial Critic marks ungrounded claim as UNSUPPORTED and rejects
    critic_res_adv = {
        "verdict": "REJECT",
        "claim_checks": [
            {"claim_id": "FACT-01", "status": "SUPPORTED"},
            {"claim_id": "FACT-02", "status": "UNSUPPORTED", "reason": "No active contract or amendment supports $180/hr."}
        ],
        "reasons": "Factual claim FACT-02 is unsupported by current active registry."
    }
    
    gov = run_governance_gate(tx, rule_results, coordinator_res, specialist_res, critic_res_adv, snapshot)
    
    # Governance fails closed and escalates to human due to Critic rejection
    assert gov["passed"] is False
    assert gov["action"] == "ESCALATE_TO_HUMAN"
    assert "CRITIC_GROUNDING_CONTROL" in gov["triggered_controls"]
