"""Marketing logic shared by the HTTP routes, the agent tools and the publisher.

Publishing is governed (docs/marketing.md#publishing): a post is published only to the
workspace's own connected account it names, only with the exact content an approval
bound (``approved_hash``), and only while no organization policy denies the
``marketing.schedule_post`` tool. Approvals go through the ordinary approvals flow
(``POST /api/v1/approvals/{id}/approve`` with the displayed payload hash); a campaign's
auto-post is itself enabled through an owner approval.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel

from ..agent_tools import ToolCall, ToolRegistry
from ..audit import AuditRecord
from ..dependencies import _unit_of_work
from ..events import CanonicalEventName, create_event
from ..model_gateway import MockModelGateway
from ..repository import AnumRepository
from ..runtime import AgentRuntime
from ..schemas import (
    AgentRunStep,
    Approval,
    ApprovalStatus,
    RunPhase,
    Task,
    TaskStatus,
    TenantContext,
    new_id,
    utc_now,
)
from ..secret_box import SecretDecryptionError, secret_cipher
from ..tool_governance import match_governance
from . import clients
from .config import marketing_settings
from .models import (
    PLATFORM_PROVIDER,
    AccountStatus,
    Campaign,
    CampaignStatus,
    Language,
    MarketingPreferences,
    MediaInput,
    MediaRef,
    Platform,
    PlatformError,
    Post,
    PostStatus,
    Provider,
    SocialAccount,
)
from .platforms import Issue, content_hash, image_size, media_kind, validate
from .store import MarketingStore, open_marketing_store

logger = logging.getLogger("anum.marketing")

SCHEDULE_TOOL = "marketing.schedule_post"
AUTO_POST_TOOL = "marketing.enable_auto_post"
DRAFT_TOOL = "marketing.draft_social_post"
SUMMARY_TOOL = "marketing.summary"
STATE_PREFIX = "mkt."
STATE_SECONDS = 15 * 60
# Refresh an access token this long before it expires.
REFRESH_MARGIN = timedelta(minutes=10)
IMAGE_SNIFF_BYTES = 30 * 1024 * 1024


def http_error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


# -- audit ---------------------------------------------------------------------------------


def audit(
    repository: AnumRepository | None,
    context: TenantContext,
    action: str,
    target: str,
    *,
    outcome: str = "success",
    metadata: dict[str, Any] | None = None,
    actor: str | None = None,
) -> None:
    record = AuditRecord(
        id=new_id("audit"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        actor=actor or context.user_id,
        action=action,
        target=target,
        outcome=outcome,
        correlation_id=target,
        created_at=utc_now(),
        metadata=metadata or {},
    )
    if repository is not None:
        repository.record_audit(record)
        return
    try:
        with _unit_of_work(context, require_membership=False) as detached:
            detached.record_audit(record)
    except Exception:  # an audit failure must not undo a publish that already happened
        logger.warning("marketing_audit_failed", extra={"anum_marketing": {"event": "audit_failed", "action": action}})


# -- tokens and OAuth state -------------------------------------------------------------------


def encrypt(value: str | None) -> str | None:
    return secret_cipher().encrypt(value) if value else None


def decrypt(value: str | None) -> str | None:
    return secret_cipher().decrypt(value) if value else None


def issue_state(context: TenantContext, provider: Provider, verifier: str | None) -> str:
    """The OAuth ``state``: encrypted and bound to the caller, the provider and 15 minutes.

    It is checked on the callback against the signed-in caller, so a code obtained in
    one workspace cannot be attached to another, and it carries the PKCE verifier."""
    document = {
        "t": context.tenant_id,
        "w": context.workspace_id,
        "u": context.user_id,
        "p": provider.value,
        "v": verifier,
        "e": int((utc_now() + timedelta(seconds=STATE_SECONDS)).timestamp()),
    }
    return STATE_PREFIX + secret_cipher().encrypt(json.dumps(document, separators=(",", ":")))


def read_state(state: str, context: TenantContext) -> tuple[Provider, str | None]:
    if not state.startswith(STATE_PREFIX):
        raise http_error(400, "This sign-in link is not from ANUM. Start the connection again.")
    try:
        document = json.loads(secret_cipher().decrypt(state[len(STATE_PREFIX):]))
    except (SecretDecryptionError, ValueError):
        raise http_error(400, "This sign-in link is not valid. Start the connection again.") from None
    if (document.get("t"), document.get("w"), document.get("u")) != (context.tenant_id, context.workspace_id, context.user_id):
        raise http_error(403, "This sign-in was started by someone else or in another workspace. Start it again here.")
    if int(document.get("e", 0)) < int(utc_now().timestamp()):
        raise http_error(400, "The sign-in took longer than 15 minutes. Start the connection again.")
    return Provider(document["p"]), document.get("v")


def setup_status() -> list[dict[str, Any]]:
    """Per provider: is the deployment's app configured, and what is missing (names only)."""
    rows = []
    for provider, connector in clients.CONNECTORS.items():
        missing = connector.missing_settings()
        rows.append({
            "provider": provider.value,
            "label": clients.PROVIDER_LABEL[provider],
            "platforms": [platform.value for platform, owner in PLATFORM_PROVIDER.items() if owner == provider],
            "configured": not missing,
            "missing": missing,
        })
    return rows


async def start_oauth(context: TenantContext, provider: Provider) -> str:
    connector = clients.connector(provider)
    missing = connector.missing_settings()
    if missing:
        raise http_error(409, f"{clients.PROVIDER_LABEL[provider]} is not set up on this server. Missing: {', '.join(missing)}.")
    verifier, challenge = clients.pkce_pair() if connector.uses_pkce else (None, None)
    return connector.authorize_url(issue_state(context, provider, verifier), challenge)


async def finish_oauth(context: TenantContext, code: str, state: str, repository: AnumRepository) -> list[SocialAccount]:
    provider, verifier = read_state(state, context)
    connector = clients.connector(provider)
    try:
        discovered = await connector.exchange(code, verifier)
    except clients.PlatformCallError as exc:
        audit(repository, context, "marketing.account.connect_failed", provider.value, outcome="failure", metadata={"code": exc.error.code})
        raise http_error(502, f"{exc.error.message} {exc.error.fix}") from None
    now = utc_now()
    saved: list[SocialAccount] = []
    with open_marketing_store(context) as store:
        for found in discovered:
            if not found.external_id:
                continue
            existing = store.find_account(context, found.platform, found.external_id)
            account = existing or SocialAccount(
                id=new_id("mkacct"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, platform=found.platform,
                provider=provider, external_id=found.external_id, display_name=found.display_name, connected_by=context.user_id,
                created_at=now, updated_at=now,
            )
            account.display_name = found.display_name[:200]
            account.handle = (found.handle or None) and str(found.handle)[:200]
            account.status = AccountStatus.CONNECTED
            account.scopes = [scope for scope in found.scopes if scope][:40]
            account.access_token_ciphertext = encrypt(found.access_token)
            account.refresh_token_ciphertext = encrypt(found.refresh_token)
            account.token_expires_at = found.expires_at
            account.details = {**account.details, **found.details}
            account.last_checked_at = now
            account.last_error = None
            account.connected_by = context.user_id
            account.updated_at = now
            if existing is None:
                store.add_account(account)
            else:
                store.save_account(account)
            saved.append(account)
    for account in saved:
        audit(repository, context, "marketing.account.connected", account.id, metadata={"platform": account.platform.value, "external_id": account.external_id})
    return saved


async def access_token(context: TenantContext, account: SocialAccount) -> str:
    """The account's access token, refreshed first when it is about to expire.

    A refusal marks the account ``needs_reauth`` and raises the platform error."""
    if account.status == AccountStatus.REVOKED or not account.access_token_ciphertext:
        raise clients.platform_error("account_revoked", f"The {account.display_name} connection was removed.", "Reconnect the account on the Marketing page.")
    try:
        token = decrypt(account.access_token_ciphertext) or ""
        refresh = decrypt(account.refresh_token_ciphertext)
    except SecretDecryptionError:
        raise clients.platform_error("token_unreadable", "The stored connection could not be decrypted (the server's secrets key changed).", "Reconnect the account, or restore the previous ANUM_SECRETS_KEY.") from None
    if account.token_expires_at is None or account.token_expires_at - utc_now() > REFRESH_MARGIN:
        return token
    if not refresh:
        _mark(context, account, clients.platform_error("auth_expired", f"The {account.display_name} connection expired.", "Reconnect the account on the Marketing page.").error)
        raise clients.platform_error("auth_expired", f"The {account.display_name} connection expired.", "Reconnect the account on the Marketing page.")
    try:
        renewed = await clients.connector(account.provider).refresh(refresh)
    except clients.PlatformCallError as exc:
        if not exc.error.retryable:
            _mark(context, account, exc.error)
        raise
    if renewed is None or not renewed.access_token:
        return token
    with open_marketing_store(context) as store:
        current = store.get_account(context, account.id, for_update=True)
        if current is not None:
            current.access_token_ciphertext = encrypt(renewed.access_token)
            current.refresh_token_ciphertext = encrypt(renewed.refresh_token) or current.refresh_token_ciphertext
            current.token_expires_at = renewed.expires_at
            current.status = AccountStatus.CONNECTED
            current.last_error = None
            current.updated_at = utc_now()
            store.save_account(current)
            account.access_token_ciphertext, account.token_expires_at = current.access_token_ciphertext, current.token_expires_at
    return renewed.access_token


def _mark(context: TenantContext, account: SocialAccount, error: PlatformError) -> None:
    with open_marketing_store(context) as store:
        current = store.get_account(context, account.id, for_update=True)
        if current is None:
            return
        current.status = AccountStatus.NEEDS_REAUTH if error.code in {"auth_expired", "permission", "token_unreadable"} else AccountStatus.ERROR
        current.last_error = error
        current.last_checked_at = current.updated_at = utc_now()
        store.save_account(current)
        account.status = current.status


async def check_account(context: TenantContext, account_id: str) -> SocialAccount:
    with open_marketing_store(context) as store:
        account = store.get_account(context, account_id)
    if account is None:
        raise http_error(404, "Account not found")
    if account.status == AccountStatus.REVOKED:
        raise http_error(409, "This account was disconnected. Connect it again to use it.")
    try:
        token = await access_token(context, account)
        details = await clients.connector(account.provider).check(account, token)
    except clients.PlatformCallError as exc:
        if exc.error.retryable:
            with open_marketing_store(context) as store:
                current = store.get_account(context, account_id, for_update=True)
                if current is not None:
                    current.last_error, current.last_checked_at = exc.error, utc_now()
                    store.save_account(current)
        else:
            _mark(context, account, exc.error)
        with open_marketing_store(context) as store:
            return store.get_account(context, account_id) or account
    with open_marketing_store(context) as store:
        current = store.get_account(context, account_id, for_update=True)
        if current is None:
            raise http_error(404, "Account not found")
        current.status = AccountStatus.CONNECTED
        current.last_error = None
        current.details = {**current.details, **{key: value for key, value in details.items() if key != "name"}}
        current.last_checked_at = current.updated_at = utc_now()
        store.save_account(current)
        return current


async def revoke_account(context: TenantContext, account_id: str, repository: AnumRepository) -> SocialAccount:
    """Revoke the token at the platform where it offers that, then forget it here.

    The row stays (as ``revoked``) so published posts keep their account name."""
    with open_marketing_store(context) as store:
        account = store.get_account(context, account_id)
    if account is None:
        raise http_error(404, "Account not found")
    note = None
    if account.status != AccountStatus.REVOKED and account.access_token_ciphertext:
        try:
            token = decrypt(account.access_token_ciphertext) or ""
            await clients.connector(account.provider).revoke(account, token)
        except (clients.PlatformCallError, SecretDecryptionError):
            note = "The platform did not confirm the revocation; remove ANUM from the account's connected apps there too."
        if account.provider == Provider.META:
            note = "Meta tokens are removed here. To revoke the app entirely, remove ANUM under Facebook Settings > Business integrations."
    now = utc_now()
    with open_marketing_store(context) as store:
        current = store.get_account(context, account_id, for_update=True)
        if current is None:
            raise http_error(404, "Account not found")
        current.status = AccountStatus.REVOKED
        current.access_token_ciphertext = current.refresh_token_ciphertext = None
        current.token_expires_at = None
        current.last_error = PlatformError(code="revoked", message="Disconnected.", fix=note or "Connect it again to publish to it.") if note else None
        current.updated_at = now
        store.save_account(current)
        # Nothing waits to go out to a disconnected account.
        from .store import PostQuery

        for post in store.list_posts(context, PostQuery(statuses={PostStatus.SCHEDULED, PostStatus.PENDING_APPROVAL}, account_id=account_id, limit=500)):
            post.status = PostStatus.DRAFT
            post.approved_hash = None
            post.last_error = PlatformError(code="account_revoked", message="The account was disconnected.", fix="Reconnect it, then schedule the post again.")
            post.updated_at = now
            store.save_post(post)
    audit(repository, context, "marketing.account.revoked", account_id, metadata={"platform": account.platform.value})
    return current


# -- media --------------------------------------------------------------------------------------


def resolve_media(context: TenantContext, inputs: list[MediaInput]) -> list[MediaRef]:
    """Files from this workspace's Files as post media (the API reads image sizes itself)."""
    from ..files import file_store, open_file_metadata_store

    refs: list[MediaRef] = []
    with open_file_metadata_store(context) as files:
        records = {item.file_id: files.get(context, item.file_id) for item in inputs}
    for item in inputs:
        record = records.get(item.file_id)
        if record is None:
            raise http_error(422, f"File {item.file_id} was not found in this workspace's Files.")
        kind = media_kind(record.content_type)
        if kind is None:
            raise http_error(422, f"{record.name} is not an image or a video.")
        width, height = item.width, item.height
        if kind.value == "image" and record.size_bytes <= IMAGE_SNIFF_BYTES:
            try:
                size = image_size(file_store.storage.get(record.storage_key)[:262144])
            except (FileNotFoundError, OSError):
                size = None
            if size:
                width, height = size
        refs.append(MediaRef(
            file_id=record.id, kind=kind, name=record.name, content_type=record.content_type, size_bytes=record.size_bytes,
            width=width, height=height, duration_seconds=item.duration_seconds,
        ))
    return refs


def media_link(context: TenantContext, file_id: str) -> str | None:
    """A signed, expiring public link to one file (Instagram fetches media by URL)."""
    base = marketing_settings.public_api_url.strip().rstrip("/")
    if not base.startswith("https://") and not base.startswith("http://localhost") and not base.startswith("http://127.0.0.1"):
        return None
    document = {"t": context.tenant_id, "w": context.workspace_id, "f": file_id, "e": int((utc_now() + timedelta(seconds=marketing_settings.media_link_seconds)).timestamp())}
    return f"{base}/api/v1/marketing/media/{secret_cipher().encrypt(json.dumps(document, separators=(',', ':')))}"


def read_media_link(token: str) -> tuple[TenantContext, str]:
    try:
        document = json.loads(secret_cipher().decrypt(token))
    except (SecretDecryptionError, ValueError):
        raise http_error(404, "Not found") from None
    if int(document.get("e", 0)) < int(utc_now().timestamp()):
        raise http_error(404, "Not found")
    context = TenantContext(tenant_id=str(document["t"]), workspace_id=str(document["w"]), user_id="system:marketing-media", roles=[])
    return context, str(document["f"])


def load_media(context: TenantContext, post: Post) -> list[clients.MediaPayload]:
    from ..files import file_store, open_file_metadata_store

    payloads: list[clients.MediaPayload] = []
    with open_file_metadata_store(context) as files:
        records = {item.file_id: files.get(context, item.file_id) for item in post.media}
    for item in post.media:
        record = records.get(item.file_id)
        if record is None:
            raise clients.platform_error("media_missing", f"{item.name or item.file_id} was deleted from Files.", "Attach the media again and schedule the post again.")
        if record.size_bytes > marketing_settings.max_media_bytes:
            raise clients.platform_error("media_too_large", f"{record.name} is larger than this server sends to platforms.", "Use a smaller file, or raise ANUM_MARKETING_MAX_MEDIA_BYTES.")
        try:
            content = file_store.storage.get(record.storage_key)
        except FileNotFoundError:
            raise clients.platform_error("media_missing", f"{record.name}'s content is missing from storage.", "Upload the file again and attach it.") from None
        payloads.append(clients.MediaPayload(ref=item, content=content, public_url=media_link(context, item.file_id)))
    return payloads


# -- posts ---------------------------------------------------------------------------------------


def post_hash(post: Post) -> str:
    return content_hash(post.text, post.title, post.hashtags, post.link_url, post.media, post.account_id)


def check(post: Post):  # type: ignore[no-untyped-def]
    return validate(post.platform, text=post.text, hashtags=post.hashtags, link_url=post.link_url, media=post.media, title=post.title)


def get_post_or_404(store: MarketingStore, context: TenantContext, post_id: str, *, for_update: bool = False) -> Post:
    post = store.get_post(context, post_id, for_update=for_update)
    if post is None:
        raise http_error(404, "Post not found")
    return post


def connected_account(store: MarketingStore, context: TenantContext, account_id: str) -> SocialAccount:
    account = store.get_account(context, account_id)
    if account is None:
        raise http_error(422, "That account is not connected to this workspace.")
    if account.status == AccountStatus.REVOKED:
        raise http_error(409, f"{account.display_name} was disconnected. Connect it again first.")
    return account


ApprovalState = Literal["none", "pending", "approved", "rejected", "expired"]


class PostView(Post):
    account_name: str = ""
    account_handle: str | None = None
    account_status: AccountStatus | None = None
    issues: list[Issue] = []
    length: int = 0
    limit: int = 0
    approval: Approval | None = None
    approval_state: ApprovalState = "none"


def view(post: Post, account: SocialAccount | None, approval: Approval | None) -> PostView:
    result = check(post)
    state: ApprovalState = "none"
    if approval is not None:
        state = {ApprovalStatus.PENDING: "pending", ApprovalStatus.APPROVED: "approved", ApprovalStatus.REJECTED: "rejected", ApprovalStatus.EXPIRED: "expired"}.get(approval.status, "none")  # type: ignore[assignment]
        if approval.status == ApprovalStatus.PENDING and approval.expires_at is not None and approval.expires_at <= utc_now():
            state = "expired"
    return PostView(
        **post.model_dump(),
        account_name=account.display_name if account else "",
        account_handle=account.handle if account else None,
        account_status=account.status if account else None,
        issues=result.issues,
        length=result.length,
        limit=result.limit,
        approval=approval if state == "pending" else None,
        approval_state=state,
    )


def views(context: TenantContext, store: MarketingStore, posts: list[Post], repository: AnumRepository) -> list[PostView]:
    """Posts with their account, problems and approval. A post whose approval was rejected
    or lapsed goes back to draft here (with the reason), so it can be edited and asked again."""
    accounts = {account.id: account for account in store.list_accounts(context)}
    result: list[PostView] = []
    for post in posts:
        approval = repository.get_approval(post.approval_id, context) if post.approval_id else None
        if post.status == PostStatus.PENDING_APPROVAL and approval is not None:
            lapsed = approval.status == ApprovalStatus.EXPIRED or (
                approval.status == ApprovalStatus.PENDING and approval.expires_at is not None and approval.expires_at <= utc_now()
            )
            if approval.status == ApprovalStatus.REJECTED or lapsed:
                post.status = PostStatus.DRAFT
                post.last_error = PlatformError(
                    code="approval_rejected" if approval.status == ApprovalStatus.REJECTED else "approval_expired",
                    message=(f"Rejected by {approval.decided_by or 'an approver'}" + (f": {approval.decision_reason}" if approval.decision_reason else "."))
                    if approval.status == ApprovalStatus.REJECTED else "The approval request lapsed before anyone decided.",
                    fix="Edit the post if needed and schedule it again to ask for approval.",
                )
                post.updated_at = utc_now()
                store.save_post(post)
        result.append(view(post, accounts.get(post.account_id), approval))
    return result


def retire_approval(repository: AnumRepository, context: TenantContext, task_id: str | None, approval_id: str | None) -> None:
    """Cancel an earlier publish request still waiting for a decision (its content changed)."""
    if not task_id:
        return
    task = repository.get_task_for_update(task_id, context)
    if task is None or task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        return
    now = utc_now()
    task.status = TaskStatus.CANCELLED
    task.updated_at = now
    repository.save_task(task)
    run = repository.find_run_for_task(task.id, context)
    if run is not None and run.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        run.status = TaskStatus.CANCELLED
        run.checkpoint.phase = RunPhase.CANCELLED
        run.checkpoint.version += 1
        run.updated_at = now
        repository.save_run(run)
    if approval_id:
        approval = repository.get_approval_for_update(approval_id, context)
        if approval is not None and approval.status == ApprovalStatus.PENDING:
            approval.status = ApprovalStatus.EXPIRED
            approval.decided_at = now
            repository.save_approval(approval)
    repository.record_event(create_event(CanonicalEventName.TASK_CANCELLED, context, task.id, {"task_id": task.id}, correlation_id=task.id).event)


def request_approval(
    repository: AnumRepository,
    context: TenantContext,
    *,
    tool: str,
    arguments: dict[str, Any],
    title: str,
    prompt: str,
    metadata: dict[str, Any],
) -> tuple[Task, Approval]:
    """A paused call of a marketing tool, waiting for approval (as developer-mode merges do)."""
    from .tools import register_marketing_tools

    call = ToolCall(name=tool, arguments=arguments)
    now = utc_now()
    task = Task(
        id=new_id("task"), title=title[:160], prompt=prompt, status=TaskStatus.CREATED, tenant_id=context.tenant_id,
        workspace_id=context.workspace_id, created_at=now, updated_at=now, created_by=context.user_id,
    )
    repository.create_task(task)
    repository.record_event(create_event(CanonicalEventName.TASK_CREATED, context, task.id, {"title": task.title}, correlation_id=task.id, created_at=now).event)
    runtime = AgentRuntime(MockModelGateway(), repository, tools=register_marketing_tools(ToolRegistry()))
    run = runtime.new_run(task)
    decision = runtime.evaluate(call, context)
    proposal = AgentRunStep(
        id=new_id("step"), type="tool_proposal", summary=f"Proposed tool call: {tool}", created_at=utc_now(),
        metadata={"tool": tool, "risk_level": decision.risk_level.value, "policy_outcome": decision.outcome.value, "governance_rules": list(decision.governance_rules), **metadata},
    )
    run.steps.append(proposal)
    run.checkpoint.phase = RunPhase.TOOL_READY
    run.checkpoint.version += 1
    run.checkpoint.tool_call = call.model_dump(mode="json")
    run.checkpoint.last_step_id = proposal.id
    task.status = run.status = TaskStatus.RUNNING
    repository.save_task(task)
    repository.save_run(run)
    # High risk always pauses; a governance deny (or a missing role) fails the run instead.
    if runtime.begin_execution(task, run, context) is not None:  # pragma: no cover - high risk never runs unapproved
        raise RuntimeError("marketing publish was not paused for approval")
    repository.save_task(task)
    repository.save_run(run)
    if run.checkpoint.phase != RunPhase.WAITING_APPROVAL or not run.checkpoint.approval_id:
        reason = run.steps[-1].summary if run.steps else "Blocked by policy."
        raise http_error(403, f"Publishing is blocked: {reason}")
    approval = repository.get_approval(run.checkpoint.approval_id, context)
    if approval is None:  # pragma: no cover - saved just above
        raise http_error(500, "Approval was not stored")
    return task, approval


def schedule_arguments(post: Post, account: SocialAccount, campaign: Campaign | None) -> dict[str, Any]:
    """What the approver sees and the payload hash binds."""
    composed = check(post).composed
    return {
        "post_id": post.id,
        "platform": post.platform.value,
        "account_id": account.id,
        "account": account.display_name + (f" (@{account.handle})" if account.handle else ""),
        "language": post.language.value,
        "scheduled_at": (post.scheduled_at or utc_now()).isoformat(),
        "text": composed,
        "title": post.title,
        "media": [f"{item.name or item.file_id} ({item.kind.value})" for item in post.media],
        "campaign": campaign.name if campaign else None,
        "content_hash": post_hash(post),
        "untrusted_source": post.untrusted_source,
    }


def auto_post_covers(campaign: Campaign | None, post: Post, today: datetime) -> bool:
    if campaign is None or not campaign.auto_post or campaign.status != CampaignStatus.ACTIVE:
        return False
    if post.account_id not in campaign.auto_post_accounts:
        return False
    if campaign.end_date is not None and (post.scheduled_at or today).date() > campaign.end_date:
        return False
    return True


async def schedule_post(context: TenantContext, post_id: str, when: datetime | None, repository: AnumRepository) -> PostView:
    """Ask to publish a post: auto-post campaigns schedule it, everything else waits for approval."""
    now = utc_now()
    with open_marketing_store(context) as store:
        post = get_post_or_404(store, context, post_id, for_update=True)
        if post.status not in {PostStatus.DRAFT, PostStatus.FAILED, PostStatus.CANCELLED, PostStatus.PENDING_APPROVAL}:
            raise http_error(409, f"This post is {post.status.value.replace('_', ' ')}; only drafts, failed or cancelled posts can be scheduled.")
        account = connected_account(store, context, post.account_id)
        if account.status != AccountStatus.CONNECTED:
            raise http_error(409, f"{account.display_name} needs attention ({account.status.value.replace('_', ' ')}). Check or reconnect it first.")
        result = check(post)
        if not result.ok:
            raise http_error(422, "Fix these first: " + " ".join(issue.message for issue in result.issues if issue.severity == "error"))
        target = when or post.scheduled_at or now
        if target < now - timedelta(minutes=1):
            raise http_error(422, "Pick a time in the future (or leave it empty to publish now).")
        post.scheduled_at = max(target, now)
        campaign = store.get_campaign(context, post.campaign_id) if post.campaign_id else None
        previous_task, previous_approval = post.approval_task_id, post.approval_id
        post.last_error = None
        post.attempts = 0
        post.next_attempt_at = None
        auto = auto_post_covers(campaign, post, now)
        if auto:
            post.status = PostStatus.SCHEDULED
            post.approved_hash = post_hash(post)
            post.authorised_by = "auto_post"
            post.approved_by = campaign.auto_post_approved_by if campaign else None
            post.approval_id = post.approval_task_id = None
            post.updated_at = now
            store.save_post(post)
    if auto:
        retire_approval(repository, context, previous_task, previous_approval)
        audit(repository, context, "marketing.post.scheduled", post.id, metadata={"platform": post.platform.value, "authorised_by": "auto_post", "campaign_id": post.campaign_id, "scheduled_at": post.scheduled_at.isoformat() if post.scheduled_at else None})
        return view(post, account, None)
    retire_approval(repository, context, previous_task, previous_approval)
    task, approval = request_approval(
        repository, context, tool="marketing.schedule_post", arguments=schedule_arguments(post, account, campaign),
        title=f"Publish to {clients.PLATFORM_LABEL[post.platform]}: {account.display_name}",
        prompt=f"Publish marketing post {post.id} to {clients.PLATFORM_LABEL[post.platform]} ({account.display_name}) at {post.scheduled_at.isoformat() if post.scheduled_at else 'now'}.",
        metadata={"marketing_post_id": post.id},
    )
    with open_marketing_store(context) as store:
        current = get_post_or_404(store, context, post_id, for_update=True)
        current.status = PostStatus.PENDING_APPROVAL
        current.scheduled_at = post.scheduled_at
        current.approval_id, current.approval_task_id = approval.id, task.id
        current.approved_hash = None
        current.authorised_by = None
        current.last_error = None
        current.attempts = 0
        current.next_attempt_at = None
        current.updated_at = utc_now()
        store.save_post(current)
    audit(repository, context, "marketing.post.approval_requested", post.id, metadata={"approval_id": approval.id, "task_id": task.id, "payload_hash": approval.payload_hash, "platform": post.platform.value})
    return view(current, account, approval)


def unschedule(post: Post, repository: AnumRepository, context: TenantContext, *, reason: PostStatus = PostStatus.DRAFT) -> None:
    """Content changed or the owner cancelled: nothing authorised remains for this post."""
    retire_approval(repository, context, post.approval_task_id, post.approval_id)
    post.status = reason
    post.approved_hash = None
    post.authorised_by = None
    post.approved_by = None
    post.approval_id = post.approval_task_id = None
    post.next_attempt_at = None


# -- preferences --------------------------------------------------------------------------------


def preferences(context: TenantContext) -> MarketingPreferences:
    with open_marketing_store(context) as store:
        return store.get_preferences(context) or MarketingPreferences()


def language_of(value: str) -> Language:
    return Language.AR if value == "ar" else Language.EN


class RefreshFailure(BaseModel):
    account_id: str
    account: str
    platform: Platform
    error: PlatformError


class RefreshResult(BaseModel):
    updated: int
    failures: list[RefreshFailure]


async def refresh_metrics(context: TenantContext, *, days: int = 30, campaign_id: str | None = None) -> RefreshResult:
    """Pull the latest numbers for posts published in the last ``days`` days."""
    from .store import PostQuery

    since = utc_now() - timedelta(days=days)
    with open_marketing_store(context) as store:
        posts = store.list_posts(context, PostQuery(statuses={PostStatus.PUBLISHED}, published_since=since, campaign_id=campaign_id, limit=500))
        accounts = {account.id: account for account in store.list_accounts(context)}
    by_account: dict[str, list[Post]] = {}
    for post in posts:
        if post.external_id:
            by_account.setdefault(post.account_id, []).append(post)
    updated = 0
    failures: list[RefreshFailure] = []
    for account_id, items in by_account.items():
        account = accounts.get(account_id)
        if account is None or account.status == AccountStatus.REVOKED:
            continue
        try:
            token = await access_token(context, account)
            numbers = await clients.connector(account.provider).metrics(account, token, [item.external_id or "" for item in items])
        except clients.PlatformCallError as exc:
            if not exc.error.retryable:
                _mark(context, account, exc.error)
            failures.append(RefreshFailure(account_id=account.id, account=account.display_name, platform=account.platform, error=exc.error))
            continue
        now = utc_now()
        with open_marketing_store(context) as store:
            for item in items:
                metrics = numbers.get(item.external_id or "")
                if metrics is None:
                    continue
                current = store.get_post(context, item.id, for_update=True)
                if current is None:
                    continue
                current.metrics, current.metrics_updated_at = metrics, now
                store.save_post(current)
                updated += 1
    return RefreshResult(updated=updated, failures=failures)


def governance_denies_publish(context: TenantContext) -> bool:
    """A policy pack that now denies the publish tool stops even approved posts."""
    from .tools import schedule_definition

    try:
        with _unit_of_work(context, require_membership=False) as repository:
            governance = repository.get_tool_governance(context)
    except Exception:
        return True  # unknown policy: do not publish
    match = match_governance(governance, tool=SCHEDULE_TOOL, target=schedule_definition().target, risk_level=schedule_definition().risk_level)
    return bool(match.deny)
