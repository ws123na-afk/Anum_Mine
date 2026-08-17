"""OIDC bearer-token validation against a Keycloak-style JWKS.

This module implements the cryptographic core of OIDC resource-server
validation: verifying a JWT access token's RS256 signature against a
JSON Web Key Set (JWKS), and checking its standard claims (`iss`, `aud`,
`exp`, `nbf`). It is deliberately self-contained and additive:

- It is NOT wired into any route or into `dependencies.tenant_context`.
  `oidc_tenant_context` below is an alternate FastAPI dependency with the
  same return shape as `tenant_context`, available for a future cutover
  that someone will decide on separately.
- It does not implement a login/redirect flow. There is no live Keycloak
  instance in this environment to build or test that against. What's
  here — validating an already-issued bearer token offline against a
  JWKS — is fully testable with a locally generated RSA keypair and is
  the part of OIDC that actually protects API requests.

CLAIM-NAME ASSUMPTIONS (NOT YET CONFIRMED):
There is no real Keycloak realm/client configured for ANUM yet, so the
mapping from OIDC claims to `TenantContext` fields below is a reasonable
placeholder, not a contract:

  - `sub`            -> TenantContext.user_id   (standard OIDC claim)
  - `tenant_id`       -> TenantContext.tenant_id  (assumed custom claim,
                          e.g. via a Keycloak protocol mapper)
  - `workspace_id`    -> TenantContext.workspace_id (assumed custom claim)
  - `roles`           -> TenantContext.roles (assumed custom claim, a list
                          of strings; Keycloak's default role claims live
                          under `realm_access.roles` / `resource_access.*.roles`
                          instead, so this will very likely need to change
                          once the actual realm/client mappers exist)

These names MUST be finalized once the real Keycloak realm and client are
configured, in coordination with whoever wires this dependency into routes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import jwt
from fastapi import Depends, Header

from .errors import ApplicationError, ErrorCode
from .schemas import TenantContext
from .settings import settings


ALLOWED_ALGORITHMS = ["RS256"]


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------


class OIDCError(Exception):
    """Base class for all OIDC/JWT validation failures."""


class JWKSFetchError(OIDCError):
    """The JWKS document could not be fetched or was malformed."""


class UnknownKeyError(OIDCError):
    """The token's `kid` does not match any key in the JWKS."""


class MalformedTokenError(OIDCError):
    """The token could not be parsed, or is missing required header/claims."""


class InvalidAlgorithmError(OIDCError):
    """The token uses an algorithm other than the pinned allow-list (e.g. `none`)."""


class TokenExpiredError(OIDCError):
    """The token's `exp` claim is in the past."""


class TokenNotYetValidError(OIDCError):
    """The token's `nbf` claim is in the future."""


class InvalidIssuerError(OIDCError):
    """The token's `iss` claim does not match the expected issuer."""


class InvalidAudienceError(OIDCError):
    """The token's `aud` claim does not match the expected audience."""


class InvalidSignatureError(OIDCError):
    """The token's signature does not verify against the resolved public key."""


# --------------------------------------------------------------------------
# JWKS fetching + caching
# --------------------------------------------------------------------------

#: A JWKS document, as returned by `{issuer}/protocol/openid-connect/certs`:
#: `{"keys": [{"kid": ..., "kty": "RSA", "n": ..., "e": ..., ...}, ...]}`
JWKSDocument = dict[str, Any]

#: Signature of the low-level fetch function, kept tiny and separately
#: mockable so tests never need to perform real network I/O.
JWKSFetcher = Callable[[str], JWKSDocument]


def _http_get_jwks(url: str) -> JWKSDocument:
    """Default JWKS fetcher: a plain HTTP GET returning parsed JSON.

    This is the only place in this module that touches the network. Tests
    should pass a stub/mock `fetcher` to `JWKSClient` instead of monkeypatching
    this function.
    """

    import httpx

    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    return response.json()


class JWKSClient:
    """Fetches and caches a JWKS document, and resolves `kid` -> public key.

    The HTTP fetch is isolated behind the `fetcher` callable so tests can
    supply an in-memory JWKS document without any network access.
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        fetcher: JWKSFetcher = _http_get_jwks,
        cache_ttl_seconds: float = 300.0,
    ) -> None:
        self._jwks_url = jwks_url
        self._fetcher = fetcher
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cached_document: JWKSDocument | None = None
        self._cached_at: float = 0.0

    def _fetch(self) -> JWKSDocument:
        try:
            document = self._fetcher(self._jwks_url)
        except Exception as exc:  # noqa: BLE001 - normalize any transport error
            raise JWKSFetchError(f"Failed to fetch JWKS from {self._jwks_url}: {exc}") from exc

        if not isinstance(document, dict) or "keys" not in document:
            raise JWKSFetchError(
                f"JWKS document from {self._jwks_url} is missing a 'keys' array"
            )
        return document

    def _document(self, *, force_refresh: bool = False) -> JWKSDocument:
        now = time.monotonic()
        is_stale = (now - self._cached_at) > self._cache_ttl_seconds
        if force_refresh or self._cached_document is None or is_stale:
            self._cached_document = self._fetch()
            self._cached_at = now
        return self._cached_document

    @staticmethod
    def _find_jwk(document: JWKSDocument, kid: str) -> dict[str, Any] | None:
        for key in document.get("keys", []):
            if key.get("kid") == kid:
                return key
        return None

    def get_signing_key(self, kid: str) -> Any:
        """Resolve a `kid` to a usable public key object for `jwt.decode`.

        Refreshes the cache once on a cache miss, to tolerate normal key
        rotation, before concluding the `kid` is genuinely unknown.
        """

        document = self._document()
        jwk = self._find_jwk(document, kid)
        if jwk is None:
            document = self._document(force_refresh=True)
            jwk = self._find_jwk(document, kid)
        if jwk is None:
            raise UnknownKeyError(f"No JWKS key found for kid={kid!r}")

        try:
            return jwt.PyJWK(jwk).key
        except jwt.PyJWKError as exc:
            raise JWKSFetchError(f"JWKS key for kid={kid!r} could not be parsed: {exc}") from exc


# --------------------------------------------------------------------------
# Token validation
# --------------------------------------------------------------------------


def validate_token(
    token: str,
    *,
    jwks_client: JWKSClient,
    issuer: str,
    audience: str,
) -> dict[str, Any]:
    """Validate a JWT bearer token and return its decoded claims.

    Verifies the RS256 signature against the JWKS-resolved public key, and
    checks `iss`, `aud`, `exp`, and `nbf`. Raises a typed `OIDCError`
    subclass on any failure; never returns claims for an invalid token.

    Algorithms are pinned to RS256 explicitly (both here and passed to
    `jwt.decode`) so a token asserting `alg: none` (or any other algorithm)
    is always rejected, rather than silently accepted unsigned.
    """

    if not token or token.count(".") != 2:
        raise MalformedTokenError("Token is not a well-formed JWT")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.exceptions.DecodeError as exc:
        raise MalformedTokenError(f"Token header could not be decoded: {exc}") from exc

    alg = header.get("alg")
    if alg not in ALLOWED_ALGORITHMS:
        raise InvalidAlgorithmError(
            f"Unsupported token algorithm {alg!r}; only {ALLOWED_ALGORITHMS} is accepted"
        )

    kid = header.get("kid")
    if not kid:
        raise MalformedTokenError("Token header is missing 'kid'")

    signing_key = jwks_client.get_signing_key(kid)

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key=signing_key,
            algorithms=ALLOWED_ALGORITHMS,
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iat"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError("Token has expired") from exc
    except jwt.ImmatureSignatureError as exc:
        raise TokenNotYetValidError("Token is not yet valid (nbf in the future)") from exc
    except jwt.InvalidIssuerError as exc:
        raise InvalidIssuerError("Token issuer does not match the expected issuer") from exc
    except jwt.InvalidAudienceError as exc:
        raise InvalidAudienceError("Token audience does not match the expected audience") from exc
    except jwt.InvalidSignatureError as exc:
        raise InvalidSignatureError("Token signature verification failed") from exc
    except jwt.InvalidAlgorithmError as exc:
        raise InvalidAlgorithmError(f"Unsupported token algorithm: {exc}") from exc
    except jwt.InvalidTokenError as exc:
        # Catch-all for any other malformed-token case PyJWT surfaces
        # (bad claim types, missing required claims, etc).
        raise MalformedTokenError(f"Token validation failed: {exc}") from exc

    return claims


# --------------------------------------------------------------------------
# FastAPI dependency
# --------------------------------------------------------------------------

_default_jwks_client: JWKSClient | None = None


def resolve_jwks_url() -> str:
    """The JWKS URL to fetch keys from.

    Uses `settings.oidc_jwks_url` if explicitly configured, otherwise
    derives the standard Keycloak JWKS path from `settings.keycloak_issuer`.
    """

    if settings.oidc_jwks_url:
        return settings.oidc_jwks_url
    return f"{settings.keycloak_issuer.rstrip('/')}/protocol/openid-connect/certs"


def get_jwks_client() -> JWKSClient:
    """FastAPI-dependency-friendly accessor for a process-wide `JWKSClient`.

    Lazily constructed from settings on first use and cached thereafter (the
    client itself caches the JWKS document). Tests should not rely on this
    singleton — construct a `JWKSClient` directly with a stub `fetcher`, or
    override this dependency, instead.
    """

    global _default_jwks_client
    if _default_jwks_client is None:
        _default_jwks_client = JWKSClient(
            resolve_jwks_url(),
            cache_ttl_seconds=settings.oidc_jwks_cache_seconds,
        )
    return _default_jwks_client


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise ApplicationError(
            ErrorCode.UNAUTHORIZED,
            "Missing Authorization header",
            status_code=401,
        )

    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        raise ApplicationError(
            ErrorCode.UNAUTHORIZED,
            "Authorization header must be a Bearer token",
            status_code=401,
        )
    return credentials.strip()


async def oidc_tenant_context(
    authorization: str | None = Header(default=None),
    jwks_client: JWKSClient = Depends(get_jwks_client),
) -> TenantContext:
    """OIDC-backed alternative to `dependencies.tenant_context`.

    Extracts a Bearer token from the `Authorization` header, validates it
    against the configured Keycloak issuer/JWKS, and maps its claims onto a
    `TenantContext` (see the module docstring for the claim-name
    assumptions this relies on). Returns the same `TenantContext` shape as
    the stub-header dependency it is meant to eventually replace, so
    downstream code (route handlers, `require_permission`, repositories)
    does not need to change when the cutover happens.

    Not currently used by any route — this dependency exists so the
    cryptographic validation path can be built and tested ahead of an
    actual Keycloak deployment.
    """

    token = _extract_bearer_token(authorization)

    try:
        claims = validate_token(
            token,
            jwks_client=jwks_client,
            issuer=settings.keycloak_issuer,
            audience=settings.oidc_audience,
        )
    except OIDCError as exc:
        raise ApplicationError(
            ErrorCode.UNAUTHORIZED,
            f"Invalid bearer token: {exc}",
            status_code=401,
        ) from exc

    user_id = claims.get("sub")
    tenant_id = claims.get("tenant_id")
    workspace_id = claims.get("workspace_id")
    roles = claims.get("roles") or []

    if not user_id or not tenant_id or not workspace_id:
        raise ApplicationError(
            ErrorCode.UNAUTHORIZED,
            "Token is missing required tenant claims (sub/tenant_id/workspace_id)",
            status_code=401,
        )

    if not isinstance(roles, list):
        roles = [str(roles)]

    return TenantContext(
        tenant_id=str(tenant_id),
        workspace_id=str(workspace_id),
        user_id=str(user_id),
        roles=[str(role) for role in roles],
    )
