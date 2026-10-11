"""PostgreSQL store for per-user preferences (migration ``0025_user_preferences``).

The session's RLS context names the tenant and the user (``open_scoped_store(...,
user_scoped=True)``); every statement repeats both, so a row of another user or tenant
is neither read nor written even if the policy were missing.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from anum_api.schemas import TenantContext, utc_now
from anum_api.user_preferences import UserPreferences

from .models import UserPreferenceRecord
from .scope import require_tenant


class SqlAlchemyUserPreferenceStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, context: TenantContext) -> UserPreferences | None:
        record = self._record(context)
        if record is None:
            return None
        return UserPreferences(remember_conversations=record.remember_conversations, updated_at=record.updated_at)

    def save(self, context: TenantContext, preferences: UserPreferences) -> UserPreferences:
        require_tenant(self.session, context.tenant_id)
        record = self._record(context)
        if record is None:
            record = UserPreferenceRecord(tenant_id=context.tenant_id, user_id=context.user_id)
            self.session.add(record)
        record.remember_conversations = preferences.remember_conversations
        record.updated_at = preferences.updated_at or utc_now()
        self.session.flush()
        return UserPreferences(remember_conversations=record.remember_conversations, updated_at=record.updated_at)

    def _record(self, context: TenantContext) -> UserPreferenceRecord | None:
        return self.session.get(UserPreferenceRecord, (context.tenant_id, context.user_id))
