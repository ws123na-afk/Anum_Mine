"""Brevo API v3 over REST (docs/brevo.md#how-anum-talks-to-brevo).

Every call ANUM makes to Brevo goes through :class:`BrevoClient`:

* **One fixed host.** The base URL is ``https://api.brevo.com/v3`` and never comes from
  input; paths are constants with integer ids only, redirects are not followed (threat
  model G1, SSRF).
* **The key stays in one header.** The workspace's API key is held as a ``SecretStr`` and
  only ever placed in the ``api-key`` header. Errors carry the HTTP status, Brevo's error
  code and a sentence safe to show, never the request headers, body or key.
* **Bounded.** A short timeout per call, compressed answers refused (``Accept-Encoding:
  identity``) and the body read up to :data:`MAX_RESPONSE_BYTES`; a larger answer fails.
* **Untrusted output.** Everything Brevo returns (list names, campaign names, subjects) is
  data from outside ANUM: the parsers keep only the fields ANUM shows, cut every string to
  a bound, and the tools label it before it reaches a model (``prompt_provenance``).

Tests replace :data:`transport` with an ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import httpx
from pydantic import SecretStr

BASE_URL = "https://api.brevo.com/v3"
HOST = "api.brevo.com"
TIMEOUT_SECONDS = 15.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TEXT = 300
# Brevo's own page size cap for lists and campaigns.
MAX_LIMIT = 50
# A v3 API key ("xkeysib-..."). An SMTP key ("xsmtpsib-...") does not work for the API.
KEY_PATTERN = re.compile(r"^xkeysib-[A-Za-z0-9-]{16,200}$")
CAMPAIGN_STATUSES = ("draft", "sent", "archive", "queued", "suspended", "in_process", "scheduled")

# Tests replace this with an ``httpx.MockTransport``; production uses the network.
transport: httpx.AsyncBaseTransport | None = None

ERROR_EXPLANATIONS: dict[int, str] = {
    400: "Brevo refused the request as invalid.",
    401: "Brevo did not accept the API key. Create a new API key in Brevo (SMTP & API, API keys) and save it again.",
    402: "The Brevo account has no credits or plan for this. Check the plan and email credits in Brevo.",
    403: "The API key is not allowed to do this. Check the key's permissions and the account's authorised IPs in Brevo.",
    404: "Brevo does not have this item any more. Refresh and try again.",
    429: "Brevo's rate limit was reached. Wait a minute and try again.",
}


class BrevoError(Exception):
    """A Brevo call failed. ``message`` is safe to show; ``code`` is Brevo's error code."""

    def __init__(self, status: int, message: str, code: str | None = None) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)

    @property
    def auth_failed(self) -> bool:
        return self.status in {401, 403}

    @property
    def retryable(self) -> bool:
        return self.status in {0, 429} or self.status >= 500


def valid_key(value: str) -> bool:
    return bool(KEY_PATTERN.match(value.strip()))


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return str(value or "").strip()[:limit]


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class Account:
    email: str
    first_name: str
    last_name: str
    company: str
    plan: str
    email_credits: int | None


@dataclass(frozen=True)
class ContactList:
    id: int
    name: str
    subscribers: int
    blacklisted: int
    folder_id: int | None


@dataclass(frozen=True)
class Sender:
    id: int
    name: str
    email: str
    active: bool


@dataclass(frozen=True)
class CampaignStats:
    sent: int = 0
    delivered: int = 0
    unique_views: int = 0
    unique_clicks: int = 0
    unsubscriptions: int = 0
    hard_bounces: int = 0
    soft_bounces: int = 0
    complaints: int = 0

    @property
    def open_rate(self) -> float | None:
        return round(self.unique_views / self.delivered * 100, 2) if self.delivered else None

    @property
    def click_rate(self) -> float | None:
        return round(self.unique_clicks / self.delivered * 100, 2) if self.delivered else None

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "open_rate": self.open_rate, "click_rate": self.click_rate}


@dataclass(frozen=True)
class Campaign:
    id: int
    name: str
    subject: str
    status: str
    sender_name: str
    sender_email: str
    list_ids: list[int]
    scheduled_at: str | None
    sent_at: str | None
    created_at: str | None
    tag: str
    stats: CampaignStats = field(default_factory=CampaignStats)
    list_stats: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stats"] = self.stats.as_dict()
        return data


def _stats(raw: Any) -> CampaignStats:
    data = raw if isinstance(raw, dict) else {}
    return CampaignStats(
        sent=_int(data.get("sent")), delivered=_int(data.get("delivered")), unique_views=_int(data.get("uniqueViews")),
        unique_clicks=_int(data.get("uniqueClicks")), unsubscriptions=_int(data.get("unsubscriptions")),
        hard_bounces=_int(data.get("hardBounces")), soft_bounces=_int(data.get("softBounces")), complaints=_int(data.get("complaints")),
    )


def _iso(value: Any) -> str | None:
    text = _text(value, 40)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def parse_campaign(raw: Any) -> Campaign:
    data = raw if isinstance(raw, dict) else {}
    sender = data.get("sender") if isinstance(data.get("sender"), dict) else {}
    recipients = data.get("recipients") if isinstance(data.get("recipients"), dict) else {}
    statistics = data.get("statistics") if isinstance(data.get("statistics"), dict) else {}
    list_stats = []
    for item in (statistics.get("campaignStats") or [])[:20] if isinstance(statistics.get("campaignStats"), list) else []:
        if isinstance(item, dict):
            stats = _stats(item)
            list_stats.append({"list_id": _int(item.get("listId")), **stats.as_dict()})
    status = _text(data.get("status"), 20).lower()
    return Campaign(
        id=_int(data.get("id")), name=_text(data.get("name")), subject=_text(data.get("subject")),
        status=status if status in CAMPAIGN_STATUSES else "unknown",
        sender_name=_text(sender.get("name")), sender_email=_text(sender.get("email"), 254),
        list_ids=[_int(item) for item in (recipients.get("lists") or [])[:50] if isinstance(recipients.get("lists"), list)],
        scheduled_at=_iso(data.get("scheduledAt")), sent_at=_iso(data.get("sentDate")), created_at=_iso(data.get("createdAt")),
        tag=_text(data.get("tag"), 120), stats=_stats(statistics.get("globalStats")), list_stats=list_stats,
    )


class BrevoClient:
    """Brevo API v3 for one workspace's API key."""

    def __init__(self, api_key: SecretStr) -> None:
        self._key = api_key

    def _headers(self) -> dict[str, str]:
        return {
            "api-key": self._key.get_secret_value(),
            "accept": "application/json",
            "content-type": "application/json",
            "accept-encoding": "identity",
            "user-agent": "ANUM/brevo",
        }

    async def _call(self, method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> Any:
        # ``path`` is one of this module's constants with integer ids; never input text.
        url = f"{BASE_URL}{path}"
        try:
            async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
                async with client.stream(method, url, params=params, headers=self._headers(),
                                         content=json.dumps(body).encode("utf-8") if body is not None else None) as response:
                    if (response.headers.get("content-encoding") or "identity").lower() not in {"identity", ""}:
                        raise BrevoError(502, "Brevo sent a compressed answer ANUM does not read. Try again later.")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise BrevoError(502, "Brevo's answer was larger than ANUM reads. Ask for fewer items.")
                        chunks.append(chunk)
                    status = response.status_code
        except httpx.TimeoutException as exc:
            raise BrevoError(0, "Brevo did not answer in time. Try again in a minute.") from exc
        except httpx.HTTPError as exc:
            raise BrevoError(0, "ANUM could not reach Brevo. Check the network and try again.") from exc
        raw = b"".join(chunks)
        payload: Any = None
        if raw:
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = None
        if 300 <= status < 400:
            raise BrevoError(502, "Brevo answered with a redirect, which ANUM does not follow.")
        if status >= 400:
            code = _text(payload.get("code"), 60) if isinstance(payload, dict) else None
            base = ERROR_EXPLANATIONS.get(status) or ("Brevo is having trouble. Try again later." if status >= 500 else "Brevo refused the request.")
            detail = _text(payload.get("message"), 200) if isinstance(payload, dict) else ""
            # Brevo's own message helps with 400s (a missing field); it is shown as data, never as HTML.
            message = f"{base} Brevo said: {detail}" if detail and status in {400, 402} else base
            raise BrevoError(status, message, code)
        return payload

    async def account(self) -> Account:
        data = await self._call("GET", "/account")
        data = data if isinstance(data, dict) else {}
        plans = data.get("plan") if isinstance(data.get("plan"), list) else []
        plan = next((item for item in plans if isinstance(item, dict)), {})
        credits = plan.get("credits") if plan.get("creditsType") == "sendLimit" else None
        return Account(
            email=_text(data.get("email"), 254), first_name=_text(data.get("firstName"), 80), last_name=_text(data.get("lastName"), 80),
            company=_text(data.get("companyName"), 160), plan=_text(plan.get("type"), 40), email_credits=_int(credits) if credits is not None else None,
        )

    async def lists(self, *, limit: int = MAX_LIMIT, offset: int = 0) -> tuple[list[ContactList], int]:
        params = {"limit": max(1, min(limit, MAX_LIMIT)), "offset": max(0, offset), "sort": "desc"}
        data = await self._call("GET", "/contacts/lists", params=params)
        data = data if isinstance(data, dict) else {}
        items = [
            ContactList(
                id=_int(item.get("id")), name=_text(item.get("name")), subscribers=_int(item.get("uniqueSubscribers") or item.get("totalSubscribers")),
                blacklisted=_int(item.get("totalBlacklisted")), folder_id=_int(item.get("folderId")) or None,
            )
            for item in (data.get("lists") or [])[:MAX_LIMIT] if isinstance(item, dict) and _int(item.get("id"))
        ] if isinstance(data.get("lists"), list) else []
        return items, _int(data.get("count"))

    async def contact_list(self, list_id: int) -> ContactList:
        data = await self._call("GET", f"/contacts/lists/{int(list_id)}")
        data = data if isinstance(data, dict) else {}
        return ContactList(
            id=_int(data.get("id")), name=_text(data.get("name")), subscribers=_int(data.get("uniqueSubscribers") or data.get("totalSubscribers")),
            blacklisted=_int(data.get("totalBlacklisted")), folder_id=_int(data.get("folderId")) or None,
        )

    async def senders(self) -> list[Sender]:
        data = await self._call("GET", "/senders")
        data = data if isinstance(data, dict) else {}
        return [
            Sender(id=_int(item.get("id")), name=_text(item.get("name"), 120), email=_text(item.get("email"), 254), active=item.get("active") is True)
            for item in (data.get("senders") or [])[:200] if isinstance(item, dict) and _int(item.get("id"))
        ] if isinstance(data.get("senders"), list) else []

    async def campaigns(self, *, limit: int = 20, offset: int = 0, status: str | None = None) -> tuple[list[Campaign], int]:
        params: dict[str, Any] = {"limit": max(1, min(limit, MAX_LIMIT)), "offset": max(0, offset), "sort": "desc", "statistics": "globalStats"}
        if status in CAMPAIGN_STATUSES:
            params["status"] = status
        data = await self._call("GET", "/emailCampaigns", params=params)
        data = data if isinstance(data, dict) else {}
        raw = data.get("campaigns") if isinstance(data.get("campaigns"), list) else []
        return [parse_campaign(item) for item in raw[:MAX_LIMIT] if isinstance(item, dict)], _int(data.get("count"))

    async def campaign(self, campaign_id: int) -> Campaign:
        data = await self._call("GET", f"/emailCampaigns/{int(campaign_id)}", params={"statistics": "globalStats,campaignStats"})
        return parse_campaign(data)

    @staticmethod
    def _content(*, name: str, subject: str, sender_id: int, html: str, list_ids: list[int], tag: str, scheduled_at: datetime | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name, "subject": subject, "sender": {"id": int(sender_id)}, "htmlContent": html,
            "recipients": {"listIds": [int(item) for item in list_ids]}, "tag": tag,
        }
        if scheduled_at is not None:
            body["scheduledAt"] = scheduled_at.isoformat()
        return body

    async def create_campaign(self, *, name: str, subject: str, sender_id: int, html: str, list_ids: list[int], tag: str,
                              scheduled_at: datetime | None = None) -> int:
        data = await self._call("POST", "/emailCampaigns", body=self._content(
            name=name, subject=subject, sender_id=sender_id, html=html, list_ids=list_ids, tag=tag, scheduled_at=scheduled_at))
        campaign_id = _int(data.get("id")) if isinstance(data, dict) else 0
        if not campaign_id:
            raise BrevoError(502, "Brevo did not return the new campaign's id. Check Email campaigns in Brevo before trying again.")
        return campaign_id

    async def update_campaign(self, campaign_id: int, *, name: str, subject: str, sender_id: int, html: str, list_ids: list[int], tag: str,
                              scheduled_at: datetime | None = None) -> None:
        await self._call("PUT", f"/emailCampaigns/{int(campaign_id)}", body=self._content(
            name=name, subject=subject, sender_id=sender_id, html=html, list_ids=list_ids, tag=tag, scheduled_at=scheduled_at))

    async def send_now(self, campaign_id: int) -> None:
        await self._call("POST", f"/emailCampaigns/{int(campaign_id)}/sendNow")

    async def send_test(self, campaign_id: int, email: str) -> None:
        await self._call("POST", f"/emailCampaigns/{int(campaign_id)}/sendTest", body={"emailTo": [email]})
