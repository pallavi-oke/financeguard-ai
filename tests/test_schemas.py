# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import pytest
from pydantic import ValidationError
from app.schemas import CoordinatorOutput, SpecialistOutput, CriticOutput
from app.tools import validate_specialist_evidence_refs, validate_critic_claim_coverage

def test_coordinator_validation():
    # Valid pairings
    assert CoordinatorOutput(domain="CLOSE", designated_specialist="close_specialist", routing_reason="OK", confidence_score=0.9)
    assert CoordinatorOutput(domain="BILLING", designated_specialist="billing_specialist", routing_reason="OK", confidence_score=0.85)
    assert CoordinatorOutput(domain="EXPENSE", designated_specialist="expense_specialist", routing_reason="OK", confidence_score=0.99)
    
    # Invalid specialist mapping
    with pytest.raises(ValidationError):
         CoordinatorOutput(domain="CLOSE", designated_specialist="expense_specialist", routing_reason="mismatch", confidence_score=0.9)
         
    # Invalid domain enum
    with pytest.raises(ValidationError):
         CoordinatorOutput(domain="INVALID_DOMAIN", designated_specialist="close_specialist", routing_reason="mismatch", confidence_score=0.9)


def test_specialist_validation():
    # Valid output
    valid_data = {
        "facts": [
            {
                "claim_id": "FACT-01",
                "claim": "User L1 created and approved the adjustment.",
                "claim_type": "TRANSACTION_FACT",
                "evidence_refs": [{"source_id": "TX-JE-1001", "source_type": "transaction"}]
            }
        ],
        "hypotheses": [
            {
                "hypothesis_id": "HYP-01",
                "statement": "Urgent posting caused SoD violation.",
                "basis_source_ids": ["TX-JE-1001"],
                "uncertainty": "unknown"
            }
        ],
        "missing_evidence": [],
        "confidence": "HIGH",
        "recommendation": "MANUAL_INVESTIGATION",
        "rationale": "Direct SOX control violation."
    }
    obj = SpecialistOutput(**valid_data)
    assert obj.confidence == "HIGH"
    
    # Missing evidence reference in facts
    invalid_data = valid_data.copy()
    invalid_data["facts"] = [
        {
            "claim_id": "FACT-01",
            "claim": "No citation fact",
            "claim_type": "TRANSACTION_FACT",
            "evidence_refs": []  # Requires at least 1 citation reference
        }
    ]
    with pytest.raises(ValidationError):
        SpecialistOutput(**invalid_data)


def test_critic_validation():
    valid_data = {
        "verdict": "PASS",
        "claim_checks": [
            {
                "claim_id": "FACT-01",
                "status": "SUPPORTED",
                "validated_source_ids": ["TX-JE-1001"],
                "reason": "Matches transaction parameters."
            }
        ],
        "unsupported_claims": [],
        "contradicted_claims": [],
        "missing_evidence": [],
        "invalid_source_references": [],
        "confidence_score": 0.98,
        "reasons": "Audit complete."
    }
    obj = CriticOutput(**valid_data)
    assert obj.verdict == "PASS"


def test_security_validators():
    # 1. Specialist evidence refs validation
    specialist_data = {
        "facts": [
            {
                "claim_id": "F1",
                "claim": "Test claim",
                "claim_type": "TRANSACTION_FACT",
                "evidence_refs": [{"source_id": "VALID-1", "source_type": "transaction"}]
            },
            {
                "claim_id": "F2",
                "claim": "Test claim 2",
                "claim_type": "TRANSACTION_FACT",
                "evidence_refs": [{"source_id": "INVALID-1", "source_type": "transaction"}]
            }
        ],
        "hypotheses": [
            {
                "hypothesis_id": "H1",
                "statement": "Hypo",
                "basis_source_ids": ["VALID-1", "INVALID-2"],
                "uncertainty": "none"
            }
        ]
    }
    
    registry = {"VALID-1": {"source_type": "transaction"}}
    invalid_refs = validate_specialist_evidence_refs(specialist_data, registry)
    assert invalid_refs == ["INVALID-1", "INVALID-2"]

    # 2. Critic coverage validation
    # Match: F1, F2
    critic_matching = {
        "claim_checks": [
            {"claim_id": "F1", "status": "SUPPORTED", "validated_source_ids": [], "reason": ""},
            {"claim_id": "F2", "status": "SUPPORTED", "validated_source_ids": [], "reason": ""}
        ]
    }
    assert validate_critic_claim_coverage(specialist_data, critic_matching)["valid"] is True
    
    # Missing F2
    critic_missing = {
        "claim_checks": [
            {"claim_id": "F1", "status": "SUPPORTED", "validated_source_ids": [], "reason": ""}
        ]
    }
    res = validate_critic_claim_coverage(specialist_data, critic_missing)
    assert res["valid"] is False
    assert res["missing_claims"] == ["F2"]
    
    # Unknown F3
    critic_unknown = {
        "claim_checks": [
            {"claim_id": "F1", "status": "SUPPORTED", "validated_source_ids": [], "reason": ""},
            {"claim_id": "F2", "status": "SUPPORTED", "validated_source_ids": [], "reason": ""},
            {"claim_id": "F3", "status": "SUPPORTED", "validated_source_ids": [], "reason": ""}
        ]
    }
    res = validate_critic_claim_coverage(specialist_data, critic_unknown)
    assert res["valid"] is False
    assert res["unknown_claims"] == ["F3"]
    
    # Duplicate F1
    critic_duplicate = {
        "claim_checks": [
            {"claim_id": "F1", "status": "SUPPORTED", "validated_source_ids": [], "reason": ""},
            {"claim_id": "F2", "status": "SUPPORTED", "validated_source_ids": [], "reason": ""},
            {"claim_id": "F1", "status": "SUPPORTED", "validated_source_ids": [], "reason": ""}
        ]
    }
    res = validate_critic_claim_coverage(specialist_data, critic_duplicate)
    assert res["valid"] is False
    assert res["duplicate_claims"] == ["F1"]
