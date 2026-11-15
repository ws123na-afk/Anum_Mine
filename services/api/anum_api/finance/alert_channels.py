"""Where each financial alert goes: in the app, to the phone, by email (docs/finance.md#alert-channels).

The person picks the channels per alert kind in Settings, Notifications, Financial alerts (``GET/PUT
/finance/alerts/settings``). The choices live in the same ``finance_alert_settings`` row as the in-app switches
(migration ``0186_pf_debts``, its ``kinds`` jsonb): the in-app switch keeps its bare kind key, the other channels use
``push:<kind>`` and ``email:<kind>``. No migration was needed.

* **In app** (default on for every kind): the inbox row ``finance_alerts`` with the full wording, as before.
* **Phone push** (default on for the time-critical kinds in ``PUSH_DEFAULT``): one ``finance.alert.raised`` event,
  private to the person, that ``push.PushDispatcher`` turns into one push per kind. It also needs the server's FCM key,
  the person's own ``push_enabled`` switch and a registered phone. FCM retries a transient failure itself (bounded,
  ``ANUM_PUSH_MAX_ATTEMPTS``); when no phone took it and FCM gave up only for now, the dispatcher reports it back
  (``push_undelivered``) and the claims are released for the next daily run, at most ``MAX_PUSH_RETRIES`` times per item.
* **Email** (default off: the person opts in): needs the mail server (``notifications.email_configured``), the person's
  ``email_enabled`` switch and an address ANUM knows. Sent with ``notifications.send_with_retries`` (three tries with
  backoff for a transient failure), never inside a transaction, in the person's email language (``calendar_email_language``,
  the one calendar reminder emails use: English, Arabic, or English followed by Arabic).

**Nothing private leaves ANUM.** A push or an email carries only the fixed text of its kind (``GENERIC`` and
``GENERIC_AR``) and a link into ANUM: never an amount, a merchant or lender name, an account or card number, a date of a
payment or a person's name. The details stay in the app, behind sign-in (and the app lock on the phone).

**Each item is told once per channel.** Before a channel tells an item it claims it in ``finance_notice_claims``
(claim kind ``alert:<kind>`` in the app, ``alert:push`` and ``alert:email`` with the item key prefixed by its kind for
the others), so a rerun on the same day, or a second worker, tells nothing again. A channel that cannot be reached
(no FCM key, no phone, no mail server, the switch off) claims nothing. **Retry without a storm**: a transient failure
releases the claims so the next daily run tries once more (items age out: recent events after 3 days, standing ones
after their week); a permanent one (the mail server refused the address) keeps them. After one email fails in a run,
the rest of that person's emails wait for the next run.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from ..schemas import TenantContext

logger = logging.getLogger("anum_api.finance.alert_channels")

Channel = Literal["in_app", "push", "email"]
CHANNELS: tuple[Channel, ...] = ("in_app", "push", "email")
EVENT_SUBJECT = "finance_alert"
PUSH_CLAIM = "alert:push"
EMAIL_CLAIM = "alert:email"
# A push FCM never took (every phone failed transiently after FCM's own retries) is tried again by the next daily run, at most
# this many times per item: each retry claims a slot ``<n>:<digest>`` under this kind for the item's day.
PUSH_RETRY_CLAIM = "alert:push_retry"
MAX_PUSH_RETRIES = 2
MAX_PUSH_REFS = 50
_REF = re.compile(r"^[0-9a-f]{24}@\d{4}-\d{2}-\d{2}$")
# Time-critical kinds that buzz the phone unless the person turns them off.
PUSH_DEFAULT = frozenset({
    "installment_due_tomorrow", "installment_paid", "unverified_payment", "card_payment_due", "loan_due_soon", "loan_installment_missed",
    "budget_nearly_exhausted", "refund_received", "source_stopped", "capture_failed",
})

# Fixed words per kind, the only text a push or an email carries: no amount, name, account digit or date.
GENERIC: dict[str, tuple[str, str]] = {
    "installment_due_tomorrow": ("Installment due tomorrow", "A buy now, pay later installment is due tomorrow."),
    "installment_paid": ("Installment paid", "A bank payment was matched to one of your installments."),
    "unverified_payment": ("A payment needs your check", "A payment is waiting for your check before it counts as paid."),
    "card_payment_due": ("Card payment due", "A credit card payment is due soon."),
    "loan_due_soon": ("Loan payment due soon", "A loan payment is due in the next few days."),
    "loan_installment_missed": ("A loan payment was missed", "A loan payment that was due is not matched to a payment."),
    "loan_payment_linked": ("A loan payment was linked", "A payment was linked to one of your loans automatically."),
    "source_stopped": ("An account stopped reporting", "One of your accounts has sent no transactions for a while."),
    "capture_failed": ("A bank message or statement could not be read", "A bank message or a statement import could not be read, so it is not in your records yet."),
    "budget_nearly_exhausted": ("Budget alert", "A budget is nearly used up or over this month."),
    "unexpected_bank_charge": ("Bank charge", "A bank fee or interest was charged."),
    "suspected_duplicate": ("Possible duplicate payment", "A payment may have been recorded twice."),
    "refund_received": ("Refund received", "Money was given back to one of your accounts."),
    "new_recurring_charge": ("New recurring charge", "A charge that repeats was found."),
    "salary_received": ("Salary received", "This month's salary arrived."),
    "salary_differs": ("Salary differs", "This month's salary differs from what was expected."),
    "lender_balance_differs": ("A lender's balance differs", "A lender's balance differs from your records."),
}
GENERIC_AR: dict[str, tuple[str, str]] = {
    "installment_due_tomorrow": ("قسط مستحق غدًا", "قسط اشترِ الآن وادفع لاحقًا مستحق غدًا."),
    "installment_paid": ("تم دفع قسط", "تمت مطابقة دفعة بنكية مع أحد أقساطك."),
    "unverified_payment": ("دفعة تحتاج إلى مراجعتك", "دفعة تنتظر مراجعتك قبل أن تُحسب مدفوعة."),
    "card_payment_due": ("دفعة بطاقة مستحقة", "دفعة بطاقة ائتمان مستحقة قريبًا."),
    "loan_due_soon": ("دفعة قرض مستحقة قريبًا", "دفعة قرض مستحقة خلال الأيام القليلة القادمة."),
    "loan_installment_missed": ("فاتت دفعة قرض", "دفعة قرض كانت مستحقة ولم تُطابق مع أي دفعة."),
    "loan_payment_linked": ("تم ربط دفعة قرض", "تم ربط دفعة بأحد قروضك تلقائيًا."),
    "source_stopped": ("حساب توقف عن الإرسال", "أحد حساباتك لم يرسل أي معاملات منذ مدة."),
    "capture_failed": ("تعذرت قراءة رسالة بنك أو كشف حساب", "تعذرت قراءة رسالة من البنك أو كشف حساب مستورد، لذلك لم يُسجل بعد في سجلاتك."),
    "budget_nearly_exhausted": ("تنبيه الميزانية", "ميزانية أوشكت على النفاد أو تجاوزت حدها هذا الشهر."),
    "unexpected_bank_charge": ("رسوم بنكية", "تم خصم رسوم أو فائدة بنكية."),
    "suspected_duplicate": ("دفعة مكررة محتملة", "ربما سُجلت دفعة مرتين."),
    "refund_received": ("تم استلام مبلغ مسترد", "أُعيد مبلغ إلى أحد حساباتك."),
    "new_recurring_charge": ("رسوم متكررة جديدة", "تم العثور على رسوم تتكرر."),
    "salary_received": ("تم استلام الراتب", "وصل راتب هذا الشهر."),
    "salary_differs": ("الراتب مختلف", "راتب هذا الشهر يختلف عن المتوقع."),
    "lender_balance_differs": ("رصيد جهة الإقراض مختلف", "رصيد لدى جهة إقراض يختلف عن سجلاتك."),
}


def generic(kind: str, language: str | None = None) -> tuple[str, str]:
    """The fixed title and body of a kind, in the phone's language (English when unknown)."""
    table = GENERIC_AR if language == "ar" else GENERIC
    return table.get(kind) or (("تنبيه مالي", "شيء في أموالك يحتاج إلى نظرة.") if language == "ar" else
                               ("Money alert", "Something in your finances needs a look."))


def default(kind: str, channel: Channel) -> bool:
    return True if channel == "in_app" else (kind in PUSH_DEFAULT if channel == "push" else False)


def stored_key(kind: str, channel: Channel) -> str:
    return kind if channel == "in_app" else f"{channel}:{kind}"


def resolve(kinds: tuple[str, ...], stored: dict[str, Any]) -> dict[str, dict[str, bool]]:
    """Every kind's three channels: the stored choice, else the default."""
    return {k: {c: bool(stored.get(stored_key(k, c), default(k, c))) for c in CHANNELS} for k in kinds}


# -- reach: can a channel be used at all, and if not, why ----------------------------------------------------


@dataclass(frozen=True)
class Reach:
    push: bool
    push_reason: str | None  # not_configured, push_off, no_device
    email: str | None  # the address, when email can be sent
    email_reason: str | None  # not_configured, email_off, no_address
    # The language the person chose for emails (Settings, Notifications; the one calendar reminder emails use): ``en``,
    # ``ar``, or None for English followed by Arabic.
    email_language: str | None = None

    def available(self, channel: Channel) -> bool:
        return channel == "in_app" or (self.push if channel == "push" else self.email is not None)


REASONS: dict[str, str] = {
    "push:not_configured": "Phone push is not set up on this ANUM server, so alerts cannot reach a phone.",
    "push:push_off": "Phone push is off in your notification settings.",
    "push:no_device": "No phone is registered: sign in on the ANUM phone app and allow notifications.",
    "email:not_configured": "Email is not set up on this ANUM server (no mail server), so alerts cannot be emailed.",
    "email:email_off": "Email notifications are off in your notification settings.",
    "email:no_address": "ANUM has no email address for you: add one to your team profile.",
}


def reach(context: TenantContext) -> Reach:
    """What this person can be reached by now. Each read is its own short unit of work (call it off the event loop)."""
    from ..notifications import email_configured, recipient_address
    from ..onboarding import NotificationPreferences, open_notification_preference_store
    from ..push import configured_account, open_push_device_store

    with open_notification_preference_store(context) as store:
        preferences = store.get(context) or NotificationPreferences()
    if configured_account() is None:
        push, push_reason = False, "not_configured"
    elif not preferences.push_enabled:
        push, push_reason = False, "push_off"
    else:
        with open_push_device_store(context) as devices:
            push = bool(devices.list(context))
        push_reason = None if push else "no_device"
    address: str | None = None
    if not email_configured():
        email_reason: str | None = "not_configured"
    elif not preferences.email_enabled:
        email_reason = "email_off"
    else:
        address = recipient_address(context, context.user_id)
        email_reason = None if address else "no_address"
    return Reach(push=push, push_reason=push_reason, email=address, email_reason=email_reason, email_language=preferences.calendar_email_language)


def channel_view(found: Reach) -> dict[str, dict[str, Any]]:
    def one(channel: str, ok: bool, reason: str | None) -> dict[str, Any]:
        return {"available": ok, "reason": reason, "message": REASONS.get(f"{channel}:{reason}") if reason else None}

    return {"in_app": one("in_app", True, None), "push": one("push", found.push, found.push_reason),
            "email": one("email", found.email is not None, found.email_reason)}


# -- delivery ------------------------------------------------------------------------------------------------


def link() -> str | None:
    """The web app's Finance page, when the server knows its public address."""
    from ..settings import settings

    base = (settings.public_app_url or settings.web_app_url or "").split("#")[0].rstrip("/")
    return f"{base}/#finance" if base else None


def email_text(kind: str, count: int, language: str | None = None) -> tuple[str, str]:
    """Subject and body of an alert email: the kind's fixed words and a link, never the details. In the person's email
    language: ``en``, ``ar``, or None for English followed by the same in Arabic (as calendar reminder emails)."""
    where = link()

    def english() -> tuple[str, str]:
        title, body = generic(kind, "en")
        more = f" ({count})" if count > 1 else ""
        open_line = f"Open ANUM to see the details: {where}" if where else "Open ANUM, Finance, to see the details."
        return (f"ANUM: {title}{more}",
                f"{body}\n\n{open_line}\n\nFor your privacy this email has no amounts, names or account numbers: they stay in ANUM.\n\n"
                "You get it because email is on for this alert in Settings, Notifications, Financial alerts.")

    def arabic() -> tuple[str, str]:
        title, body = generic(kind, "ar")
        more = f" ({count})" if count > 1 else ""
        open_line = f"افتح ANUM لرؤية التفاصيل: {where}" if where else "افتح ANUM، المالية، لرؤية التفاصيل."
        return (f"ANUM: {title}{more}",
                f"{body}\n\n{open_line}\n\nحفاظًا على خصوصيتك لا تحتوي هذه الرسالة على مبالغ أو أسماء أو أرقام حسابات: تبقى في ANUM.\n\n"
                "تصلك لأن البريد مفعّل لهذا التنبيه في الإعدادات، الإشعارات، التنبيهات المالية.")

    if language == "en":
        return english()
    if language == "ar":
        return arabic()
    (subject, en_body), (_ar_subject, ar_body) = english(), arabic()
    return subject, f"{en_body}\n\n{ar_body}"


def push_ref(claim_key: str, day: date) -> str:
    """An opaque reference to one push claim: a digest of its key and its day (the key itself can hold figures)."""
    return f"{hashlib.sha256(claim_key[:200].encode()).hexdigest()[:24]}@{day.isoformat()}"


def push_refs(value: Any) -> tuple[str, ...]:
    """The well-formed push claim references of an event payload (anything else is ignored)."""
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value[:MAX_PUSH_REFS] if isinstance(v, str) and _REF.fullmatch(v))


def push_undelivered(context: TenantContext, refs: tuple[str, ...] | list[str], now: datetime | None = None) -> int:
    """The dispatcher's report that no phone took a push and FCM gave up only for now: release each named push claim so the
    next daily run tries once more, at most ``MAX_PUSH_RETRIES`` times per item (then the claim stays, as for a refusal).
    ``context`` is the person the alert is private to. Answers how many claims were released."""
    from ..schemas import utc_now
    from .rules_store import open_rule_stores

    wanted: set[tuple[str, date]] = set()
    for ref in push_refs(list(refs)):
        digest, _, day = ref.partition("@")
        wanted.add((digest, date.fromisoformat(day)))
    if not wanted:
        return 0
    moment = now or utc_now()
    released = 0
    with open_rule_stores(context) as (_finance, store):
        for key, day in store.list_item_notices(context, PUSH_CLAIM, min(d for _r, d in wanted)):
            digest = push_ref(key, day).partition("@")[0]
            if (digest, day) not in wanted:
                continue
            if not any(store.claim_item_notices(context, PUSH_RETRY_CLAIM, [f"{n}:{digest}"], day, moment) for n in range(1, MAX_PUSH_RETRIES + 1)):
                continue  # retried enough: it stays told, like a refusal
            store.release_item_notices(context, PUSH_CLAIM, [key], day)
            released += 1
    if released:
        logger.info("finance_alert_push_released count=%s", released)
    return released


def send_push(context: TenantContext, kind: str, count: int, refs: list[str] | None = None) -> bool:
    """Record the push trigger: one ``finance.alert.raised`` event, private to the person: kind, count and the opaque references
    of its push claims (``push_ref``), so the dispatcher can report a push FCM never took (``push_undelivered``)."""
    from ..events import CanonicalEventName, create_event
    from ..team_work import open_team_unit

    payload: dict[str, Any] = {"recipient": context.user_id, "alert_kind": kind, "count": count}
    if refs:
        payload["claims"] = list(refs[:MAX_PUSH_REFS])
    try:
        with open_team_unit(context) as unit:
            unit.repository.record_event(create_event(
                CanonicalEventName.FINANCE_ALERT_RAISED, context, f"{EVENT_SUBJECT}:{kind}", payload, private_to=context.user_id,
            ).event)
        return True
    except Exception as exc:  # noqa: BLE001 - the claims are released and the next run tries again
        logger.warning("finance_alert_push_failed error_class=%s", type(exc).__name__)
        return False


EmailResult = Literal["sent", "transient", "permanent"]


def send_email(address: str, kind: str, count: int, language: str | None = None) -> EmailResult:
    """Send one alert email now (bounded retries with backoff), in the person's email language. Never call it with a
    transaction open."""
    from .. import notifications

    subject, text = email_text(kind, count, language)
    outcome = notifications.send_with_retries(address, subject, text)
    if outcome.status == "sent":
        return "sent"
    logger.warning("finance_alert_email_failed status=%s attempts=%s", outcome.status, outcome.attempts)
    # A refusal (5xx, a bad address, the sign-in) is permanent; unreachable or 4xx after the last try may pass by the next run.
    return "transient" if outcome.status == "not_configured" or outcome.transient else "permanent"


__all__ = ["CHANNELS", "EMAIL_CLAIM", "GENERIC", "GENERIC_AR", "MAX_PUSH_RETRIES", "PUSH_CLAIM", "PUSH_DEFAULT", "PUSH_RETRY_CLAIM", "Channel", "Reach",
           "channel_view", "default", "email_text", "generic", "push_ref", "push_refs", "push_undelivered", "reach", "resolve", "send_email", "send_push",
           "stored_key"]
