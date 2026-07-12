# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import asyncio
import os
import json
import time
import csv
import subprocess
from datetime import datetime
import numpy as np
from pydantic import ValidationError
from google import genai
from google.genai import types
from google.auth.credentials import Credentials

from app.schemas import CoordinatorOutput
from app.agent import COORDINATOR_INSTRUCTION
from app.mock_data import get_mixed_feed, get_adversarial_feed
from evals import GROUND_TRUTH, print_table

class TokenCredentials(Credentials):
    def __init__(self, token):
        super().__init__()
        self.token = token
    def refresh(self, request):
        pass
    def apply(self, headers, token=None):
        headers['authorization'] = f"Bearer {self.token}"

def get_gcloud_token():
    try:
        res = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True)
        token = res.stdout.strip()
        if token and not token.startswith("ERROR"):
            return token
    except Exception:
        pass
    return None

# --- Comparable Cost Bases Config ---
# 1. Gemini: Hosted Gemini API Pricing
GEMINI_INPUT_RATE_1M = 1.50   # Paid Standard Tier
GEMINI_OUTPUT_RATE_1M = 9.00  # Paid Standard Tier

# 2. Gemma: Self-Hosted GPU Infrastructure Model
# Assumptions:
# - GPU Instance Type: Google Cloud G2 VM (g2-standard-8) with 1x NVIDIA L4 GPU (24GB VRAM)
# - Quantization: 4-bit / 8-bit serving via vLLM
# - Hourly Compute Cost (GPU + CPU VM): $0.48 / hour ($11.52 / day)
# - Utilization rate: 30% active utilization (typical for internal tools)
# - Concurrency / Throughput: 10 decisions / minute when active (max capacity ~14,400 decisions / day)
# - Daily decisions at 30% utilization: 4,320 decisions / day
# - Cost per 1,000 decisions = Daily compute cost ($11.52) / (Daily decisions / 1,000) = $2.667 / 1,000 decisions.
# - Idle cost factor: Included in the flat daily compute allocation.
GEMMA_INFRA_GPU = "NVIDIA L4 (24GB VRAM)"
GEMMA_HOURLY_RATE = 0.48
GEMMA_UTILIZATION = 0.30
GEMMA_DAILY_CAPACITY = 14400
GEMMA_EST_DECISIONS_DAILY = 4320
GEMMA_COST_PER_1K = (GEMMA_HOURLY_RATE * 24) / (GEMMA_EST_DECISIONS_DAILY / 1000) # $2.667

# 3. Gemma: Hosted API Pricing (from Gemini API if accessed via AI Studio)
GEMMA_API_INPUT_RATE_1M = 0.00   # Free developer tier on Google AI Studio
GEMMA_API_OUTPUT_RATE_1M = 0.00  # Free developer tier on Google AI Studio

async def call_routing_model(
    client: genai.Client,
    model_name: str,
    prompt: str,
    project_id: str,
    location: str
) -> dict:
    """Invokes the live model. Raised errors will abort the benchmark execution.
    
    No simulation fallbacks allowed.
    """
    timestamp = datetime.utcnow().isoformat() + "Z"
    start_time = time.time()
    
    config = types.GenerateContentConfig(
        system_instruction=COORDINATOR_INSTRUCTION,
        response_mime_type="application/json"
    )
    if "gemini" in model_name:
        config.response_schema = CoordinatorOutput

    # Vertex AI Endpoint path vs. standard model ID resolution
    endpoint_path = f"projects/{project_id}/locations/{location}/publishers/google/models/{model_name}"
    
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=config
    )
    
    latency = (time.time() - start_time) * 1000
    
    in_tokens = 0
    out_tokens = 0
    if response.usage_metadata:
        in_tokens = response.usage_metadata.prompt_token_count
        out_tokens = response.usage_metadata.candidates_token_count
    
    response_id = "N/A"
    # Try to extract response ID or signature metadata if present
    if hasattr(response, "response_id") and response.response_id:
        response_id = response.response_id
        
    raw_text = response.text or ""
    clean_text = raw_text.strip()
    if clean_text.startswith("```json"):
        clean_text = clean_text[7:]
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3]
    clean_text = clean_text.strip()

    schema_ok = False
    try:
        parsed = json.loads(clean_text)
        CoordinatorOutput(**parsed)
        schema_ok = True
    except (json.JSONDecodeError, ValidationError):
        pass

    return {
        "timestamp": timestamp,
        "response_id": response_id,
        "clean_response": clean_text,
        "in_tokens": in_tokens,
        "out_tokens": out_tokens,
        "latency_ms": latency,
        "schema_success": schema_ok,
        "endpoint_path": endpoint_path,
        "error": None
    }


async def run_benchmark():
    print("BENCHMARK_MODE=LIVE")
    
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "financeguard-ai-demo")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    
    api_key = os.environ.get("GOOGLE_API_KEY")
    api_key_configured = bool(api_key and api_key != "YOUR_GEMINI_API_KEY_HERE" and "placeholder" not in api_key.lower())
    
    # Authenticate GenAI client with standard credentials or active gcloud auth token
    if api_key_configured:
        print(f"Auth Method: API_KEY (AI Studio)")
        client = genai.Client()
    else:
        gcloud_token = get_gcloud_token()
        if not gcloud_token:
            raise RuntimeError("CRITICAL ERROR: No active credentials. Set GOOGLE_API_KEY or run 'gcloud auth login' to authenticate.")
        
        print(f"Auth Method: OAuth2 Token (Vertex AI Keyless)")
        creds = TokenCredentials(gcloud_token)
        client = genai.Client(
            vertexai=True,
            project=project_id,
            location=location,
            credentials=creds
        )

    # Ingest 17 ground truth cases
    combined_feed = get_mixed_feed() + get_adversarial_feed()
    target_ids = list(GROUND_TRUTH.keys())
    benchmark_cases = [tx for tx in combined_feed if (tx.get("journal_id") or tx.get("invoice_id") or tx.get("report_id")) in target_ids]

    models = ["gemini-3.5-flash", "gemma-4-31b-it"]
    raw_results = []
    summary_stats = {}

    for model in models:
        print(f"\nModel Target: {model}")
        print(f"Model Endpoint: projects/{project_id}/locations/{location}/publishers/google/models/{model}")
        
        model_runs = []
        aborted = False

        for idx, tx in enumerate(benchmark_cases):
            tx_id = tx.get("journal_id") or tx.get("invoice_id") or tx.get("report_id")
            gt = GROUND_TRUTH[tx_id]
            
            coord_input = {k: v for k, v in tx.items() if k != "tx_type"}
            coord_prompt = f"Determine category and routing for transaction data:\n{json.dumps(coord_input, separators=(',', ':'))}"

            # 1. Execute Warm-up Call
            print(f"  [{idx+1}/{len(benchmark_cases)}] Case {tx_id:<8} | Calling warm-up...")
            try:
                await call_routing_model(client, model, coord_prompt, project_id, location)
            except Exception as we:
                print(f"  ❌ Warm-up call failed for {model}: {we}")
                print(f"  CRITICAL ERROR: Stop and report error: Model access failed.")
                aborted = True
                break

            # 2. Run 5 Repetitions
            reps_latencies = []
            reps_tokens_in = []
            reps_tokens_out = []
            reps_schema = []
            reps_correct = []
            
            for rep in range(5):
                try:
                    res = await call_routing_model(client, model, coord_prompt, project_id, location)
                    
                    # Parse routed specialist
                    routed_specialist = None
                    if res["schema_success"]:
                        try:
                            parsed = json.loads(res["clean_response"])
                            routed_specialist = parsed.get("designated_specialist")
                        except Exception:
                            pass
                            
                    is_correct = (routed_specialist == gt["expected_specialist"])
                    
                    reps_latencies.append(res["latency_ms"])
                    reps_tokens_in.append(res["in_tokens"])
                    reps_tokens_out.append(res["out_tokens"])
                    reps_schema.append(res["schema_success"])
                    reps_correct.append(is_correct)
                    
                    # Record raw request log
                    raw_results.append({
                        "timestamp": res["timestamp"],
                        "model": model,
                        "tx_id": tx_id,
                        "rep": rep + 1,
                        "response_id": res["response_id"],
                        "latency_ms": res["latency_ms"],
                        "in_tokens": res["in_tokens"],
                        "out_tokens": res["out_tokens"],
                        "schema_success": res["schema_success"],
                        "is_correct": is_correct,
                        "expected": gt["expected_specialist"],
                        "actual": routed_specialist,
                        "clean_response": res["clean_response"],
                        "endpoint": res["endpoint_path"],
                        "error": None
                    })
                except Exception as e:
                    print(f"  ❌ Call {rep+1} failed: {e}")
                    raw_results.append({
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "model": model,
                        "tx_id": tx_id,
                        "rep": rep + 1,
                        "response_id": "ERROR",
                        "latency_ms": 0.0,
                        "in_tokens": 0,
                        "out_tokens": 0,
                        "schema_success": False,
                        "is_correct": False,
                        "expected": gt["expected_specialist"],
                        "actual": "API_ERROR",
                        "clean_response": "",
                        "endpoint": f"projects/{project_id}/locations/{location}/publishers/google/models/{model}",
                        "error": str(e)
                    })
                    print(f"  CRITICAL ERROR: Stop and report error: Model access failed.")
                    aborted = True
                    break
            
            if aborted:
                break
                
            # Log aggregate for this case
            model_runs.append({
                "tx_id": tx_id,
                "latencies": reps_latencies,
                "in_tokens": reps_tokens_in,
                "out_tokens": reps_tokens_out,
                "schema": reps_schema,
                "correct": reps_correct
            })
            
            median_lat = np.median(reps_latencies)
            p95_lat = np.percentile(reps_latencies, 95)
            print(f"  ↳ Case {tx_id} completed: 5 runs | Median Latency: {median_lat:.1f}ms | p95 Latency: {p95_lat:.1f}ms")

        if aborted:
            summary_stats[model] = {"status": "FAILED", "error": "Model access/authorization failed."}
            continue

        # Compile model statistics
        flat_correct = [c for run in model_runs for c in run["correct"]]
        flat_schema = [s for run in model_runs for s in run["schema"]]
        flat_latencies = [l for run in model_runs for l in run["latencies"]]
        flat_in_tokens = [i for run in model_runs for i in run["in_tokens"]]
        flat_out_tokens = [o for run in model_runs for o in run["out_tokens"]]

        summary_stats[model] = {
            "status": "SUCCESS",
            "accuracy": (sum(flat_correct) / len(flat_correct)) * 100,
            "schema_rate": (sum(flat_schema) / len(flat_schema)) * 100,
            "median_latency": np.median(flat_latencies),
            "p95_latency": np.percentile(flat_latencies, 95),
            "avg_in_tokens": np.mean(flat_in_tokens),
            "avg_out_tokens": np.mean(flat_out_tokens),
            "total_in_tokens": sum(flat_in_tokens),
            "total_out_tokens": sum(flat_out_tokens)
        }

    # --- Export Raw per-request results to JSON ---
    export_path = "benchmark_results.json"
    with open(export_path, "w") as f:
        json.dump(raw_results, f, indent=2)
    print(f"\n📂 Raw per-request benchmark results exported to: {export_path}")

    # --- Render Benchmark Report ---
    print("\n" + "=" * 80)
    print("                    AUDIT-READY ROUTING BENCHMARK REPORT                  ")
    print("=" * 80)
    
    report_rows = []
    for model in models:
        stats = summary_stats[model]
        if stats["status"] == "FAILED":
            report_rows.append([model, "FAILED", "N/A", "N/A", "N/A", "N/A", "N/A"])
            continue
            
        # Cost math
        if model == "gemini-3.5-flash":
            bench_cost = ((stats["total_in_tokens"] / 1_000_000) * GEMINI_INPUT_RATE_1M) + ((stats["total_out_tokens"] / 1_000_000) * GEMINI_OUTPUT_RATE_1M)
            cost_per_1k = 1000 * (((stats["avg_in_tokens"] / 1_000_000) * GEMINI_INPUT_RATE_1M) + ((stats["avg_out_tokens"] / 1_000_000) * GEMINI_OUTPUT_RATE_1M))
            cost_label = "Gemini API Paid Tier"
        else:
            # Gemma 4 self-hosted VM cost model
            bench_cost = (stats["total_in_tokens"] + stats["total_out_tokens"]) * 0.0  # free token base in AI studio
            cost_per_1k = GEMMA_COST_PER_1K
            cost_label = f"Self-Hosted GPU (1x L4, 30% util)"

        report_rows.append([
            model,
            f"{stats['accuracy']:.1f}% [MEASURED]",
            f"{stats['schema_rate']:.1f}% [MEASURED]",
            f"{stats['median_latency']:.1f}ms / {stats['p95_latency']:.1f}ms [MEASURED]",
            f"${bench_cost:.6f} [MEASURED]",
            f"${cost_per_1k:.4f} [MODELED]",
            cost_label
        ])

    print_table(
        ["Model ID", "Accuracy", "Schema success", "Median/p95 Latency", "Benchmark Cost", "Cost / 1k Decisions", "Cost Basis"],
        report_rows
    )
    print("=" * 80)
    
    # Print GPU Assumptions
    print("\n🔍 SELF-HOSTED GPU COST MODEL ASSUMPTIONS:")
    print(f"  - Server Infrastructure: {GEMMA_INFRA_GPU}")
    print(f"  - Compute Instance Rate: ${GEMMA_HOURLY_RATE:.2f}/hr (Google Cloud G2 VM)")
    print(f"  - Model Optimization: 4-bit quantized serving via vLLM")
    print(f"  - Idle Allocation: Daily instances hosted flat rate (${GEMMA_HOURLY_RATE * 24:.2f}/day)")
    print(f"  - Utilization Rate: {GEMMA_UTILIZATION * 100:.0f}% active concurrency")
    print(f"  - Average Decisions Daily: {GEMMA_EST_DECISIONS_DAILY} decisions/day")
    print(f"  - Formula: Daily Instance Cost / (Daily Decisions / 1000) = ${GEMMA_COST_PER_1K:.4f}")
    print("-" * 80)

if __name__ == "__main__":
    asyncio.run(run_benchmark())
