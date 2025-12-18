from fastapi.testclient import TestClient

from app.main import app
from app.repositories import conversations_repo
from seed_demo_data import _reset_db, _reset_in_memory, _reset_metrics, seed_demo_data


client = TestClient(app)


def test_seed_demo_data_populates_owner_endpoints() -> None:
    business_id = "seed_demo_test"
    _reset_db(business_id)
    _reset_in_memory(business_id)
    _reset_metrics(business_id)
    try:
        seed_demo_data(business_id, anonymize=True, dry_run=False)

        headers = {"X-Business-ID": business_id}

        service_mix = client.get(
            "/v1/owner/service-mix?days=30", headers=headers
        ).json()
        assert service_mix["total_appointments_30d"] > 0

        time_to_book = client.get(
            "/v1/owner/time-to-book?days=90", headers=headers
        ).json()
        assert time_to_book["overall_samples"] > 0

        conversion = client.get(
            "/v1/owner/conversion-funnel?days=90", headers=headers
        ).json()
        assert conversion["overall_leads"] > 0

        sms_metrics = client.get("/v1/owner/sms-metrics", headers=headers).json()
        assert sms_metrics["total_messages"] > 0

        if hasattr(conversations_repo, "_by_id"):
            review = client.get(
                "/v1/owner/conversations/review", headers=headers
            ).json()
            assert len(review["items"]) > 0
    finally:
        _reset_db(business_id)
        _reset_in_memory(business_id)
        _reset_metrics(business_id)
