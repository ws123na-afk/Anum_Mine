"""Approval routing by amount and the contact suppression list.

- ``approval_rules`` gains ``conditions`` (an AED amount band, a share of the order value,
  a disputed flag), ``approver_steps`` (one or two steps of approvers: owner, org_admin,
  a workspace role or named members) and ``updated_at`` / ``updated_by`` for edits
  (docs/approvals-and-risk.md#approval-routing). Existing rows get ``{}`` and ``[]`` and
  keep their behaviour. The table keeps its forced tenant RLS.

- ``contact_suppressions``: the tenant's opt-out list (docs/privacy.md#contact-suppression).
  The contact is stored only as a keyed SHA-256 hash and a masked form. One row per
  (tenant, contact hash, channel). Tenant-level: forced RLS on ``anum.tenant_id``. Every
  change is also an ``audit_records`` row.

No role is granted anything new and nothing bypasses RLS. Expand only: new nullable or
defaulted columns and a new table the previous release never reads, so ``helm rollback``
keeps working against this schema.

Chained after ``0053_tyre_supabase_auth``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0054_routing_suppression"
down_revision = "0053_tyre_supabase_auth"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"


def upgrade() -> None:
    op.add_column(
        "approval_rules",
        sa.Column("conditions", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "approval_rules",
        sa.Column("approver_steps", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("approval_rules", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("approval_rules", sa.Column("updated_by", sa.String(160), nullable=True))

    op.create_table(
        "contact_suppressions",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("recipient_hash", sa.String(64), nullable=False),
        sa.Column("masked", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_contact_suppressions"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_contact_suppressions_tenant"),
        sa.UniqueConstraint("tenant_id", "recipient_hash", "channel", name="uq_contact_suppressions_contact"),
        sa.CheckConstraint("kind in ('phone', 'email')", name="ck_contact_suppressions_kind"),
        sa.CheckConstraint("channel in ('email', 'whatsapp', 'sms', 'all')", name="ck_contact_suppressions_channel"),
        sa.CheckConstraint("purpose in ('marketing', 'all')", name="ck_contact_suppressions_purpose"),
    )
    op.execute("alter table contact_suppressions enable row level security")
    op.execute("alter table contact_suppressions force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_contact_suppressions on contact_suppressions
        using ({TENANT})
        with check ({TENANT})
        """
    )


def downgrade() -> None:
    op.drop_table("contact_suppressions")
    op.drop_column("approval_rules", "updated_by")
    op.drop_column("approval_rules", "updated_at")
    op.drop_column("approval_rules", "approver_steps")
    op.drop_column("approval_rules", "conditions")
