"""Optional Mailcow API for the platform operator (docs/email.md#operator-view, #mail-domains).

Off unless ``ANUM_MAILCOW_API_ENABLED=true``. Reads use ``ANUM_MAILCOW_API_KEY`` (a Mailcow
*read-only* API key). Changes (add or remove a domain or an alias) are a second, separate
switch: ``ANUM_MAILCOW_WRITE_ENABLED=true`` with ``ANUM_MAILCOW_API_WRITE_KEY`` (a read-write
key), and they run only from an approved ``change_mail_domains`` call (``domains_api.py``).
Neither key is ever returned, logged or audited. Only the fixed paths below are ever called.

Reads are reduced to counts and states: the server version, which containers are not running,
how many messages wait in the queue, per domain how many mailboxes exist and how full the quota
is, the aliases of the domains (address and destinations) and each domain's DKIM public key
(never the private key). Queue items and mailbox names are counted, never returned.

Reading and sending mail never go through this client: the Email Center talks IMAP and SMTP as
each person (``server.py``).
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
ALIASES_PATH = "/api/v1/get/alias/all"
DKIM_PATH = "/api/v1/get/dkim/{domain}"
# The only Mailcow endpoints that change anything; called with the write key after an approval.
WRITE_PATHS = {
    "add_domain": "/api/v1/add/domain",
    "remove_domain": "/api/v1/delete/domain",
    "add_alias": "/api/v1/add/alias",
    "remove_alias": "/api/v1/delete/alias",
}


class MailcowSettings(BaseSettings):
    api_enabled: bool = False
    # The Mailcow web host, for example https://mail.example.com (https outside local).
    api_url: str = ""
    api_key: SecretStr = SecretStr("")
    timeout_seconds: float = Field(default=8.0, ge=1, le=60)
    # Changes to domains and aliases (always after an approval): off unless switched on with a
    # separate read-write key.
    write_enabled: bool = False
    api_write_key: SecretStr = SecretStr("")
    # The mail server's host name that MX records must point to (for example mail.example.com).
    # Empty: the Email Center's IMAP host, else the API host.
    mail_host: str = ""
    # Each live DNS lookup of the domains page gives up after this long.
    dns_timeout_seconds: float = Field(default=3.0, ge=0.5, le=15)

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


# -- domains, aliases and DKIM (docs/email.md#mail-domains) ---------------------------------


class MailcowAlias(BaseModel):
    id: str
    address: str
    goto: list[str]
    active: bool


class MailcowDkim(BaseModel):
    selector: str
    txt: str
    public_key: str


class MailcowDirectory(BaseModel):
    """The domains and their aliases, or why they could not be read."""

    state: str  # off | misconfigured | unreachable | refused | ok
    message: str | None = None
    domains: list[MailcowDomain] = Field(default_factory=list)
    aliases: list[MailcowAlias] = Field(default_factory=list)


class WriteResult(BaseModel):
    ok: bool
    message: str


def _client(cfg: MailcowSettings, key: str) -> httpx.Client:
    return httpx.Client(
        base_url=_base_url(cfg) or "", timeout=cfg.timeout_seconds, transport=mailcow_transport, follow_redirects=False,
        headers={"X-API-Key": key, "Accept": "application/json"},
    )


def _aliases(payload: Any) -> list[MailcowAlias]:
    out: list[MailcowAlias] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict) or not item.get("address") or item.get("id") is None:
            continue
        goto = [part.strip()[:254] for part in str(item.get("goto") or "").split(",") if part.strip()]
        out.append(MailcowAlias(
            id=str(item["id"])[:40], address=str(item["address"]).strip().lower()[:254], goto=goto[:50],
            active=str(item.get("active", "1")) in {"1", "True", "true"},
        ))
    return sorted(out, key=lambda a: a.address)


def read_directory(cfg: MailcowSettings | None = None) -> MailcowDirectory:
    """Domains and aliases through the read-only key, or why they were not read."""
    cfg = cfg or mailcow_settings
    if not cfg.api_enabled:
        return MailcowDirectory(state="off")
    key = cfg.api_key.get_secret_value().strip()
    if _base_url(cfg) is None or not key:
        return MailcowDirectory(
            state="misconfigured",
            message="Set ANUM_MAILCOW_API_URL (https, host only) and ANUM_MAILCOW_API_KEY (a read-only key) on the API.",
        )
    answers: dict[str, Any] = {}
    try:
        with _client(cfg, key) as http:
            for name, path in (("domains", READ_PATHS["domains"]), ("aliases", ALIASES_PATH)):
                response = http.get(path)
                if response.status_code in {401, 403}:
                    return MailcowDirectory(state="refused", message="Mailcow refused the API key. Check it is active and allowed for this API host.")
                if response.status_code != 200:
                    return MailcowDirectory(state="unreachable", message=f"Mailcow answered {response.status_code} for {name}.")
                answers[name] = response.json()
    except (httpx.HTTPError, ValueError):
        return MailcowDirectory(state="unreachable", message="Mailcow's API could not be reached or did not answer with JSON.")
    return MailcowDirectory(state="ok", domains=_domains(answers.get("domains")), aliases=_aliases(answers.get("aliases")))


def read_dkim(domain: str, cfg: MailcowSettings | None = None) -> MailcowDkim | None:
    """The domain's DKIM selector and public TXT value in Mailcow, or None (none yet, or not read)."""
    cfg = cfg or mailcow_settings
    key = cfg.api_key.get_secret_value().strip()
    if not cfg.api_enabled or _base_url(cfg) is None or not key:
        return None
    try:
        with _client(cfg, key) as http:
            response = http.get(DKIM_PATH.format(domain=domain))
            payload = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict) or not payload.get("dkim_txt"):
        return None
    # The private key, when Mailcow includes it, is dropped here and never leaves this function.
    return MailcowDkim(
        selector=str(payload.get("dkim_selector") or "dkim")[:63], txt=str(payload["dkim_txt"])[:4000],
        public_key="".join(str(payload.get("pubkey") or "").split())[:4000],
    )


def write_problem(cfg: MailcowSettings | None = None) -> str | None:
    """Why domains and aliases cannot be changed from ANUM right now, or None."""
    cfg = cfg or mailcow_settings
    if not cfg.api_enabled:
        return "The Mailcow API is off. Set ANUM_MAILCOW_API_ENABLED=true on the API."
    if _base_url(cfg) is None:
        return "Set ANUM_MAILCOW_API_URL (https, host only) on the API."
    if not cfg.write_enabled:
        return "Changes are off. Set ANUM_MAILCOW_WRITE_ENABLED=true and ANUM_MAILCOW_API_WRITE_KEY (a read-write key) on the API."
    if not cfg.api_write_key.get_secret_value().strip():
        return "Set ANUM_MAILCOW_API_WRITE_KEY (a Mailcow read-write API key) on the API."
    return None


def _answer(payload: Any) -> WriteResult:
    """Mailcow answers a change with a list of {type, msg}; every item must be a success."""
    items = payload if isinstance(payload, list) else [payload]
    texts: list[str] = []
    ok = bool(items)
    for item in items:
        if not isinstance(item, dict):
            ok = False
            continue
        if str(item.get("type", "")).lower() != "success":
            ok = False
        msg = item.get("msg")
        parts = msg if isinstance(msg, list) else [msg]
        texts.append(" ".join(str(part) for part in parts if part is not None))
    message = "; ".join(text for text in texts if text)[:300]
    return WriteResult(ok=ok, message=message or ("Mailcow accepted the change." if ok else "Mailcow refused the change."))


def write(operation: str, body: Any, cfg: MailcowSettings | None = None) -> WriteResult:
    """Run one change with the read-write key. Only the paths in WRITE_PATHS are ever posted to."""
    cfg = cfg or mailcow_settings
    problem = write_problem(cfg)
    if problem:
        return WriteResult(ok=False, message=problem)
    try:
        with _client(cfg, cfg.api_write_key.get_secret_value().strip()) as http:
            response = http.post(WRITE_PATHS[operation], json=body)
            if response.status_code in {401, 403}:
                return WriteResult(ok=False, message="Mailcow refused the write key. Check it is a read-write key allowed for this API host.")
            if response.status_code != 200:
                return WriteResult(ok=False, message=f"Mailcow answered {response.status_code}.")
            return _answer(response.json())
    except (httpx.HTTPError, ValueError):
        return WriteResult(ok=False, message="Mailcow could not be reached or did not answer with JSON. Check the domain in Mailcow before asking again.")


def add_domain(domain: str, cfg: MailcowSettings | None = None) -> WriteResult:
    return write("add_domain", {
        "domain": domain, "description": "Added from ANUM", "active": "1", "aliases": "400", "mailboxes": "10",
        "defquota": "3072", "maxquota": "10240", "quota": "10240", "dkim_selector": "dkim", "key_size": "2048", "restart_sogo": "1",
    }, cfg)


def remove_domain(domain: str, cfg: MailcowSettings | None = None) -> WriteResult:
    return write("remove_domain", [domain], cfg)


def add_alias(address: str, goto: list[str], cfg: MailcowSettings | None = None) -> WriteResult:
    return write("add_alias", {"address": address, "goto": ",".join(goto), "active": "1"}, cfg)


def remove_alias(alias_id: str, cfg: MailcowSettings | None = None) -> WriteResult:
    return write("remove_alias", [alias_id], cfg)
