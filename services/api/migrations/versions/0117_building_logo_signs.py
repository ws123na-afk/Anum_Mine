"""Logo and brand signs for office buildings (expand only).

Adds three nullable columns to ``office_buildings`` (the sign style, the brand colour and the SHA-256 of the
owner's uploaded logo, which doubles as its cache validator) and one new table, ``office_building_logos``, that
holds the re-encoded logo itself (a PNG of at most 512 x 512 pixels, never a public URL). The bytes sit in their
own table so a building list never carries them. Existing rows keep NULL: a building without a logo draws a
lettered sign with its real name. Nothing is backfilled; the previous release never reads the new columns or the
new table, so a rollback is safe. The new table follows 0097: forced workspace RLS, composite scope foreign key
(with delete cascade, so removing an empty building removes its logo) and the tenant purge policy.
"""
import sqlalchemy as sa
from alembic import op

revision = "0117_building_logo_signs"
down_revision = "0116_room_dept_executive"
branch_labels = None
depends_on = None

TABLE = "office_building_logos"
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
    op.add_column("office_buildings", sa.Column("sign_style", sa.String(12), nullable=True))
    op.add_column("office_buildings", sa.Column("brand_color", sa.String(7), nullable=True))
    op.add_column("office_buildings", sa.Column("logo_sha256", sa.String(64), nullable=True))
    op.create_check_constraint("ck_office_building_sign_style", "office_buildings",
                               "sign_style is null or sign_style in ('plain','plaque','illuminated','banner')")
    op.create_check_constraint("ck_office_building_brand_color", "office_buildings",
                               "brand_color is null or brand_color ~ '^#[0-9a-f]{6}$'")
    op.create_check_constraint("ck_office_building_logo_sha", "office_buildings",
                               "logo_sha256 is null or logo_sha256 ~ '^[0-9a-f]{64}$'")
    op.create_table(
        TABLE,
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("building_id", sa.String(80), primary_key=True),
        sa.Column("png", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "building_id"],
            ["office_buildings.tenant_id", "office_buildings.workspace_id", "office_buildings.id"],
            name="fk_office_building_logos_building", ondelete="CASCADE",
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_office_building_logos_sha"),
        sa.CheckConstraint("width between 1 and 512 and height between 1 and 512", name="ck_office_building_logos_size"),
        sa.CheckConstraint("byte_size between 1 and 1048576", name="ck_office_building_logos_bytes"),
    )
    _rls(TABLE)


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute(f"drop policy if exists lifecycle_purge_{TABLE} on {TABLE}")
    op.drop_table(TABLE)
    op.drop_constraint("ck_office_building_logo_sha", "office_buildings", type_="check")
    op.drop_constraint("ck_office_building_brand_color", "office_buildings", type_="check")
    op.drop_constraint("ck_office_building_sign_style", "office_buildings", type_="check")
    op.drop_column("office_buildings", "logo_sha256")
    op.drop_column("office_buildings", "brand_color")
    op.drop_column("office_buildings", "sign_style")
