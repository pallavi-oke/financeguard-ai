# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import pytest
import json
from benchmark_routing import call_routing_model
from app.schemas import CoordinatorOutput

@pytest.mark.asyncio
async def test_simulated_call_routing_model():
    """Asserts that simulated model adapter returns valid CoordinatorOutput JSON."""
    clean_res, in_tokens, out_tokens, latency, schema_ok = await call_routing_model(
        client=None,
        model_name="gemma-4-31b-it",
        prompt="Determine category and routing for transaction data: {amount: 100.0}",
        is_simulated=True,
        simulated_gt_specialist="billing_specialist"
    )

    assert schema_ok is True
    assert in_tokens > 0
    assert out_tokens > 0
    assert latency >= 0.0

    parsed = json.loads(clean_res)
    assert parsed["designated_specialist"] == "billing_specialist"
    assert parsed["domain"] == "BILLING"

    # Verify Pydantic schema validation is satisfied
    coord_out = CoordinatorOutput(**parsed)
    assert coord_out.domain == "BILLING"
    assert coord_out.designated_specialist == "billing_specialist"
    assert coord_out.confidence_score == 0.98
