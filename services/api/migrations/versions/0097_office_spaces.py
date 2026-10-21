"""ANUM MINE office layout storage, isolated per tenant/workspace.

Expand-only production schema: buildings, floors, rooms, and desks.
The API stays disabled until authorization, migrations, integration tests and
app feature flags are verified. This schema must not be used to infer agent activity.

Every table has forced workspace RLS, composite scope foreign keys,
revision counters and explicit lifecycle purge policy.
"""
import sqlalchemy as sa
from alembic import op

revision = "0097_office_spaces"
down_revision = "0096_invoice_credit_notes"
branch_labels = None
depends_on = None

TABLES = ("office_buildings", "office_floors", "office_rooms", "office_desks")
SCOPE = "tenant_id = nullif(current_setting('anum.tenant_id', true), '') and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
TS = sa.DateTime(timezone=True)


def _scope() -> list:
    return [
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"]),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_office_scoped_" + "placeholder"),
    ]


def _common(name: str) -> list:
    cols = _scope()
    cols[-1] = sa.UniqueConstraint("tenant_id", "workspace_id", "id", name=f"uq_{name}_scoped_id")
    return cols


def _fk(table: str, column: str, parent: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id", column],
        [f"{parent}.tenant_id", f"{parent}.workspace_id", f"{parent}.id"],
        name=f"fk_{table}_{column}",
        ondelete="RESTRICT",
    )


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({SCOPE}) with check ({SCOPE})")
    # Same tenant/workspace purge service pattern as 0096; no blanket permissions.
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to anum_tenant_purger")
    op.execute(
        f"""create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger
        using (
            tenant_id = nullif(current_setting('anum.tenant_id', true), '')
            and exists (select 1 from tenant_lifecycle_jobs j
                        where j.tenant_id = {table}.tenant_id and j.status = 'purging'
                        and (j.kind = 'organisation_deletion'
                             or j.workspace_id = {table}.workspace_id))
        )"""
    )


def upgrade() -> None:
    op.create_table(
        "office_buildings",
        *_common("office_buildings"),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.CheckConstraint("revision > 0", name="ck_office_buildings_revision"),
        sa.CheckConstraint("latitude between -90 and 90 or latitude is null", name="ck_office_buildings_lat"),
        sa.CheckConstraint("longitude between -180 and 180 or longitude is null", name="ck_office_buildings_lon"),
    )
    op.create_table(
        "office_floors",
        *_common("office_floors"),
        sa.Column("building_id", sa.String(80), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        _fk("office_floors", "building_id", "office_buildings"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "building_id", "level", name="uq_office_floor_level"),
        sa.CheckConstraint("level between -20 and 120", name="ck_office_floor_level"),
    )
    op.create_table(
        "office_rooms",
        *_common("office_rooms"),
        sa.Column("floor_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        _fk("office_rooms", "floor_id", "office_floors"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "floor_id", "name", name="uq_office_room_name"),
        sa.CheckConstraint("kind in ('office','meeting','reception','workshop','warehouse','lounge')", name="ck_office_room_kind"),
        sa.CheckConstraint("capacity between 1 and 5000", name="ck_office_room_capacity"),
    )
    op.create_table(
        "office_desks",
        *_common("office_desks"),
        sa.Column("room_id", sa.String(80), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("agent_id", sa.String(80), nullable=True),
        _fk("office_desks", "room_id", "office_rooms"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "room_id", "label", name="uq_office_desk_label"),
    )
    # Occupying a second desk is rejected concurrently by PostgreSQL, not just frontend validation.
    op.create_index("uq_office_agent_desk", "office_desks", ["tenant_id", "workspace_id", "agent_id"], unique=True, postgresql_where=sa.text("agent_id is not null"))
    for table, parent in (
        ("office_floors", "building_id"),
        ("office_rooms", "floor_id"),
        ("office_desks", "room_id"),
    ):
        op.create_index(f"ix_{table}_{parent}", table, ["tenant_id", "workspace_id", parent])
    for table in TABLES:
        _rls(table)


def downgrade() -> None:
    # Never automatically downgrade a live populated layout; back up and disable feature first.
    for table in reversed(TABLES):
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.drop_table(table)
