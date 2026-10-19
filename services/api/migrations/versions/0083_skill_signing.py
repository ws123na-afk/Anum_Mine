"""Ed25519 signing of skill versions and integration packages (docs/skills.md#signing).

* ``skill_publisher_keys``: the tenant's trusted publisher keys, managed by organization
  administrators. Public keys only (raw 32-byte Ed25519, base64) with their fingerprint,
  status (``active``, ``rotated``, ``revoked``), the key that replaced a rotated one and who
  revoked a key and why.
* ``skill_signing_settings``: one row per tenant; ``disable_flagged`` (on when the row is
  missing) turns off every installation a revoked key signed, in every workspace.
* Both are tenant-level under forced RLS on ``anum.tenant_id`` (``tenant_isolation_<table>``,
  the 0008 pattern).
* ``skill_versions`` and ``marketplace_packages`` gain the signature (``signature_key_id``,
  ``signature``) and when and by whom it was attached (``signed_at``, ``signed_by``).
  Nullable: a version or package may be published unsigned and signed later. Verification is
  computed on every read, never stored.
* ``skill_installations`` and ``marketplace_installs`` gain ``disabled_reason``: why the API
  turned an installation off.
* Organisation deletion (migration ``0079_tenant_lifecycle``): both new tables get the
  ``anum_tenant_purger`` grants and purge policy (``lifecycle_purge_<table>``), copied from
  0079's ``_purge_policy`` for organisation-wide tables.
* Revocation finds the installations to turn off in the tenant's other workspaces as
  ``anum_maintenance``: a column grant of scope, id, version and the enabled flag only (never
  approved tools or anything else) and a select policy for that role. The change itself runs
  as the application role inside each workspace's RLS context (``anum_api/maintenance.py``).

Expand only: new tables, nullable columns, two column grants and two policies. The previous
release ignores them.

Numbered 0083 and chained after ``0079_tenant_lifecycle``, the head of ``main`` when written;
renumber at merge if another migration lands first.
"""

import sqlalchemy as sa
from alembic import op

revision = "0083_skill_signing"
down_revision = "0079_tenant_lifecycle"
branch_labels = None
depends_on = None

TABLES = ("skill_publisher_keys", "skill_signing_settings")
SIGNED = ("skill_versions", "marketplace_packages")
INSTALLS = ("skill_installations", "marketplace_installs")
TS = sa.DateTime(timezone=True)
PREDICATE = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"
KEY_STATUSES = ("active", "rotated", "revoked")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _enable_rls(table: str) -> None:
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


def _purge_policy(table: str) -> None:
    """Organisation deletion may remove these rows (0079's ``_purge_policy``, organisation wide)."""
    op.execute(f"grant select (tenant_id, ctid), delete on {table} to {PURGER}")
    op.execute(
        f"""
        create policy lifecycle_purge_{table} on {table}
        for all to {PURGER}
        using (
          {PREDICATE}
          and exists (
            select 1 from {JOBS} j
            where j.tenant_id = {table}.tenant_id and j.status = 'purging' and j.kind = 'organisation_deletion'
          )
        )
        with check (false)
        """  # nosec B608 - fixed table names
    )


def upgrade() -> None:
    op.create_table(
        "skill_publisher_keys",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("publisher", sa.String(160), nullable=False),
        sa.Column("public_key", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("rotated_at", TS, nullable=True),
        sa.Column("replaced_by", sa.String(80), nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("revoked_by", sa.String(160), nullable=True),
        sa.Column("revoke_reason", sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_skill_publisher_keys_tenant"),
        sa.CheckConstraint(_in("status", KEY_STATUSES), name="ck_skill_publisher_keys_status"),
        sa.CheckConstraint("length(public_key) = 44", name="ck_skill_publisher_keys_public_key"),
        sa.CheckConstraint(
            "(status = 'revoked') = (revoked_at is not null)", name="ck_skill_publisher_keys_revoked"
        ),
    )
    op.create_table(
        "skill_signing_settings",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("disable_flagged", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_skill_signing_settings_tenant"),
    )
    for table in SIGNED:
        op.add_column(table, sa.Column("signature_key_id", sa.String(80), nullable=True))
        op.add_column(table, sa.Column("signature", sa.String(200), nullable=True))
        op.add_column(table, sa.Column("signed_at", TS, nullable=True))
        op.add_column(table, sa.Column("signed_by", sa.String(160), nullable=True))
        op.create_check_constraint(
            f"ck_{table}_signature_pair", table, "(signature_key_id is null) = (signature is null)",
        )
    for table in INSTALLS:
        op.add_column(table, sa.Column("disabled_reason", sa.String(300), nullable=True))
    for table in TABLES:
        _enable_rls(table)
        _purge_policy(table)
    # Revocation discovers enabled installations in the tenant's other workspaces.
    op.execute(
        "grant select (tenant_id, workspace_id, skill_id, version, enabled) on skill_installations to anum_maintenance"
    )
    op.execute(
        """
        create policy maintenance_skill_installations on skill_installations
        for select to anum_maintenance
        using (enabled)
        """
    )
    op.execute(
        "grant select (tenant_id, workspace_id, package_id, enabled) on marketplace_installs to anum_maintenance"
    )
    op.execute(
        """
        create policy maintenance_marketplace_installs on marketplace_installs
        for select to anum_maintenance
        using (enabled)
        """
    )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
    op.execute("drop policy if exists maintenance_marketplace_installs on marketplace_installs")
    op.execute("drop policy if exists maintenance_skill_installations on skill_installations")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on marketplace_installs from anum_maintenance;
            revoke all on skill_installations from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in INSTALLS:
        op.drop_column(table, "disabled_reason")
    for table in SIGNED:
        op.drop_constraint(f"ck_{table}_signature_pair", table, type_="check")
        for column in ("signed_by", "signed_at", "signature", "signature_key_id"):
            op.drop_column(table, column)
    op.drop_table("skill_signing_settings")
    op.drop_table("skill_publisher_keys")
