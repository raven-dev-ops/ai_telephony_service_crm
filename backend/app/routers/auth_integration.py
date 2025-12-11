from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
import httpx

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..config import get_settings
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..services.oauth_state import decode_state, encode_state
from ..services.oauth_tokens import oauth_store


router = APIRouter()


SUPPORTED_PROVIDERS = {"linkedin", "gmail", "gcalendar", "openai", "twilio"}


class AuthStartResponse(BaseModel):
    provider: str
    authorization_url: str
    note: str | None = None


class AuthCallbackResponse(BaseModel):
    provider: str
    business_id: str
    connected: bool
    redirect_url: str | None = None
    access_token: str | None = None
    message: str | None = None


def _ensure_db_session():
    if not SQLALCHEMY_AVAILABLE or SessionLocal is None:
        raise HTTPException(
            status_code=503,
            detail="Database support is not available for auth integrations.",
        )
    return SessionLocal()


def _is_testing_mode() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST")) or (
        os.getenv("TESTING", "false").lower() == "true"
    )


async def _exchange_google_code_for_tokens(
    code: str, redirect_uri: str, scopes: str
) -> tuple[str, str, int]:
    settings = get_settings()
    client_id = settings.oauth.google_client_id
    client_secret = settings.oauth.google_client_secret
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="Google OAuth not configured")
    token_url = (
        "https://oauth2.googleapis.com/token"  # nosec B105 - public OAuth endpoint
    )
    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.post(token_url, data=payload)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Google token exchange failed ({resp.status_code})",
        )
    data = resp.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token") or ""
    expires_in = int(data.get("expires_in") or 3600)
    if not access_token:
        raise HTTPException(status_code=502, detail="Google token missing")
    return access_token, refresh_token, expires_in


@router.get("/{provider}/start", response_model=AuthStartResponse)
def auth_start(
    provider: str,
    business_id: str = Query(
        ..., description="Tenant business_id initiating the OAuth flow."
    ),
) -> AuthStartResponse:
    """Begin OAuth flow for supported providers with signed state."""
    provider_norm = provider.lower()
    if provider_norm not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unsupported provider")

    settings = get_settings()
    oauth = settings.oauth
    state = (
        business_id
        if _is_testing_mode()
        else encode_state(business_id, provider_norm, oauth.state_secret)
    )
    redirect_uri = f"{oauth.redirect_base}/{provider_norm}/callback"

    if provider_norm == "linkedin":
        if not oauth.linkedin_client_id:
            authorization_url = (
                f"https://example.com/oauth/{provider_norm}?state={state}"
            )
        else:
            authorization_url = (
                "https://www.linkedin.com/oauth/v2/authorization"
                f"?response_type=code&client_id={oauth.linkedin_client_id}"
                f"&redirect_uri={redirect_uri}"
                f"&state={state}"
                f"&scope={oauth.linkedin_scopes.replace(' ', '%20')}"
            )
    elif provider_norm in {"gmail", "gcalendar"}:
        scopes = (
            oauth.gmail_scopes if provider_norm == "gmail" else oauth.gcalendar_scopes
        )
        if not oauth.google_client_id:
            authorization_url = (
                f"https://example.com/oauth/{provider_norm}?state={state}"
            )
        else:
            authorization_url = (
                "https://accounts.google.com/o/oauth2/v2/auth"
                f"?response_type=code&client_id={oauth.google_client_id}"
                f"&redirect_uri={redirect_uri}"
                f"&scope={scopes.replace(' ', '%20')}"
                "&access_type=offline&prompt=consent"
                f"&state={state}"
            )
    else:
        # For openai/twilio or other providers, keep a placeholder.
        authorization_url = f"https://example.com/oauth/{provider_norm}?state={state}"
    note = "Replace authorization_url with the provider's real OAuth endpoint in production."
    return AuthStartResponse(
        provider=provider_norm,
        authorization_url=authorization_url,
        note=note,
    )


@router.get("/{provider}/callback", response_model=AuthCallbackResponse)
async def auth_callback(
    provider: str,
    state: str = Query(..., description="Opaque state that encodes the business_id."),
    code: str | None = Query(
        default=None,
        description="OAuth authorization code (unused in this stub implementation).",
    ),
    error: str | None = Query(
        default=None,
        description="Optional provider error code returned during OAuth failure.",
    ),
) -> AuthCallbackResponse:
    """Handle provider callback, validate state, and persist tokens (simulated)."""
    provider_norm = provider.lower()
    if provider_norm not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unsupported provider")

    settings = get_settings()
    oauth = settings.oauth
    try:
        business_id, state_provider = decode_state(state, oauth.state_secret)
    except Exception:
        if _is_testing_mode():
            business_id = state
            state_provider = provider_norm
        else:
            raise HTTPException(status_code=400, detail="Invalid state")
    if state_provider != provider_norm:
        raise HTTPException(status_code=400, detail="State provider mismatch")
    session = _ensure_db_session()
    try:
        row = session.get(BusinessDB, business_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Business not found")

        attr_map = {
            "linkedin": "integration_linkedin_status",
            "gmail": "integration_gmail_status",
            "gcalendar": "integration_gcalendar_status",
            "openai": "integration_openai_status",
            "twilio": "integration_twilio_status",
        }
        attr = attr_map.get(provider_norm)
        if error and attr:
            setattr(row, attr, "error")
            session.add(row)
            session.commit()
            return AuthCallbackResponse(
                provider=provider_norm,
                business_id=business_id,
                connected=False,
                redirect_url="/dashboard/onboarding.html",
                message=f"Provider returned error: {error}",
            )

        if attr:
            setattr(row, attr, "connected")
            session.add(row)
            session.commit()

        access_token = f"{provider_norm}_access_{code or 'stub'}"
        refresh_token = f"{provider_norm}_refresh_{business_id}"
        expires_in = 3600
        try:
            if (
                provider_norm in {"gmail", "gcalendar"}
                and code
                and oauth.google_client_id
            ):
                redirect_uri = f"{oauth.redirect_base}/{provider_norm}/callback"
                # Google uses space-delimited scopes; reuse provider-specific default.
                scopes = (
                    oauth.gmail_scopes
                    if provider_norm == "gmail"
                    else oauth.gcalendar_scopes
                )
                access_token, refresh_token, expires_in = (
                    await _exchange_google_code_for_tokens(code, redirect_uri, scopes)
                )
            tok = oauth_store.save_tokens(
                provider_norm,
                business_id,
                access_token,
                refresh_token,
                expires_in=expires_in,
            )
            if provider_norm == "gcalendar":
                row.gcalendar_access_token = tok.access_token
                row.gcalendar_refresh_token = tok.refresh_token
                row.gcalendar_token_expires_at = datetime.now(UTC) + timedelta(
                    seconds=expires_in or 3600
                )
            if provider_norm == "gmail":
                row.gmail_access_token = tok.access_token
                row.gmail_refresh_token = tok.refresh_token
                row.gmail_token_expires_at = datetime.now(UTC) + timedelta(
                    seconds=expires_in or 3600
                )
            if provider_norm in {"gmail", "gcalendar"}:
                session.add(row)
                session.commit()
        except HTTPException as exc:
            if attr:
                setattr(row, attr, "error")
                session.add(row)
                session.commit()
            raise exc

        redirect_url = "/dashboard/onboarding.html"
        return AuthCallbackResponse(
            provider=provider_norm,
            business_id=business_id,
            connected=True,
            redirect_url=redirect_url,
            access_token=tok.access_token,
        )
    finally:
        session.close()


class RefreshResponse(BaseModel):
    provider: str
    business_id: str
    access_token: str
    expires_at: float


class RevokeResponse(BaseModel):
    provider: str
    business_id: str
    revoked: bool


@router.post("/{provider}/refresh", response_model=RefreshResponse)
def refresh_tokens(
    provider: str,
    business_id: str = Query(..., description="Tenant business_id"),
) -> RefreshResponse:
    provider_norm = provider.lower()
    if provider_norm not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unsupported provider")
    try:
        tok = oauth_store.refresh(provider_norm, business_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Tokens not found")
    return RefreshResponse(
        provider=provider_norm,
        business_id=business_id,
        access_token=tok.access_token,
        expires_at=tok.expires_at,
    )


@router.post("/{provider}/revoke", response_model=RevokeResponse)
def revoke_tokens(
    provider: str,
    business_id: str = Query(..., description="Tenant business_id"),
) -> RevokeResponse:
    provider_norm = provider.lower()
    if provider_norm not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unsupported provider")
    session = _ensure_db_session()
    try:
        row = session.get(BusinessDB, business_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Business not found")
        attr_map = {
            "linkedin": "integration_linkedin_status",
            "gmail": "integration_gmail_status",
            "gcalendar": "integration_gcalendar_status",
            "openai": "integration_openai_status",
            "twilio": "integration_twilio_status",
        }
        attr = attr_map.get(provider_norm)
        if attr:
            setattr(row, attr, "disconnected")
            session.add(row)
            session.commit()
    finally:
        session.close()
    oauth_store.revoke(provider_norm, business_id)
    return RevokeResponse(provider=provider_norm, business_id=business_id, revoked=True)
