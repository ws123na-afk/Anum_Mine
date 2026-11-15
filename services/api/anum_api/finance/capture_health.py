"""Is the iPhone capture working? SMS and Apple Pay ingestion health (docs/finance-matching.md#capture-health).

Read only, from what ingestion already keeps: the device tokens (never the token: name, state, last seen, last
accepted, last refusal code), the device events (codes only, 30 days), the stored messages (status, reason, sender
key and received time; never a body here), the settings (account and Wallet card mappings). Nothing here logs or
returns a token, a message body, a full card or account number, or a balance.

What it cannot see, said plainly: a request with an unknown or wrong token is refused before ANUM knows whose it is,
so it is never recorded against anyone; a message the iPhone Shortcut never sent (the automation did not fire, the
phone was off, iOS asked to confirm) leaves no trace, which is why gaps in capture are shown instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from ..schemas import TenantContext, utc_now

GAP_HOURS = 72  # a longer gap between captured messages is shown
SCAN = 1_000  # messages read for the figures
INTEGRATIONS = (("finance.sms", "sms"), ("finance.wallet", "wallet"))
ACCEPTED = ("posted", "attached", "duplicate")


class DeviceHealth(BaseModel):
    id: str
    name: str
    status: str
    scopes: list[str]
    token_hint: str
    last_seen_at: datetime | None
    last_ok_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None


class ChannelHealth(BaseModel):
    channel: Literal["sms", "wallet"]
    last_captured_at: datetime | None  # a message accepted and recorded or attached
    last_failed_at: datetime | None  # a message that failed to process, or a refused request of a known device
    last_failed_code: str | None
    refused_7d: int  # requests of a known device refused (paused, scope missing, switched off, rate limited)
    awaiting_review: int
    review_reasons: dict[str, int]


class SenderHealth(BaseModel):
    sender: str  # the normalised sender key the phone reported (a bank's short name), never a body
    messages: int
    last_at: datetime


class Gap(BaseModel):
    start: datetime
    end: datetime
    hours: int


class Mapping(BaseModel):
    kind: Literal["card_number", "wallet_card"]
    label: str  # last four digits, or the Wallet card's name
    account_id: str
    account_name: str | None


class CaptureHealth(BaseModel):
    state: Literal["off", "ok", "attention"]
    enabled: bool
    self_test_passed: bool
    devices: list[DeviceHealth]
    channels: list[ChannelHealth]
    unknown_senders: list[SenderHealth]
    unmapped_wallet_cards: int
    mappings: list[Mapping]
    accounts_without_mapping: list[str]  # account names
    gaps: list[Gap]
    problems: list[str]  # codes the clients put into words
    checked_at: datetime


def build(context: TenantContext, *, now: datetime | None = None) -> CaptureHealth:
    from . import sms_service as svc
    from .links_store import open_link_stores
    from .sms_store import DeviceStatus

    moment = now or utc_now()
    with open_link_stores(context) as (finance, sms, links):
        settings = svc.settings_for(sms, context)
        devices = sms.list_devices(context)
        messages, _ = sms.list_messages(context, status=None, limit=SCAN, offset=0)
        summary = {s.integration: s for s in links.event_summary(context, moment - timedelta(days=7))}
        problems_by = {key: links.list_events(context, integration=key, outcome="problem", limit=1, offset=0)[0] for key, _ in INTEGRATIONS}
        accounts = {a.id: a for a in finance.list_accounts(context)}
    channels: list[ChannelHealth] = []
    for key, channel in INTEGRATIONS:
        mine = [m for m in messages if (m.source == "wallet") == (channel == "wallet") and m.source in ("device", "wallet")]
        captured = [m.received_at for m in mine if m.status.value in ACCEPTED]
        failed = [m for m in mine if m.status.value == "failed"]
        event = problems_by[key][0] if problems_by[key] else None
        fail_at, fail_code = (failed[0].received_at, failed[0].reason) if failed else (None, None)
        if event is not None and (fail_at is None or event.created_at > fail_at):
            fail_at, fail_code = event.created_at, event.code
        reasons: dict[str, int] = {}
        for m in mine:
            if m.status.value == "review":
                reasons[m.reason or "review"] = reasons.get(m.reason or "review", 0) + 1
        channels.append(ChannelHealth(channel=channel, last_captured_at=max(captured) if captured else None, last_failed_at=fail_at,
                                      last_failed_code=(fail_code or None) and str(fail_code)[:40],
                                      refused_7d=summary[key].failed_7d if key in summary else 0,
                                      awaiting_review=sum(reasons.values()), review_reasons=reasons))
    senders: dict[str, SenderHealth] = {}
    for m in messages:
        if m.status.value == "review" and m.reason == "unknown_sender" and m.sender:
            found = senders.get(m.sender)
            if found is None:
                senders[m.sender] = SenderHealth(sender=m.sender[:40], messages=1, last_at=m.received_at)
            else:
                found.messages += 1
                found.last_at = max(found.last_at, m.received_at)
    wallet_unmapped = sum(1 for m in messages if m.status.value == "review" and m.reason == "wallet_card_unknown")
    mappings = [Mapping(kind="card_number", label=a.last4, account_id=a.account_id, account_name=getattr(accounts.get(a.account_id), "name", None))
                for a in settings.account_map]
    mappings += [Mapping(kind="wallet_card", label=w.card, account_id=w.account_id, account_name=getattr(accounts.get(w.account_id), "name", None))
                 for w in settings.wallet_cards]
    mapped = {a.account_id for a in settings.account_map} | {w.account_id for w in settings.wallet_cards} | {settings.default_account_id}
    unmapped_accounts = sorted(a.name for a in accounts.values() if getattr(a.kind, "value", a.kind) in ("bank", "card") and a.id not in mapped
                               and not getattr(a, "archived", False))
    stamps = sorted(m.received_at for m in messages if m.source in ("device", "wallet") and m.status.value in ACCEPTED
                    and m.received_at >= moment - timedelta(days=30))
    gaps: list[Gap] = []
    for a, b in zip(stamps, [*stamps[1:], moment], strict=True) if stamps else ():
        hours = int((b - a).total_seconds() // 3600)
        if hours >= GAP_HOURS:
            gaps.append(Gap(start=a, end=b, hours=hours))
    active = [d for d in devices if d.status == DeviceStatus.ACTIVE]
    problems: list[str] = []
    if settings.enabled:
        if settings.self_test_passed_at is None:
            problems.append("self_test_missing")
        if not active:
            problems.append("no_active_device")
        if any(d.last_error for d in active):
            problems.append("device_refused")
        if senders:
            problems.append("unknown_senders")
        if wallet_unmapped:
            problems.append("wallet_cards_unmapped")
        if any(c.awaiting_review for c in channels):
            problems.append("messages_waiting")
        if gaps and gaps[-1].end == moment:
            problems.append("no_recent_capture")
        if any(c.last_failed_at and (c.last_captured_at is None or c.last_failed_at > c.last_captured_at) for c in channels):
            problems.append("last_capture_failed")
    return CaptureHealth(
        state="off" if not settings.enabled else ("attention" if problems else "ok"), enabled=settings.enabled,
        self_test_passed=settings.self_test_passed_at is not None,
        devices=[DeviceHealth(id=d.id, name=d.name, status=d.status.value, scopes=list(d.scopes), token_hint=d.token_hint, last_seen_at=d.last_seen_at,
                              last_ok_at=d.last_ok_at, last_error=d.last_error, last_error_at=d.last_error_at) for d in devices],
        channels=channels, unknown_senders=sorted(senders.values(), key=lambda s: s.last_at, reverse=True)[:20], unmapped_wallet_cards=wallet_unmapped,
        mappings=mappings, accounts_without_mapping=unmapped_accounts[:20], gaps=gaps[-10:], problems=problems, checked_at=moment,
    )


__all__ = ["CaptureHealth", "build"]
