# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import asyncio
import os
import json
import time
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

# --- Comparable Pay-As-You-Go Cost Bases ---
PRICING = {
    "gemini-3.5-flash": {
        "input_1m": 1.50,
        "output_1m": 9.00
    },
    "gemma-4-26b-a4b-it-maas": {
        "input_1m": 0.15,
        "output_1m": 0.60
    }
}

async def call_routing_model(
    client: genai.Client,
    model_name: str,
    prompt: str,
    project_id: str,
    location: str
) -> dict:
    """Invokes the live serverless model. Raised errors will abort execution."""
    timestamp = datetime.utcnow().isoformat() + "Z"
    start_time = time.time()
    
    config = types.GenerateContentConfig(
        system_instruction=COORDINATOR_INSTRUCTION,
        response_mime_type="application/json"
    )
    # Gemini models support Pydantic response_schema directly
    if "gemini" in model_name:
        config.response_schema = CoordinatorOutput

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

    models = ["gemini-3.5-flash", "gemma-4-26b-a4b-it-maas"]
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

    # --- Export Raw Results ---
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
        price_cfg = PRICING[model]
        bench_cost = ((stats["total_in_tokens"] / 1_000_000) * price_cfg["input_1m"]) + ((stats["total_out_tokens"] / 1_000_000) * price_cfg["output_1m"])
        cost_per_1k = 1000 * (((stats["avg_in_tokens"] / 1_000_000) * price_cfg["input_1m"]) + ((stats["avg_out_tokens"] / 1_000_000) * price_cfg["output_1m"]))
        cost_label = f"Vertex AI MaaS (Standard Pay-As-You-Go)"

        report_rows.append([
            model,
            f"{stats['accuracy']:.1f}% [MEASURED]",
            f"{stats['schema_rate']:.1f}% [MEASURED]",
            f"{stats['median_latency']:.1f}ms / {stats['p95_latency']:.1f}ms [MEASURED]",
            f"${bench_cost:.6f} [CALCULATED FROM MEASURED TOKEN USAGE]",
            f"${cost_per_1k:.4f} [CALCULATED FROM MEASURED TOKEN USAGE]",
            cost_label
        ])

    print_table(
        ["Model ID", "Accuracy", "Schema success", "Median/p95 Latency", "Benchmark Cost", "Cost / 1k Decisions", "Cost Basis"],
        report_rows
    )
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(run_benchmark())
