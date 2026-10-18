"""Optional, read-only Mailcow API for the platform operator (docs/email.md#operator-view).

Off unless ``ANUM_MAILCOW_API_ENABLED=true``. The key comes only from settings (a secret,
``ANUM_MAILCOW_API_KEY``; use a Mailcow *read-only* API key); it is never returned, logged
or audited. Only the fixed GET paths below are ever called, and the answer is reduced to
counts and states: the server version, which containers are not running, how many messages
wait in the queue, and per domain how many mailboxes exist and how full the quota is. Queue
items (senders, recipients) and mailbox names are counted, never returned.

This client is used by the operator view only. Reading and sending mail never go through it:
the Email Center talks IMAP and SMTP as each person (``server.py``).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..settings import settings

# The only Mailcow endpoints ANUM may call; all are reads.
READ_PATHS = {
    "version": "/api/v1/get/status/version",
    "containers": "/api/v1/get/status/containers",
    "queue": "/api/v1/get/mailq/all",
    "domains": "/api/v1/get/domain/all",
}


class MailcowSettings(BaseSettings):
    api_enabled: bool = False
    # The Mailcow web host, for example https://mail.example.com (https outside local).
    api_url: str = ""
    api_key: SecretStr = SecretStr("")
    timeout_seconds: float = Field(default=8.0, ge=1, le=60)

    model_config = SettingsConfigDict(env_prefix="ANUM_MAILCOW_", env_file=".env", extra="ignore", hide_input_in_errors=True)


mailcow_settings = MailcowSettings()
# Tests replace this with ``httpx.MockTransport``.
mailcow_transport: httpx.BaseTransport | None = None


class MailcowDomain(BaseModel):
    domain: str
    active: bool
    mailboxes: int
    max_mailboxes: int | None = None
    quota_used_percent: float | None = None


class MailcowStatus(BaseModel):
    """What the operator sees: off, misconfigured, unreachable, or counts and states."""

    state: str  # off | misconfigured | unreachable | refused | ok
    message: str | None = None
    version: str | None = None
    containers_total: int | None = None
    containers_not_running: list[str] = Field(default_factory=list)
    queue_messages: int | None = None
    domains: list[MailcowDomain] = Field(default_factory=list)


def _base_url(cfg: MailcowSettings) -> str | None:
    parts = urlsplit(cfg.api_url.strip())
    local = settings.environment.strip().lower() in {"local", "test"}
    if parts.scheme not in ({"https", "http"} if local else {"https"}) or not parts.hostname:
        return None
    if parts.path not in {"", "/"} or parts.query or parts.fragment or parts.username or parts.password:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _domains(payload: Any) -> list[MailcowDomain]:
    out: list[MailcowDomain] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict) or not item.get("domain_name"):
            continue
        quota, used = _int(item.get("max_quota_for_domain")), _int(item.get("quota_used_in_domain"))
        out.append(MailcowDomain(
            domain=str(item["domain_name"])[:253], active=str(item.get("active", "1")) in {"1", "True", "true"},
            mailboxes=_int(item.get("mboxes_in_domain")) or 0, max_mailboxes=_int(item.get("max_num_mboxes_for_domain")),
            quota_used_percent=round(used * 100 / quota, 1) if quota and used is not None else None,
        ))
    return sorted(out, key=lambda d: d.domain)


def read_status(cfg: MailcowSettings | None = None) -> MailcowStatus:
    """Read the server's state through the read-only API, or say why it was not read."""
    cfg = cfg or mailcow_settings
    if not cfg.api_enabled:
        return MailcowStatus(state="off")
    base = _base_url(cfg)
    key = cfg.api_key.get_secret_value().strip()
    if base is None or not key:
        return MailcowStatus(
            state="misconfigured",
            message="Set ANUM_MAILCOW_API_URL (https, host only) and ANUM_MAILCOW_API_KEY (a read-only key) on the API.",
        )
    answers: dict[str, Any] = {}
    try:
        with httpx.Client(
            base_url=base, timeout=cfg.timeout_seconds, transport=mailcow_transport, follow_redirects=False,
            headers={"X-API-Key": key, "Accept": "application/json"},
        ) as http:
            for name, path in READ_PATHS.items():
                response = http.get(path)
                if response.status_code in {401, 403}:
                    return MailcowStatus(state="refused", message="Mailcow refused the API key. Check it is active and allowed for this API host.")
                if response.status_code != 200:
                    return MailcowStatus(state="unreachable", message=f"Mailcow answered {response.status_code} for {name}.")
                answers[name] = response.json()
    except (httpx.HTTPError, ValueError):
        return MailcowStatus(state="unreachable", message="Mailcow's API could not be reached or did not answer with JSON.")
    containers = answers.get("containers") if isinstance(answers.get("containers"), dict) else {}
    not_running = sorted(
        str(name)[:80] for name, info in containers.items()
        if not (isinstance(info, dict) and str(info.get("state", "")).lower() == "running")
    )
    queue = answers.get("queue")
    version = answers.get("version")
    return MailcowStatus(
        state="ok",
        version=str(version.get("version"))[:60] if isinstance(version, dict) and version.get("version") else None,
        containers_total=len(containers), containers_not_running=not_running,
        queue_messages=len(queue) if isinstance(queue, list) else None,
        domains=_domains(answers.get("domains")),
    )
