"""Ed25519 signing of skill versions (docs/skills.md#signing).

* ``skill_publisher_keys``: the tenant's trusted publisher keys, managed by organization
  administrators. Public keys only (raw 32-byte Ed25519, base64) with their fingerprint,
  status (``active``, ``rotated``, ``revoked``), the key that replaced a rotated one and who
  revoked a key and why. Tenant-level, under forced RLS on ``anum.tenant_id``
  (``tenant_isolation_skill_publisher_keys``, the 0008 pattern).
* ``skill_versions`` gains the signature (``signature_key_id``, ``signature``) and when and
  by whom it was attached (``signed_at``, ``signed_by``). Nullable: a version may be
  published unsigned and signed later. Verification is computed on every read, never stored.

Expand only: one new table and nullable columns. The previous release ignores both.

Numbered 0082 and chained after ``0074_brevo_campaigns``, the head of ``main`` when written;
renumber at merge if another migration lands first.
"""

import sqlalchemy as sa
from alembic import op

revision = "0082_skill_signing"
down_revision = "0074_brevo_campaigns"
branch_labels = None
depends_on = None

TABLES = ("skill_publisher_keys",)
TS = sa.DateTime(timezone=True)
PREDICATE = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"
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
    op.add_column("skill_versions", sa.Column("signature_key_id", sa.String(80), nullable=True))
    op.add_column("skill_versions", sa.Column("signature", sa.String(200), nullable=True))
    op.add_column("skill_versions", sa.Column("signed_at", TS, nullable=True))
    op.add_column("skill_versions", sa.Column("signed_by", sa.String(160), nullable=True))
    op.create_check_constraint(
        "ck_skill_versions_signature_pair", "skill_versions",
        "(signature_key_id is null) = (signature is null)",
    )
    for table in TABLES:
        _enable_rls(table)


def downgrade() -> None:
    op.drop_constraint("ck_skill_versions_signature_pair", "skill_versions", type_="check")
    for column in ("signed_by", "signed_at", "signature", "signature_key_id"):
        op.drop_column("skill_versions", column)
    op.drop_table("skill_publisher_keys")
