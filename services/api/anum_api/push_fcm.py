"""Firebase Cloud Messaging HTTP v1 client for phone push (docs/notifications.md).

Android and iOS both deliver through FCM (iOS through FCM's APNs bridge, configured in
the Firebase project). The API authenticates with the Firebase project's service
account (``ANUM_FCM_SERVICE_ACCOUNT_JSON``): it signs a short-lived JWT with the
account's private key, exchanges it for an OAuth access token
(``https://www.googleapis.com/auth/firebase.messaging``) and caches that token until a
minute before it expires.

``FcmSender.send`` tries one message at most ``max_attempts`` times. 429, 5xx and
network errors are retried with exponential backoff and full jitter (a ``Retry-After``
header is honoured up to ``MAX_RETRY_AFTER_SECONDS``); a 401 drops the cached access
token and retries; ``UNREGISTERED``, ``INVALID_ARGUMENT`` and ``SENDER_ID_MISMATCH``
mean the device token is no longer usable and the caller removes the device. Nothing in
this module logs or raises with a device token, an access token or the private key.

This module does not import ``anum_api.settings``: the settings validator uses
``service_account_problem`` while the settings module is still loading.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx
import jwt

logger = logging.getLogger("anum.push")

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
DEFAULT_OAUTH_URI = "https://oauth2.googleapis.com/token"
FCM_ENDPOINT = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
MAX_RETRY_AFTER_SECONDS = 60.0
# FCM error codes that mean "remove this token" (firebase.google.com/docs/cloud-messaging/manage-tokens).
INVALID_TOKEN_CODES = frozenset({"UNREGISTERED", "INVALID_ARGUMENT", "SENDER_ID_MISMATCH"})
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class ServiceAccount:
    project_id: str
    client_email: str
    private_key: str = field(repr=False)
    token_uri: str = DEFAULT_OAUTH_URI


def parse_service_account(raw: str) -> ServiceAccount:
    """The Firebase service account from its JSON key file; ``ValueError`` names the problem only."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError("ANUM_FCM_SERVICE_ACCOUNT_JSON must be the service account's JSON key file") from None
    if not isinstance(data, dict):
        raise ValueError("ANUM_FCM_SERVICE_ACCOUNT_JSON must be a JSON object")
    if data.get("type") != "service_account":
        raise ValueError('ANUM_FCM_SERVICE_ACCOUNT_JSON must have "type": "service_account"')
    missing = [key for key in ("project_id", "client_email", "private_key") if not str(data.get(key) or "").strip()]
    if missing:
        raise ValueError("ANUM_FCM_SERVICE_ACCOUNT_JSON is missing " + ", ".join(missing))
    private_key = str(data["private_key"])
    if "PRIVATE KEY-----" not in private_key:
        raise ValueError("ANUM_FCM_SERVICE_ACCOUNT_JSON private_key must be a PEM private key")
    token_uri = str(data.get("token_uri") or DEFAULT_OAUTH_URI)
    if not token_uri.startswith("https://"):
        raise ValueError("ANUM_FCM_SERVICE_ACCOUNT_JSON token_uri must be https")
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        load_pem_private_key(private_key.encode("utf-8"), password=None)
    except (TypeError, ValueError):
        raise ValueError("ANUM_FCM_SERVICE_ACCOUNT_JSON private_key cannot be read") from None
    return ServiceAccount(
        project_id=str(data["project_id"]).strip(),
        client_email=str(data["client_email"]).strip(),
        private_key=private_key,
        token_uri=token_uri,
    )


def service_account_problem(raw: str) -> str | None:
    try:
        parse_service_account(raw)
    except ValueError as exc:
        return str(exc)
    return None


class SendOutcome(StrEnum):
    SENT = "sent"
    UNREGISTERED = "unregistered"
    FAILED = "failed"


@dataclass(frozen=True)
class PushMessage:
    """One notification for one device. ``data`` holds ids only, never content."""

    token: str = field(repr=False)
    title: str
    body: str
    data: Mapping[str, str]
    # Replaces an earlier notification about the same item on the device.
    collapse_id: str | None = None

    def payload(self) -> dict[str, Any]:
        android: dict[str, Any] = {"priority": "HIGH"}
        apns: dict[str, Any] = {
            "headers": {"apns-priority": "10"},
            "payload": {"aps": {"sound": "default"}},
        }
        if self.collapse_id:
            android["notification"] = {"tag": self.collapse_id[:64]}
            apns["headers"]["apns-collapse-id"] = self.collapse_id[:64]
        return {
            "message": {
                "token": self.token,
                "notification": {"title": self.title, "body": self.body},
                "data": dict(self.data),
                "android": android,
                "apns": apns,
            }
        }


class FcmSender:
    def __init__(
        self,
        account: ServiceAccount,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
        max_attempts: int = 4,
        base_backoff: float = 1.0,
        max_backoff: float = 30.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.account = account
        self.max_attempts = max_attempts
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout)
        self._access_token: str | None = None
        self._access_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def endpoint(self) -> str:
        return FCM_ENDPOINT.format(project_id=self.account.project_id)

    async def close(self) -> None:
        await self._client.aclose()

    def backoff_for(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(MAX_RETRY_AFTER_SECONDS, max(0.0, retry_after))
        ceiling = min(self.max_backoff, self.base_backoff * (2 ** max(0, attempt - 1)))
        return ceiling * self._jitter()

    async def _token(self) -> str:
        async with self._token_lock:
            now = self._clock()
            if self._access_token is not None and now < self._access_expires_at - 60:
                return self._access_token
            assertion = jwt.encode(
                {
                    "iss": self.account.client_email,
                    "scope": FCM_SCOPE,
                    "aud": self.account.token_uri,
                    "iat": int(now),
                    "exp": int(now) + 3600,
                },
                self.account.private_key,
                algorithm="RS256",
            )
            response = await self._client.post(
                self.account.token_uri,
                data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
            )
            if response.status_code != 200:
                raise FcmAuthError(response.status_code)
            body = response.json()
            self._access_token = str(body["access_token"])
            self._access_expires_at = now + float(body.get("expires_in", 3600))
            return self._access_token

    def _forget_token(self) -> None:
        self._access_token = None
        self._access_expires_at = 0.0

    async def send(self, message: PushMessage) -> SendOutcome:
        payload = message.payload()
        for attempt in range(1, self.max_attempts + 1):
            retry_after: float | None = None
            try:
                token = await self._token()
                response = await self._client.post(
                    self.endpoint, json=payload, headers={"Authorization": f"Bearer {token}"}
                )
            except FcmAuthError as exc:
                if exc.status not in RETRYABLE_STATUS:
                    logger.warning(
                        "push_auth_failed status=%s",
                        exc.status,
                        extra={"anum_push": {"event": "auth_failed", "status": exc.status}},
                    )
                    return SendOutcome.FAILED
            except httpx.HTTPError as exc:
                logger.info("push_network_error %s", type(exc).__name__)
            else:
                status = response.status_code
                if status == 200:
                    return SendOutcome.SENT
                code = _error_code(response)
                # Only FCM's token error codes remove a device: a bare 404 or 403 can also
                # mean a wrong project, which must not wipe every registered phone.
                if code in INVALID_TOKEN_CODES:
                    return SendOutcome.UNREGISTERED
                if status == 401:
                    self._forget_token()
                elif status in RETRYABLE_STATUS:
                    retry_after = _retry_after(response)
                else:
                    logger.warning(
                        "push_rejected status=%s code=%s",
                        status,
                        code,
                        extra={"anum_push": {"event": "rejected", "status": status, "code": code}},
                    )
                    return SendOutcome.FAILED
            if attempt < self.max_attempts:
                await self._sleep(self.backoff_for(attempt, retry_after))
        return SendOutcome.FAILED


class FcmAuthError(RuntimeError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"FCM access token request failed with HTTP {status}")


def _error_code(response: httpx.Response) -> str | None:
    """The FCM ``errorCode`` (or the Google status) of an error response, if any."""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return None
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and detail.get("errorCode"):
            return str(detail["errorCode"])
    status = error.get("status")
    return str(status) if status else None


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
