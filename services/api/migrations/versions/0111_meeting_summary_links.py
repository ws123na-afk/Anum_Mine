"""Meeting summary and decision to work links (matrix rows 9.6, 9.7, 9.9, 14.8).

Expand only. Chained on 0110_avatar_appearance; renumbered at merge when parallel branches collide.

* ``agent_meeting_outputs.kind`` also accepts ``summary`` (a draft the owner confirms, like a decision). The check
  is only widened, so the previous release keeps working; it never writes a summary and a row of that kind is
  just one more output to it.
* Two new columns record where an output came from: ``origin`` (``person`` by default, or ``model`` for a draft the
  model extracted from the transcript) and ``drafted_with`` (the provider and model that wrote it). Defaults keep every
  existing row valid.
* A partial unique index allows one live (draft or confirmed) summary per meeting. A dismissed one may be replaced.
* ``agent_meeting_output_links``: a confirmed decision points at the work it led to, a task, an approval or a run of
  the same workspace. Scoped key, scoped foreign keys, forced row level security and the lifecycle purge policy like
  the 0104 tables. The target id is checked by the API inside the caller's scoped transaction (no foreign key: a
  task, approval or run keeps its own lifecycle), and the unique key makes the link idempotent.
"""
import sqlalchemy as sa
from alembic import op

revision = "0111_meeting_summary_links"
down_revision = "0110_avatar_appearance"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
TABLE = "agent_meeting_output_links"
PREDICATE = "tenant_id = nullif(current_setting('anum.tenant_id', true), '') and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"grant select (tenant_id, workspace_id, ctid), delete on {table} to anum_tenant_purger")
    purge_policy = (f"create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger "  # nosec B608: fixed table only
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)")
    op.execute(purge_policy)


def upgrade() -> None:
    op.drop_constraint("ck_agent_meeting_output_kind", "agent_meeting_outputs", type_="check")
    op.create_check_constraint("ck_agent_meeting_output_kind", "agent_meeting_outputs", "kind in ('decision', 'action_item', 'summary')")
    op.add_column("agent_meeting_outputs", sa.Column("origin", sa.String(8), nullable=False, server_default="person"))
    op.add_column("agent_meeting_outputs", sa.Column("drafted_with", sa.String(120)))
    op.create_check_constraint("ck_agent_meeting_output_origin", "agent_meeting_outputs", "origin in ('person', 'model')")
    op.create_index(
        "uq_agent_meeting_live_summary", "agent_meeting_outputs", ["tenant_id", "workspace_id", "session_id"], unique=True,
        postgresql_where=sa.text("kind = 'summary' and state in ('draft', 'confirmed')"),
    )
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("session_id", sa.String(80), nullable=False),
        sa.Column("output_id", sa.String(80), nullable=False),
        sa.Column("target_kind", sa.String(10), nullable=False),
        sa.Column("target_id", sa.String(80), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name=f"pk_{TABLE}_scoped"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{TABLE}_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "session_id"],
            ["agent_meeting_sessions.tenant_id", "agent_meeting_sessions.workspace_id", "agent_meeting_sessions.id"],
            name=f"fk_{TABLE}_session", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "output_id"],
            ["agent_meeting_outputs.tenant_id", "agent_meeting_outputs.workspace_id", "agent_meeting_outputs.id"],
            name=f"fk_{TABLE}_output", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "workspace_id", "output_id", "target_kind", "target_id", name="uq_agent_meeting_output_link"),
        sa.CheckConstraint("target_kind in ('task', 'approval', 'run')", name="ck_agent_meeting_output_link_kind"),
        sa.CheckConstraint("length(btrim(target_id)) > 0", name="ck_agent_meeting_output_link_target"),
    )
    op.create_index(f"ix_{TABLE}_session", TABLE, ["tenant_id", "workspace_id", "session_id"])
    op.create_index(f"ix_{TABLE}_target", TABLE, ["tenant_id", "workspace_id", "target_kind", "target_id"])
    _rls(TABLE)


def downgrade() -> None:
    # Manual operator approval and a backup are required for live data: summaries and links are dropped.
    op.drop_table(TABLE)
    op.drop_index("uq_agent_meeting_live_summary", table_name="agent_meeting_outputs")
    op.execute("delete from agent_meeting_outputs where kind = 'summary'")
    op.drop_constraint("ck_agent_meeting_output_origin", "agent_meeting_outputs", type_="check")
    op.drop_column("agent_meeting_outputs", "drafted_with")
    op.drop_column("agent_meeting_outputs", "origin")
    op.drop_constraint("ck_agent_meeting_output_kind", "agent_meeting_outputs", type_="check")
    op.create_check_constraint("ck_agent_meeting_output_kind", "agent_meeting_outputs", "kind in ('decision', 'action_item')")
