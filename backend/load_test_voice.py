"""
Simple load test for the /v1/voice/session and /telephony APIs.

This script uses httpx (already a backend dependency) to exercise:

- Voice session API:
  - POST /v1/voice/session/start
  - POST /v1/voice/session/{session_id}/input
  - POST /v1/voice/session/{session_id}/end

- Telephony API:
  - POST /telephony/inbound
  - POST /telephony/audio
  - POST /telephony/end

It runs a configurable number of concurrent "sessions" against a target backend and
prints basic timing statistics. It is intended for local/staging use only.

Usage (from repo root, with backend running on http://localhost:8000):

    cd backend
    python -m venv .venv
    .venv\\Scripts\\activate  # or source .venv/bin/activate on Unix
    pip install -e .[dev]

    # Voice session API
    python load_test_voice.py --mode voice --concurrency 10 --sessions 50 --backend http://localhost:8000 --api-key YOUR_API_KEY

    # Telephony API
    python load_test_voice.py --mode telephony --concurrency 10 --sessions 50 --backend http://localhost:8000 --api-key YOUR_API_KEY
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from typing import Optional

import httpx


async def run_single_session_voice(
    client: httpx.AsyncClient,
    backend_base: str,
    api_key: Optional[str],
    business_id: Optional[str],
) -> float:
    """Run a single synthetic /v1/voice/session flow and return total elapsed seconds."""
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    if business_id:
        headers["X-Business-ID"] = business_id

    t0 = time.perf_counter()

    # 1) Start session.
    start_resp = await client.post(
        f"{backend_base}/v1/voice/session/start",
        json={"caller_phone": "555-0000", "lead_source": "Load Test"},
        headers=headers,
        timeout=10.0,
    )
    start_resp.raise_for_status()
    session_id = start_resp.json()["session_id"]

    # 2) Send a small sequence of turns (name, address, problem).
    async def turn(text: str) -> None:
        resp = await client.post(
            f"{backend_base}/v1/voice/session/{session_id}/input",
            json={"text": text},
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()

    await turn("John Test")
    await turn("123 Main Street, Merriam Kansas")
    await turn("my water heater is leaking and I need service")

    # 3) End session.
    end_resp = await client.post(
        f"{backend_base}/v1/voice/session/{session_id}/end",
        headers=headers,
        timeout=5.0,
    )
    end_resp.raise_for_status()

    t1 = time.perf_counter()
    return t1 - t0


async def run_single_session_telephony(
    client: httpx.AsyncClient,
    backend_base: str,
    api_key: Optional[str],
    business_id: Optional[str],
) -> float:
    """Run a single synthetic /telephony flow and return total elapsed seconds."""
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    if business_id:
        headers["X-Business-ID"] = business_id

    t0 = time.perf_counter()

    # 1) Inbound call (start + greeting).
    inbound_resp = await client.post(
        f"{backend_base}/telephony/inbound",
        json={"caller_phone": "555-0000", "lead_source": "Load Test"},
        headers=headers,
        timeout=10.0,
    )
    inbound_resp.raise_for_status()
    payload = inbound_resp.json()
    session_id = payload["session_id"]

    # 2) Send a few turns of caller text via /telephony/audio.
    async def turn(text: str) -> None:
        resp = await client.post(
            f"{backend_base}/telephony/audio",
            json={"session_id": session_id, "text": text},
            timeout=10.0,
        )
        resp.raise_for_status()

    await turn("John Test")
    await turn("123 Main Street, Merriam Kansas")
    await turn("my water heater is leaking and I need service")

    # 3) End call.
    end_resp = await client.post(
        f"{backend_base}/telephony/end",
        json={"session_id": session_id},
        timeout=5.0,
    )
    end_resp.raise_for_status()

    t1 = time.perf_counter()
    return t1 - t0


async def run_load_test(
    backend_base: str,
    api_key: Optional[str],
    business_id: Optional[str],
    total_sessions: int,
    concurrency: int,
    mode: str,
) -> None:
    connector_limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency * 2)
    async with httpx.AsyncClient(limits=connector_limits) as client:
        sem = asyncio.Semaphore(concurrency)
        timings: list[float] = []
        errors = 0

        async def worker(idx: int) -> None:
            nonlocal errors
            async with sem:
                try:
                    if mode == "telephony":
                        elapsed = await run_single_session_telephony(client, backend_base, api_key, business_id)
                    else:
                        elapsed = await run_single_session_voice(client, backend_base, api_key, business_id)
                    timings.append(elapsed)
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    print(f"[session {idx}] error: {exc}")

        tasks = [asyncio.create_task(worker(i + 1)) for i in range(total_sessions)]
        t0 = time.perf_counter()
        await asyncio.gather(*tasks)
        t1 = time.perf_counter()

    completed = len(timings)
    print(f"Total sessions requested: {total_sessions}")
    print(f"Completed without error: {completed}")
    print(f"Errors: {errors}")
    print(f"Wall-clock time: {t1 - t0:.2f}s")

    if not timings:
        return

    timings.sort()
    avg = statistics.mean(timings)
    p50 = statistics.median(timings)
    p95_idx = int(0.95 * (completed - 1))
    p99_idx = int(0.99 * (completed - 1))
    p95 = timings[p95_idx]
    p99 = timings[p99_idx]

    print("Per-session total latency (start + 3 turns + end):")
    print(f"  avg: {avg:.3f}s")
    print(f"  p50: {p50:.3f}s")
    print(f"  p95: {p95:.3f}s")
    print(f"  p99: {p99:.3f}s")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load test /v1/voice/session or /telephony APIs.",
    )
    parser.add_argument(
        "--backend",
        default="http://localhost:8000",
        help="Base URL for the backend (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Tenant API key to send as X-API-Key (optional in dev).",
    )
    parser.add_argument(
        "--business-id",
        default=None,
        help="Business ID to send as X-Business-ID (optional).",
    )
    parser.add_argument(
        "--sessions",
        type=int,
        default=20,
        help="Total number of synthetic sessions to run (default: 20).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Maximum concurrent sessions (default: 5).",
    )
    parser.add_argument(
        "--mode",
        choices=["voice", "telephony"],
        default="voice",
        help="Which API surface to test: 'voice' (/v1/voice/session/*) or 'telephony' (/telephony/*).",
    )
    args = parser.parse_args()

    print(
        f"Running {args.mode} load test against {args.backend} "
        f"with {args.sessions} sessions at concurrency {args.concurrency}..."
    )
    asyncio.run(
        run_load_test(
            backend_base=args.backend,
            api_key=args.api_key,
            business_id=args.business_id,
            total_sessions=args.sessions,
            concurrency=args.concurrency,
            mode=args.mode,
        )
    )


if __name__ == "__main__":
    main()
