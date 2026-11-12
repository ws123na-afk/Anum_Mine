"""Mail domains and aliases on the Mailcow server, for the platform operator (docs/email.md#mail-domains).

Routes under ``/api/v1/platform/mail`` next to ``operator_api.py``; every one needs
``platform:operate`` (403 before anything is read):

* ``GET /domains``: the Mailcow domains with their aliases, the domains ``ANUM_MAILSERVER_ALLOWED_DOMAINS``
  names that Mailcow does not have yet, whether changes are switched on, and the changes waiting
  for an approval.
* ``GET /domains/{domain}/dns``: a live DNS check of MX, SPF, DKIM and DMARC (each lookup with a
  timeout) and the exact records to set at the DNS host. Only for allowed or Mailcow domains.
* ``POST /domains/changes``: ask to add or remove a domain, to add or remove an alias, or to add a
  mailbox. Nothing
  changes here: the change waits for an approval bound to its payload hash
  (``change_mail_domains``, high risk, always asked) and runs only when approved.
* ``DELETE /domains/changes/{approval_id}``: withdraw a change still waiting (nothing changes).

Changes stay inside ``ANUM_MAILSERVER_ALLOWED_DOMAINS``: a domain outside it is shown, never changed.
The approved call checks everything again against Mailcow before it writes (the domain still
allowed, still absent or still empty, the alias still the same, the mailbox address still free),
so an approval of something that changed in between refuses instead of acting on stale facts.

A new mailbox's first password is sealed with ``ANUM_SECRETS_KEY`` the moment it is asked for: the
approval binds the sealed value (``password_sealed``, shown to the approver as redacted), the
handler opens it only to pass it to Mailcow, and it is never returned, logged or audited. A
sealed password older than ``SEALED_PASSWORD_DAYS`` is refused (ask again). Mailcow makes the
mailbox's owner change it at the first sign-in.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, SecretStr

from ..agent_tools import ToolCall, ToolDefinition, ToolPolicyOutcome, ToolResult, holds_organization_permission
from ..authorization import Permission
from ..dependencies import repository_context, require_permission, tenant_context
from ..email_api import _pause_for_approval, _retire
from ..model_gateway import MockModelGateway
from ..repository import AnumRepository
from ..runtime import AgentRuntime
from ..approval_paging import pending_now
from ..schemas import Approval, ApprovalStatus, RiskLevel, TenantContext, utc_now
from ..secret_box import SecretDecryptionError, secret_cipher
from . import mailcow
from .api import _audit
from .config import mail_settings
from .domain_dns import DnsReport, check_domain, valid_domain
from .mime import valid_address

router = APIRouter(prefix="/api/v1/platform/mail", tags=["platform"])

DOMAIN_TOOL = "change_mail_domains"
Operation = Literal["add_domain", "remove_domain", "add_alias", "remove_alias", "add_mailbox"]
OPERATIONS = frozenset({"add_domain", "remove_domain", "add_alias", "remove_alias", "add_mailbox"})
MAX_GOTO = 10
# A new mailbox: its first password, its quota in MiB and how long a sealed password may wait.
PASSWORD_MIN, PASSWORD_MAX = 12, 128
QUOTA_MIN_MB, QUOTA_MAX_MB, QUOTA_DEFAULT_MB = 256, 102400, 3072
SEALED_PASSWORD_DAYS = 14
# Arguments left out when comparing a new request with the ones already waiting.
_NOT_COMPARED = frozenset({"requested_by", "password_sealed"})
LOCAL_PART = re.compile(r"^[a-z0-9](?:[a-z0-9._%+-]{0,62}[a-z0-9])?$")


class AliasView(BaseModel):
    id: str
    address: str
    goto: list[str]
    active: bool


class MailboxView(BaseModel):
    address: str
    name: str
    active: bool
    quota_mb: int | None
    used_percent: float | None


class DomainView(BaseModel):
    domain: str
    in_mailcow: bool
    active: bool
    # In ANUM_MAILSERVER_ALLOWED_DOMAINS: only those can be changed from ANUM.
    allowed: bool
    mailboxes: int
    max_mailboxes: int | None
    quota_used_percent: float | None
    aliases: list[AliasView]
    mailbox_list: list[MailboxView] = Field(default_factory=list)


class PendingChange(BaseModel):
    approval: Approval
    operation: Operation
    domain: str
    address: str | None
    goto: list[str]
    name: str | None = None
    quota_mb: int | None = None


class MailDomainsView(BaseModel):
    mailcow_state: str
    message: str | None
    can_change: bool
    change_note: str | None
    mail_host: str | None
    allowed_domains: list[str]
    domains: list[DomainView]
    pending: list[PendingChange]
    # False when Mailcow's mailbox list could not be read: no mailbox can be added then.
    mailboxes_read: bool = False


class ChangeBody(BaseModel):
    operation: Operation
    domain: str | None = Field(default=None, max_length=253)
    address: str | None = Field(default=None, max_length=254)
    goto: list[str] = Field(default_factory=list, max_length=MAX_GOTO)
    alias_id: str | None = Field(default=None, max_length=40)
    # add_mailbox: the display name, the first password (SecretStr, so a validation error never
    # echoes it) and the quota in MiB.
    name: str | None = Field(default=None, max_length=120)
    password: SecretStr | None = None
    quota_mb: int | None = None


class Waiting(BaseModel):
    status: Literal["waiting_approval"]
    summary: str
    approval: Approval


# -- helpers ------------------------------------------------------------------------------------


def mail_host() -> str | None:
    """The host MX records should point to: the setting, else the IMAP host, else the API host."""
    for value in (mailcow.mailcow_settings.mail_host, mail_settings.imap_host, urlsplit(mailcow.mailcow_settings.api_url.strip()).hostname or ""):
        host = (value or "").strip().lower().rstrip(".")
        if host and valid_domain(host):
            return host
    return None


def _allowed() -> frozenset[str]:
    return frozenset(d for d in (valid_domain(item) for item in mail_settings.domains) if d)


def _domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1] if "@" in address else ""


def _clean_alias(address: str | None) -> str | None:
    value = (address or "").strip().lower()
    if value.count("@") != 1:
        return None
    local, domain = value.split("@")
    return value if LOCAL_PART.match(local) and valid_domain(domain) == domain else None


def _clean_goto(values: list[str], alias: str) -> list[str] | str:
    """The destinations without repeats, or why they cannot be used."""
    out: list[str] = []
    for value in values:
        address = value.strip().lower()
        if not address:
            continue
        if not valid_address(address):
            return f"'{address[:80]}' is not an email address."
        if address == alias:
            return "An alias cannot deliver to itself."
        if address not in out:
            out.append(address)
    if not out:
        return "Name at least one address the alias delivers to."
    if len(out) > MAX_GOTO:
        return f"An alias delivers to at most {MAX_GOTO} addresses."
    return out


def _change_problem(arguments: dict[str, Any], directory: mailcow.MailcowDirectory) -> str | None:
    """Why this change cannot be made now (checked when asked and again when approved), or None."""
    operation = arguments.get("operation")
    domain = str(arguments.get("domain") or "")
    allowed = _allowed()
    if domain not in allowed:
        return f"{domain or 'That domain'} is not in ANUM_MAILSERVER_ALLOWED_DOMAINS, so ANUM does not change it."
    if directory.state != "ok":
        return directory.message or "Mailcow could not be read."
    known = {d.domain: d for d in directory.domains}
    if operation == "add_domain":
        return f"{domain} is already in Mailcow." if domain in known else None
    if domain not in known:
        return f"{domain} is not in Mailcow."
    if operation == "remove_domain":
        if known[domain].mailboxes:
            return f"{domain} still has {known[domain].mailboxes} mailbox(es). Move or remove them in Mailcow first."
        if any(_domain_of(a.address) == domain for a in directory.aliases):
            return f"{domain} still has aliases. Remove them first."
        return None
    address = str(arguments.get("address") or "")
    if operation == "add_alias":
        if any(a.address == address for a in directory.aliases):
            return f"{address} is already an alias."
        if any(m.address == address for m in directory.mailboxes):
            return f"{address} is already a mailbox."
        return None
    if operation == "add_mailbox":
        if not directory.mailboxes_read:
            return "Mailcow's mailbox list could not be read, so no mailbox can be added right now. Look again in a moment."
        if any(m.address == address for m in directory.mailboxes):
            return f"{address} is already a mailbox."
        if any(a.address == address for a in directory.aliases):
            return f"{address} is already an alias. Remove the alias first."
        limit = known[domain].max_mailboxes
        if limit is not None and known[domain].mailboxes >= limit:
            return f"{domain} already has its {limit} mailboxes. Raise the limit in Mailcow first."
        return None
    if operation == "remove_alias":
        found = next((a for a in directory.aliases if a.id == str(arguments.get("alias_id") or "")), None)
        if found is None or found.address != address:
            return "That alias is gone or changed. Look at the domain again."
        return None
    return "Unknown change."


def _summary(arguments: dict[str, Any]) -> str:
    operation, domain, address = arguments.get("operation"), arguments.get("domain"), arguments.get("address")
    if operation == "add_domain":
        return f"Add the mail domain {domain} to Mailcow"
    if operation == "remove_domain":
        return f"Remove the mail domain {domain} from Mailcow"
    if operation == "add_alias":
        return f"Add the alias {address} delivering to {', '.join(arguments.get('goto') or [])}"
    if operation == "add_mailbox":
        return f"Add the mailbox {address} for {arguments.get('name')} ({arguments.get('quota_mb')} MiB)"
    return f"Remove the alias {address}"


def _pending(repository: AnumRepository, context: TenantContext) -> list[PendingChange]:
    now = utc_now()
    out: list[PendingChange] = []
    for approval in pending_now(repository, context, now):  # the pending rows only
        if approval.action != DOMAIN_TOOL:
            continue
        args = approval.arguments
        out.append(PendingChange(
            approval=approval, operation=args.get("operation"), domain=str(args.get("domain") or ""),
            address=args.get("address") or None, goto=list(args.get("goto") or []),
            name=args.get("name") or None, quota_mb=args.get("quota_mb") if isinstance(args.get("quota_mb"), int) else None,
        ))
    return sorted(out, key=lambda item: item.approval.created_at)


# -- routes -------------------------------------------------------------------------------------


@router.get("/domains", response_model=MailDomainsView)
def list_domains(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> MailDomainsView:
    """Mailcow's domains with their aliases, the allowed domains it lacks, and the changes waiting."""
    require_permission(context, Permission.PLATFORM_OPERATE)
    directory = mailcow.read_directory()
    allowed = _allowed()
    by_domain: dict[str, DomainView] = {}
    for item in directory.domains:
        aliases = [AliasView(**a.model_dump()) for a in directory.aliases if _domain_of(a.address) == item.domain]
        boxes = [MailboxView(**m.model_dump()) for m in directory.mailboxes if _domain_of(m.address) == item.domain]
        by_domain[item.domain] = DomainView(
            domain=item.domain, in_mailcow=True, active=item.active, allowed=item.domain in allowed, mailboxes=item.mailboxes,
            max_mailboxes=item.max_mailboxes, quota_used_percent=item.quota_used_percent, aliases=aliases, mailbox_list=boxes,
        )
    for domain in allowed - set(by_domain):
        by_domain[domain] = DomainView(domain=domain, in_mailcow=False, active=False, allowed=True, mailboxes=0, max_mailboxes=None, quota_used_percent=None, aliases=[])
    problem = mailcow.write_problem()
    if problem is None and directory.state != "ok":
        problem = "Mailcow could not be read, so nothing can be changed right now."
    return MailDomainsView(
        mailcow_state=directory.state, message=directory.message, can_change=problem is None, change_note=problem, mail_host=mail_host(),
        allowed_domains=sorted(allowed), domains=sorted(by_domain.values(), key=lambda d: (not d.allowed, d.domain)), pending=_pending(repository, context),
        mailboxes_read=directory.mailboxes_read,
    )


@router.get("/domains/{domain}/dns", response_model=DnsReport)
def domain_dns(domain: str, context: TenantContext = Depends(tenant_context)) -> DnsReport:
    """Live MX, SPF, DKIM and DMARC of one allowed or Mailcow domain, and the records to set."""
    require_permission(context, Permission.PLATFORM_OPERATE)
    name = valid_domain(domain)
    if name is None:
        raise HTTPException(status_code=422, detail="That is not a domain name.")
    if name not in _allowed():
        directory = mailcow.read_directory()
        if name not in {d.domain for d in directory.domains}:
            raise HTTPException(status_code=404, detail="That domain is neither allowed nor in Mailcow.")
    dkim = mailcow.read_dkim(name)
    cfg = mailcow.mailcow_settings
    return check_domain(
        name, mail_host(), dkim.selector if dkim else "dkim", dkim.txt if dkim else None, _dkim_key(dkim), cfg.dns_timeout_seconds,
    )


def _dkim_key(dkim: mailcow.MailcowDkim | None) -> str | None:
    if dkim is None:
        return None
    if dkim.public_key:
        return dkim.public_key
    tags = {part.split("=", 1)[0].strip().lower(): part.split("=", 1)[1].strip() for part in dkim.txt.split(";") if "=" in part}
    return "".join(tags.get("p", "").split()) or None


@router.post("/domains/changes", response_model=Waiting)
def ask_domain_change(body: ChangeBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Waiting:
    """Ask to add or remove a domain or an alias: made only after an approval of exactly this change."""
    require_permission(context, Permission.PLATFORM_OPERATE)
    problem = mailcow.write_problem()
    if problem:
        raise HTTPException(status_code=409, detail=problem)
    arguments: dict[str, Any] = {"operation": body.operation, "requested_by": context.user_id}
    if body.operation in {"add_domain", "remove_domain"}:
        domain = valid_domain(body.domain or "")
        if domain is None:
            raise HTTPException(status_code=422, detail="Enter a domain name such as example.com.")
        arguments["domain"] = domain
    else:
        address = _clean_alias(body.address)
        if address is None:
            noun = "mailbox" if body.operation == "add_mailbox" else "alias"
            raise HTTPException(status_code=422, detail=f"Enter the {noun} as name@domain, for example sales@example.com.")
        arguments.update(domain=_domain_of(address), address=address)
        if body.operation == "add_mailbox":
            arguments.update(_mailbox_arguments(body, address))
        elif body.operation == "add_alias":
            goto = _clean_goto(body.goto, address)
            if isinstance(goto, str):
                raise HTTPException(status_code=422, detail=goto)
            arguments["goto"] = goto
        else:
            if not body.alias_id:
                raise HTTPException(status_code=422, detail="Say which alias to remove.")
            arguments["alias_id"] = body.alias_id
    reason = _change_problem(arguments, mailcow.read_directory())
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    same = {key: value for key, value in arguments.items() if key not in _NOT_COMPARED}
    for waiting in _pending(repository, context):
        if {key: value for key, value in waiting.approval.arguments.items() if key in same} == same:
            raise HTTPException(status_code=409, detail="This change is already waiting for an approval.")
    call = ToolCall(name=DOMAIN_TOOL, arguments=arguments)
    from .service import mail_tool_registry

    registry = mail_tool_registry()
    decision = AgentRuntime(MockModelGateway(), repository, tools=registry).evaluate(call, context)
    if decision.outcome == ToolPolicyOutcome.BLOCK:
        _audit(repository, context, f"mail.{DOMAIN_TOOL}", arguments["domain"], "denied", {"reason": decision.reason[:200]})
        raise HTTPException(status_code=403, detail=f"Blocked by policy: {decision.reason}")
    title = _summary(arguments)
    _, approval = _pause_for_approval(repository, context, call, title, f"{title} ({DOMAIN_TOOL}).", registry=registry)
    _audit(repository, context, "mail.domains.requested", arguments["domain"], metadata={
        "approval_id": approval.id, "payload_hash": approval.payload_hash, "operation": body.operation,
    })
    return Waiting(status="waiting_approval", summary=f"Waiting for approval: {title}.", approval=approval)


def _mailbox_arguments(body: ChangeBody, address: str) -> dict[str, Any]:
    """The checked name and quota of a new mailbox and its sealed first password (422 when not usable)."""
    name = " ".join((body.name or "").split())
    if not name:
        raise HTTPException(status_code=422, detail="Enter the name of the person or team the mailbox is for.")
    password = body.password.get_secret_value() if body.password else str()
    problem = password_problem(password, address)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    quota = QUOTA_DEFAULT_MB if body.quota_mb is None else body.quota_mb
    if not QUOTA_MIN_MB <= quota <= QUOTA_MAX_MB:
        raise HTTPException(status_code=422, detail=f"The quota must be {QUOTA_MIN_MB} to {QUOTA_MAX_MB} MiB.")
    return {"name": name, "quota_mb": quota, "password_sealed": secret_cipher().encrypt(password)}


def password_problem(password: str, address: str) -> str | None:
    """Why ``password`` cannot be a new mailbox's first password, or None. Never echoes it."""
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        return f"The first password must be {PASSWORD_MIN} to {PASSWORD_MAX} characters."
    if password != password.strip() or any(ord(ch) < 32 for ch in password):
        return "The first password cannot start or end with a space or hold control characters."
    if not (any(ch.isalpha() for ch in password) and any(ch.isdigit() for ch in password)):
        return "The first password needs letters and digits."
    if address.split("@", 1)[0] in password.lower():
        return "The first password cannot contain the mailbox name."
    return None


@router.delete("/domains/changes/{approval_id}", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_domain_change(approval_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Withdraw a domain or alias change still waiting for approval (nothing changes)."""
    require_permission(context, Permission.PLATFORM_OPERATE)
    approval = repository.get_approval(approval_id, context)
    if approval is None or approval.action != DOMAIN_TOOL:
        raise HTTPException(status_code=404, detail="Change not found.")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail="This change was already decided.")
    _retire(repository, context, SimpleNamespace(task_id=approval.task_id, approval_id=approval.id))  # type: ignore[arg-type]
    _audit(repository, context, "mail.domains.withdrawn", str(approval.arguments.get("domain") or ""), metadata={"approval_id": approval.id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- the approval-only tool -----------------------------------------------------------------------


def _failed(summary: str, kind: str) -> ToolResult:
    return ToolResult(status="failed", summary=summary, output={"kind": kind})


async def change_mail_domains(call: ToolCall, context: TenantContext) -> ToolResult:
    """Make an approved domain or alias change, after checking it again against Mailcow."""
    # Whoever runs the approved call (the person who decided it, or a replayed task) must be a
    # platform operator: a workspace owner alone never changes the deployment's mail server.
    if not holds_organization_permission(context, Permission.PLATFORM_OPERATE.value):
        return _failed("Not changed: only a platform operator can change mail domains and aliases.", "forbidden")
    args = dict(call.arguments)
    operation = args.get("operation")
    if operation not in OPERATIONS:
        return _failed("Not changed: the approved call names no known change.", "invalid")
    problem = mailcow.write_problem()
    if problem:
        return _failed(f"Not changed: {problem}", "unavailable")
    domain = valid_domain(str(args.get("domain") or ""))
    if domain is None or domain != args.get("domain"):
        return _failed("Not changed: the approved call names no valid domain.", "invalid")
    password: str | None = None
    if operation in {"add_alias", "remove_alias", "add_mailbox"}:
        address = _clean_alias(str(args.get("address") or ""))
        if address is None or address != args.get("address") or _domain_of(address) != domain:
            return _failed("Not changed: the approved call names no valid address.", "invalid")
        if operation == "add_mailbox":
            quota = args.get("quota_mb")
            name = args.get("name")
            if not isinstance(quota, int) or not QUOTA_MIN_MB <= quota <= QUOTA_MAX_MB or not isinstance(name, str) or not name or len(name) > 120:
                return _failed("Not changed: the approved mailbox name or quota is not valid.", "invalid")
            try:
                password = secret_cipher().decrypt_fresh(str(args.get("password_sealed") or ""), SEALED_PASSWORD_DAYS * 86400)
            except SecretDecryptionError:
                return _failed(
                    f"Not changed: the first password could not be opened (older than {SEALED_PASSWORD_DAYS} days, or the "
                    "secrets key changed). Ask for the mailbox again.", "stale",
                )
            if password_problem(password, address):
                return _failed("Not changed: the approved first password is not usable. Ask for the mailbox again.", "invalid")
        if operation == "add_alias":
            goto = _clean_goto(list(args.get("goto") or []), address)
            if isinstance(goto, str) or goto != args.get("goto"):
                return _failed("Not changed: the approved destinations are not valid.", "invalid")
    reason = _change_problem(args, await asyncio.to_thread(mailcow.read_directory))
    if reason:
        return _failed(f"Not changed: {reason}", "stale")
    if operation == "add_domain":
        result = await asyncio.to_thread(mailcow.add_domain, domain)
    elif operation == "remove_domain":
        result = await asyncio.to_thread(mailcow.remove_domain, domain)
    elif operation == "add_alias":
        result = await asyncio.to_thread(mailcow.add_alias, str(args["address"]), list(args["goto"]))
    elif operation == "add_mailbox":
        result = await asyncio.to_thread(mailcow.add_mailbox, str(args["address"]), str(args["name"]), password or "", int(args["quota_mb"]))
    else:
        result = await asyncio.to_thread(mailcow.remove_alias, str(args["alias_id"]))
    audit = {"tool": DOMAIN_TOOL, "operation": operation, "domain": domain}
    if not result.ok:
        return ToolResult(status="failed", summary=f"Not changed: {result.message}", output={"kind": "mailcow", **audit}, audit_metadata=audit)
    return ToolResult(status="succeeded", summary=f"Done: {_summary(args)}.", output=audit, audit_metadata=audit)


DOMAIN_TOOL_DEFINITIONS: list[tuple[ToolDefinition, Any]] = [
    (
        ToolDefinition(
            name=DOMAIN_TOOL,
            description="Add or remove a mail domain or alias, or add a mailbox, on the Mailcow server, after approval of exactly that change.",
            # No workspace role gate: only platform operators (an organization role), whatever their
            # workspace role, may plan, decide or run it. Checked by the runtime policy and the handler.
            risk_level=RiskLevel.HIGH, timeout_seconds=60, category="email", action_kind="update", always_ask=True,
            required_organization_permission=Permission.PLATFORM_OPERATE.value,
        ),
        change_mail_domains,
    ),
]
