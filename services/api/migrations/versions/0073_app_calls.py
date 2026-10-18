"""In-app calls between teammates (docs/in-app-calls.md#storage).

* ``app_calls``: one row per call: caller and callee (user ids and display names at the
  time of the call), the device of each side that is bound to the call, audio or video,
  the status (ringing, accepted, ended), the outcome and the times (created, answered,
  ended, last heartbeat) with the duration.
* ``app_call_signals``: the offers, answers and ICE candidate batches of a call that is in
  progress, each addressed to one device of the other participant. Deleted when the call
  ends (and with the call, ``on delete cascade``).

Both tables belong to one tenant and workspace (foreign key to ``workspaces``) under forced
RLS: ``tenant_isolation_<table>`` on ``anum.tenant_id`` and ``anum.workspace_id``, a
restrictive ``participants_only_<table>`` that admits a row only to its two participants
(``anum.user_id``), and a restrictive insert policy: only the caller inserts a call and
only the sender inserts a signal.

Retention (``python -m anum_api.voice_retention``, the daily job): ``anum_maintenance`` may
discover ended calls older than seven days, and reads only ``id``, ``tenant_id``,
``workspace_id``, ``caller_user_id`` and ``ended_at`` (a column grant and a permissive select
policy for that role; the participants-only policy admits that role's reads). The job
then deletes as the application role inside the caller's RLS context. Grant
``anum_maintenance`` ``WITH INHERIT FALSE, SET TRUE`` (as the bootstrap already does).

Expand only: two new tables. The previous release ignores them.

Chained after ``0072_push_device_language`` (main's head when merged).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0073_app_calls"
down_revision = "0072_push_device_language"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
ME = "nullif(current_setting('anum.user_id', true), '')"
PARTICIPANTS = {
    # The maintenance role reads (never writes) ended calls for retention; see below.
    "app_calls": f"{ME} in (caller_user_id, callee_user_id) or current_user = 'anum_maintenance'",
    "app_call_signals": f"{ME} in (sender_user_id, recipient_user_id)",
}
INSERTER = {
    "app_calls": f"caller_user_id = {ME}",
    "app_call_signals": f"sender_user_id = {ME}",
}
TS = sa.DateTime(timezone=True)


def _scope(table: str) -> list:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def upgrade() -> None:
    op.create_table(
        "app_calls",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope("app_calls"),
        sa.Column("caller_user_id", sa.String(160), nullable=False),
        sa.Column("callee_user_id", sa.String(160), nullable=False),
        sa.Column("caller_name", sa.String(120), nullable=False),
        sa.Column("callee_name", sa.String(120), nullable=False),
        sa.Column("caller_client_id", sa.String(64), nullable=False),
        sa.Column("callee_client_id", sa.String(64), nullable=True),
        sa.Column("media", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("ended_by_user_id", sa.String(160), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("answered_at", TS, nullable=True),
        sa.Column("ended_at", TS, nullable=True),
        sa.Column("heartbeat_at", TS, nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        _workspace_fk("app_calls"),
        sa.CheckConstraint("caller_user_id <> callee_user_id", name="ck_app_calls_two_people"),
        sa.CheckConstraint("media in ('audio', 'video')", name="ck_app_calls_media"),
        sa.CheckConstraint("status in ('ringing', 'accepted', 'ended')", name="ck_app_calls_status"),
        sa.CheckConstraint(
            "outcome is null or outcome in ('completed', 'declined', 'missed', 'cancelled', 'busy', 'failed', 'dropped')",
            name="ck_app_calls_outcome",
        ),
        sa.CheckConstraint("(status = 'ended') = (outcome is not null)", name="ck_app_calls_outcome_when_ended"),
        sa.CheckConstraint("status = 'ringing' or callee_client_id is not null or status = 'ended'", name="ck_app_calls_bound"),
        sa.CheckConstraint("duration_seconds is null or duration_seconds >= 0", name="ck_app_calls_duration"),
    )
    op.create_index("ix_app_calls_scope_caller", "app_calls", ["tenant_id", "workspace_id", "caller_user_id", "created_at"])
    op.create_index("ix_app_calls_scope_callee", "app_calls", ["tenant_id", "workspace_id", "callee_user_id", "created_at"])

    op.create_table(
        "app_call_signals",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope("app_call_signals"),
        sa.Column("call_id", sa.String(80), nullable=False),
        sa.Column("sender_user_id", sa.String(160), nullable=False),
        sa.Column("recipient_user_id", sa.String(160), nullable=False),
        sa.Column("recipient_client_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("app_call_signals"),
        sa.ForeignKeyConstraint(["call_id"], ["app_calls.id"], name="fk_app_call_signals_call", ondelete="CASCADE"),
        sa.CheckConstraint("kind in ('offer', 'answer', 'ice')", name="ck_app_call_signals_kind"),
        sa.CheckConstraint("octet_length(payload::text) <= 40000", name="ck_app_call_signals_size"),
    )
    op.create_index(
        "ix_app_call_signals_recipient",
        "app_call_signals",
        ["tenant_id", "workspace_id", "recipient_client_id", "call_id", "created_at"],
    )

    for table in ("app_calls", "app_call_signals"):
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
        op.execute(
            f"""
            create policy tenant_isolation_{table} on {table}
            using (
              {PREDICATE}
            )
            with check (
              {PREDICATE}
            )
            """
        )
        op.execute(
            f"""
            create policy participants_only_{table} on {table}
            as restrictive
            using ({PARTICIPANTS[table]})
            with check ({PARTICIPANTS[table].split(" or current_user")[0]})
            """
        )
        op.execute(
            f"""
            create policy inserter_only_{table} on {table}
            as restrictive
            for insert
            with check ({INSERTER[table]})
            """
        )


    op.execute("grant select (id, tenant_id, workspace_id, caller_user_id, ended_at) on app_calls to anum_maintenance")
    # Permissive policies are OR-ed per role, so this widens access for this role only.
    op.execute(
        """
        create policy maintenance_ended_app_calls on app_calls
        for select to anum_maintenance
        using (status = 'ended' and ended_at <= now() - interval '7 days')
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_ended_app_calls on app_calls")
    op.drop_table("app_call_signals")
    op.drop_table("app_calls")
