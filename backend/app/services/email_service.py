from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
import asyncio
from typing import List

import httpx

from ..config import get_settings
from ..services.oauth_tokens import oauth_store
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB


logger = logging.getLogger(__name__)


@dataclass
class SentEmail:
    to: str
    subject: str
    body: str
    business_id: str | None = None
    provider: str = "stub"


@dataclass
class EmailResult:
    sent: bool
    detail: str | None = None
    provider: str = "stub"


class EmailService:
    """Lightweight email sender with Gmail (per-tenant OAuth) or SendGrid support.

    When neither provider is configured, messages are recorded locally for
    observability and treated as stubbed/unsent.
    """

    def __init__(self) -> None:
        self._sent: List[SentEmail] = []

    @property
    def sent_messages(self) -> List[SentEmail]:
        return list(self._sent)

    def _mark_gmail_status(self, business_id: str, status: str) -> None:
        """Best-effort update of Gmail integration status in the DB."""
        if not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
            return
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, business_id)
            if not row:
                return
            row.integration_gmail_status = status  # type: ignore[assignment]
            session.add(row)
            session.commit()
        except Exception:
            logger.warning(
                "email_status_update_failed",
                exc_info=True,
                extra={"business_id": business_id, "status": status},
            )
        finally:
            session.close()

    def _load_gmail_tokens_from_db(self, business_id: str):
        from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
        from ..db_models import BusinessDB

        if not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
            return None
        session = SessionLocal()
        try:
            row = session.get(BusinessDB, business_id)
            if not row:
                return None
            if getattr(row, "gmail_access_token", None) and getattr(
                row, "gmail_refresh_token", None
            ):
                expires_at = (
                    row.gmail_token_expires_at.timestamp()
                    if getattr(row, "gmail_token_expires_at", None)
                    else time.time() + 3600
                )
                return oauth_store.save_tokens(
                    "gmail",
                    business_id,
                    access_token=row.gmail_access_token,
                    refresh_token=row.gmail_refresh_token,
                    expires_in=int(expires_at - time.time()),
                )
        finally:
            session.close()
        return None

    def _encode_message(self, from_email: str, to: str, subject: str, body: str) -> str:
        raw = (
            f"From: {from_email}\r\n"
            f"To: {to}\r\n"
            f"Subject: {subject}\r\n"
            'Content-Type: text/plain; charset="utf-8"\r\n'
            "\r\n"
            f"{body}"
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("utf-8")

    async def _refresh_token_if_needed(
        self, business_id: str, client_id: str | None, client_secret: str | None
    ):
        tok = oauth_store.get_tokens("gmail", business_id) or self._load_gmail_tokens_from_db(business_id)
        if not tok:
            return None
        now = time.time()
        if tok.expires_at - now > 60:
            return tok
        # Try real refresh if credentials are available; otherwise fall back to stub refresh.
        if client_id and client_secret and tok.refresh_token:
            token_url = (
                "https://oauth2.googleapis.com/token"  # nosec B105 - OAuth endpoint
            )
            data = {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": tok.refresh_token,
                "grant_type": "refresh_token",
            }
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.post(token_url, data=data)
                if resp.status_code == 200:
                    payload = resp.json()
                    access_token = payload.get("access_token")
                    expires_in = int(payload.get("expires_in") or 3600)
                    if access_token:
                        return oauth_store.save_tokens(
                            "gmail",
                            business_id,
                            access_token=access_token,
                            refresh_token=tok.refresh_token,
                            expires_in=expires_in,
                        )
            except Exception:
                logger.warning(
                    "email_refresh_failed",
                    exc_info=True,
                    extra={"business_id": business_id},
                )
        # Stub refresh path.
        return oauth_store.refresh("gmail", business_id)

    async def _send_via_sendgrid(
        self,
        to: str,
        subject: str,
        body: str,
        from_email: str,
        api_key: str,
        attempts: int = 3,
    ) -> EmailResult:
        url = "https://api.sendgrid.com/v3/mail/send"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": from_email},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                if 200 <= resp.status_code < 300:
                    return EmailResult(sent=True, detail=None, provider="sendgrid")
                logger.warning(
                    "email_send_failed",
                    extra={
                        "provider": "sendgrid",
                        "status": resp.status_code,
                        "attempt": attempt + 1,
                    },
                )
                if resp.status_code < 500:
                    break
            except Exception:
                logger.warning(
                    "email_send_exception",
                    exc_info=True,
                    extra={"provider": "sendgrid", "attempt": attempt + 1},
                )
            await asyncio.sleep(0.2 * (attempt + 1))
        return EmailResult(
            sent=False, detail="SendGrid send failed", provider="sendgrid"
        )

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        business_id: str | None = None,
        from_email: str | None = None,
    ) -> EmailResult:
        settings = get_settings()
        email_cfg = getattr(settings, "email", None)
        provider = (getattr(email_cfg, "provider", "stub") or "stub").lower()
        from_default = from_email or (
            getattr(email_cfg, "from_email", None) if email_cfg else None
        )
        # Always record locally for observability.
        self._sent.append(
            SentEmail(
                to=to,
                subject=subject,
                body=body,
                business_id=business_id,
                provider=provider,
            )
        )

        if not to:
            return EmailResult(sent=False, detail="Missing recipient", provider="stub")

        if provider == "sendgrid":
            api_key = (
                getattr(email_cfg, "sendgrid_api_key", None) if email_cfg else None
            )
            if not api_key:
                return EmailResult(
                    sent=False, detail="SendGrid API key missing", provider="stub"
                )
            sender = from_default or "no-reply@example.com"
            return await self._send_via_sendgrid(to, subject, body, sender, api_key)

        if provider == "gmail":
            if not business_id:
                return EmailResult(
                    sent=False, detail="Missing business_id", provider="stub"
                )
            gmail_cfg = settings.oauth
            # Pull tokens and refresh if close to expiry.
            try:
                tok = await self._refresh_token_if_needed(
                    business_id,
                    gmail_cfg.google_client_id,
                    gmail_cfg.google_client_secret,
                )
            except KeyError:
                tok = None
            if not tok:
                return EmailResult(
                    sent=False,
                    detail="Gmail tokens not found for tenant",
                    provider="stub",
                )

            sender = from_default or "me"
            raw = self._encode_message(sender, to, subject, body)
            headers = {"Authorization": f"Bearer {tok.access_token}"}
            url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
            attempts = 3
            for attempt in range(attempts):
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.post(
                            url, headers=headers, json={"raw": raw}
                        )
                    if 200 <= resp.status_code < 300:
                        self._mark_gmail_status(business_id, "connected")
                        return EmailResult(sent=True, detail=None, provider="gmail")
                    logger.warning(
                        "email_send_failed",
                        extra={
                            "business_id": business_id,
                            "status": resp.status_code,
                            "body": resp.text,
                            "attempt": attempt + 1,
                        },
                    )
                    if resp.status_code < 500:
                        break
                except Exception:
                    logger.exception(
                        "email_send_exception",
                        extra={
                            "business_id": business_id,
                            "provider": "gmail",
                            "attempt": attempt + 1,
                        },
                    )
                await asyncio.sleep(0.2 * (attempt + 1))
            self._mark_gmail_status(business_id, "error")
            return EmailResult(
                sent=False,
                detail="Gmail send failed",
                provider="gmail",
            )

        # Stub/default path.
        return EmailResult(
            sent=False, detail="Email provider not configured", provider="stub"
        )

    async def notify_owner(
        self,
        subject: str,
        body: str,
        *,
        business_id: str,
        owner_email: str | None = None,
    ) -> EmailResult:
        to = owner_email
        if not to and business_id and get_settings().sms.owner_number:
            # No email configured; signal unsent.
            return EmailResult(
                sent=False, detail="Owner email not configured", provider="stub"
            )
        return await self.send_email(
            to=to or "",
            subject=subject,
            body=body,
            business_id=business_id,
            from_email=to or None,
        )


email_service = EmailService()
