from urllib.parse import urlparse, parse_qs

import pytest
from fastapi.testclient import TestClient

from app.main import create_app, get_settings
from app.services.oauth_tokens import oauth_store


@pytest.mark.skipif(get_settings().oauth.google_client_id is None, reason="OAuth clients not configured")
def test_google_and_linkedin_oauth_flow(monkeypatch):
    settings = get_settings()
    settings.oauth.google_client_id = settings.oauth.google_client_id or "google-id"
    settings.oauth.linkedin_client_id = settings.oauth.linkedin_client_id or "linkedin-id"
    settings.oauth.state_secret = "test-secret"
    app = create_app()
    client = TestClient(app)

    start = client.get("/auth/gmail/start", params={"business_id": "biz-oauth"})
    assert start.status_code == 200
    url = start.json()["authorization_url"]
    parsed = urlparse(url)
    assert parsed.hostname == "accounts.google.com"
    params = parse_qs(parsed.query)
    assert params["client_id"][0] == settings.oauth.google_client_id
    state = params["state"][0]

    cb = client.get(f"/auth/gmail/callback?state={state}&code=abc123")
    assert cb.status_code == 200
    body = cb.json()
    assert body["connected"] is True
    assert body["access_token"].startswith("gmail_access_")

    refresh = client.post("/auth/gmail/refresh", params={"business_id": "biz-oauth"})
    assert refresh.status_code == 200
    assert refresh.json()["access_token"].startswith("gmail_access_")

    revoke = client.post("/auth/gmail/revoke", params={"business_id": "biz-oauth"})
    assert revoke.status_code == 200
    assert oauth_store.get_tokens("gmail", "biz-oauth") is None

    start_linkedin = client.get("/auth/linkedin/start", params={"business_id": "biz-li"})
    assert start_linkedin.status_code == 200
    url_li = start_linkedin.json()["authorization_url"]
    parsed_li = urlparse(url_li)
    params_li = parse_qs(parsed_li.query)
    assert params_li["client_id"][0] == settings.oauth.linkedin_client_id
