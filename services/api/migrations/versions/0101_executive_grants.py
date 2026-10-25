"""ANUM MINE campus profiles and executive grants (expand only).

* ``campus_workspace_profiles``: what kind of building a workspace is on the campus
  (executive, business, department, personal) and its icon. Owner-set, never guessed.
* ``executive_grants``: an owner's explicit, revocable, optionally expiring permission
  for one person to read a *summary* of the owner's workspace (counts, tasks, approvals,
  cost, meetings). A new or widened grant starts ``pending`` and becomes ``active`` only
  when an owner approves the exact payload hash that was shown.
* ``anum_executive_reader``: a NOLOGIN role that may read only the identifying columns of
  the caller's own *active, unexpired* grants inside one tenant. It lets the API find the
  workspaces a person was granted without a cross-tenant query and without BYPASSRLS.
  Grant it to the API login ``WITH INHERIT FALSE, SET TRUE`` and reach it only with
  ``SET LOCAL ROLE`` (see infra/helm/bootstrap-database.sql).

Both tables have forced row level security by tenant and workspace and the lifecycle
purge policy. Nothing existing is altered, so the previous release keeps running.
Downgrade drops both tables; use it only in an empty workspace, otherwise disable the
feature and restore from backup (docs/ANUM_MINE_PRESENCE_AND_CAMPUS.md#rollback).
"""
import sqlalchemy as sa
from alembic import op

revision = "0101_executive_grants"
down_revision = "0100_office_room_geometry"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
TABLES = ("campus_workspace_profiles", "executive_grants")
SCOPE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({SCOPE}) with check ({SCOPE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to anum_tenant_purger")
    # The table name comes from the fixed TABLES tuple above, never from a request.
    op.execute(
        f"create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger "  # nosec B608
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)"
    )


def upgrade() -> None:
    op.create_table(
        "campus_workspace_profiles",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("icon", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_campus_workspace_profiles"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"],
            name="fk_campus_workspace_profiles_workspace",
        ),
        sa.CheckConstraint(
            "kind in ('executive', 'business', 'department', 'personal')", name="ck_campus_profile_kind"
        ),
        sa.CheckConstraint("icon ~ '^[a-z][a-z-]{1,19}$'", name="ck_campus_profile_icon"),
        sa.CheckConstraint("revision >= 1", name="ck_campus_profile_revision"),
    )
    op.create_table(
        "executive_grants",
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("grantee_user_id", sa.String(160), nullable=False),
        sa.Column("scopes", sa.ARRAY(sa.String(20)), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("requested_by", sa.String(160), nullable=False),
        sa.Column("requested_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("request_expires_at", TS, nullable=False),
        sa.Column("approved_by", sa.String(160)),
        sa.Column("approved_at", TS),
        sa.Column("expires_at", TS),
        sa.Column("revoked_by", sa.String(160)),
        sa.Column("revoked_at", TS),
        sa.Column("decision_reason", sa.String(500)),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_executive_grants"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"],
            name="fk_executive_grants_workspace",
        ),
        sa.CheckConstraint(
            "status in ('pending', 'active', 'rejected', 'revoked', 'superseded')",
            name="ck_executive_grants_status",
        ),
        sa.CheckConstraint(
            "cardinality(scopes) between 1 and 5 and "
            "scopes <@ array['counts', 'tasks', 'approvals', 'cost', 'meetings']::varchar[]",
            name="ck_executive_grants_scopes",
        ),
        sa.CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_executive_grants_hash"),
        sa.CheckConstraint("revision >= 1", name="ck_executive_grants_revision"),
        sa.CheckConstraint(
            "status <> 'active' or (approved_by is not null and approved_at is not null)",
            name="ck_executive_grants_active_approved",
        ),
    )
    op.create_index(
        "uq_executive_grants_one_active", "executive_grants",
        ["tenant_id", "workspace_id", "grantee_user_id"], unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_executive_grants_grantee", "executive_grants", ["tenant_id", "grantee_user_id", "status"])
    for table in TABLES:
        _rls(table)

    op.execute(
        """
        do $$
        begin
          if not exists (select 1 from pg_roles where rolname = 'anum_executive_reader') then
            create role anum_executive_reader nologin;
          end if;
        end
        $$
        """
    )
    op.execute("grant usage on schema public to anum_executive_reader")
    op.execute(
        "grant select (id, tenant_id, workspace_id, grantee_user_id, scopes, status, expires_at, revoked_at) "
        "on executive_grants to anum_executive_reader"
    )
    op.execute(
        """
        create policy executive_reader_own_active on executive_grants
        for select to anum_executive_reader
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and grantee_user_id = nullif(current_setting('anum.user_id', true), '')
          and status = 'active'
          and revoked_at is null
          and (expires_at is null or expires_at > now())
        )
        """
    )


def downgrade() -> None:
    # Manual operator approval, a stopped feature and a backup are required for live data.
    op.execute("drop policy if exists executive_reader_own_active on executive_grants")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_executive_reader') then
            revoke all on executive_grants from anum_executive_reader;
            revoke usage on schema public from anum_executive_reader;
          end if;
        end
        $$
        """
    )
    op.drop_table("executive_grants")
    op.drop_table("campus_workspace_profiles")
