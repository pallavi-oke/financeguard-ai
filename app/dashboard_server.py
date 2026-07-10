# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import os
import json
import logging
from pathlib import Path
from fastapi import FastAPI, HTTPException, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.mock_data import get_mixed_feed, EVIDENCE_REGISTRY
from app.tools import run_deterministic_rules
from app.audit import write_audit_packet, verify_audit_packet, verify_manifest_chain, AUDIT_LOG_DIR
from run_pilot import run_pipeline, run_governance_gate

# Pydantic schemas for request bodies
from pydantic import BaseModel
class DecisionRequest(BaseModel):
    decision: str
    override_reason: str
    reviewer: str

router = APIRouter()



HTML_PATH = Path(__file__).resolve().parent.parent / "finance_ai_control_center.html"

# In-memory storage for session reviews
MOCK_REVIEW_DATA = {}


@router.get("/", response_class=HTMLResponse)
def read_root():
    """Serves the main human review dashboard page."""
    # Prioritize the packaged index.html inside the container
    app_index = Path(__file__).resolve().parent / "index.html"
    if app_index.exists():
        with open(app_index, "r") as f:
            return HTMLResponse(content=f.read(), status_code=200)
            
    if HTML_PATH.exists():
        with open(HTML_PATH, "r") as f:
            return HTMLResponse(content=f.read(), status_code=200)
            
    parent_path = Path(__file__).resolve().parent.parent.parent / "finance_ai_control_center.html"
    if parent_path.exists():
        with open(parent_path, "r") as f:
            return HTMLResponse(content=f.read(), status_code=200)
            
    raise HTTPException(status_code=404, detail="finance_ai_control_center.html dashboard file not found.")


@router.get("/api/exceptions")
def get_exceptions():
    """Returns the exception queue by screening the mixed transaction feed."""
    feed = get_mixed_feed()
    exceptions = []
    for tx in feed:
        rules = run_deterministic_rules(tx)
        exceptions.append({
            "transaction": tx,
            "rules_screening": rules
        })
    return exceptions


@router.get("/api/exceptions/{transaction_id}")
def get_exception_detail(transaction_id: str):
    """Returns detail for a single transaction exception."""
    feed = get_mixed_feed()
    for tx in feed:
        tx_id = tx.get("journal_id") or tx.get("invoice_id") or tx.get("report_id")
        if tx_id == transaction_id:
            rules = run_deterministic_rules(tx)
            return {
                "transaction": tx,
                "rules_screening": rules
            }
    raise HTTPException(status_code=404, detail="Exception not found.")


@router.post("/api/exceptions/{transaction_id}/investigate")
async def investigate_exception(transaction_id: str):
    """Executes the multi-agent investigation pipeline (Coordinator -> Worker -> Critic -> Gate).
    
    If the Gemini API key is a placeholder, returns high-fidelity simulated structured output
    to allow full dashboard functionality in offline/mock mode.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    api_key_configured = bool(api_key and api_key != "YOUR_GEMINI_API_KEY_HERE" and "placeholder" not in api_key.lower())
    is_cloud_run = os.environ.get("K_SERVICE") is not None
    
    use_live_pipeline = api_key_configured or is_cloud_run
    
    feed = get_mixed_feed()
    target_tx = None
    for tx in feed:
        tx_id = tx.get("journal_id") or tx.get("invoice_id") or tx.get("report_id")
        if tx_id == transaction_id:
            target_tx = tx
            break
            
    if not target_tx:
        raise HTTPException(status_code=404, detail="Transaction not found.")
        
    rules = run_deterministic_rules(target_tx)
    
    if use_live_pipeline:
        try:
            report_str, critic_res, gov_res, spec_name, evidence_bundle, coord_res = await run_pipeline(target_tx, rules)
            specialist_res = json.loads(report_str)
            
            # Save in-memory for decision submission
            MOCK_REVIEW_DATA[transaction_id] = {
                "report": specialist_res,
                "critic": critic_res,
                "governance": gov_res,
                "evidence": evidence_bundle,
                "spec_name": spec_name,
                "coordinator": coord_res
            }
            
            return {
                "specialist_output": specialist_res,
                "critic_output": critic_res,
                "governance_gate": gov_res,
                "evidence_registry": evidence_bundle,
                "coordinator_route": spec_name
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")
    else:
        # Offline High-Fidelity Mock Generator to support zero-config review experience
        spec_name = "close_specialist" if "journal_id" in target_tx else ("billing_specialist" if "invoice_id" in target_tx else "expense_specialist")
        
        # Specialist Mock Output
        if spec_name == "close_specialist":
            specialist_res = {
                "facts": [
                    {
                        "claim_id": "FACT-01",
                        "claim": "Journal Entry JE-1001 was created and approved by the same user account (ANALYST_01).",
                        "claim_type": "TRANSACTION_FACT",
                        "evidence_refs": [{"source_id": f"TX-{transaction_id}", "source_type": "transaction", "source_field": "created_by"}]
                    },
                    {
                        "claim_id": "FACT-02",
                        "claim": "Policy JE-101 requires segregation of duties for all manual adjusting journal entries.",
                        "claim_type": "POLICY_FACT",
                        "evidence_refs": [{"source_id": "POL-SOD", "source_type": "policy", "source_excerpt": "separate, designated users"}]
                    }
                ],
                "hypotheses": [
                    {
                        "hypothesis_id": "HYP-01",
                        "statement": "Urgent manual balance adjustments bypassed normal dual-control review procedures.",
                        "basis_source_ids": [f"TX-{transaction_id}", "POL-SOD"],
                        "uncertainty": "Unconfirmed if VP Ops Finance verbal approval was given prior to posting."
                    }
                ],
                "missing_evidence": [],
                "confidence": "HIGH",
                "recommendation": "MANUAL_INVESTIGATION",
                "rationale": "Must escalate to human due to direct SOX control violation."
            }
        elif spec_name == "billing_specialist":
            if transaction_id == "INV-2001":
                specialist_res = {
                    "facts": [
                        {
                            "claim_id": "FACT-01",
                            "claim": "INV-2001 bills ConsultingCorp at $180 per hour.",
                            "claim_type": "TRANSACTION_FACT",
                            "evidence_refs": [{"source_id": "TX-INV-2001", "source_type": "transaction", "source_field": "unit_rate"}]
                        },
                        {
                            "claim_id": "FACT-02",
                            "claim": "The active contract rate is $150 per hour.",
                            "claim_type": "CONTRACT_FACT",
                            "evidence_refs": [{"source_id": "CON-CONSULTINGCORP-2026", "source_type": "contract", "source_field": "hourly_rate"}]
                        },
                        {
                            "claim_id": "FACT-03",
                            "claim": "A historical $180 per hour exception existed for emergency weekend migration support in Q4 2025.",
                            "claim_type": "HISTORICAL_CONTEXT",
                            "evidence_refs": [{"source_id": "MEMO-MIGRATION-RATE-2025", "source_type": "historical_memo"}]
                        }
                    ],
                    "hypotheses": [
                        {
                            "hypothesis_id": "HYP-01",
                            "statement": "The vendor may have applied the prior migration-support rate to the current invoice.",
                            "basis_source_ids": ["TX-INV-2001", "MEMO-MIGRATION-RATE-2025"],
                            "uncertainty": "The available evidence does not show that the prior exception applies to this invoice."
                        }
                    ],
                    "missing_evidence": [
                        "Current contract amendment authorizing $180 per hour",
                        "Invoice-specific rate override",
                        "Evidence that the work qualifies as emergency weekend migration support"
                    ],
                    "confidence": "MEDIUM",
                    "recommendation": "REQUEST_DOCUMENTS",
                    "rationale": "The rate mismatch is confirmed, but applicability of the historical exception is unresolved."
                }
            else:
                specialist_res = {
                    "facts": [
                        {
                            "claim_id": "FACT-01",
                            "claim": "Billed invoice price exceeds contract flat rate of $150.00.",
                            "claim_type": "TRANSACTION_FACT",
                            "evidence_refs": [
                                {"source_id": f"TX-{transaction_id}", "source_type": "transaction", "source_field": "unit_price"},
                                {"source_id": "CON-CONSULTINGCORP-2026", "source_type": "contract", "source_excerpt": "$150.00/hour"}
                            ]
                        }
                    ],
                    "hypotheses": [
                        {
                            "hypothesis_id": "HYP-01",
                            "statement": "Billed consulting hours matched rate cap conflicts in PO pricing.",
                            "basis_source_ids": [f"TX-{transaction_id}"],
                            "uncertainty": "Requires checking if rates changed in subsequent PO adjustments."
                        }
                    ],
                    "missing_evidence": [],
                    "confidence": "HIGH",
                    "recommendation": "VENDOR_DISPUTE",
                    "rationale": "Overbilling violates pricing agreement."
                }
        else: # expense_specialist
            specialist_res = {
                "facts": [
                    {
                        "claim_id": "FACT-01",
                        "claim": "Client business meals expense is $220.00, exceeding daily $150.00 cap limit.",
                        "claim_type": "TRANSACTION_FACT",
                        "evidence_refs": [{"source_id": f"TX-{transaction_id}", "source_type": "transaction", "source_field": "amount"}]
                    }
                ],
                "hypotheses": [
                    {
                        "hypothesis_id": "HYP-01",
                        "statement": "Pre-authorized meal cap exception memo approved by VP Ops Finance on 2026-06-30 covers the overage.",
                        "basis_source_ids": ["MEMO-MEAL-CAP-WU"],
                        "uncertainty": "None. Overage justified by memo."
                    }
                ],
                "missing_evidence": [],
                "confidence": "HIGH",
                "recommendation": "APPROVE_WITH_EXCEPTION",
                "rationale": "Exception is authorized by historical memo."
            }
            
        evidence_bundle = gather_evidence_registry_snapshot(target_tx, spec_name)
        
        # Build critic checks dynamically from specialist facts to ensure 100% schema and coverage consistency!
        claim_checks = []
        for fact in specialist_res["facts"]:
            claim_checks.append({
                "claim_id": fact["claim_id"],
                "status": "SUPPORTED",
                "validated_source_ids": [ref["source_id"] for ref in fact["evidence_refs"]],
                "reason": "Grounded successfully in snapshot document."
            })
            
        # Critic Mock Output
        critic_res = {
            "verdict": "PASS",
            "claim_checks": claim_checks,
            "unsupported_claims": [],
            "contradicted_claims": [],
            "missing_evidence": [] if transaction_id != "INV-2001" else [
                "Current authorization for the $180 per hour rate",
                "Evidence that the current work falls within the historical exception scope"
            ],
            "invalid_source_references": [],
            "confidence_score": 0.98 if transaction_id != "INV-2001" else 0.95,
            "reasons": "All facts successfully grounded in registry snapshot." if transaction_id != "INV-2001" else "The specialist's claims are grounded, but the case remains unresolved because current authorization is missing."
        }
        
        coord_res = {
            "domain": "CLOSE" if spec_name == "close_specialist" else ("BILLING" if spec_name == "billing_specialist" else "EXPENSE"),
            "designated_specialist": spec_name,
            "routing_reason": "Seeded offline exception pattern matches domain rules.",
            "confidence_score": 0.98
        }
        # Governance Gate Mock Output
        gov_res = run_governance_gate(target_tx, rules, coord_res, specialist_res, critic_res, evidence_bundle)
        
        # Save in-memory
        MOCK_REVIEW_DATA[transaction_id] = {
            "report": specialist_res,
            "critic": critic_res,
            "governance": gov_res,
            "evidence": evidence_bundle,
            "spec_name": spec_name,
            "coordinator": coord_res
        }
        
        return {
            "specialist_output": specialist_res,
            "critic_output": critic_res,
            "governance_gate": gov_res,
            "evidence_registry": evidence_bundle,
            "coordinator_route": spec_name
        }


@router.post("/api/exceptions/{transaction_id}/decision")
def submit_decision(transaction_id: str, req: DecisionRequest):
    """Saves the human override disposition and generates a tamper-evident audit packet."""
    review_data = MOCK_REVIEW_DATA.get(transaction_id)
    if not review_data:
        raise HTTPException(status_code=400, detail="Must run investigate before submitting decision.")
        
    feed = get_mixed_feed()
    target_tx = None
    for tx in feed:
        tx_id = tx.get("journal_id") or tx.get("invoice_id") or tx.get("report_id")
        if tx_id == transaction_id:
            target_tx = tx
            break
            
    if not target_tx:
        raise HTTPException(status_code=404, detail="Transaction not found.")
        
    rules = run_deterministic_rules(target_tx)
    
    human_decision = {
        "status": req.decision,
        "reviewer": req.reviewer,
        "timestamp": datetime.now().isoformat(),
        "override_reason": req.override_reason,
        "comments": "Submited via human-in-the-loop dashboard."
    }
    
    # Write to local JSON audit trail
    try:
        coord_results = review_data.get("coordinator") or {
            "domain": "CLOSE" if review_data["spec_name"] == "close_specialist" else ("BILLING" if review_data["spec_name"] == "billing_specialist" else "EXPENSE"),
            "designated_specialist": review_data["spec_name"],
            "routing_reason": "Mapped schema.",
            "confidence_score": 1.0
        }
        packet_path, checksum = write_audit_packet(
            target_tx, rules, coord_results, review_data["report"],
            review_data["evidence"], review_data["critic"], review_data["governance"],
            human_decision
        )
        return {
            "status": "success",
            "packet_path": packet_path,
            "checksum": checksum
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write audit packet: {str(e)}")


@router.get("/api/audit-packets/{transaction_id}")
def get_audit_packet(transaction_id: str):
    """Loads and returns the saved JSON audit packet for a transaction."""
    if not AUDIT_LOG_DIR.exists():
        raise HTTPException(status_code=404, detail="No audit packets exist yet.")
        
    for file in AUDIT_LOG_DIR.iterdir():
        if file.suffix == ".json" and transaction_id in file.name:
            with open(file, "r") as f:
                return json.load(f)
                
    raise HTTPException(status_code=404, detail="Audit packet not found.")


@router.post("/api/audit/verify/{transaction_id}")
def verify_packet(transaction_id: str):
    """Verifies the checksum integrity of a transaction's audit packet."""
    if not AUDIT_LOG_DIR.exists():
        raise HTTPException(status_code=404, detail="No audit packets exist yet.")
        
    target_file = None
    for file in AUDIT_LOG_DIR.iterdir():
        if file.suffix == ".json" and transaction_id in file.name:
            target_file = file
            break
            
    if not target_file:
        raise HTTPException(status_code=404, detail="Audit packet not found.")
        
    is_valid = verify_audit_packet(str(target_file))
    return {
        "transaction_id": transaction_id,
        "valid": is_valid
    }


@router.get("/api/manifest/verify")
def get_manifest_verification():
    """Runs a complete cryptographic manifest chain audit."""
    verification = verify_manifest_chain()
    return verification


# Validation Test Vector for Browser/Python Hashing Equivalence
@router.get("/api/hash-test-vector")
def get_hash_test_vector():
    """Returns the expected test vector string and pre-calculated SHA-256 hash."""
    return {
        "payload": {
            "a": 1,
            "b": {
                "c": True,
                "d": ["x", "y"]
            }
        },
        "expected_canonical_string": '{"a":1,"b":{"c":true,"d":["x","y"]}}',
        "expected_sha256": "9532718f9669695b8c37c64bcbdd6f898d62b86d5583c036d4522e0f899ba535"
    }


app = FastAPI(title="FinanceGuard AI Dashboard Server", version="1.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.dashboard_server:app", host="127.0.0.1", port=8080, reload=True)
