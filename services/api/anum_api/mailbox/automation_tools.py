"""The two approval-only tools of mailbox settings and forwarding rules (docs/email.md#mailbox-settings).

* ``change_mail_settings`` applies a change to a mailbox's retention and background sync, and
  only when the settings are still at the version the change was asked against (a change made
  in between makes the approval stale: ask again).
* ``activate_mail_forward_rule`` turns a forwarding rule on, and only when the rule still does
  exactly what the approval named (its hash); any edit after asking makes it refuse.

Both are high risk with ``always_ask``: workspace autonomy can never let them run without a
person's approval bound to the payload hash. Both act in the mailbox owner's own scope.
"""

from __future__ import annotations

from typing import Any

from ..agent_tools import ToolCall, ToolDefinition, ToolResult
from ..schemas import RiskLevel, TenantContext, utc_now
from .automation import (
    MAX_FORWARD_TO,
    MAX_RETENTION_DAYS,
    SETTING_FIELDS,
    default_settings,
    hash_of,
    open_automation_store,
    settings_values,
)
from .config import mail_settings
from .mime import valid_address
from .service import MailError, require_available
from .store import open_mail_store

SETTINGS_TOOL = "change_mail_settings"
RULE_TOOL = "activate_mail_forward_rule"
_PEOPLE = frozenset({"owner", "member"})


def _owner(call: ToolCall, context: TenantContext) -> TenantContext:
    owner = str(call.arguments.get("owner_user_id") or "")
    return context.model_copy(update={"user_id": owner}) if owner else context


def _failed(summary: str, kind: str) -> ToolResult:
    return ToolResult(status="failed", summary=summary, output={"kind": kind})


def retention_value(value: object) -> int | None:
    """A retention in days: ``None`` keeps, else 1 to 3650 (anything else raises ``ValueError``)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_RETENTION_DAYS:
        raise ValueError("retention")
    return value


def forward_problem(addresses: list[str], mailbox: str) -> str | None:
    """Why these addresses cannot be a rule's recipients now, or None (checked at every run)."""
    if not addresses:
        return "Name at least one address to forward to."
    if len(addresses) > min(MAX_FORWARD_TO, mail_settings.max_recipients):
        return f"A rule forwards to at most {min(MAX_FORWARD_TO, mail_settings.max_recipients)} addresses."
    for address in addresses:
        if not valid_address(address):
            return f"'{address[:80]}' is not an email address."
        if address.strip().lower() == mailbox.strip().lower():
            return "A rule cannot forward a mailbox's mail to itself."
    return None


async def change_mail_settings(call: ToolCall, context: TenantContext) -> ToolResult:
    """Apply an approved change of one mailbox's settings (owner scope, version checked)."""
    args = call.arguments
    owner = _owner(call, context)
    try:
        require_available(owner)
    except MailError as exc:
        return _failed(f"Not changed: {exc.message}", exc.kind)
    account_id = str(args.get("account_id") or "")
    with open_mail_store(owner) as mail:
        account = mail.get_account(account_id)
    if account is None:
        return _failed("Not changed: the mailbox is gone, or it is no longer this person's.", "gone")
    changes = args.get("settings")
    if not isinstance(changes, dict) or set(changes) != set(SETTING_FIELDS):
        return _failed("Not changed: the approved call does not name every setting.", "invalid")
    try:
        values: dict[str, Any] = {key: retention_value(changes[key]) for key in SETTING_FIELDS if key != "background_sync"}
    except ValueError:
        return _failed("Not changed: a retention must be between 1 and 3650 days, or keep.", "invalid")
    if not isinstance(changes["background_sync"], bool):
        return _failed("Not changed: background sync must be on or off.", "invalid")
    values["background_sync"] = changes["background_sync"]
    base = args.get("base_version")
    if not isinstance(base, int) or isinstance(base, bool):
        return _failed("Not changed: the approved call does not name the settings version.", "invalid")
    with open_automation_store(owner) as store:
        current = store.get_settings(account.id)
        version = current.version if current else 0
        if version != base:
            return _failed("Not changed: the settings changed after this was asked. Look at them again and ask again.", "stale")
        item = (current or default_settings(account.id, owner.tenant_id, owner.workspace_id, owner.user_id)).model_copy(
            update={**values, "version": version + 1, "updated_by": str(args.get("requested_by") or owner.user_id), "updated_at": utc_now(),
                       "pending_approval_id": None, "pending_task_id": None}
        )
        if not store.save_settings(item, current.version if current else None):
            return _failed("Not changed: the settings changed at the same moment. Ask again.", "stale")
    return ToolResult(
        status="succeeded",
        summary=f"Changed the settings of {account.address}.",
        output={"account_id": account.id, "version": item.version, "settings": settings_values(item)},
        audit_metadata={"tool": SETTINGS_TOOL, "account_id": account.id, "version": item.version, **{key: values[key] for key in SETTING_FIELDS}},
    )


async def activate_mail_forward_rule(call: ToolCall, context: TenantContext) -> ToolResult:
    """Turn on an approved forwarding rule, only if it still does exactly what was approved."""
    args = call.arguments
    owner = _owner(call, context)
    try:
        require_available(owner)
    except MailError as exc:
        return _failed(f"Not turned on: {exc.message}", exc.kind)
    with open_mail_store(owner) as mail:
        account = mail.get_account(str(args.get("account_id") or ""))
    if account is None:
        return _failed("Not turned on: the mailbox is gone, or it is no longer this person's.", "gone")
    approved = str(args.get("rule_hash") or "")
    with open_automation_store(owner) as store:
        rule = store.get_rule(str(args.get("rule_id") or ""))
        if rule is None or rule.account_id != account.id:
            return _failed("Not turned on: the rule was removed.", "gone")
        if rule.status != "pending_approval" or hash_of(rule) != approved or rule.rule_hash != approved:
            return _failed("Not turned on: the rule changed after this was asked. Ask again from the rule.", "stale")
        problem = forward_problem(rule.forward_to, account.address)
        if problem:
            return _failed(f"Not turned on: {problem}", "invalid")
        now = utc_now()
        rule = rule.model_copy(update={
            "status": "active", "approved_hash": approved, "approved_at": now, "last_uid": None, "last_error": None, "updated_at": now,
        })
        store.save_rule(rule)
    return ToolResult(
        status="succeeded",
        summary=f"Turned on the forwarding rule '{rule.name}' of {account.address} to {', '.join(rule.forward_to)}.",
        output={"rule_id": rule.id, "account_id": account.id, "status": rule.status},
        audit_metadata={"tool": RULE_TOOL, "rule_id": rule.id, "account_id": account.id, "recipients": len(rule.forward_to)},
    )


AUTOMATION_TOOL_DEFINITIONS: list[tuple[ToolDefinition, Any]] = [
    (
        ToolDefinition(
            name=SETTINGS_TOOL,
            description="Change a mailbox's retention and background sync settings, after approval of exactly that change.",
            risk_level=RiskLevel.HIGH, required_roles=_PEOPLE, timeout_seconds=30, category="email", action_kind="update", always_ask=True,
        ),
        change_mail_settings,
    ),
    (
        ToolDefinition(
            name=RULE_TOOL,
            description="Turn on a rule that forwards matching new inbox mail to the named addresses, after approval of exactly that rule.",
            risk_level=RiskLevel.HIGH, required_roles=_PEOPLE, timeout_seconds=30, category="email", action_kind="send", always_ask=True,
        ),
        activate_mail_forward_rule,
    ),
]
