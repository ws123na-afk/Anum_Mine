"""Financial alerts by phone push and email as well as in the app (docs/finance.md#alert-channels).

Per kind channel choice, reach (why a channel cannot be used), each item told once per channel, a transient failure retried
by the next run and a permanent one not, one failed email pausing the person's other emails, nothing private in a push or an
email, and another person's choices and pushes kept apart. Memory backend; FCM and SMTP are faked; every record is SYNTHETIC
and low entropy.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal as D

import pytest
from pydantic import SecretStr
from test_finance_personal_money import ME, OTHER, OWNER, VIEWER, clean, client  # noqa: F401  (``clean`` resets the stores)
from test_push import SERVICE_ACCOUNT, FakeSender

from anum_api import notifications
from anum_api.dependencies import memory_repository
from anum_api.events import CanonicalEventName, create_event
from anum_api.finance import alert_channels as C
from anum_api.finance import alerts as A
from anum_api.finance.models import InstallmentStatementEntry
from anum_api.finance.rules_store import rules_memory_store
from anum_api.finance.store import finance_store
from anum_api.onboarding import NotificationPreferences, _notifications
from anum_api.push import PushKind, intent_for, intent_text, push_device_store, push_dispatcher, resolve_recipients
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings

API = "/api/v1/finance"
PHONE = "fcm-token-example-finance-phone"
ADDRESS = "owner@example.com"


def today() -> date:
    return utc_now().date()


def _now() -> datetime:
    """Today at 08:00 UTC: the same calendar day in UTC and in the Finance time zone (Asia/Dubai), whatever the hour the test
    runs at (late in the UTC evening Dubai is already on the next day, which moved the due dates the fixtures use)."""
    return datetime.combine(today(), time(8), tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    def reset() -> None:
        rules_memory_store.clear()
        push_device_store.clear()
        _notifications.clear()
        memory_repository.store.events[:] = [e for e in memory_repository.store.events if e.type != "finance.alert.raised"]
        with push_dispatcher._lock:
            push_dispatcher._queue.clear()

    reset()
    yield
    reset()


@pytest.fixture
def phone(monkeypatch: pytest.MonkeyPatch) -> FakeSender:
    """Push on for the server, one phone registered for the owner."""
    fake = FakeSender()
    monkeypatch.setattr(settings, "fcm_service_account_json", SecretStr(SERVICE_ACCOUNT))
    monkeypatch.setattr(push_dispatcher, "_sender_factory", lambda: fake)
    monkeypatch.setattr(push_dispatcher, "_sender", None)
    made = client.post("/api/v1/me/devices", headers=OWNER, json={"platform": "android", "token": PHONE, "language": "en"})
    assert made.status_code in (200, 201), made.text
    return fake


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """A mail server that records what it is given; the owner has email on and an address."""
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "anum@example.com")
    monkeypatch.setattr(settings, "smtp_retry_backoff_seconds", 0.0)
    monkeypatch.setattr(notifications, "recipient_address", lambda context, user_id: ADDRESS if user_id == ME.user_id else None)
    monkeypatch.setattr(notifications, "send_email", lambda to, subject, text, *rest: sent.append((to, subject, text)))
    _notifications.save(ME, NotificationPreferences(email_enabled=True))
    return sent


def _bnpl(status: str, due_on: date, installment: int) -> InstallmentStatementEntry:
    return InstallmentStatementEntry(provider="Tamara", order_id="order-example", merchant="Example Store", due_on=due_on, installment=installment,
                                     total_installments=3, amount=D(125), currency="SAR", source_status=status)


def _tamara() -> None:
    """A provider report without a matched bank payment and one installment due tomorrow: two kinds to tell."""
    planning = finance_store.get_planning(ME)
    planning.installment_entries = [_bnpl("paid", today() - timedelta(days=1), 1), _bnpl("scheduled", today() + timedelta(days=1), 2)]
    finance_store.save_planning(ME, planning)


def _alert_events(user: str = ME.user_id) -> list:
    return [e for e in memory_repository.store.events if e.type == "finance.alert.raised" and e.payload.get("recipient") == user]


def _setting(view: dict, kind: str) -> dict:
    return next(k for k in view["kinds"] if k["kind"] == kind)


# -- channel choice -----------------------------------------------------------------------------------------------------


def test_channels_default_and_save_per_kind_and_the_settings_say_why_a_channel_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", None)
    monkeypatch.setattr(settings, "fcm_service_account_json", None)
    view = client.get(f"{API}/alerts/settings", headers=OWNER).json()
    due = _setting(view, "installment_due_tomorrow")
    assert due == {"kind": "installment_due_tomorrow", "on": True, "title": "Installment due tomorrow", "in_app": True, "push": True, "email": False}
    assert _setting(view, "salary_received")["push"] is False  # not time-critical: in the app only until chosen
    assert view["channels"]["in_app"]["available"] is True
    assert view["channels"]["email"] == {"available": False, "reason": "not_configured", "message": C.REASONS["email:not_configured"]}
    assert view["channels"]["push"]["reason"] == "not_configured" and "not set up" in view["channels"]["push"]["message"]
    saved = client.put(f"{API}/alerts/settings", headers=OWNER, json={"channels": {"refund_received": {"email": True, "push": False}}})
    assert saved.status_code == 200, saved.text
    assert {k: v for k, v in _setting(saved.json(), "refund_received").items() if k in C.CHANNELS} == {"in_app": True, "push": False, "email": True}
    # The older body (in-app switches only) still works and leaves the other channels alone.
    older = client.put(f"{API}/alerts/settings", headers=OWNER, json={"kinds": {"refund_received": False}}).json()
    assert {k: v for k, v in _setting(older, "refund_received").items() if k in C.CHANNELS} == {"in_app": False, "push": False, "email": True}
    assert A.settings(ME)["refund_received"] is False and A.channel_settings(ME)["refund_received"]["email"] is True
    assert client.put(f"{API}/alerts/settings", headers=OWNER, json={"channels": {"made_up": {"push": True}}}).status_code == 422
    assert client.put(f"{API}/alerts/settings", headers=OWNER, json={"channels": {"refund_received": {"sms": True}}}).status_code == 422
    assert client.put(f"{API}/alerts/settings", headers=VIEWER, json={"channels": {}}).status_code == 403


def test_reach_names_the_missing_step(monkeypatch: pytest.MonkeyPatch, phone: FakeSender, mailbox: list) -> None:
    assert C.reach(ME) == C.Reach(push=True, push_reason=None, email=ADDRESS, email_reason=None)
    _notifications.save(ME, NotificationPreferences(email_enabled=False, push_enabled=False))
    found = C.reach(ME)
    assert (found.push_reason, found.email_reason) == ("push_off", "email_off")
    other = TenantContext(tenant_id=ME.tenant_id, workspace_id=ME.workspace_id, user_id="other_pm", roles=["owner"])
    _notifications.save(other, NotificationPreferences(email_enabled=True))
    found = C.reach(other)  # no phone of theirs, no address of theirs: the owner's are never used
    assert (found.push, found.push_reason, found.email, found.email_reason) == (False, "no_device", None, "no_address")


# -- delivery, once per channel -----------------------------------------------------------------------------------------


def test_each_item_is_told_once_per_channel_and_push_and_email_carry_no_details(phone: FakeSender, mailbox: list) -> None:
    _tamara()
    client.put(f"{API}/alerts/settings", headers=OWNER, json={"channels": {"installment_due_tomorrow": {"email": True},
                                                                           "unverified_payment": {"email": True}}})
    sent, failed = A.run(ME, now=_now())
    assert failed == 0 and sent == 6  # two kinds, three channels each
    events = _alert_events()
    assert {e.payload["alert_kind"] for e in events} == {"installment_due_tomorrow", "unverified_payment"}
    assert all(e.private_to == ME.user_id and set(e.payload) == {"recipient", "alert_kind", "count", "claims"} for e in events)
    # The claims are opaque references (a digest and a day), never the item keys, which can hold figures.
    assert all(C.push_refs(e.payload["claims"]) == tuple(e.payload["claims"]) for e in events)
    assert len(mailbox) == 2 and all(to == ADDRESS for to, _s, _t in mailbox)
    asyncio.run(push_dispatcher.run_pending())
    assert [m.token for m in phone.messages] == [PHONE, PHONE]
    # Nothing private leaves ANUM: no amount, merchant, provider order, currency or digit.
    for text in [f"{m.title} {m.body} {sorted(m.data.values())}" for m in phone.messages] + [f"{s} {t}" for _to, s, t in mailbox]:
        for secret in ("125", "SAR", "Example Store", "order-example", "Tamara"):
            assert secret not in text, (secret, text)
    assert {m.title for m in phone.messages} == {"Installment due tomorrow", "A payment needs your check"}
    assert all(m.data["anum_kind"] == "finance_alert" and m.data["finance_alert_kind"] in A.KINDS for m in phone.messages)
    # The inbox keeps the details for the person, behind sign in.
    inbox = client.get("/api/v1/notifications", headers=OWNER).json()["items"]
    assert any("125" in n["body"] for n in inbox if n["kind"] == "finance_alerts")
    # A second run the same day tells nothing again, in any channel.
    assert A.run(ME, now=_now()) == (0, 0)
    assert len(_alert_events()) == 2 and len(mailbox) == 2


def test_a_channel_that_is_off_or_unreachable_claims_nothing(monkeypatch: pytest.MonkeyPatch, mailbox: list) -> None:
    _tamara()
    monkeypatch.setattr(settings, "fcm_service_account_json", None)  # push cannot be used: nothing is claimed for it
    client.put(f"{API}/alerts/settings", headers=OWNER, json={"channels": {"unverified_payment": {"in_app": False, "email": False}}})
    sent, failed = A.run(ME, now=_now())
    assert failed == 0 and sent == 1 and _alert_events() == [] and mailbox == []  # only the due installment, in the app
    claims = {(k[3], k[4]) for k in rules_memory_store.notice_claims}
    assert not any(kind in (C.PUSH_CLAIM, C.EMAIL_CLAIM) for kind, _key in claims)
    assert not any(kind == "alert:unverified_payment" for kind, _key in claims)


def test_a_transient_email_failure_is_retried_next_run_a_refusal_is_not_and_one_failure_pauses_the_rest(
        monkeypatch: pytest.MonkeyPatch, mailbox: list) -> None:
    _tamara()
    monkeypatch.setattr(settings, "fcm_service_account_json", None)
    client.put(f"{API}/alerts/settings", headers=OWNER, json={"channels": {k: {"email": True, "in_app": False} for k in A.KINDS}})
    tries: list[str] = []

    def unreachable(to: str, subject: str, text: str, *rest: object) -> None:
        tries.append(subject)
        raise notifications.EmailRefused("The mail server could not be reached (OSError)", transient=True)

    monkeypatch.setattr(notifications, "send_email", unreachable)
    sent, failed = A.run(ME, now=_now())
    # Three bounded tries for the first email, then the person's other emails wait: no retry storm.
    assert (sent, failed) == (0, 1) and len(tries) == 3
    assert not any(k[3] == C.EMAIL_CLAIM for k in rules_memory_store.notice_claims)  # released for the next run
    monkeypatch.setattr(notifications, "send_email", lambda to, subject, text, *rest: mailbox.append((to, subject, text)))
    assert A.run(ME, now=_now()) == (2, 0) and len(mailbox) == 2
    # A refusal (a bad address) is permanent: the claim stays and the next run does not try again.
    rules_memory_store.clear()
    refused: list[str] = []

    def refuse(to: str, subject: str, text: str, *rest: object) -> None:
        refused.append(subject)
        raise notifications.EmailRefused("The mail server refused the recipient address")

    monkeypatch.setattr(notifications, "send_email", refuse)
    assert A.run(ME, now=_now()) == (0, 1) and len(refused) == 1
    assert A.run(ME, now=_now()) == (0, 1) and len(refused) == 2  # the second kind's turn; the first is never retried
    assert A.run(ME, now=_now()) == (0, 0) and len(refused) == 2


def test_a_failed_push_trigger_releases_its_claims(monkeypatch: pytest.MonkeyPatch, phone: FakeSender) -> None:
    _tamara()
    monkeypatch.setattr(C, "send_push", lambda context, kind, count, refs=None: False)
    sent, failed = A.run(ME, now=_now())
    assert failed == 2 and sent == 2  # in the app told, the push trigger failed
    assert not any(k[3] == C.PUSH_CLAIM for k in rules_memory_store.notice_claims)


def test_a_push_fcm_never_took_is_retried_by_the_next_run_a_bounded_number_of_times(phone: FakeSender) -> None:
    from anum_api.push_fcm import SendOutcome

    _tamara()
    client.put(f"{API}/alerts/settings", headers=OWNER, json={"channels": {"unverified_payment": {"push": False}}})
    phone.outcomes[PHONE] = SendOutcome.RETRY_LATER  # FCM's own retries ran out: transient

    def push_claims() -> int:
        return sum(1 for k in rules_memory_store.notice_claims if k[3] == C.PUSH_CLAIM)

    pushes = 0
    for _attempt in range(C.MAX_PUSH_RETRIES):
        A.run(ME, now=_now())
        pushes += 1
        assert push_claims() == 1
        asyncio.run(push_dispatcher.run_pending())
        assert len(phone.messages) == pushes
        assert push_claims() == 0  # released: the next daily run tries once more
    A.run(ME, now=_now())
    asyncio.run(push_dispatcher.run_pending())
    assert len(phone.messages) == C.MAX_PUSH_RETRIES + 1 and push_claims() == 1  # retried enough: it stays told
    assert A.run(ME, now=_now()) == (0, 0)
    asyncio.run(push_dispatcher.run_pending())
    assert len(phone.messages) == C.MAX_PUSH_RETRIES + 1


@pytest.mark.parametrize("outcome", ["sent", "failed", "unregistered"])
def test_a_push_delivered_refused_or_to_a_removed_phone_keeps_its_claim(phone: FakeSender, outcome: str) -> None:
    from anum_api.push_fcm import SendOutcome

    _tamara()
    client.put(f"{API}/alerts/settings", headers=OWNER, json={"channels": {"unverified_payment": {"push": False}}})
    phone.outcomes[PHONE] = SendOutcome(outcome)
    A.run(ME, now=_now())
    asyncio.run(push_dispatcher.run_pending())
    assert sum(1 for k in rules_memory_store.notice_claims if k[3] == C.PUSH_CLAIM) == 1
    assert A.run(ME, now=_now()) == (0, 0)


def test_the_dispatcher_report_releases_only_the_named_claims_of_that_person(phone: FakeSender) -> None:
    other = TenantContext(tenant_id=ME.tenant_id, workspace_id=ME.workspace_id, user_id="other_pm", roles=["owner"])
    day = today()
    with __import__("anum_api.finance.rules_store", fromlist=["open_rule_stores"]).open_rule_stores(ME) as (_f, store):
        store.claim_item_notices(ME, C.PUSH_CLAIM, ["refund_received:refund:a", "refund_received:refund:b"], day, utc_now())
    refs = [C.push_ref("refund_received:refund:a", day), "not-a-ref", "refund_received:refund:b"]
    assert C.push_undelivered(other, refs) == 0  # another person's report never touches the owner's claims
    assert C.push_undelivered(ME, refs) == 1  # only the well-formed reference to a held claim
    held = {k[4] for k in rules_memory_store.notice_claims if k[3] == C.PUSH_CLAIM}
    assert held == {"refund_received:refund:b"}


# -- content ------------------------------------------------------------------------------------------------------------


def test_the_fixed_words_hold_no_figures_and_cover_every_kind() -> None:
    assert set(C.GENERIC) == set(C.GENERIC_AR) == set(A.KINDS)
    for language in ("en", "ar"):
        for kind in A.KINDS:
            title, body = C.generic(kind, language)
            assert title and body and not re.search(r"\d", title + body)
    subject, text = C.email_text("card_payment_due", 3)
    assert subject == "ANUM: Card payment due (3)" and "no amounts, names or account numbers" in text
    assert not re.search(r"\d", text)


def test_the_push_goes_only_to_the_person_it_is_private_to(phone: FakeSender) -> None:
    def event(recipient: str, private_to: str | None, kind: str = "refund_received") -> object:
        return create_event(CanonicalEventName.FINANCE_ALERT_RAISED, ME, f"finance_alert:{kind}", {"recipient": recipient, "alert_kind": kind, "count": 1},
                            private_to=private_to).event

    intent = intent_for(event(ME.user_id, ME.user_id))  # type: ignore[arg-type]
    assert intent is not None and intent.kind == PushKind.FINANCE_ALERT and intent.collapse_id() == "finance_alert:refund_received"
    assert resolve_recipients(intent) == [ME.user_id]
    assert intent_text(intent, "ar")[0] == C.GENERIC_AR["refund_received"][0]
    assert intent_for(event(OTHER["x-user-id"], ME.user_id)) is None  # type: ignore[arg-type]  # names someone it is not private to
    assert intent_for(event(ME.user_id, None)) is None  # type: ignore[arg-type]  # a public event never pushes money
    assert intent_for(event(ME.user_id, ME.user_id, "made_up")) is None  # type: ignore[arg-type]


def test_another_persons_choices_and_alerts_are_their_own(phone: FakeSender) -> None:
    other = TenantContext(tenant_id=ME.tenant_id, workspace_id=ME.workspace_id, user_id="other_pm", roles=["owner"])
    client.put(f"{API}/alerts/settings", headers=OWNER, json={"channels": {"refund_received": {"email": True}}})
    assert A.channel_settings(other)["refund_received"]["email"] is False
    assert _setting(client.get(f"{API}/alerts/settings", headers=OTHER).json(), "refund_received")["email"] is False
    _tamara()
    A.run(ME, now=_now())
    assert A.run(other, now=_now()) == (0, 0)  # the owner's installments are not theirs
    assert _alert_events("other_pm") == []
