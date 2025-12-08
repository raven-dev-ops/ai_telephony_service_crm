from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds


class InMemoryOAuthStore:
    """Lightweight per-provider token store keyed by business_id."""

    def __init__(self) -> None:
        self._tokens: Dict[tuple[str, str], OAuthToken] = {}

    def save_tokens(
        self,
        provider: str,
        business_id: str,
        access_token: str,
        refresh_token: str,
        expires_in: int = 3600,
    ) -> OAuthToken:
        tok = OAuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
        )
        self._tokens[(provider, business_id)] = tok
        return tok

    def get_tokens(self, provider: str, business_id: str) -> Optional[OAuthToken]:
        return self._tokens.get((provider, business_id))

    def refresh(self, provider: str, business_id: str) -> OAuthToken:
        existing = self.get_tokens(provider, business_id)
        if not existing:
            raise KeyError("tokens_missing")
        # Generate a synthetic new access token; reuse refresh token.
        new_access = f"{provider}_access_{int(time.time())}"
        return self.save_tokens(
            provider=provider,
            business_id=business_id,
            access_token=new_access,
            refresh_token=existing.refresh_token,
            expires_in=3600,
        )

    def revoke(self, provider: str, business_id: str) -> None:
        self._tokens.pop((provider, business_id), None)


oauth_store = InMemoryOAuthStore()
