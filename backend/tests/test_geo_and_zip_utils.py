import httpx

from app.services import geo_utils, zip_enrichment


def test_derive_neighborhood_label_prefers_zip_code() -> None:
    assert geo_utils.derive_neighborhood_label("123 Main St, Anytown, 94107") == "94107"


def test_derive_neighborhood_label_uses_trailing_segment_when_no_zip() -> None:
    assert (
        geo_utils.derive_neighborhood_label("456 Oak Ave, Springfield") == "Springfield"
    )


def test_derive_neighborhood_label_falls_back_to_unspecified() -> None:
    assert geo_utils.derive_neighborhood_label("") == "unspecified"
    assert (
        geo_utils.derive_neighborhood_label("No commas or zips here") == "unspecified"
    )


def test_geocode_address_returns_none_without_api_key(monkeypatch) -> None:
    called = {"count": 0}

    def fake_httpx_get(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("httpx.get should not be called without API key")

    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "")
    monkeypatch.setattr(geo_utils, "httpx", type("H", (), {"get": fake_httpx_get}))

    assert geo_utils.geocode_address("123 Main St") is None
    assert geo_utils.geocode_address("   ") is None
    assert called["count"] == 0


def test_fetch_zip_income_rejects_invalid_or_short_zip() -> None:
    profile = zip_enrichment.fetch_zip_income("12")
    assert profile.zip_code == "12"
    assert profile.median_household_income is None
    assert profile.source == "none"
    assert profile.error == "Invalid or empty ZIP code"


def test_fetch_zip_income_parses_valid_response(monkeypatch) -> None:
    # Dummy HTTPX client returning a valid Census-style payload.
    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[list[str]]:
            return [
                ["NAME", "B19013_001E", "state", "zip code tabulation area"],
                ["ZCTA5 94107", "75000", "06", "94107"],
            ]

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        def __enter__(self) -> "DummyClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
            return None

        def get(self, url, params=None):  # type: ignore[no-untyped-def]
            return DummyResponse()

    monkeypatch.setattr(httpx, "Client", DummyClient)

    profile = zip_enrichment.fetch_zip_income("94107")
    assert profile.zip_code == "94107"
    assert profile.median_household_income == 75000
    assert profile.source == "census_acs_2022"
    assert profile.error is None


def test_fetch_zip_income_handles_parse_errors(monkeypatch) -> None:
    # Dummy HTTPX client returning an unexpected payload shape to trigger parse_error.
    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return ["not-a-table"]

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        def __enter__(self) -> "DummyClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
            return None

        def get(self, url, params=None):  # type: ignore[no-untyped-def]
            return DummyResponse()

    monkeypatch.setattr(httpx, "Client", DummyClient)

    profile = zip_enrichment.fetch_zip_income("94107")
    assert profile.zip_code == "94107"
    assert profile.median_household_income is None
    assert profile.source == "census_acs_2022"
    assert profile.error == "parse_error"


def test_haversine_km_basic() -> None:
    km = geo_utils.haversine_km((39.0997, -94.5786), (38.6270, -90.1994))  # KC to STL
    assert 350 <= km <= 420


def test_geocode_address_success_and_cache(monkeypatch):
    calls = {"count": 0}
    geo_utils._GEOCODE_CACHE.clear()  # type: ignore[attr-defined]

    class DummyResponse:
        def __init__(self, status="OK"):
            self._status = status

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": self._status,
                "results": [{"geometry": {"location": {"lat": 1.23, "lng": 4.56}}}],
            }

    def fake_get(url, params=None, timeout=5.0):
        calls["count"] += 1
        return DummyResponse()

    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "demo-key")
    monkeypatch.setattr(geo_utils, "httpx", type("H", (), {"get": fake_get}))

    coords = geo_utils.geocode_address("123 Main St")
    assert coords == (1.23, 4.56)
    # Second call should use cache, not increment calls.
    coords2 = geo_utils.geocode_address("123 Main St")
    assert coords2 == (1.23, 4.56)
    assert calls["count"] == 1


def test_geocode_address_handles_non_ok(monkeypatch):
    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "ZERO_RESULTS", "results": []}

    def fake_get(url, params=None, timeout=5.0):
        return DummyResponse()

    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "demo-key")
    monkeypatch.setattr(geo_utils, "httpx", type("H", (), {"get": fake_get}))

    assert geo_utils.geocode_address("missing") is None
