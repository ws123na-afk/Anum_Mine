"""Background sync of the Email Center (docs/email.md#background-sync).

:class:`MailSyncRunner` runs in every API replica every ``ANUM_MAILSERVER_SYNC_POLL_SECONDS``
(PostgreSQL or memory). It finds mailboxes due a check as ``anum_maintenance`` (ids, scope, the
mailbox status and the scheduling columns only), then for each one, in its owner's own scope as
the application role:

1. claims it with a compare-and-set lease (one replica wins; a replica that stops mid-check lets
   the lease run out and the next pass takes over);
2. reads the folder list (IMAP STATUS: nothing is marked read) and keeps the inbox's unread and
   total counts, which the sidebar badge reads through ``GET /api/v1/mail/unread``;
3. runs the mailbox's active forwarding rules over new inbox mail. Every rule is checked again
   first: still ``active`` on the hash that was approved, recipients still valid, the owner still
   a member, the Email Center still on, and the mailbox's hourly send limit (sends and forwards
   together). A rule that fails a check is paused with the reason and never forwards; a message
   is forwarded at most once (a unique row per rule and UID) and never when it is itself an
   automatic message (loop guard). Each rule's cursor (``last_uid``) is persisted; a pass reads
   at most ``SCAN`` messages forward from it, so a burst of any size is worked off over the
   next passes instead of being skipped;
4. stores the counts and when it is next due (later after a failure; a refused password is left
   alone until the owner enters it again, so ANUM never keeps trying a wrong password).

Polling, not IMAP IDLE: see docs/email.md#background-sync for why.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesHeaderParser
from typing import Any

from sqlalchemy import text

from ..audit import AuditRecord
from ..schemas import TenantContext, new_id, utc_now
from ..settings import settings
from . import monitor
from .automation import SYNC_LEASE, ForwardLog, ForwardRule, SyncState, automation_backend, hash_of, open_automation_store, rule_matches
from .automation_tools import forward_problem
from .config import mail_settings
from .mime import Outgoing, build_message, forward_subject, new_message_id, parse_message, quoted
from .server import Folder, MailServer, Summary
from .service import MailError, folder_by_role, on_server, require_available
from .store import AccountStatus, MailAccount, mail_backend, open_mail_store

logger = logging.getLogger("anum.mail")
BATCH = 50
# A pass reads at most this many new inbox messages (headers only), forward from the lowest rule
# cursor. A bigger burst is read over the next passes: never skipped.
SCAN = 500
# A mailbox still working off a burst is due again this soon (the next runner poll takes it).
CATCH_UP = timedelta(seconds=1)
RULE_HEADER = "X-ANUM-Forward-Rule"

_DUE = text(
    "select a.tenant_id, a.workspace_id, a.owner_user_id, a.id from mail_accounts a "
    "left join mail_sync_state s on s.account_id = a.id "
    "left join mail_account_settings c on c.account_id = a.id "
    "where a.status <> 'auth_failed' and coalesce(c.background_sync, true) "
    "and (s.account_id is null or (s.next_sync_at <= :now and (s.lease_until is null or s.lease_until < :now))) "
    "order by s.next_sync_at nulls first, a.id limit :limit"
)


def due_mailboxes(now: datetime, limit: int = BATCH) -> list[tuple[str, str, str, str]]:
    if settings.repository_backend == "memory":
        with mail_backend.lock, automation_backend.lock:
            found = []
            for account in mail_backend.accounts.values():
                kept = automation_backend.settings.get(account.id)
                state = automation_backend.sync.get(account.id)
                if account.status == AccountStatus.AUTH_FAILED or (kept is not None and not kept.background_sync):
                    continue
                if state is None or (state.next_sync_at <= now and (state.lease_until is None or state.lease_until < now)):
                    found.append((account.tenant_id, account.workspace_id, account.owner_user_id, account.id))
        return found[:limit]
    from ..maintenance import default_session_factory, discover

    rows = discover(default_session_factory, _DUE, {"now": now, "limit": limit})
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def _audit(context: TenantContext, action: str, target: str, outcome: str, metadata: dict[str, Any]) -> None:
    from ..dependencies import _unit_of_work

    with _unit_of_work(context, require_membership=False) as repository:
        repository.record_audit(
            AuditRecord(
                id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor="anum.mail.sync",
                action=action, target=target, outcome=outcome, correlation_id=target, created_at=utc_now(), metadata=metadata,
            )
        )


def _automatic(raw: bytes) -> bool:
    """A message another system sent on its own (or one of ANUM's forwards): never forwarded."""
    headers = BytesHeaderParser(policy=policy.default).parsebytes(raw)
    submitted = str(headers.get("auto-submitted") or "no").strip().lower()
    return submitted != "no" or headers.get(RULE_HEADER) is not None


@dataclass
class PassResult:
    forwarded: int = 0
    failed: int = 0
    # More new mail waits than this pass read or could forward: check the mailbox again soon.
    behind: bool = False


class _Budget:
    """The mailbox's hourly send limit (sends and forwards together) and the per-pass cap."""

    def __init__(self, left: int) -> None:
        self.left = max(0, left)

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def _sends_left(context: TenantContext, account: MailAccount, rules: list[ForwardRule], now: datetime) -> tuple[int, int]:
    """What the hour still allows (sends and forwards together) and this pass's own cap."""
    from .writers import recent_sends

    hour = now - timedelta(hours=1)
    # Every writer's sends count: a mailbox shared to send has one hourly limit, not one per person.
    sent = recent_sends(account.id, hour)
    with open_automation_store(context) as store:
        forwarded = store.count_forwards_since([rule.id for rule in rules], hour)
    return max(0, mail_settings.send_per_hour - sent - forwarded), mail_settings.forward_per_pass


def _forward_raw(account: MailAccount, rule: ForwardRule, original: bytes) -> bytes:
    parsed = parse_message(original)
    files = len(parsed.attachments)
    intro = f"Forwarded automatically by the rule '{rule.name}' of {account.address} in ANUM."
    if files:
        intro += f" The original has {files} attachment(s); open it in the mailbox to see them."
    return build_message(Outgoing(
        sender_name=account.display_name or "", sender_address=account.address, to=list(rule.forward_to), cc=[], bcc=[],
        subject=forward_subject(parsed.subject), body=intro + quoted(parsed, forward=True), message_id=new_message_id(account.address),
        headers=(("Auto-Submitted", "auto-forwarded"), (RULE_HEADER, rule.id)),
    ))


async def _forward_one(context: TenantContext, account: MailAccount, inbox: Folder, rule: ForwardRule, item: Summary) -> str:
    """Forward one message for one rule, at most once. Returns forwarded, failed, unknown or skipped."""
    with open_automation_store(context) as store:
        fresh = store.get_rule(rule.id)
        if fresh is None or fresh.status != "active" or fresh.approved_hash != rule.approved_hash or hash_of(fresh) != rule.approved_hash:
            return "stopped"
        claimed = store.claim_forward(ForwardLog(
            id=new_id("mfwd"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
            rule_id=rule.id, uid=item.uid, message_id=item.message_id,
        ))
    if not claimed:
        return "skipped"

    def finish(status: str, error: str | None) -> None:
        with open_automation_store(context) as store:
            store.finish_forward(rule.id, item.uid, status, error)  # type: ignore[arg-type]

    try:
        original = await on_server(context, account, lambda server: server.fetch(inbox.id, item.uid, mark_seen=False, max_bytes=mail_settings.message_max_bytes))
        if _automatic(original):
            await asyncio.to_thread(finish, "failed", "Not forwarded: an automatic message (loop guard).")
            return "skipped"
        raw = _forward_raw(account, rule, original)
        await on_server(context, account, lambda server: server.send(account.address, list(rule.forward_to), raw))
    except MailError as exc:
        status = "unknown" if exc.kind == "uncertain" else "failed"
        await asyncio.to_thread(finish, status, exc.message[:500])
        await asyncio.to_thread(monitor.bump, context, "send_unknown" if status == "unknown" else "send_failed")
        await asyncio.to_thread(_audit, context, "mail.forward.rule", rule.id, "failure", {"rule_id": rule.id, "uid": item.uid, "kind": exc.kind})
        return status
    await asyncio.to_thread(finish, "forwarded", None)
    await asyncio.to_thread(monitor.bump, context, "sent")
    await asyncio.to_thread(_audit, context, "mail.forward.rule", rule.id, "success", {"rule_id": rule.id, "uid": item.uid, "recipients": len(rule.forward_to)})

    def save_copy(server: MailServer) -> None:
        sent = folder_by_role(server.folders(), "sent")
        if sent is not None:
            server.append(sent.id, raw, seen=True)

    try:
        await on_server(context, account, save_copy)
    except MailError as exc:
        logger.warning("mail.forward_copy_failed", extra={"anum_kind": exc.kind})
    return "forwarded"


async def run_rules(context: TenantContext, account: MailAccount, inbox: Folder | None, now: datetime) -> PassResult:
    """One pass of the mailbox's active forwarding rules over new inbox mail.

    Each rule's ``last_uid`` (with ``last_uidvalidity``) is its persisted cursor. A pass reads at
    most SCAN messages forward from the lowest cursor, oldest first, and moves each rule's cursor
    only past the messages it finished. A burst bigger than SCAN, or than the per-pass send cap,
    is worked off over the next passes (``PassResult.behind`` brings the mailbox back soon), so no
    message is skipped. A message is forwarded at most once: the forward record (unique per rule
    and UID) is claimed in its own short unit of work before anything is sent, and no transaction
    is open during an IMAP or SMTP call.
    """
    result = PassResult()
    with open_automation_store(context) as store:
        rules = [rule for rule in store.list_rules(account.id) if rule.status == "active"]
    if not rules or inbox is None:
        return result
    checked: list[ForwardRule] = []
    for rule in rules:
        problem = None
        if not rule.approved_hash or rule.approved_hash != rule.rule_hash or hash_of(rule) != rule.approved_hash:
            problem = "Stopped: the rule changed after it was approved. Ask for approval again."
        else:
            reason = forward_problem(rule.forward_to, account.address)
            problem = f"Stopped: {reason}" if reason else None
        if problem:
            with open_automation_store(context) as store:
                store.refuse_rule(rule.id, problem, now)
            await asyncio.to_thread(_audit, context, "mail.forward.refused", rule.id, "failure", {"rule_id": rule.id})
            continue
        checked.append(rule)
    if not checked:
        return result
    validity = inbox.uidvalidity

    def restarts(rule: ForwardRule) -> bool:
        # Never run yet, or the inbox's UIDs were renumbered (UIDVALIDITY changed): start from the newest.
        return rule.last_uid is None or (validity is not None and rule.last_uidvalidity is not None and rule.last_uidvalidity != validity)

    starting = [rule for rule in checked if restarts(rule)]
    running = [rule for rule in checked if not restarts(rule)]
    if starting:
        # The first pass after approval (or after the UIDs were renumbered) only marks where the
        # rule starts: older mail is not forwarded, and old forward records above it go.
        newest_page = await on_server(context, account, lambda server: server.messages(inbox.id, before=None, limit=1, query=None, unread=False, flagged=False))
        newest = max((item.uid for item in newest_page.items), default=0)
        for rule in starting:
            with open_automation_store(context) as store:
                store.restart_rule(rule.id, rule.approved_hash or "", newest, validity, now)
    if not running:
        return result
    floor = min(rule.last_uid or 0 for rule in running)
    page = await on_server(context, account, lambda server: server.messages_after(inbox.id, after=floor, limit=SCAN))
    items = sorted(page.items, key=lambda item: item.uid)
    # This batch only; the rest of a burst waits for the next pass.
    if page.total > len(items):
        result.behind = True
    hour_left, pass_cap = await asyncio.to_thread(_sends_left, context, account, checked, now)
    budget = _Budget(min(hour_left, pass_cap))
    for rule in running:
        approved = rule.approved_hash or ""
        last, forwarded, error = rule.last_uid, 0, None
        for item in (i for i in items if i.uid > (rule.last_uid or 0)):
            sender = f"{item.sender.name} <{item.sender.address}>" if item.sender else ""
            own = item.sender is not None and item.sender.address.lower() == account.address.lower()
            if own or not rule_matches(rule, sender, item.subject):
                last = item.uid
                continue
            if not budget.take():
                if hour_left <= pass_cap:
                    error = f"Waiting: this mailbox reached its limit of {mail_settings.send_per_hour} messages an hour. The rest go when the hour allows."
                else:
                    # Only this pass's cap: the backlog continues on the next pass, soon.
                    result.behind = True
                    error = None
                break
            outcome = await _forward_one(context, account, inbox, rule, item)
            if outcome == "stopped":
                break
            if outcome == "skipped":
                budget.left += 1  # nothing was sent
            last = item.uid
            if outcome == "forwarded":
                forwarded += 1
            elif outcome in {"failed", "unknown"}:
                result.failed += 1
                error = "The last forward was not sent. Check the mailbox's Sent folder and the recipients."
        result.forwarded += forwarded
        with open_automation_store(context) as store:
            store.record_run(rule.id, approved, last, forwarded, error, now, validity)
    return result


def _still_a_member(context: TenantContext) -> bool:
    from .outgoing import _still_a_member as check

    return check(context)


async def handle_mailbox(tenant_id: str, workspace_id: str, owner_user_id: str, account_id: str, now: datetime | None = None) -> str:
    """One mailbox's background check, in its owner's scope. Returns what happened (logs, tests)."""
    now = now or utc_now()
    context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=owner_user_id, roles=["member"])
    interval = timedelta(seconds=mail_settings.sync_interval_seconds)
    state = SyncState(account_id=account_id, tenant_id=tenant_id, workspace_id=workspace_id, owner_user_id=owner_user_id)

    def claim() -> tuple[bool, MailAccount | None, SyncState | None]:
        with open_mail_store(context) as mail:
            account = mail.get_account(account_id)
        if account is None:
            return False, None, None
        with open_automation_store(context) as store:
            if not store.claim_sync(state, now, now + SYNC_LEASE):
                return False, account, None
            return True, account, store.get_sync(account_id)

    claimed, account, current = await asyncio.to_thread(claim)
    if account is None:
        return "gone"
    if not claimed:
        return "skipped"
    base = current or state

    def finish(status: str, error: str | None, wait: timedelta, unread: int | None = None, total: int | None = None) -> None:
        kept = base.model_copy(update={
            "status": status, "last_error": error, "next_sync_at": now + wait, "lease_until": None,
            **({"inbox_unread": unread, "inbox_total": total, "checked_at": now} if status == "ok" else {}),
        })
        with open_automation_store(context) as store:
            store.save_sync(kept)

    try:
        require_available(context)
    except MailError as exc:
        await asyncio.to_thread(finish, "error", exc.message[:500], interval * 12)
        return "unavailable"
    if not await asyncio.to_thread(_still_a_member, context):
        await asyncio.to_thread(finish, "error", "The mailbox's owner is no longer a member of this workspace.", interval * 12)
        return "not_member"
    try:
        folders = await on_server(context, account, lambda server: server.folders())
        inbox = folder_by_role(folders, "inbox")
        forwarded = await run_rules(context, account, inbox, now)
    except MailError as exc:
        status = "auth_failed" if exc.kind == "auth" else "offline" if exc.kind in {"offline", "tls", "uncertain"} else "error"
        await asyncio.to_thread(finish, status, exc.message[:500], interval * (12 if status == "auth_failed" else 2))
        return status
    wait = CATCH_UP if forwarded.behind else interval
    await asyncio.to_thread(finish, "ok", None, wait, inbox.unread if inbox else 0, inbox.total if inbox else 0)
    return "forwarded" if forwarded.forwarded else "checked"


async def run_once(now: datetime | None = None, limit: int = BATCH) -> dict[str, int]:
    """Check every due mailbox once; one mailbox's failure never stops the others."""
    now = now or utc_now()
    counts: dict[str, int] = {}
    for tenant_id, workspace_id, owner, account_id in await asyncio.to_thread(due_mailboxes, now, limit):
        try:
            outcome = await handle_mailbox(tenant_id, workspace_id, owner, account_id, now)
        except Exception:  # noqa: BLE001 - logged with ids only; the lease runs out and the next pass looks again
            logger.exception("mail.sync_failed", extra={"anum_mail": {"account_id": account_id}})
            outcome = "error"
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


class MailSyncRunner:
    """Every ``ANUM_MAILSERVER_SYNC_POLL_SECONDS``: check due mailboxes, safe on every replica."""

    def __init__(self, poll_interval: float) -> None:
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    def start(self) -> None:
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="anum-mail-sync")

    async def stop(self, timeout: float = 60.0) -> None:
        # A check in progress finishes (its lease covers it); the loop does not start another.
        task, self._task = self._task, None
        if self._stop is not None:
            self._stop.set()
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except Exception:
                logger.exception("Mail sync runner stopped with an error")

    async def _run(self) -> None:
        stop = self._stop
        if stop is None:
            raise RuntimeError("Mail sync runner started without a stop event")
        while not stop.is_set():
            try:
                await run_once()
            except Exception:
                logger.exception("Mail sync pass failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass


def build_sync_runner() -> MailSyncRunner | None:
    if not mail_settings.enabled or mail_settings.sync_poll_seconds <= 0:
        return None
    return MailSyncRunner(mail_settings.sync_poll_seconds)
