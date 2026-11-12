"""Group calls between teammates (docs/in-app-calls.md#group-calls).

* ``app_call_rooms``: one row per group call: the host, audio or video, live or ended, the
  members (``member_user_ids`` for the row policy, ``members`` with each person's name at
  the time, state, device and times) and when it was created and ended.
* ``app_calls.room_id``: the group call a pair call belongs to. Every two people in a group
  call have their own pair call (a mesh), so signaling, heartbeats and the participants-only
  policy of ``app_calls`` stay as they are. ``on delete set null``: the call history keeps
  the pair when the group row is purged first.

``app_call_rooms`` belongs to one tenant and workspace under forced RLS:
``tenant_isolation_app_call_rooms`` on ``anum.tenant_id`` and ``anum.workspace_id``, a
restrictive ``members_only_app_call_rooms`` that admits a row only to a person named in
``member_user_ids`` (``anum.user_id``), and a restrictive insert policy: only the host
inserts it. Deleting the organisation or a workspace purges it through ``anum_tenant_purger``
(the grant and policy of ``0079_tenant_lifecycle``). Retention needs no new grant: the daily job deletes a group row together with
its pair calls, as the application role inside a member's context.

Expand only: a new table and a nullable column. The previous release ignores both (it never
writes ``room_id``; its pair calls are plain calls between two people).

Chained after ``0178_refund_requests_vault``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0179_app_call_rooms"
down_revision = "0178_refund_requests_vault"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "app_call_rooms",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("host_user_id", sa.String(160), nullable=False),
        sa.Column("media", sa.String(8), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("member_user_ids", postgresql.ARRAY(sa.String(160)), nullable=False),
        sa.Column("members", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", TS, nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_app_call_rooms_workspace"
        ),
        sa.CheckConstraint("media in ('audio', 'video')", name="ck_app_call_rooms_media"),
        sa.CheckConstraint("status in ('live', 'ended')", name="ck_app_call_rooms_status"),
        sa.CheckConstraint("(status = 'ended') = (ended_at is not null)", name="ck_app_call_rooms_ended"),
        sa.CheckConstraint("cardinality(member_user_ids) between 1 and 12", name="ck_app_call_rooms_members"),
        sa.CheckConstraint("jsonb_typeof(members) = 'array'", name="ck_app_call_rooms_members_json"),
        sa.CheckConstraint("octet_length(members::text) <= 8000", name="ck_app_call_rooms_members_size"),
    )
    op.create_index("ix_app_call_rooms_scope", "app_call_rooms", ["tenant_id", "workspace_id", "created_at"])

    op.execute("alter table app_call_rooms enable row level security")
    op.execute("alter table app_call_rooms force row level security")
    op.execute(
        """
        create policy tenant_isolation_app_call_rooms on app_call_rooms
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
        )
        with check (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
        )
        """
    )
    op.execute(
        """
        create policy members_only_app_call_rooms on app_call_rooms
        as restrictive
        using (nullif(current_setting('anum.user_id', true), '') = any(member_user_ids) or current_user = 'anum_tenant_purger')
        with check (nullif(current_setting('anum.user_id', true), '') = any(member_user_ids))
        """
    )
    op.execute(
        """
        create policy inserter_only_app_call_rooms on app_call_rooms
        as restrictive
        for insert
        with check (host_user_id = nullif(current_setting('anum.user_id', true), ''))
        """
    )
    # Deleting the organisation or one workspace removes these rows too (0079's purge policy).
    op.execute("grant select (tenant_id, ctid, workspace_id), delete on app_call_rooms to anum_tenant_purger")
    op.execute(
        """
        create policy lifecycle_purge_app_call_rooms on app_call_rooms
        for all to anum_tenant_purger
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and exists (
            select 1 from tenant_lifecycle_jobs j
            where j.tenant_id = app_call_rooms.tenant_id and j.status = 'purging'
              and (j.kind = 'organisation_deletion'
                   or (j.kind = 'workspace_deletion' and j.workspace_id = app_call_rooms.workspace_id))
          )
        )
        with check (false)
        """
    )

    op.add_column("app_calls", sa.Column("room_id", sa.String(80), nullable=True))
    op.create_foreign_key("fk_app_calls_room", "app_calls", "app_call_rooms", ["room_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_app_calls_room", "app_calls", ["tenant_id", "workspace_id", "room_id"])


def downgrade() -> None:
    op.drop_index("ix_app_calls_room", table_name="app_calls")
    op.drop_constraint("fk_app_calls_room", "app_calls", type_="foreignkey")
    op.drop_column("app_calls", "room_id")
    op.drop_table("app_call_rooms")
