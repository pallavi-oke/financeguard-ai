# ruff: noqa
# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import os
import google.auth
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

# Load local environment variables from .env if present
load_dotenv()

# Import custom tools
from .tools import (
    query_vendor_contract,
    query_related_transactions,
    read_finance_policy,
    search_historical_memos,
    get_user_profile
)
from .audit import MODEL_ID

# Route authentication based on presence of GOOGLE_API_KEY
if os.environ.get("GOOGLE_API_KEY"):
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "False"
else:
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
    try:
        _, project_id = google.auth.default()
        os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    except Exception:
        if "GOOGLE_CLOUD_PROJECT" not in os.environ:
            os.environ["GOOGLE_CLOUD_PROJECT"] = "financeguard-ai-demo"

# Shared Model Configuration
SHARED_MODEL = Gemini(
    model=MODEL_ID,
    retry_options=types.HttpRetryOptions(attempts=3),
)

# --- 1. Coordinator Agent Prompt (Strict JSON Output matching CoordinatorOutput) ---
COORDINATOR_INSTRUCTION = """You are the Finance Exception Coordinator. 
Your role is to inspect the incoming transaction data payload, determine the transaction category, and designate the specialist agent who should handle the investigation.

Categories & Specialists:
- JOURNAL_ENTRY -> Close Specialist (domain: CLOSE, designated_specialist: close_specialist)
- BILLING_INVOICE -> Billing Specialist (domain: BILLING, designated_specialist: billing_specialist)
- EXPENSE_REPORT -> Expense Specialist (domain: EXPENSE, designated_specialist: expense_specialist)

You MUST respond strictly with a valid JSON object matching this schema:
{
  "domain": "CLOSE / BILLING / EXPENSE",
  "designated_specialist": "close_specialist / billing_specialist / expense_specialist",
  "routing_reason": "Provide a detailed justification based on transaction field patterns.",
  "confidence_score": 0.95
}

Do not include any text, markdown formatting blocks (like ```json), or headers. Output ONLY the JSON object.
"""

coordinator_agent = Agent(
    name="coordinator_agent",
    model=SHARED_MODEL,
    instruction=COORDINATOR_INSTRUCTION,
)

# --- 2. Worker Specialists Prompt (Strict JSON Output matching SpecialistOutput) ---

SPECIALIST_COMMON_INSTRUCTION = """You are a Finance Specialist Agent. 
Your role is to investigate exceptions, compile facts, state hypotheses, and suggest a resolution recommendation.

Input Context:
- The transaction payload to investigate.
- The list of deterministic control checks that failed.
- The EVIDENCE SNAPSHOT containing all retrieved evidence policy files, vendor contracts, user profiles, and historical memos, each labelled with a stable SOURCE ID (e.g. POL-SOD, CON-CONSULTINGCORP-2026, USR-ANALYST_01).

You MUST respond strictly with a valid JSON object matching this schema:
{
  "facts": [
    {
      "claim_id": "FACT-01",
      "claim": "Specific factual claim stated in the transaction or retrieved evidence.",
      "claim_type": "TRANSACTION_FACT / POLICY_FACT / CONTRACT_FACT / HISTORICAL_CONTEXT",
      "evidence_refs": [
        {
          "source_id": "Stable Source ID cited directly from the Evidence Registry",
          "source_type": "transaction / policy / contract / historical_memo / user_profile / related_transaction",
          "source_field": "Field name in source if applicable, or null",
          "source_excerpt": "Direct text quote or excerpt from the source file"
        }
      ]
    }
  ],
  "hypotheses": [
    {
      "hypothesis_id": "HYP-01",
      "statement": "Your interpretation of why the control checks failed or potential risks.",
      "basis_source_ids": ["Cited source IDs supporting this interpretation"],
      "uncertainty": "Description of any residual doubts or unconfirmed factors."
    }
  ],
  "missing_evidence": ["List of any required source IDs or context that could not be found"],
  "confidence": "HIGH / MEDIUM / LOW",
  "recommendation": "APPROVE_WITH_EXCEPTION / REJECT_AND_ESCALATE / REQUEST_DOCUMENTS / REQUEST_MANAGER_REVIEW / VENDOR_DISPUTE / MANUAL_INVESTIGATION",
  "rationale": "Detailed explanation of your recommendation decision."
}

Specialist Rules:
1. Every factual claim MUST include at least one evidence_ref citing a valid source_id from the registry.
2. Separate hypotheses clearly from facts. Do not present speculation or predictions as facts.
3. Historical patterns (from historical_memo or related_transactions) must never be presented as proof of the current transaction. They are secondary context only.
4. If a required policy, contract, or profile is missing, explicitly list it in missing_evidence.
5. Do not include markdown codeblocks (```json). Output ONLY the raw JSON.
"""

# A. Close Specialist
CLOSE_SPECIALIST_INSTRUCTION = SPECIALIST_COMMON_INSTRUCTION + "\nSpecific Domain: General ledger adjusting journal entries, monthly close cutoffs, segregation of duties, sensitive clearing accounts."
close_specialist_agent = Agent(
    name="close_specialist_agent",
    model=SHARED_MODEL,
    instruction=CLOSE_SPECIALIST_INSTRUCTION,
    tools=[get_user_profile, read_finance_policy, query_related_transactions, search_historical_memos],
)

# B. Billing Specialist
BILLING_SPECIALIST_INSTRUCTION = SPECIALIST_COMMON_INSTRUCTION + "\nSpecific Domain: Vendor billing invoice rates, purchase order matches, duplicate invoicing, rate cap reviews."
billing_specialist_agent = Agent(
    name="billing_specialist_agent",
    model=SHARED_MODEL,
    instruction=BILLING_SPECIALIST_INSTRUCTION,
    tools=[query_vendor_contract, read_finance_policy, query_related_transactions, search_historical_memos],
)

# C. Expense Specialist
EXPENSE_SPECIALIST_INSTRUCTION = SPECIALIST_COMMON_INSTRUCTION + "\nSpecific Domain: Employee travel and entertainment expense reports, per-diem caps, receipt requirements, restricted electronic items."
expense_specialist_agent = Agent(
    name="expense_specialist_agent",
    model=SHARED_MODEL,
    instruction=EXPENSE_SPECIALIST_INSTRUCTION,
    tools=[read_finance_policy, get_user_profile, query_related_transactions, search_historical_memos],
)

# --- 3. Critic Agent Prompt (Strict JSON Output matching CriticOutput) ---
CRITIC_INSTRUCTION = """You are the Control Critic Agent. 
Your role is to audit the specialist's draft report by verifying every claim check at the factual level against the retrieved evidence bundle.

You will be provided with:
- The raw transaction payload.
- The retrieved evidence bundle registry containing policy texts, contract rates, user profiles, and historical memos with stable source IDs.
- The specialist's drafted JSON report (containing facts, hypotheses, and citations).

You MUST respond strictly with a valid JSON object matching this schema:
{
  "verdict": "PASS / REJECT",
  "claim_checks": [
    {
      "claim_id": "Citing claim_id from specialist report",
      "status": "SUPPORTED / UNSUPPORTED / CONTRADICTED / INSUFFICIENT_EVIDENCE / INVALID_SOURCE_REFERENCE",
      "validated_source_ids": ["List of source IDs that verify this claim"],
      "reason": "Detailed logic of why this claim is verified, contradicted, or missing source reference."
    }
  ],
  "unsupported_claims": ["List of specialist claims that were not supported by the evidence bundle"],
  "contradicted_claims": ["List of specialist claims contradicted by the evidence bundle"],
  "missing_evidence": ["List of required evidence details or documents missing from the bundle"],
  "invalid_source_references": ["List of cited source IDs that do not exist in the evidence registry"],
  "confidence_score": 0.95,
  "reasons": "Overall summary of the audit evaluation result."
}

Critic Rules:
1. Verdict must be REJECT if there is any UNSUPPORTED claim, CONTRADICTED claim, INVALID_SOURCE_REFERENCE, incorrect citation mapping, or missing required evidence.
2. Every specialist factual claim must cite a source ID that actually exists in the provided evidence registry.
3. Hypotheses or speculation must never be marked as validated facts.
4. Do not include markdown codeblocks (```json). Output ONLY the raw JSON.
"""

critic_agent = Agent(
    name="critic_agent",
    model=SHARED_MODEL,
    instruction=CRITIC_INSTRUCTION,
)

# Root app
app = App(
    root_agent=coordinator_agent,
    name="app",
)
