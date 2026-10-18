"""Usage meters of one organisation, from ANUM's own counters (docs/billing.md#plans-and-limits).

An organisation's rows sit in many workspaces, and the workspace tables' row policies
admit one workspace at a time. The meters are therefore read as ``anum_maintenance`` with
``anum.billing_tenant`` set to the caller's own organisation (migration
``0081_customer_billing``): column grants on ids, sizes, flags and timestamps only, and
policies that admit only that organisation's rows. In memory the same counts come from
the in-process stores.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..settings import settings

GB = 1024**3
# key -> (limit key in plan limits, unit)
METERS: dict[str, tuple[str, str]] = {
    "seats": ("seats", "seats"),
    "workspaces": ("workspaces", "workspaces"),
    "agent_runs": ("agent_runs_per_month", "runs"),
    "storage": ("storage_gb", "bytes"),
    "messages": ("messages_per_month", "messages"),
}


@dataclass(frozen=True)
class Usage:
    seats: int
    workspaces: int
    agent_runs: int
    storage: int
    messages: int

    def get(self, key: str) -> int:
        return int(getattr(self, key))


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def period_key(now: datetime) -> str:
    return f"{now.year:04d}-{now.month:02d}"


_SQL = """
select
  (select count(distinct m.user_id) from workspace_memberships m where m.tenant_id = :t and m.active)
  + (select count(*) from workspace_invitations i where i.tenant_id = :t and i.status = 'pending' and i.expires_at > now())
    as seats,
  (select count(*) from workspaces w where w.tenant_id = :t and w.archived_at is null) as workspaces,
  (select count(*) from tasks x where x.tenant_id = :t and x.created_at >= :since) as agent_runs,
  (select coalesce(sum(f.size_bytes), 0) from workspace_files f where f.tenant_id = :t) as storage,
  (select count(*) from conversation_turns c where c.tenant_id = :t and c.role = 'user' and c.created_at >= :since) as messages
"""


def measure(tenant_id: str, now: datetime) -> Usage:
    since = month_start(now)
    if settings.repository_backend == "postgresql":
        from .rows import reader_rows

        row = reader_rows("anum_maintenance", {"anum.billing_tenant": tenant_id}, _SQL, {"t": tenant_id, "since": since})[0]
        return Usage(**{key: int(row[key] or 0) for key in ("seats", "workspaces", "agent_runs", "storage", "messages")})
    return _measure_memory(tenant_id, since, now)


def _measure_memory(tenant_id: str, since: datetime, now: datetime) -> Usage:
    from ..conversations import conversation_store
    from ..dependencies import memory_repository
    from ..files import file_store

    store = memory_repository.store
    members = {user for (tenant, _, user), membership in store.memberships.items() if tenant == tenant_id and membership.active}
    pending = sum(
        1 for invitation in store.invitations.values()
        if invitation.tenant_id == tenant_id and invitation.status == "pending" and invitation.expires_at > now
    )
    workspaces = sum(1 for w in store.workspaces.values() if w.tenant_id == tenant_id and w.archived_at is None)
    runs = sum(1 for task in store.tasks.values() if task.tenant_id == tenant_id and task.created_at >= since)
    storage = sum(record.size_bytes for record in list(file_store.records.values()) if record.tenant_id == tenant_id)
    messages = 0
    for conversation in list(conversation_store.conversations.values()):
        if conversation.tenant_id != tenant_id:
            continue
        messages += sum(
            1 for turn in conversation_store.turn_log.get(conversation.id, [])
            if turn.role == "user" and turn.created_at >= since
        )
    return Usage(seats=len(members) + pending, workspaces=workspaces, agent_runs=runs, storage=storage, messages=messages)


def limit_of(limits: dict[str, object], key: str, per_seat: bool = False, seats: int = 1) -> int | None:
    """The plan's limit for a meter in the meter's unit (storage in bytes); ``None`` is unlimited."""
    if key == "seats" and per_seat:
        return max(1, seats)
    raw = limits.get(METERS[key][0])
    if raw is None:
        return None
    value = int(raw)  # type: ignore[call-overload]
    return value * GB if key == "storage" else value
