# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import json
from datetime import datetime

# --- 1. Source Evidence Registry Constants ---
EVIDENCE_REGISTRY = {
    # Policies
    "POL-SOD": {
        "source_type": "policy",
        "title": "JE-101 Segregation of Duties",
        "content": "Every manual journal entry must be created and approved by separate, designated users. Automated scripts are exempt."
    },
    "POL-MATERIALITY": {
        "source_type": "policy",
        "title": "Sec 4.2 Materiality Limits",
        "content": "Manual postings > $250,000 require a documented business authorization reference (authorization_ref)."
    },
    "POL-CUTOFF": {
        "source_type": "policy",
        "title": "Month-End Period Cutoff Protocol",
        "content": "Ledger closes at 5:00 PM on second business day of subsequent month. Postings to closed periods require Controller approval."
    },
    "POL-SENSITIVE": {
        "source_type": "policy",
        "title": "JE-204 sensitive account restrictions",
        "content": "Direct manual postings to Retained Earnings (300000-300099) are highly restricted and require CFO co-signature."
    },
    "POL-BILLING": {
        "source_type": "policy",
        "title": "Procurement BI-201 Billing matching",
        "content": "Billed invoice unit rates must match contract rate agreements exactly. Any variance > 0% must trigger vendor dispute."
    },
    "POL-MEAL": {
        "source_type": "policy",
        "title": "T&E Meal Cap Policy TE-302",
        "content": "Standard business meal caps are $150.00 per day. Excess requires pre-approved VP authorization exception memo."
    },
    "POL-RECEIPT": {
        "source_type": "policy",
        "title": "T&E Receipt Verification TE-104",
        "content": "Expenses > $25.00 require an attached readable receipt. Non-business electronics are barred from reimbursement."
    },
    # Contracts
    "CON-CONSULTINGCORP-2026": {
        "source_type": "contract",
        "vendor": "ConsultingCorp",
        "effective_date": "2026-01-01",
        "expiration_date": "2026-12-31",
        "contracted_rate_usd": 150.00,
        "content": "Master Service Agreement: consulting hours billed at flat rate of $150.00/hour."
    },
    "CON-LOGISTICSINC-2026": {
        "source_type": "contract",
        "vendor": "LogisticsInc",
        "effective_date": "2026-01-01",
        "expiration_date": "2026-12-31",
        "contracted_rate_usd": 450.00,
        "content": "Standard Freight logistics shipment billed at flat rate of $450.00/shipment."
    },
    "CON-OFFICESUPPLIESCO-2026": {
        "source_type": "contract",
        "vendor": "OfficeSuppliesCo",
        "effective_date": "2026-01-01",
        "expiration_date": "2026-12-31",
        "contracted_rate_usd": 12.00,
        "content": "Office catalog supplies pricing contract."
    },
    # Expired Contract (Adversarial Case)
    "CON-CONSULTINGCORP-EXPIRED": {
        "source_type": "contract",
        "vendor": "ConsultingCorp",
        "effective_date": "2024-01-01",
        "expiration_date": "2024-12-31", # Expired!
        "contracted_rate_usd": 130.00,
        "content": "EXPIRED Master Service Agreement."
    },
    # User Profiles
    "USR-ANALYST_01": {
        "source_type": "user_profile",
        "user_id": "ANALYST_01",
        "name": "Alice Chen",
        "role": "Accruals Analyst",
        "level": "L2"
    },
    "USR-ANALYST_02": {
        "source_type": "user_profile",
        "user_id": "ANALYST_02",
        "name": "Bob Miller",
        "role": "Junior Services Accountant",
        "level": "L1"
    },
    "USR-ANALYST_03": {
        "source_type": "user_profile",
        "user_id": "ANALYST_03",
        "name": "Carol Smith",
        "role": "Senior General Ledger Accountant",
        "level": "L3"
    },
    "USR-ANALYST_04": {
        "source_type": "user_profile",
        "user_id": "ANALYST_04",
        "name": "David Wu",
        "role": "FP&A Associate",
        "level": "L2"
    },
    "USR-MANAGER_01": {
        "source_type": "user_profile",
        "user_id": "MANAGER_01",
        "name": "Emily Davis",
        "role": "Accounting Manager",
        "level": "L4"
    },
    "USR-MANAGER_02": {
        "source_type": "user_profile",
        "user_id": "MANAGER_02",
        "name": "Frank Jones",
        "role": "Corporate Controller",
        "level": "L5"
    },
    "USR-SYS_AUTO": {
        "source_type": "user_profile",
        "user_id": "SYS_AUTO",
        "name": "System Batch Scheduler",
        "role": "Automated System Script",
        "level": "N/A"
    },
    # Historical Memos
    "MEMO-WAR-229": {
        "source_type": "historical_memo",
        "content": "Approved exception warranty journal entries: physical spares logs verified by VP Ops Finance on 2026-06-30."
    },
    "MEMO-MEAL-CAP-WU": {
        "source_type": "historical_memo",
        "content": "Pre-authorization approved for David Wu for supplier dinner overage up to $250.00."
    },
    # Distractor Memo (Adversarial Case)
    "MEMO-DISTRACTOR-DELL": {
        "source_type": "historical_memo",
        "content": "Memo concerns unrelated transaction: Laptop purchase authorization exception for Emily Davis from 2025."
    },
    "MEMO-MIGRATION-RATE-2025": {
        "source_type": "historical_memo",
        "vendor": "ConsultingCorp",
        "approved_rate": 180,
        "scope": "Emergency weekend migration support",
        "effective_from": "2025-10-01",
        "effective_to": "2025-12-31",
        "applies_to_current_invoice": False,
        "content": "A temporary rate of $180/hour was approved for emergency weekend migration support during Q4 2025."
    }
}

# --- 2. Synthetic Ledgers with Seeded Anomaly Profiles ---
MOCK_LEDGER = [
    {
        "tx_type": "JOURNAL_ENTRY",
        "journal_id": "JE-1000",
        "account": "180200",
        "amount": 12500.00,
        "posting_date": "2026-07-01T08:00:00",
        "period": "07-2026",
        "created_by": "SYS_AUTO",
        "approved_by": "SYS_AUTO",
        "description": "Amortization of software license",
        "source_id": "TX-JE-1000"
    },
    {
        "tx_type": "JOURNAL_ENTRY",
        "journal_id": "JE-1001",
        "account": "610200",
        "amount": 125000.00,
        "posting_date": "2026-07-02T10:15:00",
        "period": "07-2026",
        "created_by": "ANALYST_01",
        "approved_by": "ANALYST_01",  # SoD violation
        "description": "Manual adjustment for warranty",
        "source_id": "TX-JE-1001"
    },
    {
        "tx_type": "JOURNAL_ENTRY",
        "journal_id": "JE-1002",
        "account": "610300",
        "amount": 320000.00,
        "posting_date": "2026-07-02T14:30:00",
        "period": "07-2026",
        "created_by": "ANALYST_02",
        "approved_by": "MANAGER_01",
        "description": "Consulting project milestones",
        "source_id": "TX-JE-1002"  # Materiality failure (no auth_ref)
    },
    {
        "tx_type": "JOURNAL_ENTRY",
        "journal_id": "JE-1003",
        "account": "620500",
        "amount": 45000.00,
        "posting_date": "2026-07-03T17:45:00",
        "period": "06-2026",  # Cutoff violation
        "created_by": "ANALYST_03",
        "approved_by": "MANAGER_02",
        "description": "Late expense report accruals reconciliation",
        "source_id": "TX-JE-1003"
    },
    {
        "tx_type": "JOURNAL_ENTRY",
        "journal_id": "JE-1004",
        "account": "300090",  # Sensitive Account posting
        "amount": 180000.00,
        "posting_date": "2026-07-05T23:30:00",
        "period": "07-2026",
        "created_by": "ANALYST_04",
        "approved_by": "MANAGER_01",
        "description": "Sensitive retained earnings adjustment",
        "source_id": "TX-JE-1004"
    }
]

MOCK_INVOICES = [
    {
        "tx_type": "BILLING_INVOICE",
        "invoice_id": "INV-2000",
        "vendor": "LogisticsInc",
        "amount": 45000.00,
        "po_number": "PO-9003",
        "unit_price": 450.00,
        "posting_date": "2026-07-02T11:00:00",
        "description": "Freight charges",
        "source_id": "TX-INV-2000"
    },
    {
        "tx_type": "BILLING_INVOICE",
        "invoice_id": "INV-2001",
        "vendor": "ConsultingCorp",
        "amount": 18000.00,
        "po_number": "PO-9002",
        "unit_price": 180.00,  # Billed $180, contract rate is $150
        "posting_date": "2026-07-02T13:45:00",
        "description": "Q2 migration support",
        "source_id": "TX-INV-2001"
    },
    {
        "tx_type": "BILLING_INVOICE",
        "invoice_id": "INV-2002",
        "vendor": "OfficeSuppliesCo",
        "amount": 1200.00,
        "po_number": "PO-9004",
        "unit_price": 12.00,
        "posting_date": "2026-07-02T09:00:00",
        "description": "Desks and chairs",
        "source_id": "TX-INV-2002"
    },
    {
        "tx_type": "BILLING_INVOICE",
        "invoice_id": "INV-2003",
        "vendor": "OfficeSuppliesCo",
        "amount": 1200.00,
        "po_number": "PO-9004",
        "unit_price": 12.00,
        "posting_date": "2026-07-02T09:30:00",  # Duplicate within 24h
        "description": "Duplicate desk purchase",
        "source_id": "TX-INV-2003"
    }
]

MOCK_EXPENSES = [
    {
        "tx_type": "EXPENSE_REPORT",
        "report_id": "EXP-3000",
        "employee": "Alice Chen",
        "category": "Travel",
        "amount": 850.00,
        "posting_date": "2026-07-03T10:00:00",
        "description": "Flight ticket to Singapore",
        "receipt_status": "ATTACHED",
        "source_id": "TX-EXP-3000"
    },
    {
        "tx_type": "EXPENSE_REPORT",
        "report_id": "EXP-3001",
        "employee": "David Wu",
        "category": "Meals",
        "amount": 220.00,  # Exceeds $150 meal cap limit
        "posting_date": "2026-07-02T20:30:00",
        "description": "Supplier sync client dinner",
        "receipt_status": "ATTACHED",
        "source_id": "TX-EXP-3001"
    },
    {
        "tx_type": "EXPENSE_REPORT",
        "report_id": "EXP-3002",
        "employee": "Bob Miller",
        "category": "Office Supplies",
        "amount": 450.00,  # Exceeds $25 with missing receipt
        "posting_date": "2026-07-04T15:00:00",
        "description": "Gaming console - team lounge",
        "receipt_status": "MISSING",
        "source_id": "TX-EXP-3002"
    }
]

# Unsorted Mixed Feed
MIXED_FEED = [
    MOCK_LEDGER[0],     # JE-1000 (Close - Normal)
    MOCK_LEDGER[1],     # JE-1001 (Close - SoD Anomaly)
    MOCK_INVOICES[0],   # INV-2000 (Billing - Normal)
    MOCK_INVOICES[1],   # INV-2001 (Billing - Rate Anomaly)
    MOCK_EXPENSES[0],   # EXP-3000 (Expense - Normal)
    MOCK_EXPENSES[1],   # EXP-3001 (Expense - Meal Anomaly)
    MOCK_LEDGER[2],     # JE-1002 (Close - Materiality Anomaly)
    MOCK_EXPENSES[2],   # EXP-3002 (Expense - Receipt Anomaly)
    MOCK_INVOICES[2],   # INV-2002 (Billing - Normal)
    MOCK_INVOICES[3],   # INV-2003 (Billing - Duplicate Anomaly)
    MOCK_LEDGER[3],     # JE-1003 (Close - Cutoff Anomaly)
    MOCK_LEDGER[4]      # JE-1004 (Close - Sensitive Account Anomaly)
]

# --- 3. Adversarial / Grounding Audit Test Cases ---
ADVERSARIAL_CASES = [
    # 1. Missing Contract
    {
        "tx_type": "BILLING_INVOICE",
        "invoice_id": "ADV-01",
        "vendor": "UnknownVendor",
        "amount": 15000.00,
        "po_number": "PO-8820",
        "unit_price": 300.00,
        "posting_date": "2026-07-02T12:00:00",
        "description": "IT consulting hours with no contract",
        "source_id": "TX-ADV-01",
        "adversarial_type": "MISSING_CONTRACT"
    },
    # 2. Expired Contract
    {
        "tx_type": "BILLING_INVOICE",
        "invoice_id": "ADV-02",
        "vendor": "ConsultingCorp",
        "amount": 13000.00,
        "po_number": "PO-9002",
        "unit_price": 130.00,
        "posting_date": "2026-07-02T12:30:00",
        "description": "Billed using an expired 2024 contract",
        "source_id": "TX-ADV-02",
        "adversarial_type": "EXPIRED_CONTRACT"
    },
    # 3. Contract rate conflicts with PO rate
    {
        "tx_type": "BILLING_INVOICE",
        "invoice_id": "ADV-03",
        "vendor": "ConsultingCorp",
        "amount": 20000.00,
        "po_number": "PO-RATE-CONFLICT",
        "unit_price": 200.00,  # PO says 200, contract says 150
        "posting_date": "2026-07-02T12:45:00",
        "description": "Consulting hours billed at conflict PO rates",
        "source_id": "TX-ADV-03",
        "adversarial_type": "RATE_CONFLICT"
    },
    # 4. Historical memo concerns a different transaction / distractor memo
    {
        "tx_type": "EXPENSE_REPORT",
        "report_id": "ADV-04",
        "employee": "Emily Davis",
        "category": "Meals",
        "amount": 250.00,
        "posting_date": "2026-07-02T20:30:00",
        "description": "Supplier sync client dinner citing Dell laptop memo",
        "receipt_status": "ATTACHED",
        "source_id": "TX-ADV-04",
        "adversarial_type": "WRONG_MEMO"
    },
    # 5. Missing User Profile
    {
        "tx_type": "JOURNAL_ENTRY",
        "journal_id": "ADV-05",
        "account": "610200",
        "amount": 4000.00,
        "posting_date": "2026-07-02T10:15:00",
        "period": "07-2026",
        "created_by": "UNKNOWN_USER", # Missing Profile!
        "approved_by": "MANAGER_01",
        "description": "Adjustment made by contractor with no role records",
        "source_id": "TX-ADV-05",
        "adversarial_type": "MISSING_PROFILE"
    }
]


def get_mixed_feed():
    """Retrieve the unsorted mixed transaction feed."""
    return MIXED_FEED

def get_adversarial_feed():
    """Retrieve the adversarial test feed."""
    return ADVERSARIAL_CASES

def get_contract(vendor: str) -> dict:
    """Retrieve contract details for a given vendor."""
    # Find matching expired or active contract in the registry
    for k, v in EVIDENCE_REGISTRY.items():
        if v.get("source_type") == "contract" and v.get("vendor") == vendor:
            return v
    return {}

def get_policy(policy_key: str) -> str:
    """Retrieve corporate policy text."""
    # Maps to the title and content
    for k, v in EVIDENCE_REGISTRY.items():
        if v.get("source_type") == "policy" and policy_key in k.lower():
            return f"{v.get('title')}: {v.get('content')}"
    return "Policy not found."

def get_user_info(user_id: str) -> str:
    """Retrieve user role metadata."""
    key = f"USR-{user_id}"
    if key in EVIDENCE_REGISTRY:
        u = EVIDENCE_REGISTRY[key]
        return f"Name: {u.get('name')}, Role: {u.get('role')}, Level: {u.get('level')}"
    return "Profile not found."
