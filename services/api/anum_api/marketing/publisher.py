"""Publishing approved posts at their time (docs/marketing.md#publishing).

The automation scheduler's loop (``ANUM_AUTOMATION_SCHEDULER_ENABLED``) runs
``run_due_posts`` on every pass. With PostgreSQL, due posts are *discovered* as
``anum_maintenance`` (ids, scope and times of ``scheduled`` posts only, migration
``0040_marketing``) and each one is then published as the application role inside its
tenant's RLS context; the row lock and the ``scheduled`` -> ``publishing`` transition
make sure two replicas never publish the same post.

A post is published only when its content still hashes to what the approval (or the
campaign's approved auto-post) authorised, its account is this workspace's own and still
connected, and no organization policy now denies ``marketing.schedule_post``. Retryable
failures (network, rate limits, platform errors) are retried with a growing wait, up to
``ANUM_MARKETING_MAX_ATTEMPTS`` tries; anything else fails with the platform's reason and
a fix.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import text

from ..schemas import TenantContext, utc_now
from ..settings import settings
from . import clients
from .config import marketing_settings
from .models import AccountStatus, PlatformError, Post, PostStatus
from .service import access_token, audit, auto_post_covers, check, governance_denies_publish, load_media, post_hash
from .store import marketing_store, open_marketing_store

logger = logging.getLogger("anum.marketing")

PUBLISHER_ACTOR = "system:marketing-publisher"
# A post left "publishing" this long (the process stopped mid-publish) is reported as
# interrupted instead of being retried blindly: it may already be live.
STALE_PUBLISHING = timedelta(minutes=15)

_DISCOVER = text(
    """
    select id, tenant_id, workspace_id
    from marketing_posts
    where status = 'scheduled' and coalesce(next_attempt_at, scheduled_at) <= :now
    order by coalesce(next_attempt_at, scheduled_at)
    limit :limit
    """
)


def _context(tenant_id: str, workspace_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=PUBLISHER_ACTOR, roles=[])


def _refuse(post: Post, code: str, message: str, fix: str, status: PostStatus = PostStatus.FAILED) -> None:
    post.status = status
    post.last_error = PlatformError(code=code, message=message, fix=fix)
    post.next_attempt_at = None
    post.updated_at = utc_now()
    if status == PostStatus.DRAFT:
        post.approved_hash = None
        post.authorised_by = None


async def publish(context: TenantContext, post_id: str) -> Post | None:
    """Publish one scheduled post now. Returns the post as it ended up (None if gone)."""
    with open_marketing_store(context) as store:
        post = store.get_post(context, post_id, for_update=True)
        if post is None or post.status != PostStatus.SCHEDULED:
            return post
        account = store.get_account(context, post.account_id)
        campaign = store.get_campaign(context, post.campaign_id) if post.campaign_id else None
        if not post.approved_hash or post_hash(post) != post.approved_hash:
            _refuse(post, "changed_after_approval", "The post changed after it was approved, so it was not published.", "Schedule it again to ask for approval.", PostStatus.DRAFT)
        elif post.authorised_by == "auto_post" and not auto_post_covers(campaign, post, utc_now()):
            _refuse(post, "auto_post_off", "Auto-post is no longer on for this campaign and account, so it was not published.", "Schedule it again to ask for approval.", PostStatus.DRAFT)
        elif account is None or account.platform != post.platform:
            _refuse(post, "account_missing", "The account is not connected to this workspace.", "Connect the account, then schedule the post again.")
        elif account.status == AccountStatus.REVOKED:
            _refuse(post, "account_revoked", f"{account.display_name} was disconnected.", "Reconnect it, then schedule the post again.", PostStatus.DRAFT)
        elif not check(post).ok:
            _refuse(post, "invalid", "The post no longer meets the platform's limits.", "Edit it and schedule it again.", PostStatus.DRAFT)
        else:
            post.status = PostStatus.PUBLISHING
            post.attempts += 1
            post.updated_at = utc_now()
        store.save_post(post)
    if post.status != PostStatus.PUBLISHING or account is None:
        audit(None, context, "marketing.post.not_published", post.id, outcome="denied", metadata={"reason": post.last_error.code if post.last_error else None}, actor=PUBLISHER_ACTOR)
        return post

    from ..demo import is_demo

    if is_demo(context):  # nothing is published from a demo workspace (docs/demo-workspace.md)
        error = PlatformError(code="demo_workspace", message="Nothing is published from the demo workspace.", fix="Publish from a real workspace.")
        return _finish_failed(context, post.id, error, retry=False)
    if governance_denies_publish(context):
        error = PlatformError(code="policy_denied", message="An organization policy pack now denies publishing.", fix="Ask an organization admin to change the policy, then retry.")
        return _finish_failed(context, post.id, error, retry=False)
    try:
        token = await access_token(context, account)
        media = load_media(context, post)
        result = await clients.connector(account.provider).publish(clients.PublishRequest(
            account=account, access_token=token, text=check(post).composed, title=post.title, hashtags=post.hashtags,
            link_url=post.link_url, media=media, language=post.language.value,
        ))
    except clients.PlatformCallError as exc:
        return _finish_failed(context, post.id, exc.error, retry=exc.error.retryable)
    except Exception as exc:  # unexpected: never leave the post "publishing"
        logger.exception("marketing_publish_crashed", extra={"anum_marketing": {"event": "publish_crashed", "post_id": post.id}})
        error = PlatformError(code="internal", message=f"Publishing stopped with an internal error ({type(exc).__name__}).", fix="Check the platform before retrying: the post may or may not be live.")
        return _finish_failed(context, post.id, error, retry=False)
    now = utc_now()
    with open_marketing_store(context) as store:
        current = store.get_post(context, post.id, for_update=True) or post
        current.status = result.status
        current.external_id = result.external_id[:300] or None
        current.external_url = (result.external_url or None) and result.external_url[:1000]
        current.published_at = now
        current.next_attempt_at = None
        current.last_error = None
        current.updated_at = now
        store.save_post(current)
    audit(None, context, "marketing.post.published", post.id, metadata={
        "platform": post.platform.value, "account_id": account.id, "external_id": result.external_id,
        "status": result.status.value, "authorised_by": post.authorised_by, "approved_by": post.approved_by,
    }, actor=PUBLISHER_ACTOR)
    return current


def _finish_failed(context: TenantContext, post_id: str, error: PlatformError, *, retry: bool) -> Post | None:
    now = utc_now()
    with open_marketing_store(context) as store:
        post = store.get_post(context, post_id, for_update=True)
        if post is None:
            return None
        post.last_error = error
        post.updated_at = now
        if retry and post.attempts < marketing_settings.max_attempts:
            post.status = PostStatus.SCHEDULED
            post.next_attempt_at = now + timedelta(minutes=2 ** post.attempts)
        else:
            post.status = PostStatus.FAILED
            post.next_attempt_at = None
        store.save_post(post)
        if error.code in {"auth_expired", "permission", "token_unreadable"}:
            account = store.get_account(context, post.account_id, for_update=True)
            if account is not None and account.status == AccountStatus.CONNECTED:
                account.status = AccountStatus.NEEDS_REAUTH
                account.last_error = error
                account.updated_at = now
                store.save_account(account)
    audit(None, context, "marketing.post.publish_failed", post_id, outcome="failure", metadata={
        "code": error.code, "retrying": post.status == PostStatus.SCHEDULED, "attempts": post.attempts,
    }, actor=PUBLISHER_ACTOR)
    return post


def stale(post: Post) -> bool:
    return post.status == PostStatus.PUBLISHING and utc_now() - post.updated_at > STALE_PUBLISHING


def _due(limit: int) -> list[tuple[str, str, str]]:
    now = utc_now()
    if settings.repository_backend == "postgresql":
        from ..maintenance import default_session_factory, discover

        return [(row.tenant_id, row.workspace_id, row.id) for row in discover(default_session_factory, _DISCOVER, {"now": now, "limit": limit})]
    return [(post.tenant_id, post.workspace_id, post.id) for post in marketing_store.due_posts(now, limit)]


async def _publish_all(due: list[tuple[str, str, str]]) -> int:
    published = 0
    for tenant_id, workspace_id, post_id in due:
        try:
            post = await publish(_context(tenant_id, workspace_id), post_id)
        except Exception:
            logger.exception("marketing_publish_pass_failed", extra={"anum_marketing": {"event": "pass_failed", "post_id": post_id}})
            continue
        if post is not None and post.status in {PostStatus.PUBLISHED, PostStatus.SENT_TO_INBOX}:
            published += 1
    return published


async def publish_due(limit: int = 25) -> int:
    return await _publish_all(_due(limit))


def run_due_posts(limit: int = 25) -> int:
    """One publisher pass for the scheduler thread: discovery first, an event loop only
    when something is due (in a helper thread if this thread already runs a loop)."""
    due = _due(limit)
    if not due:
        return 0
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_publish_all(due))
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _publish_all(due)).result()
