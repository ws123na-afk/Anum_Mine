"""Mail domains and aliases on the Mailcow server, for the platform operator (docs/email.md#mail-domains).

Routes under ``/api/v1/platform/mail`` next to ``operator_api.py``; every one needs
``platform:operate`` (403 before anything is read):

* ``GET /domains``: the Mailcow domains with their aliases, the domains ``ANUM_MAILSERVER_ALLOWED_DOMAINS``
  names that Mailcow does not have yet, whether changes are switched on, and the changes waiting
  for an approval.
* ``GET /domains/{domain}/dns``: a live DNS check of MX, SPF, DKIM and DMARC (each lookup with a
  timeout) and the exact records to set at the DNS host. Only for allowed or Mailcow domains.
* ``POST /domains/changes``: ask to add or remove a domain, or to add or remove an alias. Nothing
  changes here: the change waits for an approval bound to its payload hash
  (``change_mail_domains``, high risk, always asked) and runs only when approved.
* ``DELETE /domains/changes/{approval_id}``: withdraw a change still waiting (nothing changes).

Changes stay inside ``ANUM_MAILSERVER_ALLOWED_DOMAINS``: a domain outside it is shown, never changed.
The approved call checks everything again against Mailcow before it writes (the domain still
allowed, still absent or still empty, the alias still the same), so an approval of something that
changed in between refuses instead of acting on stale facts.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from ..agent_tools import ToolCall, ToolDefinition, ToolPolicyOutcome, ToolResult, holds_organization_permission
from ..authorization import Permission
from ..dependencies import repository_context, require_permission, tenant_context
from ..email_api import _pause_for_approval, _retire
from ..model_gateway import MockModelGateway
from ..repository import AnumRepository
from ..runtime import AgentRuntime
from ..schemas import Approval, ApprovalStatus, RiskLevel, TenantContext, utc_now
from . import mailcow
from .api import _audit
from .config import mail_settings
from .domain_dns import DnsReport, check_domain, valid_domain
from .mime import valid_address

router = APIRouter(prefix="/api/v1/platform/mail", tags=["platform"])

DOMAIN_TOOL = "change_mail_domains"
Operation = Literal["add_domain", "remove_domain", "add_alias", "remove_alias"]
MAX_GOTO = 10
LOCAL_PART = re.compile(r"^[a-z0-9](?:[a-z0-9._%+-]{0,62}[a-z0-9])?$")


class AliasView(BaseModel):
    id: str
    address: str
    goto: list[str]
    active: bool


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


class PendingChange(BaseModel):
    approval: Approval
    operation: Operation
    domain: str
    address: str | None
    goto: list[str]


class MailDomainsView(BaseModel):
    mailcow_state: str
    message: str | None
    can_change: bool
    change_note: str | None
    mail_host: str | None
    allowed_domains: list[str]
    domains: list[DomainView]
    pending: list[PendingChange]


class ChangeBody(BaseModel):
    operation: Operation
    domain: str | None = Field(default=None, max_length=253)
    address: str | None = Field(default=None, max_length=254)
    goto: list[str] = Field(default_factory=list, max_length=MAX_GOTO)
    alias_id: str | None = Field(default=None, max_length=40)


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
    return f"Remove the alias {address}"


def _pending(repository: AnumRepository, context: TenantContext) -> list[PendingChange]:
    now = utc_now()
    out: list[PendingChange] = []
    for approval in repository.list_approvals(context):
        if approval.action != DOMAIN_TOOL or approval.status != ApprovalStatus.PENDING or (approval.expires_at is not None and approval.expires_at <= now):
            continue
        args = approval.arguments
        out.append(PendingChange(
            approval=approval, operation=args.get("operation"), domain=str(args.get("domain") or ""),
            address=args.get("address") or None, goto=list(args.get("goto") or []),
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
        by_domain[item.domain] = DomainView(
            domain=item.domain, in_mailcow=True, active=item.active, allowed=item.domain in allowed, mailboxes=item.mailboxes,
            max_mailboxes=item.max_mailboxes, quota_used_percent=item.quota_used_percent, aliases=aliases,
        )
    for domain in allowed - set(by_domain):
        by_domain[domain] = DomainView(domain=domain, in_mailcow=False, active=False, allowed=True, mailboxes=0, max_mailboxes=None, quota_used_percent=None, aliases=[])
    problem = mailcow.write_problem()
    if problem is None and directory.state != "ok":
        problem = "Mailcow could not be read, so nothing can be changed right now."
    return MailDomainsView(
        mailcow_state=directory.state, message=directory.message, can_change=problem is None, change_note=problem, mail_host=mail_host(),
        allowed_domains=sorted(allowed), domains=sorted(by_domain.values(), key=lambda d: (not d.allowed, d.domain)), pending=_pending(repository, context),
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
            raise HTTPException(status_code=422, detail="Enter the alias as name@domain, for example sales@example.com.")
        arguments.update(domain=_domain_of(address), address=address)
        if body.operation == "add_alias":
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
    same = {key: value for key, value in arguments.items() if key != "requested_by"}
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
    if operation not in {"add_domain", "remove_domain", "add_alias", "remove_alias"}:
        return _failed("Not changed: the approved call names no known change.", "invalid")
    problem = mailcow.write_problem()
    if problem:
        return _failed(f"Not changed: {problem}", "unavailable")
    domain = valid_domain(str(args.get("domain") or ""))
    if domain is None or domain != args.get("domain"):
        return _failed("Not changed: the approved call names no valid domain.", "invalid")
    if operation in {"add_alias", "remove_alias"}:
        address = _clean_alias(str(args.get("address") or ""))
        if address is None or address != args.get("address") or _domain_of(address) != domain:
            return _failed("Not changed: the approved call names no valid alias.", "invalid")
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
            description="Add or remove a mail domain or alias on the Mailcow server, after approval of exactly that change.",
            # No workspace role gate: only platform operators (an organization role), whatever their
            # workspace role, may plan, decide or run it. Checked by the runtime policy and the handler.
            risk_level=RiskLevel.HIGH, timeout_seconds=60, category="email", action_kind="update", always_ask=True,
            required_organization_permission=Permission.PLATFORM_OPERATE.value,
        ),
        change_mail_domains,
    ),
]
