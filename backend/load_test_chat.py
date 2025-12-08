"""
Simple load test for the /v1/chat endpoint (owner assistant).

This script measures latency percentiles and error rate while firing a mix
of chat requests concurrently against a target backend.

Usage (from repo root, backend running on http://localhost:8000):

    cd backend
    python load_test_chat.py --requests 50 --concurrency 10 \
      --backend http://localhost:8000 \
      --owner-token YOUR_OWNER_TOKEN --business-id YOUR_BUSINESS_ID

Options:
- --requests: total chat requests to send (default: 50)
- --concurrency: number of concurrent in-flight requests (default: 5)
- --backend: base URL of the backend (default: http://localhost:8000)
- --owner-token: optional X-Owner-Token if dashboard auth is enabled
- --api-key: optional X-API-Key
- --business-id: optional X-Business-ID
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from typing import Optional

import httpx


async def run_single_chat(
    client: httpx.AsyncClient,
    backend_base: str,
    api_key: Optional[str],
    owner_token: Optional[str],
    business_id: Optional[str],
    text: str,
) -> float:
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    if owner_token:
        headers["X-Owner-Token"] = owner_token
    if business_id:
        headers["X-Business-ID"] = business_id

    t0 = time.perf_counter()
    resp = await client.post(
        f"{backend_base}/v1/chat",
        json={"text": text},
        headers=headers,
        timeout=20.0,
    )
    resp.raise_for_status()
    return time.perf_counter() - t0


async def run_load(
    requests: int,
    concurrency: int,
    backend_base: str,
    api_key: Optional[str],
    owner_token: Optional[str],
    business_id: Optional[str],
) -> tuple[list[float], int]:
    connector_limits = httpx.Limits(max_keepalive_connections=concurrency * 2)
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    failures = 0
    sample_texts = [
        "Summarize bookings this week vs last week",
        "How many emergencies are scheduled tomorrow?",
        "List today's jobs with assigned techs",
        "Show my top repeat customers in the last 90 days",
        "What is the earliest available slot for a water heater install?",
    ]

    async with httpx.AsyncClient(limits=connector_limits) as client:

        async def worker(idx: int) -> None:
            nonlocal failures
            async with sem:
                try:
                    text = sample_texts[idx % len(sample_texts)]
                    elapsed = await run_single_chat(
                        client, backend_base, api_key, owner_token, business_id, text
                    )
                    latencies.append(elapsed)
                except Exception:
                    failures += 1

        await asyncio.gather(*(worker(i) for i in range(requests)))

    return latencies, failures


def summarize(latencies: list[float]) -> dict[str, float]:
    if not latencies:
        return {"avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    sorted_vals = sorted(latencies)

    def pct(p: float) -> float:
        if not sorted_vals:
            return 0.0
        idx = int(round((len(sorted_vals) - 1) * p))
        idx = max(0, min(idx, len(sorted_vals) - 1))
        return sorted_vals[idx]

    return {
        "avg": statistics.mean(sorted_vals),
        "p50": pct(0.50),
        "p95": pct(0.95),
        "p99": pct(0.99),
        "max": max(sorted_vals),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Load test /v1/chat")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument(
        "--backend", type=str, default="http://localhost:8000", help="Backend base URL"
    )
    parser.add_argument("--api-key", type=str, default=None, help="X-API-Key")
    parser.add_argument("--owner-token", type=str, default=None, help="X-Owner-Token")
    parser.add_argument("--business-id", type=str, default=None, help="X-Business-ID")
    args = parser.parse_args()

    latencies, failures = asyncio.run(
        run_load(
            requests=args.requests,
            concurrency=args.concurrency,
            backend_base=args.backend.rstrip("/"),
            api_key=args.api_key,
            owner_token=args.owner_token,
            business_id=args.business_id,
        )
    )

    completed = len(latencies)
    stats = summarize(latencies)
    print(f"Chat load test: {completed} completed, {failures} failed")
    print(
        f"avg={stats['avg']*1000:.1f}ms p50={stats['p50']*1000:.1f}ms "
        f"p95={stats['p95']*1000:.1f}ms p99={stats['p99']*1000:.1f}ms "
        f"max={stats['max']*1000:.1f}ms"
    )


if __name__ == "__main__":
    main()
