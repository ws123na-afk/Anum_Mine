"""Marketing storage: one protocol, an in-memory store and the PostgreSQL store.

``ANUM_REPOSITORY_BACKEND=memory`` (local and tests) keeps everything in this process;
``postgresql`` uses ``anum_api.db.marketing_repository`` under forced RLS (migration
``0040_marketing``). Every method works inside the caller's tenant and workspace.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Protocol

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .models import Campaign, CampaignStatus, MarketingPreferences, Platform, Post, PostStatus, SocialAccount

MAX_PAGE = 100
MAX_RANGE = 500


@dataclass
class PostQuery:
    statuses: set[PostStatus] = field(default_factory=set)
    campaign_id: str | None = None
    platform: Platform | None = None
    account_id: str | None = None
    group_id: str | None = None
    # Scheduled (or planned) time window, for the calendar.
    start: datetime | None = None
    end: datetime | None = None
    published_since: datetime | None = None
    text: str | None = None
    limit: int = 50
    before: Post | None = None
    # "recent": newest first with keyset paging; "schedule": by time inside [start, end).
    order: str = "recent"


class MarketingStore(Protocol):
    def list_accounts(self, context: TenantContext) -> list[SocialAccount]: ...
    def get_account(self, context: TenantContext, account_id: str, *, for_update: bool = False) -> SocialAccount | None: ...
    def find_account(self, context: TenantContext, platform: Platform, external_id: str) -> SocialAccount | None: ...
    def add_account(self, account: SocialAccount) -> SocialAccount: ...
    def save_account(self, account: SocialAccount) -> SocialAccount: ...

    def list_campaigns(self, context: TenantContext, *, status: CampaignStatus | None, text: str | None, limit: int, before: Campaign | None) -> list[Campaign]: ...
    def get_campaign(self, context: TenantContext, campaign_id: str, *, for_update: bool = False) -> Campaign | None: ...
    def add_campaign(self, campaign: Campaign) -> Campaign: ...
    def save_campaign(self, campaign: Campaign) -> Campaign: ...
    def delete_campaign(self, context: TenantContext, campaign_id: str) -> bool: ...

    def add_post(self, post: Post) -> Post: ...
    def get_post(self, context: TenantContext, post_id: str, *, for_update: bool = False) -> Post | None: ...
    def save_post(self, post: Post) -> Post: ...
    def delete_post(self, context: TenantContext, post_id: str) -> bool: ...
    def list_posts(self, context: TenantContext, query: PostQuery) -> list[Post]: ...
    def count_posts(self, context: TenantContext, campaign_id: str) -> dict[str, int]: ...

    def get_preferences(self, context: TenantContext) -> MarketingPreferences | None: ...
    def save_preferences(self, context: TenantContext, preferences: MarketingPreferences) -> MarketingPreferences: ...


def _scoped(item: SocialAccount | Campaign | Post, context: TenantContext) -> bool:
    return (item.tenant_id, item.workspace_id) == (context.tenant_id, context.workspace_id)


def _recent_key(post: Post) -> tuple[datetime, str]:
    return (post.created_at, post.id)


def matches(post: Post, query: PostQuery) -> bool:
    if query.statuses and post.status not in query.statuses:
        return False
    if query.campaign_id and post.campaign_id != query.campaign_id:
        return False
    if query.platform and post.platform != query.platform:
        return False
    if query.account_id and post.account_id != query.account_id:
        return False
    if query.group_id and post.group_id != query.group_id:
        return False
    if query.start or query.end:
        if post.scheduled_at is None:
            return False
        if query.start and post.scheduled_at < query.start:
            return False
        if query.end and post.scheduled_at >= query.end:
            return False
    if query.published_since and (post.published_at is None or post.published_at < query.published_since):
        return False
    if query.text:
        needle = query.text.lower()
        if needle not in post.text.lower() and needle not in post.title.lower():
            return False
    return True


class InMemoryMarketingStore:
    def __init__(self) -> None:
        self._accounts: dict[str, SocialAccount] = {}
        self._campaigns: dict[str, Campaign] = {}
        self._posts: dict[str, Post] = {}
        self._preferences: dict[tuple[str, str], MarketingPreferences] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._accounts.clear()
            self._campaigns.clear()
            self._posts.clear()
            self._preferences.clear()

    # accounts
    def list_accounts(self, context: TenantContext) -> list[SocialAccount]:
        with self._lock:
            items = [item.model_copy(deep=True) for item in self._accounts.values() if _scoped(item, context)]
        return sorted(items, key=lambda item: (item.platform.value, item.display_name.lower(), item.id))

    def get_account(self, context: TenantContext, account_id: str, *, for_update: bool = False) -> SocialAccount | None:
        with self._lock:
            found = self._accounts.get(account_id)
        return found.model_copy(deep=True) if found is not None and _scoped(found, context) else None

    def find_account(self, context: TenantContext, platform: Platform, external_id: str) -> SocialAccount | None:
        with self._lock:
            for item in self._accounts.values():
                if _scoped(item, context) and item.platform == platform and item.external_id == external_id:
                    return item.model_copy(deep=True)
        return None

    def add_account(self, account: SocialAccount) -> SocialAccount:
        with self._lock:
            self._accounts[account.id] = account.model_copy(deep=True)
        return account

    def save_account(self, account: SocialAccount) -> SocialAccount:
        with self._lock:
            if account.id not in self._accounts:
                raise KeyError(account.id)
            self._accounts[account.id] = account.model_copy(deep=True)
        return account

    # campaigns
    def list_campaigns(self, context: TenantContext, *, status: CampaignStatus | None, text: str | None, limit: int, before: Campaign | None) -> list[Campaign]:
        with self._lock:
            items = [item for item in self._campaigns.values() if _scoped(item, context)]
        if status:
            items = [item for item in items if item.status == status]
        if text:
            needle = text.lower()
            items = [item for item in items if needle in item.name.lower() or needle in item.product.lower() or needle in item.brief.lower()]
        if before is not None:
            items = [item for item in items if (item.created_at, item.id) < (before.created_at, before.id)]
        items.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def get_campaign(self, context: TenantContext, campaign_id: str, *, for_update: bool = False) -> Campaign | None:
        with self._lock:
            found = self._campaigns.get(campaign_id)
        return found.model_copy(deep=True) if found is not None and _scoped(found, context) else None

    def add_campaign(self, campaign: Campaign) -> Campaign:
        with self._lock:
            self._campaigns[campaign.id] = campaign.model_copy(deep=True)
        return campaign

    def save_campaign(self, campaign: Campaign) -> Campaign:
        with self._lock:
            if campaign.id not in self._campaigns:
                raise KeyError(campaign.id)
            self._campaigns[campaign.id] = campaign.model_copy(deep=True)
        return campaign

    def delete_campaign(self, context: TenantContext, campaign_id: str) -> bool:
        with self._lock:
            found = self._campaigns.get(campaign_id)
            if found is None or not _scoped(found, context):
                return False
            del self._campaigns[campaign_id]
            for post in self._posts.values():
                if post.campaign_id == campaign_id and _scoped(post, context):
                    post.campaign_id = None
        return True

    # posts
    def add_post(self, post: Post) -> Post:
        with self._lock:
            self._posts[post.id] = post.model_copy(deep=True)
        return post

    def get_post(self, context: TenantContext, post_id: str, *, for_update: bool = False) -> Post | None:
        with self._lock:
            found = self._posts.get(post_id)
        return found.model_copy(deep=True) if found is not None and _scoped(found, context) else None

    def save_post(self, post: Post) -> Post:
        with self._lock:
            if post.id not in self._posts:
                raise KeyError(post.id)
            self._posts[post.id] = post.model_copy(deep=True)
        return post

    def delete_post(self, context: TenantContext, post_id: str) -> bool:
        with self._lock:
            found = self._posts.get(post_id)
            if found is None or not _scoped(found, context):
                return False
            del self._posts[post_id]
        return True

    def list_posts(self, context: TenantContext, query: PostQuery) -> list[Post]:
        with self._lock:
            items = [item for item in self._posts.values() if _scoped(item, context) and matches(item, query)]
        if query.order == "schedule":
            items.sort(key=lambda item: (item.scheduled_at or item.created_at, item.id))
        else:
            if query.before is not None:
                items = [item for item in items if _recent_key(item) < _recent_key(query.before)]
            items.sort(key=_recent_key, reverse=True)
        return [item.model_copy(deep=True) for item in items[: query.limit]]

    def count_posts(self, context: TenantContext, campaign_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for item in self._posts.values():
                if _scoped(item, context) and item.campaign_id == campaign_id:
                    counts[item.status.value] = counts.get(item.status.value, 0) + 1
        return counts

    def due_posts(self, now: datetime, limit: int) -> list[Post]:
        """Every tenant's scheduled posts whose time has come (the memory backend's discovery)."""
        with self._lock:
            due = [
                item.model_copy(deep=True)
                for item in self._posts.values()
                if item.status == PostStatus.SCHEDULED and (item.next_attempt_at or item.scheduled_at or now) <= now
            ]
        due.sort(key=lambda item: item.next_attempt_at or item.scheduled_at or now)
        return due[:limit]

    # preferences
    def get_preferences(self, context: TenantContext) -> MarketingPreferences | None:
        with self._lock:
            found = self._preferences.get((context.tenant_id, context.workspace_id))
        return found.model_copy(deep=True) if found is not None else None

    def save_preferences(self, context: TenantContext, preferences: MarketingPreferences) -> MarketingPreferences:
        with self._lock:
            self._preferences[(context.tenant_id, context.workspace_id)] = preferences.model_copy(deep=True)
        return preferences


marketing_store = InMemoryMarketingStore()


@contextmanager
def open_marketing_store(context: TenantContext) -> Iterator[MarketingStore]:
    """One unit of work in the caller's tenant and workspace scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.marketing_repository import SqlAlchemyMarketingStore

        return SqlAlchemyMarketingStore(session, context)

    with open_scoped_store(context, marketing_store, sql_store) as store:
        yield store
