# Evaluation & Observability Blueprint: Langfuse and ADK

This guide details the validation strategy for the **Finance Close Exception Investigator**. It covers local quality-assurance (evaluation) and production trace monitoring (observability) using **Langfuse**.

---

## 1. Quality-Assurance (Evals)

Because financial transactions demand high precision, we avoid asserting on output strings in traditional unit tests. Instead, we use the ADK’s built-in **LLM-as-judge** evaluation engine.

### Core Metrics to Track:
1.  **Citations Accuracy (Grounding):** Does the AI's investigation report base its findings *only* on the policies and history retrieved, or did it introduce hallucinated facts?
2.  **Facts vs. Hypotheses Separation:** Does the agent strictly separate verified ledger facts (e.g., *"Posting occurred at 11:30 PM"*) from logical hypotheses (e.g., *"This indicates a potential manual override"*), keeping the language neutral?
3.  **Triage Routing Correctness:** Does the model recommend escalation for high-risk profiles, and request supporting invoices when policies mandate them?

### Running the Eval Loop:
We define our evaluation dataset in `tests/eval/datasets/close_investigations.json`. To run the evaluation:

```bash
# 1. Run the agent over the evaluation dataset to generate traces
agents-cli eval generate --dataset tests/eval/datasets/close_investigations.json

# 2. Grade the traces using the LLM-as-judge metrics in eval_config.yaml
agents-cli eval grade

# 3. Compare current results with a prior baseline
agents-cli eval compare prior_results.json latest_results.json
```

---

## 2. Production Trace Monitoring (Langfuse Integration)

The Google Agent Development Kit (ADK) implements native **OpenTelemetry (OTel)** hooks under the hood. Any model call, tool execution, or state change generates standard OpenTelemetry span events.

Because **Langfuse** natively supports the standard OpenTelemetry OTLP collection format, you can route all execution traces to your Langfuse dashboard without adding *any* proprietary client libraries or editing Python code.

### Step 1: Configure OpenTelemetry Exporter
In `app/app_utils/telemetry.py`, the ADK registers telemetry collectors. When deploying or running the agent, inject the following environment variables:

```bash
# Set Langfuse OTLP receiver endpoint
export OTEL_EXPORTER_OTLP_ENDPOINT="https://cloud.langfuse.com/api/public/otlp"

# Pass your Langfuse project credentials (public_key and secret_key in base64)
# Format: "Basic [base64_encode(public_key + ":" + secret_key)]"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic dGtfcHVibGljX2tleV9oZXJlOnNrX3ByaXZhdGVfa2V5X2hlcmU="

# Set your project trace namespace
export OTEL_SERVICE_NAME="close-investigator-pilot"
```

### Step 2: Telemetry Initialization Code (`app/app_utils/telemetry.py`)
The app automatically initializes telemetry on startup:

```python
import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

def setup_telemetry():
    """Bridges ADK's native OpenTelemetry spans to the external Langfuse collector."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        # Silently skip if no external collector configured during local dev
        return
        
    provider = TracerProvider()
    processor = BatchSpanProcessor(
        OTLPSpanExporter(
            endpoint=endpoint,
            headers=os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
        )
    )
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    print(f"[OBSERVABILITY] OpenTelemetry export active. Routing traces to: {endpoint}")
```

### Step 3: What You See in Langfuse
*   **Trace Map:** View the complete waterfall trace of the close run.
*   **Tool Execution Spans:** Look at the exact inputs and outputs for `get_user_profile`, `read_corporate_policy`, and `query_related_transactions`.
*   **Prompt Monitoring:** Audit the exact system prompt version and Gemini's temperature.
*   **Latency Analysis:** Drill down to see if latency is driven by tool executions (e.g. slow SQL queries) or the LLM generation phase.
