# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

from typing import Literal, List, Optional
from pydantic import BaseModel, Field, model_validator

# --- 1. Coordinator Output Schema ---
class CoordinatorOutput(BaseModel):
    domain: Literal["CLOSE", "BILLING", "EXPENSE"]
    designated_specialist: Literal[
        "close_specialist",
        "billing_specialist",
        "expense_specialist"
    ]
    routing_reason: str
    confidence_score: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_domain_specialist_pair(self) -> "CoordinatorOutput":
        expected = {
            "CLOSE": "close_specialist",
            "BILLING": "billing_specialist",
            "EXPENSE": "expense_specialist"
        }

        if self.designated_specialist != expected[self.domain]:
            raise ValueError(
                f"{self.domain} must route to {expected[self.domain]}"
            )

        return self

# --- 2. Specialist Output Schema ---
class EvidenceReference(BaseModel):
    source_id: str
    source_type: Literal[
        "transaction",
        "policy",
        "contract",
        "historical_memo",
        "user_profile",
        "related_transaction"
    ]
    source_field: Optional[str] = None
    source_excerpt: Optional[str] = None

class GroundedClaim(BaseModel):
    claim_id: str
    claim: str
    claim_type: Literal[
        "TRANSACTION_FACT",
        "POLICY_FACT",
        "CONTRACT_FACT",
        "HISTORICAL_CONTEXT"
    ]
    evidence_refs: List[EvidenceReference] = Field(min_length=1)

class Hypothesis(BaseModel):
    hypothesis_id: str
    statement: str
    basis_source_ids: List[str]
    uncertainty: str

class SpecialistOutput(BaseModel):
    facts: List[GroundedClaim]
    hypotheses: List[Hypothesis]
    missing_evidence: List[str]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    recommendation: Literal[
        "APPROVE_WITH_EXCEPTION",
        "REJECT_AND_ESCALATE",
        "REQUEST_DOCUMENTS",
        "REQUEST_MANAGER_REVIEW",
        "VENDOR_DISPUTE",
        "MANUAL_INVESTIGATION"
    ]
    rationale: str

# --- 3. Critic Output Schema ---
class ClaimCheck(BaseModel):
    claim_id: str
    status: Literal[
        "SUPPORTED",
        "UNSUPPORTED",
        "CONTRADICTED",
        "INSUFFICIENT_EVIDENCE",
        "INVALID_SOURCE_REFERENCE"
    ]
    validated_source_ids: List[str]
    reason: str

class CriticOutput(BaseModel):
    verdict: Literal["PASS", "REJECT"]
    claim_checks: List[ClaimCheck]
    unsupported_claims: List[str]
    contradicted_claims: List[str]
    missing_evidence: List[str]
    invalid_source_references: List[str]
    confidence_score: float = Field(ge=0.0, le=1.0)
    reasons: str

# --- 4. Deterministic Governance Decision Schema ---
class GovernanceDecision(BaseModel):
    passed: bool
    action: Literal[
        "AUTO_APPROVE",
        "ESCALATE_TO_HUMAN",
        "REQUEST_DOCUMENTS",
        "BLOCKED_BY_SAFETY_GATE",
        "SYSTEM_ERROR_ESCALATION"
    ]
    reasons: List[str]
    triggered_controls: List[str]
