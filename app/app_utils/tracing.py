# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import os
import json
from pathlib import Path
from datetime import datetime

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
TRACES_FILE = PROJECT_DIR / "traces.jsonl"


def log_trace(
    tx_id: str,
    agent_name: str,
    prompt: str,
    response: str,
    tool_calls: list = None,
    latency_ms: float = 0.0,
    estimated_input_tokens: int = 0,
    estimated_output_tokens: int = 0,
    status: str = "SUCCESS"
):
    """Appends an execution trace locally to traces.jsonl for offline observability.
    
    Logs include latency, prompt context, tool invocations, and token metrics.
    """
    trace_entry = {
        "timestamp": datetime.now().isoformat(),
        "transaction_id": tx_id,
        "agent_name": agent_name,
        "prompt": prompt,
        "response": response,
        "tool_calls": tool_calls or [],
        "latency_ms": latency_ms,
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "status": status
    }
    
    try:
        with open(TRACES_FILE, "a") as f:
            f.write(json.dumps(trace_entry) + "\n")
    except Exception as e:
        print(f"[TRACING ERROR] Failed to write local trace log: {e}")
