from app.services import gcp_storage


def test_gcs_health_not_configured_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("GCS_DASHBOARD_BUCKET", raising=False)

    health = gcp_storage.get_gcs_health()
    assert health.configured is False
    assert health.can_connect is False
    assert "GCP_PROJECT_ID or GCS_DASHBOARD_BUCKET not set" in (health.error or "")


def test_gcs_health_reports_library_missing(monkeypatch) -> None:
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("GCS_DASHBOARD_BUCKET", "test-bucket")
    monkeypatch.setattr(gcp_storage, "_HAVE_STORAGE", False)

    health = gcp_storage.get_gcs_health()
    assert health.configured is True
    assert health.library_available is False
    assert health.can_connect is False
    assert "google-cloud-storage library is not installed" in (health.error or "")

