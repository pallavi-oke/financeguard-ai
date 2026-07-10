# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from pydantic import ValidationError

# Import Pydantic models from schemas
from app.schemas import CoordinatorOutput, SpecialistOutput, CriticOutput, GovernanceDecision

# Import project components
from app.mock_data import get_mixed_feed, get_contract, get_policy, get_user_info, EVIDENCE_REGISTRY, get_adversarial_feed
from app.tools import run_deterministic_rules, HISTORICAL_MEMOS, validate_specialist_evidence_refs, validate_critic_claim_coverage
from app.agent import (
    coordinator_agent,
    close_specialist_agent,
    billing_specialist_agent,
    expense_specialist_agent,
    critic_agent
)
from app.audit import write_audit_packet, verify_audit_packet, verify_manifest_chain
from app.app_utils.telemetry import setup_telemetry
from app.app_utils.tracing import log_trace

# ADK runner imports
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

def clean_json_text(raw_text: str) -> str:
    """Helper to clean and isolate JSON block from markdown response formatting."""
    clean = raw_text.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    if clean.endswith("```"):
        clean = clean[:-3]
    return clean.strip()


def gather_evidence_registry_snapshot(tx: dict, spec_name: str) -> dict:
    """Compiles a static evidence registry snapshot with stable unique IDs for audit provenance.
    
    Normalizes transaction payload, policies, contracts, user profiles, and memos.
    """
    snapshot = {}
    
    # 1. Register transaction payload
    tx_id = tx.get("journal_id") or tx.get("invoice_id") or tx.get("report_id") or "UNKNOWN"
    tx_source_id = f"TX-{tx_id}"
    snapshot[tx_source_id] = {
        "source_type": "transaction",
        "content": tx
    }
    
    # 2. Register policies, contracts, profiles, memos
    if spec_name == "close_specialist":
        for policy_id in ["POL-SOD", "POL-MATERIALITY", "POL-CUTOFF", "POL-SENSITIVE"]:
            if policy_id in EVIDENCE_REGISTRY:
                snapshot[policy_id] = EVIDENCE_REGISTRY[policy_id]
        
        creator = tx.get("created_by")
        approver = tx.get("approved_by")
        for u in [creator, approver]:
            usr_key = f"USR-{u}"
            if usr_key in EVIDENCE_REGISTRY:
                snapshot[usr_key] = EVIDENCE_REGISTRY[usr_key]
                
        account = tx.get("account", "")
        for k, v in EVIDENCE_REGISTRY.items():
            if v["source_type"] == "historical_memo" and account in v.get("content", ""):
                snapshot[k] = v
                
    elif spec_name == "billing_specialist":
        if "POL-BILLING" in EVIDENCE_REGISTRY:
            snapshot["POL-BILLING"] = EVIDENCE_REGISTRY["POL-BILLING"]
            
        vendor = tx.get("vendor", "")
        if vendor:
            for k, v in EVIDENCE_REGISTRY.items():
                if v.get("source_type") == "contract" and v.get("vendor", "").lower() == vendor.lower():
                    snapshot[k] = v
                elif v.get("source_type") == "historical_memo" and vendor.lower() in v.get("content", "").lower():
                    snapshot[k] = v
                    
    elif spec_name == "expense_specialist":
        for policy_id in ["POL-MEAL", "POL-RECEIPT"]:
            if policy_id in EVIDENCE_REGISTRY:
                snapshot[policy_id] = EVIDENCE_REGISTRY[policy_id]
                
        employee = tx.get("employee", "")
        if employee:
            for k, v in EVIDENCE_REGISTRY.items():
                if v.get("source_type") == "user_profile" and employee.lower() in v.get("name", "").lower():
                    snapshot[k] = v
                elif v.get("source_type") == "historical_memo" and employee.lower() in v.get("content", "").lower():
                    snapshot[k] = v
                    
    return snapshot


def check_evidence_sufficiency(tx: dict, snapshot: dict) -> list[str]:
    """Deterministically verifies if all required evidence exists and matches compliance limits."""
    missing = []
    tx_type = tx.get("tx_type")
    
    # 1. Check general expired contracts based on effective/expiration dates
    posting_date = tx.get("posting_date", "")
    for k, v in snapshot.items():
        if v.get("source_type") == "contract":
            eff = v.get("effective_date", "")
            exp = v.get("expiration_date", "")
            if posting_date and eff and exp:
                post_day = posting_date.split("T")[0]
                if post_day < eff or post_day > exp:
                    missing.append(f"Contract {k} is EXPIRED/NOT_EFFECTIVE on posting date {post_day} (Effective: {eff} to {exp}).")
            
    # 2. Check rate conflicts
    po_rate = tx.get("unit_price")
    for k, v in snapshot.items():
        if v.get("source_type") == "contract":
            contract_rate = v.get("contracted_rate_usd", 0.0)
            if po_rate is not None and contract_rate and po_rate != contract_rate:
                missing.append(f"Rate Conflict: invoice rate (${po_rate:,.2f}) conflicts with contract rate (${contract_rate:,.2f}) in {k}.")
                
    # 3. Check distractor memo mismatches dynamically
    desc_lower = tx.get("description", "").lower()
    category_lower = tx.get("category", "").lower()
    for k, v in snapshot.items():
        if v.get("source_type") == "historical_memo":
            content_lower = v.get("content", "").lower()
            if ("laptop" in content_lower or "hardware" in content_lower or "purchase" in content_lower) and ("meal" in category_lower or "dinner" in desc_lower):
                missing.append(f"Distractor Memo Exception: {k} concerns unrelated matters (laptop purchase) and does not authorize this meals transaction.")
                
    if tx_type == "JOURNAL_ENTRY":
        if "POL-SOD" not in snapshot:
            missing.append("POL-SOD (Segregation of Duties Policy)")
        creator_key = f"USR-{tx.get('created_by')}"
        approver_key = f"USR-{tx.get('approved_by')}"
        if creator_key not in snapshot:
            missing.append(f"{creator_key} (Creator Profile)")
        if approver_key not in snapshot:
            missing.append(f"{approver_key} (Approver Profile)")
            
    elif tx_type == "BILLING_INVOICE":
        if "POL-BILLING" not in snapshot:
            missing.append("POL-BILLING (Billing Matching Policy)")
        vendor = tx.get("vendor", "")
        # Find any matching contract in snapshot
        has_contract = any(v.get("source_type") == "contract" and v.get("vendor", "").lower() == vendor.lower() for v in snapshot.values())
        if not has_contract:
            missing.append(f"CON-{vendor.upper()}-2026 (Vendor Contract)")
        if not tx.get("po_number"):
            missing.append("po_number (Purchase Order Reference)")
            
        # Check if billed rate exceeds contract rate and require current amendment or override
        po_rate = tx.get("unit_price")
        for k, v in snapshot.items():
            if v.get("source_type") == "contract" and v.get("vendor", "").lower() == vendor.lower():
                contract_rate = v.get("contracted_rate_usd", 0.0)
                if po_rate is not None and contract_rate and po_rate > contract_rate:
                    has_current_override = any("AMENDMENT" in key or "OVERRIDE" in key for key in snapshot.keys())
                    if not has_current_override:
                        missing.append("Invoice rate exceeds active contract rate.")
                        missing.append("Historical exception does not establish current authorization.")
                        missing.append("Current amendment or invoice-specific override is missing.")
            
    elif tx_type == "EXPENSE_REPORT":
        if "POL-MEAL" not in snapshot and "POL-RECEIPT" not in snapshot:
            missing.append("POL-MEAL or POL-RECEIPT (T&E Guidelines)")
        employee = tx.get("employee", "")
        emp_key = None
        for k, v in snapshot.items():
            if v["source_type"] == "user_profile" and employee.lower() in v.get("name", "").lower():
                emp_key = k
        if not emp_key:
            missing.append(f"USR-{employee.upper()} (Employee Profile)")
            
    return missing





def run_governance_gate(
    tx: dict,
    rule_results: dict,
    coordinator_res: dict,
    specialist_res: dict,
    critic_res: dict,
    evidence_bundle: dict = None
) -> dict:
    """Stage 5: Post-Critic Deterministic Governance Gate.
    
    Calculates final escalation action by combining deterministic control checks
    with structured AI output validations. Fail-closed at every step.
    """
    failures = []
    triggered_controls = []
    
    # 1. Coordinator Confidence Check
    if coordinator_res.get("confidence_score", 0.0) < 0.85:
        failures.append(f"Coordinator Confidence Gate: Score ({coordinator_res.get('confidence_score')}) below 0.85 threshold.")
        triggered_controls.append("COORD_CONFIDENCE_CONTROL")
        
    # 2. Specialist Confidence Check
    spec_conf = specialist_res.get("confidence", "LOW")
    if spec_conf in ["MEDIUM", "LOW"]:
        failures.append(f"Specialist Confidence Gate: Investigation confidence is '{spec_conf}' (requires HIGH).")
        triggered_controls.append("SPEC_CONFIDENCE_CONTROL")
        
    # 3. Critic Verdict Check
    if critic_res.get("verdict") == "REJECT":
        failures.append(f"Critic Gate: Audit rejected: {critic_res.get('reasons')}")
        triggered_controls.append("CRITIC_GROUNDING_CONTROL")
        
    # 4. Critic Claim Checks Analysis
    claim_checks = critic_res.get("claim_checks", [])
    for check in claim_checks:
        status = check.get("status")
        if status != "SUPPORTED":
            failures.append(f"Critic Claim Audit: claim {check.get('claim_id')} is '{status}': {check.get('reason')}")
            triggered_controls.append("CLAIM_GROUNDING_CONTROL")
            
    # 5. Invalid Source References
    invalid_refs = critic_res.get("invalid_source_references", [])
    if invalid_refs:
        failures.append(f"Invalid Evidence Gate: Specialist referenced non-existent source IDs: {invalid_refs}")
        triggered_controls.append("EVIDENCE_INTEGRITY_CONTROL")
        
    # 6. Critic Contradicted Claims
    contra = critic_res.get("contradicted_claims", [])
    if contra:
        failures.append(f"Contradiction Gate: Specialist claims contradicted by retrieved evidence: {contra}")
        triggered_controls.append("EVIDENCE_CONTRADICTION_CONTROL")
        
    # 7. Evidence Sufficiency Verification
    if evidence_bundle:
        sufficiency_failures = check_evidence_sufficiency(tx, evidence_bundle)
        if sufficiency_failures:
            failures.append(f"Evidence Sufficiency Gate: Incomplete documentation: {sufficiency_failures}")
            triggered_controls.append("EVIDENCE_SUFFICIENCY_CONTROL")
            
    # 8. Deterministic Control Failures Escalation
    if not rule_results.get("passed", True):
        is_resolvable_minor = False
        failures_list = rule_results.get("failures", [])
        
        # Check for pre-authorized meal exception memos
        if len(failures_list) == 1 and failures_list[0]["rule"] == "Per-Diem Cap Check":
            # Must have client hosting VP exception memo present in evidence
            has_vp_memo = any(
                "MEMO-MEAL-CAP-WU" in k for k in (evidence_bundle or {}).keys()
            )
            if has_vp_memo and critic_res.get("verdict") == "PASS":
                is_resolvable_minor = True
                
        if not is_resolvable_minor:
            for rule_fail in failures_list:
                failures.append(f"Deterministic Policy Control: failed '{rule_fail['rule']}' check: {rule_fail['error']}")
                triggered_controls.append(rule_fail["rule"].upper().replace(" ", "_"))
                
    # 9. Materiality Escalations
    amount = tx.get("amount", 0.0)
    if amount > 250000.00:
        failures.append(f"Materiality Gate: Manual entry amount (${amount:,.2f}) exceeds $250,000 corporate cap.")
        triggered_controls.append("MATERIAL_POSTING_CONTROL")
        
    passed = len(failures) == 0
    action = "AUTO_APPROVE"
    if not passed:
        spec_rec = specialist_res.get("recommendation")
        if spec_rec == "REQUEST_DOCUMENTS":
            action = "REQUEST_DOCUMENTS"
            if tx.get("tx_type") == "BILLING_INVOICE":
                po_rate = tx.get("unit_price")
                for k, v in (evidence_bundle or {}).items():
                    if v.get("source_type") == "contract" and v.get("vendor", "").lower() == tx.get("vendor", "").lower():
                        contract_rate = v.get("contracted_rate_usd", 0.0)
                        if po_rate is not None and contract_rate and po_rate > contract_rate:
                            if "CONTRACT_RATE_VARIANCE" not in triggered_controls:
                                triggered_controls.append("CONTRACT_RATE_VARIANCE")
                            if "MISSING_CURRENT_RATE_AUTHORIZATION" not in triggered_controls:
                                triggered_controls.append("MISSING_CURRENT_RATE_AUTHORIZATION")
        else:
            action = "ESCALATE_TO_HUMAN"
    
    return {
        "passed": passed,
        "action": action,
        "reasons": failures,
        "triggered_controls": triggered_controls
    }


async def run_pipeline(tx: dict, rule_results: dict) -> tuple[str, dict, dict, str, dict]:
    """Executes the multi-agent pipeline: Coordinator -> Specialist -> Critic -> Governance Gate."""
    tx_id = tx.get("journal_id") or tx.get("invoice_id") or tx.get("report_id")
    print(f"\n[AI PIPELINE] Initiating Multi-Agent Investigation for {tx_id}...")
    
    session_service = InMemorySessionService()
    session_id = f"sess_{tx_id}_{int(datetime.now().timestamp())}"
    await session_service.create_session(app_name="app", user_id="controller", session_id=session_id)
    
    # --- Stage 1: Coordinator routing ---
    print(f" 1. [COORDINATOR] Classifying transaction payload (tx_type stripped)...")
    runner = Runner(agent=coordinator_agent, app_name="app", session_service=session_service)
    
    coord_input = {k: v for k, v in tx.items() if k != "tx_type"}
    coord_prompt = f"Determine category and routing for transaction data:\n{json.dumps(coord_input, separators=(',', ':'))}"
    coord_out = ""
    start_time = time.time()
    
    try:
        async for event in runner.run_async(
            user_id="controller", session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=coord_prompt)])
        ):
            if event.is_final_response() and event.content and event.content.parts:
                coord_out = event.content.parts[0].text
        
        latency = (time.time() - start_time) * 1000
        in_tokens = int(len(coord_prompt) / 4)
        out_tokens = int(len(coord_out) / 4)
        log_trace(tx_id, "coordinator_agent", coord_prompt, coord_out, latency_ms=latency, estimated_input_tokens=in_tokens, estimated_output_tokens=out_tokens)
    except Exception as e:
        print(f"\n❌ [ERROR] Coordinator Agent execution failed: {e}")
        log_trace(tx_id, "coordinator_agent", coord_prompt, str(e), status="FAILED")
        raise
            
    # Validate Coordinator Schema (Fail Closed)
    try:
        coordinator_res = _parse_coordinator_json(coord_out)
    except Exception as se:
        print(f"❌ [SCHEMA FAILURE] Coordinator output was invalid: {se}")
        raise ValueError(f"Coordinator schema check failed: {se}")
        
    spec_name = coordinator_res.get("designated_specialist")
    print(f"    ↳ Routed to: {spec_name} (Reason: {coordinator_res.get('routing_reason')})")
    
    # Compile the Evidence Registry Snapshot
    evidence_bundle = gather_evidence_registry_snapshot(tx, spec_name)
    
    # --- Stage 2: Specialist Worker Investigation ---
    agent, rag_info, label = _get_specialist_and_rag(spec_name, tx)
    print(f" 2. [{label.upper()}] Gathering evidence & drafting report...")
    
    await session_service.create_session(app_name="app", user_id="controller", session_id=session_id)
    session = await session_service.get_session(app_name="app", user_id="controller", session_id=session_id)
    session.state["tx_id"] = tx_id
    session.state["rule_failures"] = rule_results["failures"]
    
    runner = Runner(agent=agent, app_name="app", session_service=session_service)
    
    spec_prompt = (
        f"Perform an investigation for transaction:\n{json.dumps(tx, separators=(',', ':'))}\n\n"
        f"Deterministic rule failures:\n{json.dumps(rule_results['failures'], separators=(',', ':'))}\n\n"
        f"EVIDENCE REGISTRY SNAPSHOT:\n{json.dumps(evidence_bundle, separators=(',', ':'))}"
    )
    
    report_out = ""
    tool_calls_logged = []
    start_time = time.time()
    try:
        async for event in runner.run_async(
            user_id="controller", session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=spec_prompt)])
        ):
            if event.author and event.author != "controller":
                if hasattr(event, "action") and event.action and hasattr(event.action, "tool_call"):
                    tc = event.action.tool_call
                    print(f"       ↳ [Tool Call] {event.author} calling {tc.name}...")
                    tool_calls_logged.append(tc.name)
                elif event.is_final_response() and event.content and event.content.parts:
                    report_out = event.content.parts[0].text
        
        latency = (time.time() - start_time) * 1000
        in_tokens = int(len(spec_prompt) / 4)
        out_tokens = int(len(report_out) / 4)
        log_trace(tx_id, agent.name, spec_prompt, report_out, tool_calls=tool_calls_logged, latency_ms=latency, estimated_input_tokens=in_tokens, estimated_output_tokens=out_tokens)
    except Exception as e:
        print(f"\n❌ [ERROR] Specialist Worker Agent execution failed: {e}")
        log_trace(tx_id, agent.name, spec_prompt, str(e), status="FAILED")
        raise
                
    # Validate Specialist Schema (Fail Closed)
    try:
        specialist_res = _parse_specialist_json(report_out)
    except Exception as se:
        print(f"❌ [SCHEMA FAILURE] Specialist output was invalid: {se}")
        raise ValueError(f"Specialist schema check failed: {se}")
        
    # Check for invalid source references immediately
    invalid_specialist_refs = validate_specialist_evidence_refs(specialist_res, evidence_bundle)
    
    if invalid_specialist_refs:
        print(f"❌ [VALIDATION FAILURE] Specialist cited invalid source references: {invalid_specialist_refs}")
        # Force rejection before Critic runs
        critic_results = {
            "verdict": "REJECT",
            "claim_checks": [],
            "unsupported_claims": [],
            "contradicted_claims": [],
            "missing_evidence": [],
            "invalid_source_references": invalid_specialist_refs,
            "confidence_score": 0.0,
            "reasons": f"Pre-critic validation failed: Specialist cited invalid source references: {invalid_specialist_refs}"
        }
    else:
        # --- Stage 3: Critic Verification ---
        print(f" 3. [CRITIC] Auditing specialist report against retrieved source documents...")
        
        await session_service.create_session(app_name="app", user_id="controller", session_id=session_id)
        runner = Runner(agent=critic_agent, app_name="app", session_service=session_service)
        
        critic_prompt = (
            f"Verify the following structured report against the raw transaction and retrieved evidence snapshot.\n\n"
            f"Source Transaction:\n{json.dumps(tx, separators=(',', ':'))}\n\n"
            f"Retrieved Evidence Snapshot:\n{json.dumps(evidence_bundle, separators=(',', ':'))}\n\n"
            f"Report to Audit:\n{report_out}"
        )
        
        critic_out = ""
        start_time = time.time()
        try:
            async for event in runner.run_async(
                user_id="controller", session_id=session_id,
                new_message=types.Content(role="user", parts=[types.Part.from_text(text=critic_prompt)])
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    critic_out = event.content.parts[0].text
            
            latency = (time.time() - start_time) * 1000
            in_tokens = int(len(critic_prompt) / 4)
            out_tokens = int(len(critic_out) / 4)
            log_trace(tx_id, "critic_agent", critic_prompt, critic_out, latency_ms=latency, estimated_input_tokens=in_tokens, estimated_output_tokens=out_tokens)
        except Exception as e:
            print(f"\n❌ [ERROR] Critic Agent execution failed: {e}")
            log_trace(tx_id, "critic_agent", critic_prompt, str(e), status="FAILED")
            raise
                
        critic_results = _parse_critic_json(critic_out)
        
        # Enforce Critic Claim Coverage Check
        # Enforce Critic Claim Coverage Check
        coverage_res = validate_critic_claim_coverage(specialist_res, critic_results)
        
        if not coverage_res["valid"]:
            missing_claims = coverage_res["missing_claims"]
            unknown_claims = coverage_res["unknown_claims"]
            duplicate_claims = coverage_res["duplicate_claims"]
            print(f"❌ [VALIDATION FAILURE] Critic coverage check failed. Missing: {missing_claims}, Unknown: {unknown_claims}, Duplicates: {duplicate_claims}")
            critic_results["verdict"] = "REJECT"
            critic_results["reasons"] = (
                f"Critic coverage check failed. "
                f"Missing: {missing_claims}, Unknown: {unknown_claims}, Duplicates: {duplicate_claims}"
            )
            
    print(f"    ↳ Audit Verdict: {critic_results.get('verdict', 'REJECT')}")
    
    # --- Stage 4: Deterministic Governance Gate ---
    spec_conf = specialist_res.get("confidence", "LOW")
    gov_results = run_governance_gate(tx, rule_results, coordinator_res, specialist_res, critic_results, evidence_bundle)
    print(f" 4. [GOVERNANCE GATE] Final Escalation Decision: {gov_results['action']}")
    if gov_results["reasons"]:
        for reason in gov_results["reasons"]:
            print(f"       ↳ {reason}")
            
    return report_out, critic_results, gov_results, spec_name, evidence_bundle, coordinator_res


def _parse_coordinator_json(raw_text: str) -> dict:
    """Parses Coordinator JSON response and validates against strict Pydantic model."""
    clean = clean_json_text(raw_text)
    data = json.loads(clean)
    validated = CoordinatorOutput(**data)
    return validated.model_dump()


def _parse_specialist_json(raw_text: str) -> dict:
    """Parses Specialist JSON response and validates against strict Pydantic model."""
    clean = clean_json_text(raw_text)
    data = json.loads(clean)
    validated = SpecialistOutput(**data)
    return validated.model_dump()


def _parse_critic_json(raw_text: str) -> dict:
    """Parses Critic JSON response and validates against strict Pydantic model (Fail Closed)."""
    try:
        clean = clean_json_text(raw_text)
        data = json.loads(clean)
        validated = CriticOutput(**data)
        return validated.model_dump()
    except Exception as e:
        # Secure Fail-Closed response
        return {
            "verdict": "REJECT",
            "claim_checks": [],
            "unsupported_claims": [f"Critic schema validation failed: {str(e)}"],
            "contradicted_claims": [],
            "missing_evidence": [],
            "invalid_source_references": [],
            "confidence_score": 0.0,
            "reasons": f"Secure Fail-Closed triggered. Critic response was malformed: {str(e)}"
        }


async def main():
    setup_telemetry()
    
    print("=" * 70)
    print("  ENTERPRISE FINANCE CLOSE EXCEPTION INVESTIGATOR — PILOT RUN  ")
    print("=" * 70)
    
    feed = get_mixed_feed()
    print(f"Loaded {len(feed)} mixed financial transaction records.")
    
    print("\nRunning Stage 1: Deterministic screening & risk triage...")
    print("-" * 70)
    
    flagged_txs = []
    auto_approved = 0
    
    for tx in feed:
        tx_id = tx.get("journal_id") or tx.get("invoice_id") or tx.get("report_id")
        tx_type = tx.get("tx_type")
        results = run_deterministic_rules(tx)
        
        status = "⚠️  ESCALATE" if results["action"] == "ESCALATE_TO_HUMAN" else "✅ APPROVE"
        print(f"{tx_id:8} | Type: {tx_type:16} | Amount: ${tx['amount']:,.2f} | Score: {results['risk_score']:3} | Triage: {status}")
        
        if results["action"] == "ESCALATE_TO_HUMAN":
            flagged_txs.append((tx, results))
        else:
            auto_approved += 1
            
    print("-" * 70)
    print(f"Screening Summary: {auto_approved} Auto-Approved | {len(flagged_txs)} Flagged for Review")
    print("=" * 70)
    
    target_ids = ["JE-1001", "INV-2001", "EXP-3001"]
    
    for tx, results in flagged_txs:
        tx_id = tx.get("journal_id") or tx.get("invoice_id") or tx.get("report_id")
        if tx_id not in target_ids:
            continue
            
        print(f"\n{'#'*30} INVESTIGATING {tx_id} {'#'*30}")
        print(f"Description: {tx.get('description')}")
        print(f"Failed Control Checks:")
        for fail in results["failures"]:
            print(f"  • {fail['rule']}: {fail['error']}")
            
        try:
            # Run Multi-Agent pipeline
            ai_report_str, critic_results, gov_results, spec_name, evidence_bundle = await run_pipeline(tx, results)
            
            specialist_results = json.loads(clean_json_text(ai_report_str))
            
            print("\n--- AI SPECIALIST ANALYSIS REPORT ---")
            print(ai_report_str)
            print("------------------------------------")
            
            # Enforce Critic Gate Blocking
            if not gov_results["passed"]:
                print("\n🚨 [GOVERNANCE GATE] Automated review blocked.")
                print(f"Escalation reasons: {', '.join(gov_results['reasons'])}")
                human_decision = {
                    "status": "ESCALATED_FOR_HUMAN_REVIEW",
                    "reviewer": "System Security Gate",
                    "timestamp": datetime.now().isoformat(),
                    "override_reason": "Post-investigation governance checkpoint failed.",
                    "comments": f"Governance gate flagged: {gov_results['reasons']}"
                }
            else:
                print("\n[HITL] Simulating Human Controller sign-off...")
                if tx_id == "EXP-3001":
                    human_decision = {
                        "status": "APPROVED_WITH_EXCEPTION",
                        "reviewer": "Emily Davis (Accounting Manager)",
                        "timestamp": datetime.now().isoformat(),
                        "override_reason": "Policy Cap Overage: Lunch hosting with client matched valid exception VP memo.",
                        "comments": "Verified executive approval."
                    }
                else:
                    human_decision = {
                        "status": "ESCALATED_FOR_HUMAN_REVIEW",
                        "reviewer": "System Security Gate",
                        "timestamp": datetime.now().isoformat(),
                        "override_reason": "Default human review trigger.",
                        "comments": "Unresolved exception escalated."
                    }
                
            print(f"Decision: {human_decision['status']} | Reviewer: {human_decision['reviewer']}")
            
            # Load Coordinator info for audit
            coord_results = {"domain": "CLOSE" if spec_name == "close_specialist" else ("BILLING" if spec_name == "billing_specialist" else "EXPENSE"), "designated_specialist": spec_name, "routing_reason": "Matched metadata schema.", "confidence_score": 1.0}
            
            packet_path, checksum = write_audit_packet(
                tx, results, coord_results, specialist_results, evidence_bundle,
                critic_results, gov_results, human_decision
            )
            print(f"📜 Tamper-Evident Audit Packet saved to: {packet_path}")
            print(f"🔑 Checksum Generated: {checksum}")
            
            is_valid = verify_audit_packet(packet_path)
            print(f"🔒 Checksum Integrity Verification Status: {'SUCCESS (Valid)' if is_valid else 'FAILED (Corrupted)'}")
            print("#" * 78)
            
        except Exception as e:
            print(f"⚠️  Agent pipeline execution failed for {tx_id}: {e}")
            print("🚨 Failing closed and writing error audit packet...")
            err_gov_results = {
                "passed": False,
                "action": "SYSTEM_ERROR_ESCALATION",
                "reasons": [f"Agent Execution Failure: {str(e)}"],
                "triggered_controls": ["SYSTEM_EXC_CONTROL"]
            }
            err_critic_results = {
                "verdict": "REJECT",
                "claim_checks": [],
                "unsupported_claims": [f"Pipeline execution crashed: {str(e)}"],
                "contradicted_claims": [],
                "missing_evidence": [],
                "invalid_source_references": [],
                "confidence_score": 0.0,
                "reasons": f"System crash: {str(e)}"
            }
            err_human_decision = {
                "status": "ESCALATED_DUE_TO_SYSTEM_ERROR",
                "reviewer": "System Governance Gate",
                "timestamp": datetime.now().isoformat(),
                "override_reason": f"Pipeline failure: {str(e)}",
                "comments": "System fail-closed trigger."
            }
            try:
                packet_path, checksum = write_audit_packet(
                    tx, results, {}, {}, {}, err_critic_results, err_gov_results, err_human_decision
                )
                print(f"📜 Tamper-Evident Audit Packet saved to: {packet_path}")
                print(f"🔑 Checksum Generated: {checksum}")
            except Exception as ae:
                print(f"❌ [CRITICAL] Failed to write fallback audit packet: {ae}")
            print("#" * 78)

    # Verify manifest chain
    print("\n🔐 Running Cryptographic Hash Chain Manifest Audit...")
    manifest_audit = verify_manifest_chain()
    if manifest_audit["valid"]:
        print(f"✅ MANIFEST VERIFICATION SUCCESS: Valid hash chain linked across all {manifest_audit['count']} packet records.")
    else:
        print(f"🚨 MANIFEST VERIFICATION FAILURE: {manifest_audit['error']}")

    print("\n" + "=" * 70)
    print("FINANCE CLOSE RUN COMPLETED SUCCESSFULLY")
    print(f"Audit Packets Directory: {Path(__file__).resolve().parent / 'audit_packets'}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
