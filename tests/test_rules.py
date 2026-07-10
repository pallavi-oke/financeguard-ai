# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

from app.tools import check_sod, check_materiality, check_cutoff, check_sensitive_accounts, check_invoice_rate, check_expense_limit, check_expense_receipt

def test_sod_rule():
    # Violation: created and approved by same user
    je_violation = {"created_by": "ANALYST_01", "approved_by": "ANALYST_01"}
    res = check_sod(je_violation)
    assert res["passed"] is False
    assert "SoD Violation" in res["detail"]
    
    # Safe: different users
    je_safe = {"created_by": "ANALYST_01", "approved_by": "MANAGER_01"}
    assert check_sod(je_safe)["passed"] is True


def test_materiality_rule():
    # Violation: amount > 250,000 and authorization_ref is empty
    je_violation = {"amount": 320000.0, "authorization_ref": None}
    res = check_materiality(je_violation)
    assert res["passed"] is False
    
    # Safe: amount < 250,000
    je_safe_low = {"amount": 180000.0, "authorization_ref": None}
    assert check_materiality(je_safe_low)["passed"] is True
    
    # Safe: amount > 250,000 but authorization_ref is present
    je_safe_auth = {"amount": 320000.0, "authorization_ref": "REF-9920"}
    assert check_materiality(je_safe_auth)["passed"] is True


def test_cutoff_rule():
    # Late posting to June period on July 3rd
    je_violation = {"period": "06-2026", "posting_date": "2026-07-03T10:00:00"}
    res = check_cutoff(je_violation)
    assert res["passed"] is False
    
    # On-time posting to June period on July 1st
    je_safe = {"period": "06-2026", "posting_date": "2026-07-01T10:00:00"}
    assert check_cutoff(je_safe)["passed"] is True


def test_sensitive_accounts_rule():
    # Violation: manual postings to clearing or retained earnings codes
    je_violation = {"account": "300090"}
    res = check_sensitive_accounts(je_violation)
    assert res["passed"] is False
    
    # Safe
    je_safe = {"account": "610200"}
    assert check_sensitive_accounts(je_safe)["passed"] is True
