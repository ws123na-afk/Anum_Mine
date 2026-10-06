from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
from jwt import PyJWK
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


LOCAL_ENVIRONMENTS = frozenset({"local", "test"})
SUPPORTED_AUTH_MODES = frozenset({"headers", "oidc"})
ANUM_ROLES = ("owner", "member", "viewer")
_SCOPE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,80}$")


class AuthConfigurationError(RuntimeError):
    """Raised at startup when the authentication configuration is unsafe or invalid."""


def is_local_environment(environment: str) -> bool:
    return environment.strip().lower() in LOCAL_ENVIRONMENTS


def local_sessions_allowed(environment: str, auth_mode: str) -> bool:
    """Local `anum_local_*` bearer sessions exist only for header-mode local development."""
    return is_local_environment(environment) and auth_mode.strip().lower() == "headers"


def validate_auth_configuration(config: Any) -> None:
    """Fail fast when the API would start with development authentication outside local/test."""
    auth_mode = str(config.auth_mode).strip().lower()
    environment = str(config.environment).strip().lower()
    if auth_mode not in SUPPORTED_AUTH_MODES:
        raise AuthConfigurationError(
            f"Unsupported ANUM_AUTH_MODE={config.auth_mode!r}; expected one of "
            f"{sorted(SUPPORTED_AUTH_MODES)}"
        )
    if environment not in LOCAL_ENVIRONMENTS and auth_mode != "oidc":
        raise AuthConfigurationError(
            f"ANUM_AUTH_MODE={config.auth_mode!r} is refused in "
            f"ANUM_ENVIRONMENT={config.environment!r}: header-asserted tenant context and "
            "anum_local_* bearer sessions are development-only and allowed only in "
            f"{sorted(LOCAL_ENVIRONMENTS)}. Set ANUM_AUTH_MODE=oidc."
        )
    if auth_mode == "oidc":
        if not str(config.keycloak_issuer or "").strip():
            raise AuthConfigurationError("ANUM_KEYCLOAK_ISSUER is required when ANUM_AUTH_MODE=oidc")
        if not str(config.oidc_audience or "").strip():
            raise AuthConfigurationError("ANUM_OIDC_AUDIENCE is required when ANUM_AUTH_MODE=oidc")


def is_valid_scope_id(value: str) -> bool:
    return _SCOPE_ID_PATTERN.fullmatch(value) is not None


class TokenValidationError(Exception):
    """A bearer token was rejected. `reason` is safe to return to the client."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class JwksUnavailableError(TokenValidationError):
    """The issuer's key set could not be fetched and no cached keys exist."""


class JwksCache:
    """Fetch and cache an issuer's JSON Web Key Set.

    Keys are cached for `cache_seconds`. A token carrying an unknown `kid` forces one refresh
    (identity-provider key rotation), and forced refreshes are rate limited by
    `min_refresh_seconds` so random `kid` values cannot be used to hammer the identity provider.
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        cache_seconds: float = 300,
        min_refresh_seconds: float = 30,
        timeout_seconds: float = 5,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.jwks_url = jwks_url
        self.cache_seconds = cache_seconds
        self.min_refresh_seconds = min_refresh_seconds
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        self._clock = clock
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None
        self._last_forced_refresh: float | None = None
        self._lock = asyncio.Lock()
        self.fetch_count = 0

    async def get_signing_key(self, kid: str) -> PyJWK:
        async with self._lock:
            now = self._clock()
            if self._fetched_at is None or now - self._fetched_at >= self.cache_seconds:
                await self._refresh()
            key = self._keys.get(kid)
            if key is not None:
                return key
            if (
                self._last_forced_refresh is None
                or now - self._last_forced_refresh >= self.min_refresh_seconds
            ):
                self._last_forced_refresh = now
                await self._refresh()
                key = self._keys.get(kid)
                if key is not None:
                    return key
        raise TokenValidationError("Bearer token signing key is unknown")

    async def _refresh(self) -> None:
        self.fetch_count += 1
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self.timeout_seconds
            ) as client:
                response = await client.get(self.jwks_url, headers={"accept": "application/json"})
                response.raise_for_status()
                document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            if self._keys:
                # Keep serving the last known key set; the next expiry retries the fetch.
                self._fetched_at = self._clock()
                return
            raise JwksUnavailableError("Identity provider signing keys are unavailable") from exc
        entries = document.get("keys", []) if isinstance(document, dict) else []
        keys: dict[str, PyJWK] = {}
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("kid"), str):
                continue
            # Keycloak also publishes encryption keys (use=enc, RSA-OAEP); only RS256
            # signature keys may verify access tokens.
            if entry.get("use", "sig") != "sig" or entry.get("kty") != "RSA":
                continue
            if entry.get("alg", "RS256") != "RS256":
                continue
            try:
                keys[entry["kid"]] = PyJWK(entry, algorithm="RS256")
            except jwt.PyJWTError:
                continue
        self._keys = keys
        self._fetched_at = self._clock()


class OidcClaims(BaseModel):
    subject: str
    tenant_id: str
    workspace_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    # Used only to match email-bound workspace invitations; never for authorization.
    email: str | None = None
    email_verified: bool = False

    @property
    def anum_roles(self) -> list[str]:
        return [role for role in ANUM_ROLES if role in self.roles]

    def tenant_context(self, workspace_id: str | None = None) -> TenantContext:
        resolved_workspace = workspace_id or self.workspace_id
        if not resolved_workspace:
            raise ValueError("workspace is not resolved")
        return TenantContext(
            tenant_id=self.tenant_id,
            workspace_id=resolved_workspace,
            user_id=self.subject,
            roles=self.anum_roles,
        )


class OidcValidator:
    """Validate Keycloak-issued RS256 access tokens for the ANUM API audience."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        *,
        jwks_url: str | None = None,
        jwks: JwksCache | None = None,
        leeway_seconds: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
        cache_seconds: float = 300,
        min_refresh_seconds: float = 30,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.leeway_seconds = leeway_seconds
        self.jwks = jwks or JwksCache(
            jwks_url or f"{self.issuer}/protocol/openid-connect/certs",
            cache_seconds=cache_seconds,
            min_refresh_seconds=min_refresh_seconds,
            transport=transport,
        )

    async def validate(self, token: str) -> OidcClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenValidationError("Bearer token is malformed") from exc
        if header.get("alg") != "RS256":
            raise TokenValidationError("Bearer token algorithm is not allowed")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise TokenValidationError("Bearer token has no key id")
        signing_key = await self.jwks.get_signing_key(kid)
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway_seconds,
                options={"require": ["exp", "iat", "iss", "sub", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenValidationError("Bearer token has expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise TokenValidationError("Bearer token audience is not accepted") from exc
        except jwt.InvalidIssuerError as exc:
            raise TokenValidationError("Bearer token issuer is not accepted") from exc
        except jwt.ImmatureSignatureError as exc:
            raise TokenValidationError("Bearer token is not yet valid") from exc
        except jwt.MissingRequiredClaimError as exc:
            raise TokenValidationError(f"Bearer token is missing the {exc.claim} claim") from exc
        except jwt.PyJWTError as exc:
            raise TokenValidationError("Bearer token signature or claims are invalid") from exc

        tenant_id = payload.get("tenant_id")
        if not isinstance(tenant_id, str) or not is_valid_scope_id(tenant_id):
            raise TokenValidationError("Bearer token is missing a valid tenant_id claim")
        workspace_id = payload.get("workspace_id")
        if workspace_id in ("", None):
            workspace_id = None
        elif not isinstance(workspace_id, str) or not is_valid_scope_id(workspace_id):
            raise TokenValidationError("Bearer token workspace_id claim is invalid")

        roles: set[str] = set()
        realm_access = payload.get("realm_access")
        if isinstance(realm_access, dict) and isinstance(realm_access.get("roles"), list):
            roles.update(role for role in realm_access["roles"] if isinstance(role, str))
        extra_roles = payload.get("roles")
        if isinstance(extra_roles, list):
            roles.update(role for role in extra_roles if isinstance(role, str))
        email = payload.get("email")
        return OidcClaims(
            subject=str(payload["sub"]),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            roles=sorted(role.lower() for role in roles),
            email=email if isinstance(email, str) and email.strip() else None,
            email_verified=payload.get("email_verified") is True,
        )
