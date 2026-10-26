"""Office layout revision history and floor/building templates (expand only).

``office_floor_revisions`` is an append-only snapshot of one floor's layout (rooms, desks, positions,
rotation, doors, equipment) written in the same transaction as every layout change, so a restore is
exact and never rebuilt from audit text. A trigger refuses UPDATE; the API prunes the oldest rows beyond
a configurable per-floor cap with DELETE. ``office_layout_templates`` holds owner-named floor and building
templates (no agents, desks empty). Both tables are new: the previous release never touches them, so a
rollback is safe. Forced workspace RLS and the purge policy follow 0097.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0112_layout_history"
down_revision = "0111_meeting_summary_links"
branch_labels = None
depends_on = None

TABLES = ("office_floor_revisions", "office_layout_templates")
SCOPE = ("tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
         "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')")
TS = sa.DateTime(timezone=True)


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({SCOPE}) with check ({SCOPE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to anum_tenant_purger")
    # Every identifier comes from the fixed TABLES tuple above, never from a request.
    purge_policy = (f"create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger "  # nosec B608: fixed TABLES only
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)")
    op.execute(purge_policy)


def upgrade() -> None:
    op.create_table(
        "office_floor_revisions",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("floor_id", sa.String(80), primary_key=True),
        sa.Column("revision_no", sa.Integer(), primary_key=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("summary", JSONB(), nullable=False),
        sa.Column("restored_from", sa.Integer(), nullable=True),
        sa.Column("restored_over", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "floor_id"],
            ["office_floors.tenant_id", "office_floors.workspace_id", "office_floors.id"],
            name="fk_office_floor_revisions_floor", ondelete="CASCADE",
        ),
        sa.CheckConstraint("revision_no > 0", name="ck_office_floor_revisions_no"),
        sa.CheckConstraint("(restored_from is null) = (restored_over is null)", name="ck_office_floor_revisions_restore"),
    )
    op.execute("""create function office_floor_revisions_append_only() returns trigger
        language plpgsql as $$ begin
            raise exception 'office_floor_revisions is append only' using errcode = '55000';
        end $$""")
    op.execute("""create trigger office_floor_revisions_no_update before update on office_floor_revisions
        for each row execute function office_floor_revisions_append_only()""")
    op.create_table(
        "office_layout_templates",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("definition", JSONB(), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"]),
        sa.UniqueConstraint("tenant_id", "workspace_id", "kind", "name", name="uq_office_layout_template_name"),
        sa.CheckConstraint("kind in ('floor','building')", name="ck_office_layout_template_kind"),
        sa.CheckConstraint("revision > 0", name="ck_office_layout_template_revision"),
    )
    for table in TABLES:
        _rls(table)


def downgrade() -> None:
    # Never automatically downgrade stored history; back it up and disable the feature first.
    for table in reversed(TABLES):
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.drop_table(table)
    op.execute("drop function if exists office_floor_revisions_append_only()")
