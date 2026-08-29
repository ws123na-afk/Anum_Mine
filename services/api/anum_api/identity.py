from __future__ import annotations

import asyncio
import hashlib
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import PyJWKClient
from pydantic import BaseModel, Field

from .schemas import TenantContext


class LocalSessionStore:
    """Process-local development sessions; only token hashes are retained."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[TenantContext, datetime]] = {}
        self._lock = threading.Lock()

    def create(self, context: TenantContext, ttl_minutes: int = 480) -> tuple[str, datetime]:
        token = f"anum_local_{secrets.token_urlsafe(32)}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        with self._lock:
            self._sessions[hashlib.sha256(token.encode()).hexdigest()] = (context, expires_at)
        return token, expires_at

    def resolve(self, token: str) -> TenantContext | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            value = self._sessions.get(digest)
            if value is None:
                return None
            context, expires_at = value
            if expires_at <= datetime.now(timezone.utc):
                self._sessions.pop(digest, None)
                return None
            return context.model_copy(deep=True)

    def revoke(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


local_sessions = LocalSessionStore()


class OidcClaims(BaseModel):
    subject: str
    tenant_id: str
    workspace_id: str
    roles: list[str] = Field(default_factory=list)

    def tenant_context(self) -> TenantContext:
        return TenantContext(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            user_id=self.subject,
            roles=self.roles,
        )


class OidcValidator:
    def __init__(
        self,
        issuer: str,
        audience: str,
        *,
        jwks_client: PyJWKClient | None = None,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.jwks_client = jwks_client or PyJWKClient(
            f"{self.issuer}/protocol/openid-connect/certs",
            cache_keys=True,
        )

    async def validate(self, token: str) -> OidcClaims:
        signing_key = await asyncio.to_thread(self.jwks_client.get_signing_key_from_jwt, token)
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.audience,
            issuer=self.issuer,
            options={"require": ["exp", "iat", "iss", "sub", "aud"]},
        )
        roles = set(payload.get("realm_access", {}).get("roles", []))
        roles.update(payload.get("roles", []))
        tenant_id = payload.get("tenant_id")
        workspace_id = payload.get("workspace_id")
        if not tenant_id or not workspace_id:
            raise jwt.InvalidTokenError("token is missing ANUM tenant or workspace claims")
        return OidcClaims(
            subject=payload["sub"],
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            roles=sorted(roles),
        )
