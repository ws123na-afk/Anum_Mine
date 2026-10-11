"""The caller's own preferences (docs/memory.md#from-conversations).

``GET /api/v1/me/preferences`` and ``PUT /api/v1/me/preferences`` read and change the
preferences of the authenticated user, never anyone else's: there is no user id in the
path or body, the store is keyed by the caller's tenant and user id, and with PostgreSQL
the ``user_preferences`` row is under forced RLS on ``anum.tenant_id`` and
``anum.user_id`` (migration ``0025_user_preferences``).

``remember_conversations`` ("Remember things from my chats", on by default) turns
conversation memory off for this user in every workspace of the tenant: no facts are
extracted from their chats and no memories are recalled into their questions. Memories
already remembered stay until the user deletes them (``DELETE /api/v1/memories?source_type=
conversation`` or one by one in the Memory view). The server setting
``ANUM_CONVERSATION_MEMORY_ENABLED`` and the tenant's memory governance still apply on
top: the response's ``conversation_memory`` says which of them, if any, keeps memory off.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Protocol

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .dependencies import tenant_context
from .schemas import TenantContext, utc_now
from .scoped_store import open_scoped_store
from .settings import settings

router = APIRouter(prefix="/api/v1", tags=["user-preferences"])


class UserPreferences(BaseModel):
    """What is stored for one user in one tenant; a missing row means these defaults."""

    remember_conversations: bool = True
    updated_at: datetime | None = None


class UserPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remember_conversations: bool = Field(description="Remember things from my chats, and recall them")


class ConversationMemoryState(StrEnum):
    """Whether the assistant remembers from and recalls into this user's chats, and if not, why."""

    ON = "on"
    OFF_BY_USER = "off_by_user"
    OFF_ON_SERVER = "off_on_server"
    OFF_FOR_ORGANIZATION = "off_for_organization"
    NOT_FOR_ROLE = "not_for_role"


class UserPreferencesView(UserPreferences):
    conversation_memory: ConversationMemoryState = Field(
        description=(
            "on; off_by_user (remember_conversations is false); off_on_server "
            "(ANUM_CONVERSATION_MEMORY_ENABLED=false); off_for_organization (memory governance "
            "does not allow the conversation source); not_for_role (the caller's role cannot create memories)"
        )
    )


class UserPreferenceStore(Protocol):
    def get(self, context: TenantContext) -> UserPreferences | None: ...
    def save(self, context: TenantContext, preferences: UserPreferences) -> UserPreferences: ...


class InMemoryUserPreferenceStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], UserPreferences] = {}
        self._lock = RLock()

    def get(self, context: TenantContext) -> UserPreferences | None:
        with self._lock:
            return self._items.get((context.tenant_id, context.user_id))

    def save(self, context: TenantContext, preferences: UserPreferences) -> UserPreferences:
        with self._lock:
            self._items[(context.tenant_id, context.user_id)] = preferences
        return preferences

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


user_preference_store = InMemoryUserPreferenceStore()


@contextmanager
def open_user_preference_store(context: TenantContext) -> Iterator[UserPreferenceStore]:
    """The caller's preferences for one unit of work (``anum.user_id`` is set)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.user_preferences_repository import SqlAlchemyUserPreferenceStore

        return SqlAlchemyUserPreferenceStore(session)

    with open_scoped_store(context, user_preference_store, sql_store, user_scoped=True) as store:
        yield store


def get_user_preferences(context: TenantContext) -> UserPreferences:
    with open_user_preference_store(context) as store:
        return store.get(context) or UserPreferences()


def remembers_conversations(context: TenantContext) -> bool:
    """The user's "Remember things from my chats" switch (on unless they turned it off).

    Raises when the store cannot be read; callers treat that as off.
    """
    return get_user_preferences(context).remember_conversations


def conversation_memory_state(context: TenantContext, preferences: UserPreferences) -> ConversationMemoryState:
    from .authorization import Permission
    from .conversation_memory import governance_allows_conversation_memory, has_permission

    if not settings.conversation_memory_enabled:
        return ConversationMemoryState.OFF_ON_SERVER
    if not preferences.remember_conversations:
        return ConversationMemoryState.OFF_BY_USER
    try:
        allowed = governance_allows_conversation_memory(context)
    except Exception:  # unknown policy: nothing is remembered (extraction_enabled)
        allowed = False
    if not allowed:
        return ConversationMemoryState.OFF_FOR_ORGANIZATION
    if not has_permission(context, Permission.MEMORY_CREATE):
        return ConversationMemoryState.NOT_FOR_ROLE
    return ConversationMemoryState.ON


def _view(context: TenantContext, preferences: UserPreferences) -> UserPreferencesView:
    return UserPreferencesView(
        **preferences.model_dump(),
        conversation_memory=conversation_memory_state(context, preferences),
    )


@router.get("/me/preferences", response_model=UserPreferencesView)
async def my_preferences(context: TenantContext = Depends(tenant_context)) -> UserPreferencesView:
    """The caller's own preferences (defaults when never saved). Open to every member."""
    return _view(context, get_user_preferences(context))


@router.put("/me/preferences", response_model=UserPreferencesView)
async def set_my_preferences(
    payload: UserPreferencesUpdate,
    context: TenantContext = Depends(tenant_context),
) -> UserPreferencesView:
    """Change the caller's own preferences; nobody can change another user's."""
    preferences = UserPreferences(remember_conversations=payload.remember_conversations, updated_at=utc_now())
    with open_user_preference_store(context) as store:
        saved = store.save(context, preferences)
    return _view(context, saved)
