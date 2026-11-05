"""Career building kind and the reception visitor register (expand only).

Owner decisions of 27 October 2026 (docs/ANUM_MINE_FEATURE_COMPLETION_MATRIX.md, O2 and O4):

* O2: "career" is its own building kind. ``campus_workspace_profiles.kind`` may now also hold ``career``. The check
  constraint is only widened: every value the previous release writes is still valid, so a rollback of the application
  keeps working against this schema (the previous web shows an unknown kind as the plain business look).
* O4: Reception is a real visitor register. ``office_visitors`` holds one row per visit (who, from which company, who
  they visit, why, when expected, when checked in and out, and its status). Building and room are optional links: a
  composite foreign key on (tenant, workspace, id) so a building or room of another workspace or tenant can never be
  linked; deleting a building or room clears only that column (``ON DELETE SET NULL (column)``, PostgreSQL 15 or
  newer, as in 0108) and keeps the visit. The table follows 0097: forced workspace RLS and the tenant purge policy.
  Nothing is backfilled and no visitor is invented; the previous release never reads the new table.
"""
import sqlalchemy as sa
from alembic import op

revision = "0118_career_visitor_register"
down_revision = "0117_building_logo_signs"
branch_labels = None
depends_on = None

TABLE = "office_visitors"
OLD_KINDS = "'executive', 'business', 'department', 'personal'"
NEW_KINDS = "'executive', 'business', 'department', 'personal', 'career'"
SCOPE = ("tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
         "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')")
TS = sa.DateTime(timezone=True)


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({SCOPE}) with check ({SCOPE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to anum_tenant_purger")
    purge_policy = (f"create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger "  # nosec B608: fixed table name
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)")
    op.execute(purge_policy)


def upgrade() -> None:
    op.drop_constraint("ck_campus_profile_kind", "campus_workspace_profiles", type_="check")
    op.create_check_constraint("ck_campus_profile_kind", "campus_workspace_profiles", f"kind in ({NEW_KINDS})")

    op.create_table(
        TABLE,
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("building_id", sa.String(80), nullable=True),
        sa.Column("room_id", sa.String(80), nullable=True),
        sa.Column("visitor_name", sa.String(120), nullable=False),
        sa.Column("company", sa.String(120), nullable=True),
        sa.Column("host_kind", sa.String(10), nullable=True),
        sa.Column("host_id", sa.String(160), nullable=True),
        sa.Column("purpose", sa.String(200), nullable=False),
        sa.Column("expected_at", TS, nullable=False),
        sa.Column("checked_in_at", TS, nullable=True),
        sa.Column("checked_out_at", TS, nullable=True),
        sa.Column("cancelled_at", TS, nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="expected"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"],
                                name="fk_office_visitors_workspace"),
        sa.CheckConstraint("status in ('expected', 'checked_in', 'checked_out', 'cancelled')", name="ck_office_visitors_status"),
        sa.CheckConstraint("revision > 0", name="ck_office_visitors_revision"),
        sa.CheckConstraint("(host_kind is null and host_id is null) or (host_kind in ('user', 'agent') and host_id is not null)",
                           name="ck_office_visitors_host"),
        sa.CheckConstraint("length(btrim(visitor_name)) > 0 and length(btrim(purpose)) > 0", name="ck_office_visitors_text"),
        # The times follow the status: a visit is checked in only with a check-in time, out only after being in.
        sa.CheckConstraint(
            "(status = 'expected' and checked_in_at is null and checked_out_at is null and cancelled_at is null) or "
            "(status = 'checked_in' and checked_in_at is not null and checked_out_at is null and cancelled_at is null) or "
            "(status = 'checked_out' and checked_in_at is not null and checked_out_at is not null "
            "and checked_out_at >= checked_in_at and cancelled_at is null) or "
            "(status = 'cancelled' and checked_in_at is null and checked_out_at is null and cancelled_at is not null)",
            name="ck_office_visitors_times"),
    )
    op.execute(
        f"alter table {TABLE} add constraint fk_office_visitors_building foreign key (tenant_id, workspace_id, building_id) "
        "references office_buildings (tenant_id, workspace_id, id) on delete set null (building_id)"
    )
    op.execute(
        f"alter table {TABLE} add constraint fk_office_visitors_room foreign key (tenant_id, workspace_id, room_id) "
        "references office_rooms (tenant_id, workspace_id, id) on delete set null (room_id)"
    )
    op.create_index("ix_office_visitors_expected", TABLE, ["tenant_id", "workspace_id", "expected_at", "id"])
    op.create_index("ix_office_visitors_in", TABLE, ["tenant_id", "workspace_id", "room_id"],
                    postgresql_where=sa.text("status = 'checked_in'"))
    _rls(TABLE)


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute(f"drop policy if exists lifecycle_purge_{TABLE} on {TABLE}")
    op.drop_table(TABLE)
    op.execute("update campus_workspace_profiles set kind = 'personal' where kind = 'career'")
    op.drop_constraint("ck_campus_profile_kind", "campus_workspace_profiles", type_="check")
    op.create_check_constraint("ck_campus_profile_kind", "campus_workspace_profiles", f"kind in ({OLD_KINDS})")
