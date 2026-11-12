"""Routes for the iPhone SMS integration and the ledger (docs/finance-sms.md#api).

Under ``/api/v1/finance``. ``finance:read`` reads; ``finance:manage`` changes. Every record is
the caller's own (owner-only RLS), so a workspace owner cannot see a member's messages.

``POST /sms/ingest`` is the only route without a session: it takes a device token
(``Authorization: Bearer``), which works in Apple Shortcuts' "Get Contents of URL" headers.
The token reaches only its owner's own rows in one workspace and only this route; it is
refused once revoked, while paused, while the integration is off, when its owner left the
workspace or became a viewer, and over the per-device rate limit.

Audit rows carry ids, statuses and reason codes: never a message, an amount, a merchant or an
account or card number. Nothing here moves money.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..audit import AuditRecord
from ..dependencies import repository_context, tenant_context
from ..identity import is_local_environment
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from ..settings import settings as app_settings
from . import ledger as L
from . import sms_parser as P
from . import link_hooks
from . import sms_service as svc
from .api import _audit, _manage, _read
from .flows import Flow, flow_of
from .ledger import EntrySource, EntryStatus, PlanError
from .models import AccountKind
from . import sms_learning as SL
from .sms_store import (
    MAX_DEVICES,
    AccountMapping,
    AutoPostLimit,
    DeviceStatus,
    LedgerKind,
    MessageStatus,
    RuleStatus,
    SenderMapping,
    SmsDevice,
    SmsMessage,
    SmsRule,
    SmsSettings,
    WalletCard,
    open_sms_stores,
    source_kind,
)
from .store import TransactionFilter
from .summary import GoalProgress, month_range, settings_for, today_in, upcoming_bills

logger = logging.getLogger("anum_api.finance.sms")
router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

SHORTCUT_FIELDS = {"message": "Shortcut Input, Content", "sender": "Shortcut Input, Sender"}


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found. It may have been deleted.")


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


# -- views --------------------------------------------------------------------------------------


class DeviceView(BaseModel):
    id: str
    name: str
    status: DeviceStatus
    token_hint: str
    scopes: list[str]
    created_at: Any
    rotated_at: Any = None
    revoked_at: Any = None
    last_seen_at: Any = None
    last_ok_at: Any = None
    last_error: str | None = None
    last_error_at: Any = None
    message_count: int


class DeviceToken(BaseModel):
    device: DeviceView
    token: str  # shown once; ANUM keeps only its hash
    ingest_path: str
    headers: dict[str, str]
    json_fields: dict[str, str]


class MessageView(BaseModel):
    id: str
    device_id: str | None
    source: str
    received_at: Any
    sender: str
    bank_key: str | None
    body_masked: str
    body_kept_until: Any = None
    status: MessageStatus
    reason: str | None
    parsed: dict[str, Any]
    rule: str
    entry_id: str | None
    duplicate_of: str | None
    observed_balance: Decimal | None
    observed_currency: str | None
    attempts: int
    learned: dict[str, Any] | None = None  # what ANUM applied from the person's earlier choices, with its undo
    fix: dict[str, Any] | None = None  # the one action that makes such a message automatic next time (docs/finance-sms.md#learning)


class MessagePage(BaseModel):
    items: list[MessageView]
    total: int
    limit: int
    offset: int


class RuleView(BaseModel):
    id: str
    sender: str
    bank_key: str | None
    kind: str
    currency: str | None
    category: str | None
    account_id: str | None
    status: RuleStatus
    from_message_id: str | None
    hits: int
    created_at: Any
    pattern_preview: str


class BankView(BaseModel):
    key: str
    name: str
    country: str
    currency: str
    senders: list[str]
    verified_by_anum: bool
    confirmed_by_you: bool


class StatusView(BaseModel):
    enabled: bool
    ledger_kind: LedgerKind
    self_test_passed_at: Any
    ready: bool  # self-test passed: the endpoint may be shown
    ingest_path: str | None
    devices_active: int
    last_received_at: Any
    last_ok_at: Any
    last_error: str | None
    last_error_at: Any
    counts: dict[str, int]
    review_count: int


def _device_view(device: SmsDevice) -> DeviceView:
    return DeviceView(**device.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id", "token_hash"}))


def _message_view(message: SmsMessage, settings: SmsSettings | None = None, accounts: list[Any] | None = None) -> MessageView:
    fix = None
    if settings is not None and accounts is not None:
        found = SL.fix_for(message, settings, accounts)
        if found is not None:
            names = {a.id: a.name for a in accounts}
            fix = {**found.__dict__, "account_name": names.get(found.account_id or "")}
    return MessageView(
        id=message.id, device_id=message.device_id, source=message.source, received_at=message.received_at, sender=message.sender,
        bank_key=message.bank_key, body_masked=message.body_masked, body_kept_until=message.body_purge_at, status=message.status,
        reason=message.reason, parsed={k: v for k, v in message.parsed.items() if k != "learned"},
        learned=message.parsed.get("learned") if isinstance(message.parsed.get("learned"), dict) else None, rule=message.rule, entry_id=message.entry_id, duplicate_of=message.duplicate_of,
        observed_balance=message.observed_balance, observed_currency=message.observed_currency, attempts=message.attempts, fix=fix,
    )


def _rule_view(rule: SmsRule) -> RuleView:
    return RuleView(**rule.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id", "pattern", "updated_at"}),
                    pattern_preview=rule.pattern[:200])


def _status(context: TenantContext) -> StatusView:
    with open_sms_stores(context) as (_finance, store):
        settings = svc.settings_for(store, context)
        devices = store.list_devices(context)
        counts = store.status_counts(context)
        recent, _ = store.list_messages(context, status=None, limit=1, offset=0)
    errors = sorted((d for d in devices if d.last_error_at), key=lambda d: d.last_error_at, reverse=True)
    oks = sorted((d for d in devices if d.last_ok_at), key=lambda d: d.last_ok_at, reverse=True)
    ready = settings.self_test_passed_at is not None
    return StatusView(
        enabled=settings.enabled, ledger_kind=settings.ledger_kind, self_test_passed_at=settings.self_test_passed_at, ready=ready,
        ingest_path=svc.INGEST_PATH if ready else None,
        devices_active=sum(1 for d in devices if d.status == DeviceStatus.ACTIVE),
        last_received_at=recent[0].received_at if recent else None, last_ok_at=oks[0].last_ok_at if oks else None,
        last_error=errors[0].last_error if errors else None, last_error_at=errors[0].last_error_at if errors else None,
        counts=counts, review_count=counts.get(MessageStatus.REVIEW.value, 0) + counts.get(MessageStatus.FAILED.value, 0),
    )


# -- status, settings and the self-test ----------------------------------------------------------


@router.get("/sms/status", response_model=StatusView)
def sms_status(context: TenantContext = Depends(tenant_context)) -> StatusView:
    _read(context)
    return _status(context)


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)
    enabled: bool | None = None
    ledger_kind: LedgerKind | None = None
    auto_post: bool | None = None
    body_retention_days: int | None = Field(default=None, ge=0, le=365)
    verified_senders: list[str] | None = Field(default=None, max_length=50)
    extra_senders: list[SenderMapping] | None = Field(default=None, max_length=50)
    account_map: list[AccountMapping] | None = Field(default=None, max_length=50)
    wallet_cards: list[WalletCard] | None = Field(default=None, max_length=50)
    default_account_id: str | None = Field(default=None, max_length=80)
    cash_account_id: str | None = Field(default=None, max_length=80)
    ai_enabled: bool | None = None
    ai_consent: bool = False  # must be true to turn AI help on: masked text goes to the workspace's model
    ai_daily_cap: int | None = Field(default=None, ge=1, le=200)
    learning_enabled: bool | None = None  # learn from reviews (docs/finance-sms.md#learning)
    learning_confirm_after: int | None = Field(default=None, ge=1, le=5)
    auto_post_limits: list[AutoPostLimit] | None = Field(default=None, max_length=20)


class SettingsView(SmsSettings):
    pass


@router.get("/sms/settings", response_model=SmsSettings)
def get_sms_settings(context: TenantContext = Depends(tenant_context)) -> SmsSettings:
    _read(context)
    with open_sms_stores(context) as (_finance, store):
        return svc.settings_for(store, context)


@router.put("/sms/settings", response_model=SmsSettings)
def put_sms_settings(body: SettingsInput, context: TenantContext = Depends(tenant_context),
                     repository: AnumRepository = Depends(repository_context)) -> SmsSettings:
    _manage(context)
    now = utc_now()
    with open_sms_stores(context) as (finance, store):
        current = svc.settings_for(store, context)
        if body.version != current.version:
            raise _conflict("These settings changed in another window. Reload and try again.")
        updated = current.model_copy(deep=True)
        if body.enabled is True and current.self_test_passed_at is None:
            raise _conflict("Run the safety check first: it must pass before SMS forwarding can be turned on.")
        accounts = {a.id: a for a in finance.list_accounts(context) if not a.archived}
        for field in ("enabled", "ledger_kind", "auto_post", "body_retention_days"):
            value = getattr(body, field)
            if value is not None:
                setattr(updated, field, value)
        if body.verified_senders is not None:
            updated.verified_senders = sorted({P.normalize_sender(s) for s in body.verified_senders if P.normalize_sender(s)})
        if body.extra_senders is not None:
            updated.extra_senders = [SenderMapping(sender=P.normalize_sender(m.sender), bank=m.bank.strip().lower()[:40])
                                     for m in body.extra_senders if P.normalize_sender(m.sender)]
        if body.account_map is not None:
            seen: set[str] = set()
            for item in body.account_map:
                if item.account_id not in accounts:
                    raise _unprocessable("Card and account numbers: one of the accounts was not found. Reload and pick it again.")
                if item.last4 in seen:
                    raise _unprocessable(f"Card and account numbers: ending {item.last4} is listed twice.")
                seen.add(item.last4)
                if item.credit_limit is not None and item.credit_limit < 0:
                    raise _unprocessable("A credit limit cannot be negative.")
            updated.account_map = body.account_map
        if body.wallet_cards is not None:
            from .wallet import card_key

            names: set[str] = set()
            for card in body.wallet_cards:
                if card.account_id not in accounts:
                    raise _unprocessable("Apple Wallet cards: one of the accounts was not found. Reload and pick it again.")
                key = card_key(card.card)
                if not key or key in names:
                    raise _unprocessable("Apple Wallet cards: each card or pass name must be filled in once.")
                names.add(key)
            updated.wallet_cards = [card.model_copy(update={"card": card_key(card.card)}) for card in body.wallet_cards]
        for field in ("default_account_id", "cash_account_id"):
            if field in body.model_fields_set:
                value = getattr(body, field) or None
                if value is not None and value not in accounts:
                    raise _unprocessable("That account was not found. Reload and pick it again.")
                if field == "cash_account_id" and value is not None and accounts[value].kind != AccountKind.CASH:
                    raise _unprocessable("Cash withdrawals go to a cash account. Pick an account of kind cash.")
                setattr(updated, field, value)
        if body.ai_enabled is True and not current.ai.enabled:
            if not body.ai_consent:
                raise _unprocessable("To turn on AI help, confirm that the masked message text is sent to your workspace's model.")
            updated.ai.enabled, updated.ai.opted_in_at = True, now
        elif body.ai_enabled is False:
            updated.ai.enabled, updated.ai.opted_in_at = False, None
        if body.ai_daily_cap is not None:
            updated.ai.daily_cap = body.ai_daily_cap
        if body.learning_enabled is not None:
            updated.learning.enabled = body.learning_enabled
        if body.learning_confirm_after is not None:
            updated.learning.confirm_after = body.learning_confirm_after
        if body.auto_post_limits is not None:
            currencies = [item.currency for item in body.auto_post_limits]
            if len(set(currencies)) != len(currencies):
                raise _unprocessable("Limits for automatic recording: each currency can have one limit.")
            updated.auto_post_limits = body.auto_post_limits
        updated.version, updated.updated_at = current.version + 1, now
        if not store.save_settings(context, updated, expected_version=current.version):
            raise _conflict("These settings changed in another window. Reload and try again.")
    _audit(repository, context, "finance.sms.settings.update", "finance_sms_settings",
           {"fields": sorted(body.model_fields_set - {"version", "ai_consent"}), "enabled": updated.enabled, "ai": updated.ai.enabled,
            "learning": updated.learning.enabled})
    return updated


class Check(BaseModel):
    name: str
    passed: bool


class SelfTest(BaseModel):
    passed: bool
    checks: list[Check]
    passed_at: Any = None


# Synthetic messages only (docs/finance-sms.md#test-fixtures): not the wording of any real bank.
SELF_TEST_PURCHASE = "SYNTHETIC TEST. Purchase of SAR 12.34 at TEST MERCHANT with card ***0000 on 01/01/2026 10:00. Avl Bal SAR 100.00"
SELF_TEST_OTP = "SYNTHETIC TEST. Your OTP is 000000. Do not share it."


@router.post("/sms/self-test", response_model=SelfTest)
def sms_self_test(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> SelfTest:
    """The safe check before real messages: parsing, refusal, masking, token handling and the
    database round trip, all with synthetic data and nothing written to the ledger."""
    _manage(context)
    now = utc_now()
    checks: list[Check] = []
    parsed = P.parse("anum-self-test", SELF_TEST_PURCHASE, extra_senders={"anumselftest": "self_test"}, verified_senders={"anumselftest"})
    checks.append(Check(name="parse_purchase", passed=parsed.status == "ok" and parsed.amount == Decimal("12.34") and parsed.kind == P.Kind.CARD_PURCHASE))
    checks.append(Check(name="refuse_one_time_codes", passed=P.parse("anum-self-test", SELF_TEST_OTP).status == "rejected"))
    checks.append(Check(name="mask_numbers", passed=P.mask("Account 1234567890123") == "Account ***0123"))
    token, digest, _hint = svc.mint(context, "fin_smsdev_selftest")
    read = svc.parse_token(token)
    secret = read[2] if read else ""
    checks.append(Check(name="token_round_trip", passed=read is not None and read[0].user_id == context.user_id and svc.secret_hash(secret) == digest
                        and svc.parse_token(token[:-2] + "xx") is not None and svc.secret_hash(token[:-2]) != digest))
    legs = L.plan(P.Kind.CARD_PURCHASE, Decimal("12.34"), account=_probe_account(context), category="groceries")
    checks.append(Check(name="double_entry_balances", passed=L.balanced(legs)))
    try:
        with open_sms_stores(context) as (_finance, store):
            store.list_devices(context)
            store.status_counts(context)
        checks.append(Check(name="storage_reachable", passed=True))
    except Exception:
        logger.exception("finance_sms_self_test_storage")
        checks.append(Check(name="storage_reachable", passed=False))
    passed = all(check.passed for check in checks)
    passed_at = None
    if passed:
        with open_sms_stores(context) as (_finance, store):
            current = svc.settings_for(store, context)
            updated = current.model_copy(update={"self_test_passed_at": now, "version": current.version + 1, "updated_at": now})
            store.save_settings(context, updated, expected_version=current.version)
            passed_at = now
    _audit(repository, context, "finance.sms.self_test", "finance_sms_settings", {"passed": passed, "failed": [c.name for c in checks if not c.passed]})
    return SelfTest(passed=passed, checks=checks, passed_at=passed_at)


def _probe_account(context: TenantContext) -> Any:
    from .models import Account

    now = utc_now()
    return Account(id="probe", tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id, name="probe",
                   kind=AccountKind.CARD, currency="SAR", created_at=now, updated_at=now)


@router.get("/sms/banks", response_model=list[BankView])
def sms_banks(context: TenantContext = Depends(tenant_context)) -> list[BankView]:
    """The bank registry: expected sender names only, none verified by ANUM (docs/finance-sms.md#banks)."""
    _read(context)
    with open_sms_stores(context) as (_finance, store):
        verified = set(svc.settings_for(store, context).verified_senders)
    return [BankView(key=b.key, name=b.name, country=b.country, currency=b.currency, senders=list(b.senders), verified_by_anum=b.verified,
                     confirmed_by_you=any(s in verified for s in b.senders)) for b in P.BANKS]


# -- devices ------------------------------------------------------------------------------------


class DeviceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    # Which iPhone automations this token serves; each ingest route accepts only its own scope.
    scopes: list[Literal["finance.sms", "finance.wallet"]] = Field(default_factory=lambda: ["finance.sms", "finance.wallet"], min_length=1, max_length=10)


def _token_view(device: SmsDevice, token: str) -> DeviceToken:
    return DeviceToken(device=_device_view(device), token=token, ingest_path=svc.INGEST_PATH,
                       headers={"Authorization": "Bearer <token>", "Content-Type": "application/json"}, json_fields=SHORTCUT_FIELDS)


@router.get("/sms/devices", response_model=list[DeviceView])
def list_devices(context: TenantContext = Depends(tenant_context)) -> list[DeviceView]:
    _read(context)
    with open_sms_stores(context) as (_finance, store):
        return [_device_view(d) for d in store.list_devices(context)]


@router.post("/sms/devices", response_model=DeviceToken, status_code=status.HTTP_201_CREATED)
def create_device(body: DeviceInput, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> DeviceToken:
    _manage(context)
    now = utc_now()
    with open_sms_stores(context) as (_finance, store):
        settings = svc.settings_for(store, context)
        if settings.self_test_passed_at is None:
            raise _conflict("Run the safety check first: devices can be connected once it passes.")
        if sum(1 for d in store.list_devices(context) if d.status != DeviceStatus.REVOKED) >= MAX_DEVICES:
            raise _conflict(f"You can connect up to {MAX_DEVICES} devices. Disconnect one you no longer use first.")
        device_id = new_id("fin_smsdev")
        token, digest, hint = svc.mint(context, device_id)
        device = SmsDevice(id=device_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                           name=P.normalize(body.name)[:60] or "iPhone", status=DeviceStatus.ACTIVE, token_hash=digest, token_hint=hint,
                           scopes=sorted(set(body.scopes)), created_at=now)
        store.save_device(device)
    _audit(repository, context, "finance.sms.device.create", device_id, {"scopes": device.scopes})
    return _token_view(device, token)


def _change_device(context: TenantContext, device_id: str, change: str) -> tuple[SmsDevice, str | None]:
    now = utc_now()
    with open_sms_stores(context) as (_finance, store):
        device = store.get_device(context, device_id)
        if device is None:
            raise _not_found("Device")
        token = None
        if device.status == DeviceStatus.REVOKED:
            raise _conflict("This device is disconnected. Connect it again as a new device.")
        if change == "rotate":
            token, device.token_hash, device.token_hint = svc.mint(context, device.id)
            device.rotated_at, device.status = now, DeviceStatus.ACTIVE
        elif change == "pause":
            device.status = DeviceStatus.PAUSED
        elif change == "resume":
            device.status = DeviceStatus.ACTIVE
        elif change == "revoke":
            # The hash is replaced too, so no token for this device can ever match again.
            device.status, device.revoked_at, device.token_hash = DeviceStatus.REVOKED, now, svc.secret_hash(new_id("revoked"))
        store.save_device(device)
    return device, token


@router.post("/sms/devices/{device_id}/rotate", response_model=DeviceToken)
def rotate_device(device_id: str, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> DeviceToken:
    _manage(context)
    device, token = _change_device(context, device_id, "rotate")
    _audit(repository, context, "finance.sms.device.rotate", device_id)
    return _token_view(device, token or "")


@router.post("/sms/devices/{device_id}/{change}", response_model=DeviceView)
def change_device(device_id: str, change: Literal["pause", "resume", "revoke"], context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> DeviceView:
    _manage(context)
    device, _ = _change_device(context, device_id, change)
    _audit(repository, context, f"finance.sms.device.{change}", device_id)
    return _device_view(device)


# -- ingest -------------------------------------------------------------------------------------


class IngestBody(BaseModel):
    """What the Shortcut sends. Nothing else is accepted: no user, workspace or account id."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(max_length=2_000)
    sender: str = Field(max_length=200)


class IngestResult(BaseModel):
    status: str
    reason: str | None = None
    message_id: str | None = None
    replayed: bool = False
    text: str


RESULT_TEXT = {
    "posted": "Recorded in your ledger.",
    "review": "Saved for your review in ANUM.",
    "duplicate": "Already recorded, so not added again.",
    "rejected": "Not a bank transaction, so nothing was kept.",
    "test_ok": "Connected. ANUM received the test and recorded nothing.",
    "attached": "Matched to the Apple Pay payment already recorded: one transaction.",
}


def _https(request: Request) -> bool:
    if is_local_environment(app_settings.environment):
        return True
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def _ingest_audit(context: TenantContext, target: str, outcome: str, meta: dict[str, Any]) -> None:
    from ..durable_runs import tenant_unit_of_work

    try:
        with tenant_unit_of_work(context) as repository:
            repository.record_audit(AuditRecord(id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                                                actor=context.user_id, action="finance.sms.ingest", target=target, outcome=outcome,
                                                correlation_id=target, created_at=utc_now(), metadata={"actor_type": "device", **meta}))
    except Exception:
        logger.exception("finance_sms_ingest_audit_failed")


# Refusals after the device's secret matched: shown as failed events on Settings, Integrations, iPhone.
_DEVICE_EVENT_CODES = frozenset({"rate_limited", "disabled", "paused", "scope_missing"})


def _device_event(context: TenantContext | None, device_id: str | None, key: str, outcome: str, code: str | None = None) -> None:
    from .devices import record_event

    if context is not None and device_id and (outcome != "denied" or code in _DEVICE_EVENT_CODES):
        record_event(context, device_id, key, key, outcome, code)


@router.post("/sms/ingest", response_model=IngestResult)
def sms_ingest(request: Request, body: IngestBody, authorization: str | None = Header(default=None)) -> Any:
    """One forwarded message from an iPhone Shortcut (docs/finance-sms.md#the-shortcut)."""
    if not _https(request):
        return JSONResponse(status_code=403, content={"detail": "Use the https address.", "code": "https_required"})
    now = utc_now()
    context: TenantContext | None = None
    device_id = None
    try:
        context, device_id, secret = svc.authorize(authorization)
        outcome = svc.ingest(context, device_id, secret, body.sender, body.message, now=now)
    except svc.IngestError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        if context is not None and device_id and exc.code in ("rate_limited", "disabled"):
            svc.record_device_error(context, device_id, exc.code, now)
            _ingest_audit(context, device_id, "denied", {"code": exc.code})
        _device_event(context, device_id, "finance.sms", "denied", exc.code)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code}, headers=headers)
    except svc._Failed as failed:
        assert context is not None  # nosec B101: _Failed is raised only after authorize returned
        message = svc.record_failure(context, failed, now)
        svc.notify(context, "failed", now)
        _ingest_audit(context, message.id, "failure", {"status": "failed"})
        _device_event(context, device_id, "finance.sms", "failed", "processing_error")
        return JSONResponse(status_code=503, content={"detail": "ANUM could not process this message. Your iPhone can send it again; it will not be recorded twice.",
                                                      "code": "processing_error", "message_id": message.id}, headers={"Retry-After": "60"})
    message = outcome.message
    _device_event(context, device_id, "finance.sms", "test" if message.reason == "test_ok" else "ok")
    if message.reason == "test_ok":
        _ingest_audit(context, device_id or "-", "success", {"status": "test_ok"})
        return IngestResult(status="test_ok", text=RESULT_TEXT["test_ok"])
    if not outcome.replayed:
        _ingest_audit(context, message.id, "success", {"status": message.status.value, "reason": message.reason, "rule": message.rule.split(":")[0]})
        if outcome.notify:
            svc.notify(context, outcome.notify, now)
    return IngestResult(status=message.status.value, reason=message.reason, message_id=message.id if message.status != MessageStatus.REJECTED else None,
                        replayed=outcome.replayed, text=RESULT_TEXT.get(message.status.value, "Saved."))


class WalletBody(BaseModel):
    """What the Apple Wallet transaction automation sends. No event time: ANUM stamps it on arrival."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["apple_wallet"]
    amount: str | int | float
    merchant: str = Field(default="", max_length=200)
    card: str = Field(max_length=200)
    name: str = Field(default="", max_length=200)


WALLET_TEXT = {
    "posted": "Recorded in your ledger.",
    "attached": "Matched to the bank message already recorded: one transaction.",
    "review": "Saved for your review in ANUM.",
    "duplicate": "Already recorded, so not added again.",
}


@router.post("/wallet/ingest", response_model=IngestResult)
def wallet_ingest(request: Request, body: WalletBody, authorization: str | None = Header(default=None)) -> Any:
    """One Apple Pay payment from the Wallet transaction automation (docs/finance-sms.md#apple-wallet)."""
    if not _https(request):
        return JSONResponse(status_code=403, content={"detail": "Use the https address.", "code": "https_required"})
    if isinstance(body.amount, str) and len(body.amount) > 40:
        return JSONResponse(status_code=422, content={"detail": "The amount is too long.", "code": "invalid_amount"})
    now = utc_now()
    context: TenantContext | None = None
    device_id = None
    event = svc.WalletEvent(amount=body.amount, merchant=body.merchant, card=body.card, name=body.name)
    try:
        context, device_id, secret = svc.authorize(authorization)
        outcome = svc.ingest(context, device_id, secret, "", "", now=now, wallet=event)
    except svc.IngestError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        if context is not None and device_id and exc.code in ("rate_limited", "disabled"):
            svc.record_device_error(context, device_id, exc.code, now)
            _ingest_audit(context, device_id, "denied", {"code": exc.code, "source": "wallet"})
        _device_event(context, device_id, "finance.wallet", "denied", exc.code)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code}, headers=headers)
    except svc._Failed as failed:
        assert context is not None  # nosec B101: _Failed is raised only after authorize returned
        message = svc.record_failure(context, failed, now)
        svc.notify(context, "failed", now)
        _ingest_audit(context, message.id, "failure", {"status": "failed", "source": "wallet"})
        _device_event(context, device_id, "finance.wallet", "failed", "processing_error")
        return JSONResponse(status_code=503, content={"detail": "ANUM could not process this payment. Your iPhone can send it again; it will not be recorded twice.",
                                                      "code": "processing_error", "message_id": message.id}, headers={"Retry-After": "60"})
    message = outcome.message
    _device_event(context, device_id, "finance.wallet", "ok")
    if not outcome.replayed:
        _ingest_audit(context, message.id, "success", {"status": message.status.value, "reason": message.reason, "source": "wallet"})
        if outcome.notify:
            svc.notify(context, outcome.notify, now)
    return IngestResult(status=message.status.value, reason=message.reason, message_id=message.id, replayed=outcome.replayed,
                        text=WALLET_TEXT.get(message.status.value, "Saved."))


class PasteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2_000)
    sender: str = Field(min_length=1, max_length=200)


@router.post("/sms/paste", response_model=IngestResult)
def sms_paste(body: PasteInput, context: TenantContext = Depends(tenant_context)) -> Any:
    """A message the person pastes by hand (older messages: iOS gives apps no access to SMS history)."""
    _manage(context)
    now = utc_now()
    try:
        outcome = svc.ingest(context, None, None, body.sender, body.message, source="paste", now=now)
    except svc.IngestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except svc._Failed as failed:
        svc.record_failure(context, failed, now)
        raise HTTPException(status_code=503, detail="ANUM could not process this message. Try again.") from None
    message = outcome.message
    _ingest_audit(context, message.id, "success", {"status": message.status.value, "reason": message.reason, "source": "paste"})
    return IngestResult(status=message.status.value, reason=message.reason, message_id=message.id if message.status != MessageStatus.REJECTED else None,
                        replayed=outcome.replayed, text=RESULT_TEXT.get(message.status.value, "Saved."))


# -- the review queue ---------------------------------------------------------------------------


@router.get("/sms/messages", response_model=MessagePage)
def list_messages(status_filter: MessageStatus | None = Query(default=None, alias="status"), limit: int = Query(default=25, ge=1, le=200),
                  offset: int = Query(default=0, ge=0), context: TenantContext = Depends(tenant_context)) -> MessagePage:
    _read(context)
    with open_sms_stores(context) as (finance, store):
        store.purge_bodies(context, utc_now())
        items, total = store.list_messages(context, status=status_filter.value if status_filter else None, limit=limit, offset=offset)
        settings, accounts = svc.settings_for(store, context), finance.list_accounts(context)
    return MessagePage(items=[_message_view(m, settings, accounts) for m in items], total=total, limit=limit, offset=offset)


@router.get("/sms/messages/{message_id}", response_model=MessageView)
def get_message(message_id: str, context: TenantContext = Depends(tenant_context)) -> MessageView:
    _read(context)
    with open_sms_stores(context) as (finance, store):
        message = store.get_message(context, message_id)
        if message is None:
            raise _not_found("Message")
        return _message_view(message, svc.settings_for(store, context), finance.list_accounts(context))


class CorrectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: P.Kind | None = None
    amount: str | None = Field(default=None, max_length=30)
    currency: str | None = Field(default=None, max_length=3)
    converted_amount: str | None = Field(default=None, max_length=30)
    account_id: str | None = Field(default=None, max_length=80)
    counter_account_id: str | None = Field(default=None, max_length=80)
    merchant: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=40)
    occurred_on: date | None = None
    reference: str | None = Field(default=None, max_length=30)
    interest: str | None = Field(default=None, max_length=30)
    fee: str | None = Field(default=None, max_length=30)
    last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    balance: str | None = Field(default=None, max_length=30)
    allow_duplicate: bool = False  # post a message ANUM flagged as a duplicate anyway
    confirm_format: bool = False  # this reading is right: trust this sender's format from now on
    teach: bool = False  # propose a rule from this correction
    remember: bool = False  # remember this kind for this party now, without waiting for the second time


class LearningNoticeView(BaseModel):
    id: str
    kind: str
    sender: str | None = None
    party: str | None = None
    value: str | None = None
    last4: str | None = None
    account_id: str | None = None
    ref: str | None = None
    created_at: Any


class CorrectionResult(BaseModel):
    message: MessageView
    entry_id: str
    canonical_id: str
    reversed_entry_id: str | None = None
    proposed_rule: RuleView | None = None
    rule_error: str | None = None
    learned: list[LearningNoticeView] = Field(default_factory=list)  # what ANUM learned from this, each with its undo


@router.post("/sms/messages/{message_id}/post", response_model=CorrectionResult)
def post_message(message_id: str, body: CorrectionInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> CorrectionResult:
    """Approve or correct a message and record it. A posted message is corrected by a
    reversing entry and a new one: the journal keeps both."""
    _manage(context)
    now = utc_now()
    corrected = body.model_dump(exclude_none=True, mode="json")
    reversed_id = rule_view = rule_error = None
    learned: list[LearningNoticeView] = []
    with svc.owner_lock(context), open_sms_stores(context) as (finance, store):
        store.lock_owner(context)  # one writer of this person's settings and journal at a time (ingest takes the same lock)
        message = store.get_message(context, message_id)
        if message is None:
            raise _not_found("Message")
        if message.status == MessageStatus.REJECTED:
            raise _conflict("This message was not a bank transaction, so it was not kept. Paste it again if it was one.")
        if message.status == MessageStatus.ATTACHED and not body.allow_duplicate:
            raise _conflict("This report is already part of a recorded transaction (Apple Pay and the bank message are one payment). Confirm that it is a separate payment to record it.")
        if message.status == MessageStatus.DUPLICATE and not body.allow_duplicate:
            raise _conflict("ANUM found this transaction already recorded. Confirm that it is a separate transaction to record it.")
        settings = svc.settings_for(store, context)
        fsettings = settings_for(finance, context)
        if not body.account_id and not body.counter_account_id and message.parsed.get("account_id"):
            corrected.setdefault("account_id", str(message.parsed["account_id"]))  # a statement line names its account
        if not corrected.get("account_id") and not body.counter_account_id:
            # Keep what ANUM read when the person changed other fields only.
            resolved = svc.resolve(_parsed_from(message), settings, finance.list_accounts(context))
            corrected.setdefault("account_id", resolved.account.id if resolved.account else None)
            corrected.setdefault("counter_account_id", resolved.counter.id if resolved.counter else None)
        try:
            draft = svc.correction_draft(finance, context, message, corrected, fsettings.timezone)
            canonical = None
            if message.status == MessageStatus.POSTED and message.entry_id:
                old = store.get_entry(context, message.entry_id)
                if old is not None and old.status == EntryStatus.POSTED:
                    reversed_id = svc.reverse(finance, store, context, old, now=now, why="corrected by the owner").id
                    canonical = old.canonical_id or old.id  # the canonical transaction id survives the correction
            entry, _ = svc.post(finance, store, context, settings, draft, source=EntrySource.CORRECTION if reversed_id else EntrySource.SMS,
                                source_ref=message.id, now=now, canonical_id=canonical)
        except PlanError as exc:
            raise _unprocessable(str(exc)) from None
        as_read = message.model_copy(deep=True)  # the reading before this correction, for learning
        message.status, message.reason, message.entry_id, message.updated_at = MessageStatus.POSTED, None, entry.id, now
        message.parsed = {**{k: v for k, v in message.parsed.items() if k != "learned"},
                          **{k: v for k, v in corrected.items() if k not in ("allow_duplicate", "confirm_format", "teach", "remember")}}
        store.save_message(message)
        learned = _learn(finance, store, context, as_read, body, corrected, draft, now)
        if body.teach:
            try:
                rule_view = _rule_view(svc.teach(store, context, message, corrected, draft, now))
            except P.TeachError as exc:
                rule_error = str(exc)
        view = _message_view(message)
    _audit(repository, context, "finance.sms.message.post", message_id,
           {"entry": entry.id, "reversed": reversed_id, "fields": sorted(set(corrected) - {"allow_duplicate", "confirm_format", "teach"}),
            "confirmed_format": body.confirm_format, "rule": rule_view.id if rule_view else None, "learned": [n.kind for n in learned]})
    return CorrectionResult(message=view, entry_id=entry.id, canonical_id=entry.canonical_id or entry.id, reversed_entry_id=reversed_id,
                            proposed_rule=rule_view, rule_error=rule_error, learned=learned)


def _learn(finance: Any, store: Any, context: TenantContext, message: SmsMessage, body: CorrectionInput, corrected: dict[str, Any],
           draft: svc.Draft, now: Any) -> list[LearningNoticeView]:
    """Confirm the format (when ticked) and learn from what the person recorded (docs/finance-sms.md#learning).

    The settings are read again and saved with their version; the version moves only when the trusted senders change,
    so a settings form open in another window keeps working after an ordinary record."""
    picked = body.account_id or body.counter_account_id
    accounts = finance.list_accounts(context)
    for _attempt in range(3):
        settings = svc.settings_for(store, context)
        updated = settings.model_copy(deep=True)
        trusted = body.confirm_format and SL.trust_sender(updated, message.sender, message.bank_key)
        other = _own_other(draft, message, settings, accounts) if draft.kind == P.Kind.OWN_TRANSFER else None
        result = SL.on_recorded(updated, message, corrected, chosen_kind=draft.kind, picked_account_id=picked, counter_account_id=other,
                                merchant=draft.merchant, explicit_confirm=body.confirm_format, remember=body.remember, accounts=accounts, now=now)
        notices = list(result.notices)
        if result.category_rule is not None:
            rule_id = _learned_category_rule(finance, context, result.category_rule, now)
            if rule_id is not None:
                notices.append(SL.category_notice(updated, result.category_rule, rule_id, now))
        if trusted or result.trusted_changed:
            updated.version, updated.updated_at = settings.version + 1, now
        if store.save_settings(context, updated, expected_version=settings.version):
            return [LearningNoticeView(**n.model_dump()) for n in notices]
    logger.warning("finance_sms_learning_conflict")
    return []


def _own_other(draft: svc.Draft, message: SmsMessage, settings: SmsSettings, accounts: list[Any]) -> str | None:
    """The person's other own account in an own transfer: not the one the message itself is about."""
    read = svc.resolve(_parsed_from(message), settings, accounts)
    named = read.account or read.counter
    sides = [a for a in (draft.resolved.account, draft.resolved.counter) if a is not None]
    if named is not None:
        others = [a.id for a in sides if a.id != named.id]
        return others[0] if len(others) == 1 else None
    return None


def _learned_category_rule(finance: Any, context: TenantContext, memory: Any, now: Any) -> str | None:
    """An approved ``learned`` finance rule from a category chosen twice for one merchant, or the rule that already does it."""
    from . import rules_service as RS
    from .categories import classify_text
    from .models import Category
    from .rules import RuleContent, RuleMatch
    from .rules_store import owner_rules, rules_store_for

    found = classify_text(memory.party, Decimal(-1), owner_rules(finance, context))
    if found.rule_id is not None and found.category.value == memory.category:
        return str(found.rule_id)
    try:
        content = RuleContent(match=RuleMatch(merchant=memory.party, direction="out"), category=Category(memory.category),
                              note="Learned from iPhone SMS review: chosen twice for this merchant")
        rules = rules_store_for(finance)
        made = RS.propose(finance, rules, context, content, provenance="learned", by=context.user_id, now=now, confidence=Decimal("0.85"))
        RS.decide(rules, context, made.rule_id, made.version, approve=True, payload=made.payload_hash, by=context.user_id, now=now)
    except (ValueError, RS.RuleError):
        logger.info("finance_sms_category_not_learned")
        return None
    return made.rule_id


def _parsed_from(message: SmsMessage) -> P.Parsed:
    data = message.parsed
    parsed = P.Parsed(status="review")
    try:
        parsed.kind = P.Kind(str(data.get("kind") or "unknown"))
    except ValueError:
        parsed.kind = P.Kind.UNKNOWN
    for key in ("last4", "second_last4", "from_last4", "to_last4", "currency", "converted_currency", "merchant", "reference"):
        value = data.get(key)
        setattr(parsed, key, str(value) if value is not None else None)
    for key in ("amount", "converted_amount"):
        value = data.get(key)
        setattr(parsed, key, Decimal(str(value)) if value not in (None, "") else None)
    return parsed


@router.post("/sms/messages/{message_id}/dismiss", response_model=MessageView)
def dismiss_message(message_id: str, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> MessageView:
    _manage(context)
    with open_sms_stores(context) as (_finance, store):
        message = store.get_message(context, message_id)
        if message is None:
            raise _not_found("Message")
        if message.status == MessageStatus.POSTED:
            raise _conflict("This message is recorded. Use Undo to reverse it instead.")
        message.status, message.reason, message.updated_at = MessageStatus.DISMISSED, "dismissed", utc_now()
        store.save_message(message)
    _audit(repository, context, "finance.sms.message.dismiss", message_id)
    return _message_view(message)


@router.post("/sms/messages/{message_id}/reverse", response_model=MessageView)
def reverse_message(message_id: str, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> MessageView:
    """Undo a recorded message with a reversing entry (the journal keeps both)."""
    _manage(context)
    now = utc_now()
    with open_sms_stores(context) as (finance, store):
        message = store.get_message(context, message_id)
        if message is None:
            raise _not_found("Message")
        entry = store.get_entry(context, message.entry_id) if message.entry_id and message.status == MessageStatus.POSTED else None
        if entry is None or entry.status != EntryStatus.POSTED:
            raise _conflict("Only a recorded message can be undone.")
        reversal = svc.reverse(finance, store, context, entry, now=now, why="undone by the owner")
        message.status, message.reason, message.updated_at = MessageStatus.DISMISSED, "reversed", now
        store.save_message(message)
    _audit(repository, context, "finance.sms.message.reverse", message_id, {"entry": entry.id, "reversal": reversal.id})
    return _message_view(message)


class Suggestion(BaseModel):
    fields: dict[str, str]
    used_today: int
    daily_cap: int
    note: str = "A suggestion from your workspace's AI model, read from the masked text. Check every field before you record it."


@router.post("/sms/messages/{message_id}/suggest", response_model=Suggestion)
def suggest_fields(message_id: str, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> Suggestion:
    """AI help for one message: off unless the person opted in (docs/finance-sms.md#ai-help)."""
    from . import sms_ai
    from ..model_budget import ModelBudgetExceededError

    _manage(context)
    now = utc_now()
    with open_sms_stores(context) as (finance, store):
        settings = svc.settings_for(store, context)
        if not settings.ai.enabled or settings.ai.opted_in_at is None:
            raise _conflict("AI help is off. Turn it on under Finance, iPhone SMS, Settings if you want it.")
        message = store.get_message(context, message_id)
        if message is None:
            raise _not_found("Message")
        if not message.body_masked:
            raise _conflict("The message text is no longer kept, so there is nothing to read.")
        today = today_in(settings_for(finance, context))
        used = settings.ai.used if settings.ai.used_on == today else 0
        if used >= settings.ai.daily_cap:
            raise HTTPException(status_code=429, detail=f"You used today's {settings.ai.daily_cap} AI suggestions. Try again tomorrow or enter the fields yourself.")
        text = message.body_masked
        updated = settings.model_copy(deep=True)
        updated.ai.used_on, updated.ai.used = today, used + 1
        updated.version = settings.version + 1
        store.save_settings(context, updated, expected_version=settings.version)
    if not sms_ai.has_model(context):
        raise _conflict("This workspace has no AI model set up. Add one in Settings, Models, or enter the fields yourself.")
    try:
        fields = sms_ai.suggest(context, text)  # no transaction is open here
    except ModelBudgetExceededError:
        raise HTTPException(status_code=429, detail="The workspace's monthly AI budget is used up.") from None
    except Exception:
        logger.exception("finance_sms_suggest_failed")
        raise HTTPException(status_code=503, detail="The AI model did not answer. Try again, or enter the fields yourself.") from None
    _audit(repository, context, "finance.sms.message.suggest", message_id, {"fields": sorted(fields)})
    return Suggestion(fields=fields, used_today=used + 1, daily_cap=settings.ai.daily_cap)


# -- rules --------------------------------------------------------------------------------------


@router.get("/sms/rules", response_model=list[RuleView])
def list_rules(context: TenantContext = Depends(tenant_context)) -> list[RuleView]:
    _read(context)
    with open_sms_stores(context) as (_finance, store):
        return [_rule_view(r) for r in store.list_rules(context)]


@router.post("/sms/rules/{rule_id}/{change}", response_model=RuleView)
def change_rule(rule_id: str, change: Literal["activate", "disable"], context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> RuleView:
    _manage(context)
    with open_sms_stores(context) as (_finance, store):
        rule = store.get_rule(context, rule_id)
        if rule is None:
            raise _not_found("Rule")
        rule.status = RuleStatus.ACTIVE if change == "activate" else RuleStatus.DISABLED
        rule.updated_at = utc_now()
        store.save_rule(rule)
    _audit(repository, context, f"finance.sms.rule.{change}", rule_id)
    return _rule_view(rule)


@router.delete("/sms/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    _manage(context)
    with open_sms_stores(context) as (_finance, store):
        if not store.delete_rule(context, rule_id):
            raise _not_found("Rule")
    _audit(repository, context, "finance.sms.rule.delete", rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- statement reconciliation --------------------------------------------------------------------


class ReconcilePair(BaseModel):
    statement_transaction_id: str
    entry_id: str
    account_id: str
    posted_on: date
    amount: Decimal
    currency: str
    description: str


class ReconcileView(BaseModel):
    import_id: str
    matched: list[ReconcilePair]
    statement_only: int  # statement rows no message reported (they stay as imported)
    sms_only: list[str]  # recorded messages' entries in the statement's dates that the statement does not show
    note: str = "A matched statement row duplicates a transaction an SMS already recorded. Confirming removes the statement copy and marks the SMS entry as reconciled."


def _reconcile(finance: Any, store: Any, context: TenantContext, import_id: str) -> ReconcileView:
    record = finance.get_import(context, import_id)
    if record is None:
        raise _not_found("Import")
    rows, _ = finance.list_transactions(context, TransactionFilter(account_id=record.account_id), limit=5_000, offset=0)
    statement = [t for t in rows if t.import_id == import_id]
    if not statement:
        return ReconcileView(import_id=import_id, matched=[], statement_only=0, sms_only=[])
    start, end = min(t.posted_on for t in statement), max(t.posted_on for t in statement)
    candidates = []
    for entry in store.list_entries(context, start=start - timedelta(days=3), end=end + timedelta(days=3)):
        if entry.status != EntryStatus.POSTED or entry.reconciled_at is not None or entry.source in (
                EntrySource.REVERSAL, EntrySource.MANUAL, EntrySource.STATEMENT):
            continue
        for posting in entry.postings:
            if posting.account_id == record.account_id:
                candidates.append((entry, posting))
    used: set[str] = set()
    matched: list[ReconcilePair] = []
    for txn in sorted(statement, key=lambda t: t.posted_on):
        best = None
        for entry, posting in candidates:
            if entry.id in used or posting.amount != txn.amount:
                continue
            gap = abs((entry.posted_on - txn.posted_on).days)
            if gap <= 3 and (best is None or gap < best[0]):
                best = (gap, entry)
        if best is not None:
            used.add(best[1].id)
            matched.append(ReconcilePair(statement_transaction_id=txn.id, entry_id=best[1].id, account_id=txn.account_id, posted_on=txn.posted_on,
                                         amount=txn.amount, currency=txn.currency, description=txn.description))
    sms_only = sorted({e.id for e, _ in candidates if e.id not in used and start <= e.posted_on <= end})
    return ReconcileView(import_id=import_id, matched=matched, statement_only=len(statement) - len(matched), sms_only=sms_only)


@router.get("/sms/reconcile/{import_id}", response_model=ReconcileView)
def reconcile_preview(import_id: str, context: TenantContext = Depends(tenant_context)) -> ReconcileView:
    _read(context)
    with open_sms_stores(context) as (finance, store):
        return _reconcile(finance, store, context, import_id)


class ReconcileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement_transaction_ids: list[str] = Field(min_length=1, max_length=5_000)


class ReconcileResult(BaseModel):
    reconciled: int
    removed_statement_rows: int


@router.post("/sms/reconcile/{import_id}", response_model=ReconcileResult)
def reconcile_confirm(import_id: str, body: ReconcileInput, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> ReconcileResult:
    _manage(context)
    now = utc_now()
    wanted = set(body.statement_transaction_ids)
    with open_sms_stores(context) as (finance, store):
        view = _reconcile(finance, store, context, import_id)
        pairs = [p for p in view.matched if p.statement_transaction_id in wanted]
        if len(pairs) != len(wanted):
            raise _conflict("Some rows no longer match a recorded message. Reload the comparison and try again.")
        for pair in pairs:
            # The statement copy is undone (its own entry reversed) and becomes a source of the recorded payment.
            copy = store.entry_for_transaction(context, pair.statement_transaction_id)
            if copy is not None:
                svc.reverse(finance, store, context, copy, now=now, why="reconciled with a recorded message")
                kept = store.get_entry(context, pair.entry_id)
                link_hooks.note_moved(copy.canonical_id or copy.id, (kept.canonical_id or kept.id) if kept else None)  # links move to the kept one
            else:
                finance.delete_transaction(context, pair.statement_transaction_id)
            store.mark_entry(context, pair.entry_id, reconciled_at=now, reconciled_ref=f"{import_id}:{pair.statement_transaction_id}")
            store.add_message(SmsMessage(
                id=new_id("fin_sms"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                device_id=None, source="statement", idem_key=svc.secret_hash(f"reconcile:{import_id}:{pair.statement_transaction_id}"), received_at=now,
                sender="statement", status=MessageStatus.ATTACHED, reason="attached_to_existing", entry_id=pair.entry_id, rule="statement",
                parsed={"amount": str(abs(pair.amount)), "currency": pair.currency, "occurred_on": pair.posted_on.isoformat(),
                        "merchant": pair.description[:60], "account_id": pair.account_id, "import_id": import_id}, created_at=now, updated_at=now))
    _audit(repository, context, "finance.sms.reconcile", import_id, {"count": len(pairs)})
    return ReconcileResult(reconciled=len(pairs), removed_statement_rows=len(pairs))


# -- the ledger dashboard ------------------------------------------------------------------------


class SyncView(BaseModel):
    enabled: bool
    devices_active: int
    last_received_at: Any
    last_ok_at: Any
    last_error: str | None
    last_error_at: Any
    review_count: int


class DebtPlan(BaseModel):
    id: str
    name: str
    currency: str
    balance: Decimal
    minimum_payment: Decimal | None
    months_to_clear: int | None  # at the minimum payment and the APR, None when it never clears


class LedgerDashboard(L.Dashboard):
    ledger_kind: LedgerKind
    base_currency: str
    bills_due: list[Any]
    goals: list[Any]
    debts: list[DebtPlan]
    sync: SyncView
    filters: dict[str, str | None]


def _months_to_clear(balance: Decimal, apr: Decimal | None, payment: Decimal | None) -> int | None:
    if payment is None or payment <= 0 or balance <= 0:
        return None if balance > 0 else 0
    rate = (apr or Decimal(0)) / Decimal(1200)
    remaining, months = balance, 0
    while remaining > 0 and months < 600:
        remaining = remaining + remaining * rate - payment
        months += 1
        if rate and remaining >= balance:
            return None
    return months if remaining <= 0 else None


@router.get("/ledger/dashboard", response_model=LedgerDashboard)
def ledger_dashboard(
    month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    account_id: str | None = Query(default=None, max_length=80),
    currency: str | None = Query(default=None, pattern=r"^[A-Z]{3}$"),
    category: str | None = Query(default=None, max_length=40),
    kind: str | None = Query(default=None, max_length=40),
    context: TenantContext = Depends(tenant_context),
) -> LedgerDashboard:
    _read(context)
    from . import obligations

    links = obligations.load(context)  # its own short unit of work, before the dashboard's
    with open_sms_stores(context) as (finance, store):
        fsettings = settings_for(finance, context)
        today = today_in(fsettings)
        chosen, _start, month_end = month_range(month, today)
        window_end = max(today, month_end)
        first = date(int(chosen[:4]) - 1, int(chosen[5:7]), 1)
        year_start = date(today.year, 1, 1)
        window_start = min(first, year_start)
        accounts = finance.list_accounts(context)
        by_id = {a.id: a for a in accounts}
        entries = store.list_entries(context, start=window_start, end=window_end)
        linked = store.linked_transaction_ids(context)
        rows: list[L.Row] = []
        # Statement and hand-entered rows are read from the rows themselves by today's rules (flows.py), so a
        # card payment or refund stored by older rules is counted the same way as a new one; their journal
        # entries (and the reversals of them) are skipped so nothing counts twice.
        plain_kinds = {L.EntrySource.MANUAL.value, L.EntrySource.STATEMENT.value}
        plain = {e.source_ref for e in entries if e.kind in plain_kinds and e.source_ref}
        # Money lent or borrowed with a person is a transfer on both paths (flows.py, obligations.Links.lending_ids).
        lending = links.lending_ids
        for entry in entries:
            if entry.kind in plain_kinds:
                continue
            lent = bool(lending) and any(p.transaction_id in lending for p in entry.postings if p.transaction_id)
            for p in entry.postings:
                ledger_account = L.lending_account(p.ledger_account, p.account_id) if lent else p.ledger_account
                rows.append(L.Row(day=entry.posted_on, ledger_account=ledger_account, account_id=p.account_id, amount=p.amount, currency=p.currency,
                                  kind=entry.kind, category=entry.category, merchant=entry.merchant, entry_id=entry.id))
        window_rows = finance.transactions_between(context, window_start, window_end)
        # Rows no rule recognised are in no figure: the dashboard says how many (the summary's rule, flows.py).
        kinds = {a.id: a.kind for a in accounts}
        uncategorized = sum(1 for t in window_rows if _start <= t.posted_on <= month_end and t.id not in lending
                            and flow_of(t, kinds) == Flow.UNCATEGORIZED)
        for txn in window_rows:
            account = by_id.get(txn.account_id)
            if (txn.id in linked and txn.id not in plain) or account is None:
                continue
            for leg in L.derived_legs(txn, account, lending=txn.id in lending):
                rows.append(L.Row(day=txn.posted_on, ledger_account=leg.ledger_account, account_id=leg.account_id, amount=leg.amount, currency=leg.currency,
                                  kind="statement", category=txn.category.value, merchant=txn.description[:60] if leg.account_id is None else None,
                                  entry_id=f"txn:{txn.id}"))
        sums = finance.account_sums(context)
        balances = {a.id: a.opening_balance + sums.get(a.id, Decimal(0)) for a in accounts}
        settings = svc.settings_for(store, context)
        limits = {m.account_id: m.credit_limit for m in settings.account_map if m.credit_limit}
        observed = store.latest_observations(context)
        suspense = store.suspense_totals(context)
        bills = upcoming_bills([b for b in finance.list_items(context, "bill") if getattr(b, "active", True)], today, links=links.bill_links)
        goals = [
            GoalProgress(id=g.id, name=g.name, currency=g.currency, target_amount=g.target_amount, saved_amount=g.saved_amount,
                         percent=max(0, min(100, int(g.saved_amount * 100 / g.target_amount))) if g.target_amount > 0 else 0,
                         target_on=g.target_on).model_dump(mode="json")
            for g in obligations.current_goals(finance.list_items(context, "goal"), accounts, sums)  # type: ignore[arg-type]
        ]
        debts = [
            DebtPlan(id=d.id, name=d.name, currency=d.currency, balance=d.balance, minimum_payment=d.minimum_payment,
                     months_to_clear=_months_to_clear(d.balance, d.apr_percent, d.minimum_payment))
            for d in obligations.current_debts(finance.list_items(context, "debt"), accounts, sums, links, today)  # type: ignore[arg-type]
        ]
    built = L.dashboard(month=chosen, today=today, accounts=accounts, rows=rows,
                        filters=L.Filters(account_id=account_id, currency=currency, category=category, kind=kind),
                        limits=limits, observed=observed, balances=balances, suspense=suspense, uncategorized_count=uncategorized)
    status_view = _status(context)
    return LedgerDashboard(
        **built.model_dump(), ledger_kind=settings.ledger_kind, base_currency=fsettings.base_currency,
        bills_due=[b.model_dump(mode="json") for b in bills], goals=goals, debts=debts,
        sync=SyncView(enabled=status_view.enabled, devices_active=status_view.devices_active, last_received_at=status_view.last_received_at,
                      last_ok_at=status_view.last_ok_at, last_error=status_view.last_error, last_error_at=status_view.last_error_at,
                      review_count=status_view.review_count),
        filters={"account_id": account_id, "currency": currency, "category": category, "kind": kind},
    )


class EntryView(BaseModel):
    id: str
    canonical_id: str | None
    posted_on: date
    kind: str
    category: str | None
    merchant: str | None
    description: str
    source: str
    source_ref: str | None
    status: str
    reverses_id: str | None
    reversed_by_id: str | None
    original_amount: Decimal | None
    original_currency: str | None
    fx_rate: Decimal | None
    reconciled_at: Any
    postings: list[dict[str, Any]]
    sources: list[dict[str, Any]]  # the reports behind this one transaction: "sms" and "wallet"


class EntryPage(BaseModel):
    items: list[EntryView]
    total: int
    limit: int
    offset: int


class CanonicalView(BaseModel):
    """One canonical transaction: its current entry, every entry of its chain and every source behind it."""

    canonical_id: str
    current: EntryView | None  # None once the whole chain was undone
    chain: list[EntryView]
    transaction_ids: list[str]  # the stage 2 rows of the current entry


def _entry_view(e: Any, sources: dict[str, list[SmsMessage]]) -> EntryView:
    return EntryView(**e.model_dump(exclude={"postings", "tenant_id", "workspace_id", "owner_user_id"}),
                     postings=[{"ledger_account": p.ledger_account, "account_id": p.account_id, "amount": str(p.amount), "currency": p.currency}
                               for p in e.postings],
                     sources=[{"kind": source_kind(m.source), "message_id": m.id, "status": m.status.value, "received_at": m.received_at.isoformat()}
                              for m in sources.get(e.id, [])])


@router.get("/ledger/transactions/{canonical_id}", response_model=CanonicalView)
def canonical_transaction(canonical_id: str, context: TenantContext = Depends(tenant_context)) -> CanonicalView:
    """The stable id other modules link to (docs/finance-sms.md#canonical-transactions)."""
    _read(context)
    with open_sms_stores(context) as (_finance, store):
        chain = store.chain(context, canonical_id)
        if not chain:
            raise _not_found("Transaction")
        sources = store.entry_sources(context, [e.id for e in chain])
    current = next((e for e in reversed(chain) if e.status == EntryStatus.POSTED and e.source != EntrySource.REVERSAL), None)
    return CanonicalView(canonical_id=canonical_id, current=_entry_view(current, sources) if current else None,
                         chain=[_entry_view(e, sources) for e in chain],
                         transaction_ids=[p.transaction_id for p in (current.postings if current else []) if p.transaction_id])


@router.get("/ledger/entries", response_model=EntryPage)
def ledger_entries(limit: int = Query(default=25, ge=1, le=200), offset: int = Query(default=0, ge=0),
                   context: TenantContext = Depends(tenant_context)) -> EntryPage:
    """The journal, newest first: every entry with its balanced postings."""
    _read(context)
    with open_sms_stores(context) as (_finance, store):
        entries = store.list_entries(context, limit=offset + limit)
        page = entries[offset:offset + limit]
        sources = store.entry_sources(context, [e.id for e in page])
    items = [_entry_view(e, sources) for e in page]
    return EntryPage(items=items, total=len(entries) if len(entries) < offset + limit else offset + limit + 1, limit=limit, offset=offset)
