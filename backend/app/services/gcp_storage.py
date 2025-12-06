from __future__ import annotations

import os
from dataclasses import dataclass

try:  # Optional dependency; health checks degrade gracefully when missing.
    from google.cloud import storage

    _HAVE_STORAGE = True
except Exception:  # pragma: no cover - library not installed
    storage = None
    _HAVE_STORAGE = False


@dataclass
class GcsHealth:
    configured: bool
    project_id: str | None
    bucket_name: str | None
    library_available: bool
    can_connect: bool
    error: str | None = None


def get_gcs_health(timeout_seconds: float = 3.0) -> GcsHealth:
    """Best-effort health check for Google Cloud Storage.

    This consults environment variables to determine configuration and, when
    possible, performs a lightweight bucket lookup using the Storage client.
    It is intentionally defensive: missing libraries, credentials, or
    connectivity result in a non-fatal error string rather than exceptions.
    """
    project_id = os.getenv("GCP_PROJECT_ID") or None
    bucket_name = os.getenv("GCS_DASHBOARD_BUCKET") or None

    configured = bool(project_id and bucket_name)
    if not configured:
        return GcsHealth(
            configured=False,
            project_id=project_id,
            bucket_name=bucket_name,
            library_available=_HAVE_STORAGE,
            can_connect=False,
            error="GCP_PROJECT_ID or GCS_DASHBOARD_BUCKET not set",
        )

    if not _HAVE_STORAGE:
        return GcsHealth(
            configured=True,
            project_id=project_id,
            bucket_name=bucket_name,
            library_available=False,
            can_connect=False,
            error="google-cloud-storage library is not installed",
        )

    try:  # pragma: no cover - exercised in real environments
        # The client will use default credentials (service account/workload
        # identity) when available.
        client = storage.Client(project=project_id)
        # lookup_bucket is a lightweight existence check compared to listing.
        bucket = client.lookup_bucket(bucket_name)
        if bucket is None:
            return GcsHealth(
                configured=True,
                project_id=project_id,
                bucket_name=bucket_name,
                library_available=True,
                can_connect=False,
                error="Bucket not found or not accessible",
            )
        return GcsHealth(
            configured=True,
            project_id=project_id,
            bucket_name=bucket_name,
            library_available=True,
            can_connect=True,
            error=None,
        )
    except Exception as exc:  # pragma: no cover - defensive in prod
        return GcsHealth(
            configured=True,
            project_id=project_id,
            bucket_name=bucket_name,
            library_available=True,
            can_connect=False,
            error=str(exc),
        )
