"""Closed beta smoke suite runner.

Runs a few end-to-end-ish scenarios against the in-process app with stub providers.
Outputs a JSON report with pass/fail and timings for CI artifacts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from fastapi.testclient import TestClient

from app.main import app
from app.repositories import appointments_repo, customers_repo
from app.services.conversation import ConversationManager
from app.services.sessions import CallSession


Result = Dict[str, Any]


def _reset_repos() -> None:
    appointments_repo._by_id.clear()
    appointments_repo._by_customer.clear()
    appointments_repo._by_business.clear()
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()


async def scenario_happy_path() -> str:
    """New caller schedules and confirms an appointment."""
    _reset_repos()
    session = CallSession(id="smoke-happy", caller_phone="555-1000")
    manager = ConversationManager()

    res = await manager.handle_input(session, None)
    assert res.new_state["stage"] == "ASK_NAME"

    res = await manager.handle_input(session, "Jane Smith")
    assert res.new_state["stage"] == "ASK_ADDRESS"

    res = await manager.handle_input(session, "123 Main St, Merriam KS")
    assert res.new_state["stage"] == "ASK_PROBLEM"

    res = await manager.handle_input(session, "Leaking faucet in kitchen")
    assert res.new_state["stage"] == "ASK_SCHEDULE"

    res = await manager.handle_input(session, "yes")
    assert res.new_state["stage"] == "CONFIRM_SLOT"
    assert "proposed_slot" in res.new_state

    res = await manager.handle_input(session, "yes")
    assert res.new_state["status"] == "SCHEDULED"

    appts = appointments_repo.list_for_business("default_business")
    assert len(appts) == 1
    return "Scheduled appointment for new caller"


async def scenario_returning_reschedule() -> str:
    """Returning customer is recognized and reschedule queue surfaces item."""
    _reset_repos()
    customer = customers_repo.upsert(
        name="Returning Customer",
        phone="555-2000",
        address="456 Elm St, KC MO",
        business_id="default_business",
    )
    now = datetime.now(UTC)
    appt = appointments_repo.create(
        customer_id=customer.id,
        start_time=now + timedelta(hours=4),
        end_time=now + timedelta(hours=5),
        service_type="Inspection",
        is_emergency=False,
        description="Existing job",
        business_id="default_business",
    )
    appt.status = "PENDING_RESCHEDULE"

    session = CallSession(id="smoke-return", caller_phone=customer.phone)
    manager = ConversationManager()
    res = await manager.handle_input(session, None)
    assert "worked with you before" in res.reply_text.lower()

    client = TestClient(app)
    resp = client.get("/v1/owner/reschedules")
    assert resp.status_code == 200
    data = resp.json()
    assert data["reschedules"]
    return "Returning customer flagged for reschedule; surfaced in owner reschedules"


async def scenario_emergency_flow() -> str:
    """Emergency intent is detected and appointment is tagged as emergency."""
    _reset_repos()
    session = CallSession(id="smoke-emergency", caller_phone="555-3000")
    manager = ConversationManager()

    await manager.handle_input(session, None)  # greeting
    await manager.handle_input(session, "Alex")  # name
    await manager.handle_input(session, "789 Oak St, KC MO")  # address
    res = await manager.handle_input(
        session, "Basement is flooding and sewage backing up"
    )
    assert res.new_state["is_emergency"] is True
    await manager.handle_input(session, "yes")  # accept scheduling
    res = await manager.handle_input(session, "yes")  # confirm slot
    assert res.new_state["status"] == "SCHEDULED"

    appts = appointments_repo.list_for_business("default_business")
    assert len(appts) == 1
    assert appts[0].is_emergency is True
    return "Emergency booking scheduled and tagged"


@dataclass
class Scenario:
    name: str
    func: Callable[[], Any]


async def run_scenarios(scenarios: List[Scenario]) -> List[Result]:
    results: List[Result] = []
    for scenario in scenarios:
        start = time.perf_counter()
        try:
            detail = await scenario.func()
            status = "passed"
        except Exception as exc:  # pragma: no cover - smoke runner
            detail = f"{type(exc).__name__}: {exc}"
            status = "failed"
        duration_ms = (time.perf_counter() - start) * 1000
        results.append(
            {
                "name": scenario.name,
                "status": status,
                "detail": detail,
                "duration_ms": round(duration_ms, 2),
            }
        )
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Closed beta smoke suite")
    parser.add_argument(
        "--output",
        default="smoke-report.json",
        help="Path to write JSON report",
    )
    args = parser.parse_args(argv)

    scenarios = [
        Scenario("happy_path_new_caller", scenario_happy_path),
        Scenario("returning_customer_reschedule", scenario_returning_reschedule),
        Scenario("emergency_flow", scenario_emergency_flow),
    ]

    results = asyncio.run(run_scenarios(scenarios))
    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "passed"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
    }
    report = {"summary": summary, "results": results}

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    for r in results:
        print(f"{r['name']}: {r['status']} ({r['duration_ms']} ms) - {r['detail']}")
    print(f"Summary: {summary['passed']}/{summary['total']} passed")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    sys.exit(main())
