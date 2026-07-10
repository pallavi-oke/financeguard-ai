# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import os
import json
import pytest
from pathlib import Path
from app.audit import (
    compute_canonical_hash,
    write_audit_packet,
    verify_audit_packet,
    verify_manifest_chain,
    MANIFEST_PATH,
    AUDIT_LOG_DIR
)

def test_canonical_hash_test_vector():
    # Enforce exact match on user-provided test vector
    payload = {
        "a": 1,
        "b": {
            "c": True,
            "d": ["x", "y"]
        }
    }
    
    canonical_hash = compute_canonical_hash(payload)
    
    # Expected output derived from: {"a":1,"b":{"c":true,"d":["x","y"]}}
    expected_hash = "9532718f9669695b8c37c64bcbdd6f898d62b86d5583c036d4522e0f899ba535"
    assert canonical_hash == expected_hash


def test_manifest_linkage_and_tampering(tmp_path, monkeypatch):
    # Relocate manifest path and audit log dir to a temp test directory
    test_manifest = tmp_path / "test_manifest.jsonl"
    test_audit_dir = tmp_path / "test_audit_packets"
    
    # Use monkeypatch to isolate from project files during test run
    monkeypatch.setattr("app.audit.MANIFEST_PATH", test_manifest)
    monkeypatch.setattr("app.audit.AUDIT_LOG_DIR", test_audit_dir)
    
    tx = {"journal_id": "T1", "amount": 100.0, "tx_type": "JOURNAL_ENTRY"}
    rules = {"passed": False, "failures": [{"rule": "Check", "error": "err"}], "risk_score": 50}
    
    # Write first packet
    path1, hash1 = write_audit_packet(tx, rules, {}, {}, {}, {}, {})
    assert Path(path1).exists()
    
    # Verify chain
    audit_res = verify_manifest_chain()
    assert audit_res["valid"] is True
    assert audit_res["count"] == 1
    
    # Write second packet
    tx2 = {"journal_id": "T2", "amount": 200.0, "tx_type": "JOURNAL_ENTRY"}
    path2, hash2 = write_audit_packet(tx2, rules, {}, {}, {}, {}, {})
    assert Path(path2).exists()
    
    audit_res = verify_manifest_chain()
    assert audit_res["valid"] is True
    assert audit_res["count"] == 2
    
    # Simulate duplicate transaction ID tampering
    # Try writing a duplicate transaction ID
    with pytest.raises(ValueError, match="Duplicate transaction entry found"):
        write_audit_packet(tx, rules, {}, {}, {}, {}, {})

    # 1. Edit transaction_payload.amount in packet 1 and verify chain failure
    with open(path1, "r") as f:
        p1 = json.load(f)
    p1["transaction_payload"]["amount"] = 999.0
    with open(path1, "w") as f:
        json.dump(p1, f)
    
    audit_res = verify_manifest_chain()
    assert audit_res["valid"] is False
    assert "Payload checksum verification failed" in audit_res["error"]

    # Restore packet 1 amount
    p1["transaction_payload"]["amount"] = 100.0
    with open(path1, "w") as f:
        json.dump(p1, f)
    assert verify_manifest_chain()["valid"] is True

    # 2. Edit specialist_output in packet 2 and verify chain failure
    with open(path2, "r") as f:
        p2 = json.load(f)
    p2["specialist_output"] = {"edited": True}
    with open(path2, "w") as f:
        json.dump(p2, f)
    
    audit_res = verify_manifest_chain()
    assert audit_res["valid"] is False
    assert "Payload checksum verification failed" in audit_res["error"]

    # Restore packet 2 specialist output
    p2["specialist_output"] = {}
    with open(path2, "w") as f:
        json.dump(p2, f)
    assert verify_manifest_chain()["valid"] is True

    # 3. Edit critic_claims_check in packet 2 and verify chain failure
    with open(path2, "r") as f:
        p2 = json.load(f)
    p2["critic_claims_check"] = [{"claim_id": "TAMPERED"}]
    with open(path2, "w") as f:
        json.dump(p2, f)
    
    audit_res = verify_manifest_chain()
    assert audit_res["valid"] is False
    assert "Payload checksum verification failed" in audit_res["error"]

    # Restore packet 2 critic check
    p2["critic_claims_check"] = {}
    with open(path2, "w") as f:
        json.dump(p2, f)
    assert verify_manifest_chain()["valid"] is True

    # 4. Edit a manifest hash in the manifest file
    with open(test_manifest, "r") as f:
        manifest_lines = [json.loads(line.strip()) for line in f if line.strip()]
    manifest_lines[1]["previous_hash"] = "a" * 64
    with open(test_manifest, "w") as f:
        for item in manifest_lines:
            f.write(json.dumps(item) + "\n")
            
    audit_res = verify_manifest_chain()
    assert audit_res["valid"] is False
    assert "Chain link broken" in audit_res["error"]

    # Restore manifest previous hash
    manifest_lines[1]["previous_hash"] = hash1
    with open(test_manifest, "w") as f:
        for item in manifest_lines:
            f.write(json.dumps(item) + "\n")
    assert verify_manifest_chain()["valid"] is True

    # 5. Delete a packet file
    os.remove(path1)
    audit_res = verify_manifest_chain()
    assert audit_res["valid"] is False
    assert "Referenced audit packet file missing" in audit_res["error"]

    # Recreate the file to restore clean state
    with open(path1, "w") as f:
        json.dump(p1, f)
    assert verify_manifest_chain()["valid"] is True

    # 6. Reorder manifest entries
    with open(test_manifest, "r") as f:
        manifest_lines = [json.loads(line.strip()) for line in f if line.strip()]
    # Swap index 0 and index 1
    manifest_lines[0], manifest_lines[1] = manifest_lines[1], manifest_lines[0]
    with open(test_manifest, "w") as f:
        for item in manifest_lines:
            f.write(json.dumps(item) + "\n")
            
    audit_res = verify_manifest_chain()
    assert audit_res["valid"] is False

