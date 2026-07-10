# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import os
import json
import hashlib
from pathlib import Path
from datetime import datetime

# Set up project-relative pathing
PROJECT_DIR = Path(__file__).resolve().parent.parent
AUDIT_LOG_DIR = PROJECT_DIR / "audit_packets"
MANIFEST_PATH = PROJECT_DIR / "audit_manifest.jsonl"

MODEL_ID = "gemini-1.5-flash"


def compute_canonical_hash(payload: dict) -> str:
    """Computes a deterministic SHA-256 hash of a dict using canonical separators and sorted keys."""
    canonical_str = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str
    )
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


def write_audit_packet(
    tx: dict,
    rule_results: dict,
    coordinator_decision: dict,
    specialist_output: dict,
    evidence_registry: dict,
    critic_output: dict,
    governance_decision: dict,
    human_decision: dict = None,
    trace_id: str = "trace-local-01"
) -> tuple[str, str]:
    tx_id = tx.get("journal_id") or tx.get("invoice_id") or tx.get("report_id") or "UNKNOWN"
    tx_type = tx.get("tx_type", "UNKNOWN")
    
    os.makedirs(AUDIT_LOG_DIR, exist_ok=True)
    
    # Enforce manifest verification before writing new entries
    if MANIFEST_PATH.exists():
        verification = verify_manifest_chain()
        if not verification["valid"]:
            raise ValueError(
                f"Audit manifest validation failed: {verification['error']}. "
                "Potential tampering detected. System write blocked to preserve audit integrity."
            )
            
        # Enforce duplicate transaction ID block
        try:
            with open(MANIFEST_PATH, "r") as f:
                for line in f:
                    if line.strip():
                        entry = json.loads(line.strip())
                        if entry.get("transaction_id") == tx_id:
                            raise ValueError(
                                f"Duplicate transaction entry found: transaction {tx_id} already has a logged audit packet."
                            )
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Could not read prior manifest entry: {e}. Writing blocked.")
            
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    decision = human_decision or {
        "status": "PENDING_REVIEW",
        "reviewer": None,
        "timestamp": None,
        "override_reason": None,
        "comments": "Queued for human controller sign-off."
    }
    
    # Payload to be hashed
    payload_to_hash = {
        "transaction_payload": tx,
        "deterministic_control_checks": {
            "passed": rule_results.get("passed", False),
            "risk_score": rule_results.get("risk_score", 0),
            "failures": rule_results.get("failures", [])
        },
        "coordinator_decision": coordinator_decision,
        "specialist_output": specialist_output,
        "evidence_registry": evidence_registry,
        "critic_claims_check": critic_output,
        "deterministic_governance_gate": governance_decision,
        "human_in_the_loop_disposition": decision
    }
    
    # Calculate checksum over the entire payload
    checksum = compute_canonical_hash(payload_to_hash)
    
    # Construct complete packet
    packet = {
        "metadata": {
            "system_version": "finance-control-center-v1.1",
            "schema_version": "audit-packet-v2",
            "model_id": MODEL_ID,
            "prompt_versions": {
                "coordinator": "coordinator-v3",
                "specialist": "specialist-v4",
                "critic": "critic-v3"
            },
            "trace_id": trace_id,
            "created_at": datetime.now().isoformat(),
            "transaction_id": tx_id,
            "transaction_type": tx_type,
            "tamper_evident_checksum": checksum
        },
        **payload_to_hash
    }
    
    file_path = AUDIT_LOG_DIR / f"audit_packet_{tx_id}_{timestamp}.json"
    
    with open(file_path, "w") as f:
        json.dump(packet, f, indent=2)
        
    # Fetch last hash for chaining
    previous_hash = "0" * 64
    if MANIFEST_PATH.exists():
        try:
            with open(MANIFEST_PATH, "r") as f:
                lines = f.readlines()
                if lines:
                    last_entry = json.loads(lines[-1].strip())
                    previous_hash = last_entry.get("packet_hash", "0" * 64)
        except Exception as e:
            raise ValueError(f"Could not read prior manifest entry: {e}. Writing blocked.")
            
    manifest_entry = {
        "timestamp": datetime.now().isoformat(),
        "transaction_id": tx_id,
        "packet_hash": checksum,
        "previous_hash": previous_hash,
        "file_name": file_path.name
    }
    
    with open(MANIFEST_PATH, "a") as f:
        f.write(json.dumps(manifest_entry) + "\n")
        
    return str(file_path), checksum


def verify_audit_packet(packet_path: str) -> bool:
    """Verifies the integrity of a written audit packet by recalculating its SHA-256 hash."""
    try:
        with open(packet_path, "r") as f:
            packet = json.load(f)
            
        recorded_checksum = packet.get("metadata", {}).get("tamper_evident_checksum")
        if not recorded_checksum:
            return False
            
        payload_to_hash = {
            "transaction_payload": packet.get("transaction_payload"),
            "deterministic_control_checks": packet.get("deterministic_control_checks"),
            "coordinator_decision": packet.get("coordinator_decision"),
            "specialist_output": packet.get("specialist_output"),
            "evidence_registry": packet.get("evidence_registry"),
            "critic_claims_check": packet.get("critic_claims_check"),
            "deterministic_governance_gate": packet.get("deterministic_governance_gate"),
            "human_in_the_loop_disposition": packet.get("human_in_the_loop_disposition")
        }
        
        calculated_checksum = compute_canonical_hash(payload_to_hash)
        return recorded_checksum == calculated_checksum
    except Exception:
        return False


def verify_manifest_chain() -> dict:
    """Replays and verifies the entire manifest hash chain.
    
    Checks:
    1. Previous-hash linkage correctness.
    2. Existential check of all packet files.
    3. Payload checksum matching.
    4. Duplicate transaction entries or reordered lines.
    """
    if not MANIFEST_PATH.exists():
        return {"valid": True, "error": None, "count": 0}
        
    try:
        with open(MANIFEST_PATH, "r") as f:
            lines = [json.loads(line.strip()) for line in f if line.strip()]
            
        expected_prev = "0" * 64
        seen_txs = set()
        
        for idx, entry in enumerate(lines):
            tx_id = entry.get("transaction_id")
            packet_hash = entry.get("packet_hash")
            prev_hash = entry.get("previous_hash")
            file_name = entry.get("file_name")
            
            # Check for duplicate transactions in sequence
            if tx_id in seen_txs:
                return {
                    "valid": False,
                    "error": f"Duplicate transaction entry found: {tx_id} at index {idx}"
                }
            seen_txs.add(tx_id)
            
            # Check linkage
            if prev_hash != expected_prev:
                return {
                    "valid": False,
                    "error": f"Chain link broken at index {idx} ({tx_id}). Expected prev: {expected_prev}, got: {prev_hash}"
                }
                
            # Verify file exists
            file_path = AUDIT_LOG_DIR / file_name
            if not file_path.exists():
                return {
                    "valid": False,
                    "error": f"Referenced audit packet file missing: {file_name}"
                }
                
            # Verify file content hash & recalculate packet body hash
            if not verify_audit_packet(str(file_path)):
                return {
                    "valid": False,
                    "error": f"Payload checksum verification failed for {file_name}. Re-computed hash does not match recorded checksum."
                }
                
            expected_prev = packet_hash
            
        return {"valid": True, "error": None, "count": len(lines)}
    except Exception as e:
        return {"valid": False, "error": f"Manifest read exception: {str(e)}"}
