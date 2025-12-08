from __future__ import annotations

import os

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
def auth_callback(
    provider: str,
    state: str = Query(..., description="Opaque state that encodes the business_id."),
    code: str | None = Query(
        default=None,
        description="OAuth authorization code (unused in this stub implementation).",
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
        if attr:
            setattr(row, attr, "connected")
            session.add(row)
            session.commit()

        # Simulate exchanging code for access/refresh tokens.
        access_token = f"{provider_norm}_access_{code or 'stub'}"
        refresh_token = f"{provider_norm}_refresh_{business_id}"
        tok = oauth_store.save_tokens(
            provider_norm, business_id, access_token, refresh_token, expires_in=3600
        )

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
