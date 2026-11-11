"""Business: refunds synced from the card provider and the portal's emailed code sign-in (expand only).

docs/business.md#refunds-made-in-the-provider-dashboard, docs/business-portal.md#signing-in-with-an-emailed-code.

Two workspace tables under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id`` (``tenant_isolation_<table>``,
the pattern of ``0176_files_audit_probe``), each with the ``anum_tenant_purger`` grants and purge policy of
``0079_tenant_lifecycle`` and listed in ``anum_api/tenant_lifecycle_tables.py``:

* ``business_invoice_refunds``: a refund the owner made in the Stripe or Moyasar dashboard on a card payment a Business
  invoice recorded, learnt from the provider's verified webhook. One row per provider refund (unique per workspace,
  provider and refund id), linked to the invoice and to the payment it gives back. ``amount`` is what it took off the
  invoice's amount paid (a refund of money the card took above the balance takes nothing off), ``provider_amount``
  what the provider gave back.
* ``business_portal_sign_ins``: one row per emailed code a customer asked for on the portal sign-in page. Only hashes
  are kept (the address, the IP address, the code and, once used, the session), with the attempts, the expiry and the
  portal access it signs in to (none when the address has no access: the row still counts towards the limits, so the
  answer never says whether an address exists).

One nullable column on ``business_invoice_payments`` (``provider_amount``: what the card was charged, which can be more
than the payment applied to the balance) and one defaulted column on ``business_settings`` (``portal_code_sign_in``,
the owner's switch, off).

Chained after ``0176_files_audit_probe``. Expand only: the previous release never reads or writes the new tables and
columns (nullable or defaulted), so it keeps working. Rollback never reverses it in production.
"""

import sqlalchemy as sa
from alembic import op

revision = "0177_business_refunds_otp"
down_revision = "0176_files_audit_probe"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("business_invoice_refunds", "business_portal_sign_ins")
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"
TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(14, 2)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _base(table: str) -> list:  # type: ignore[type-arg]
    return [
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name=f"uq_{table}_scope_id"),
    ]


def _ref(table: str, column: str, target: str, *, cascade: bool) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id", column],
        [f"{target}.tenant_id", f"{target}.workspace_id", f"{target}.id"],
        name=f"fk_{table}_{column}",
        ondelete="CASCADE" if cascade else "RESTRICT",
    )


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
    """Deletion of the organisation or of one workspace may remove these rows (0175's ``_purge_policy``)."""
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to {PURGER}")
    op.execute(
        f"""
        create policy lifecycle_purge_{table} on {table}
        for all to {PURGER}
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and exists (
            select 1 from {JOBS} j
            where j.tenant_id = {table}.tenant_id and j.status = 'purging'
              and (j.kind = 'organisation_deletion'
                   or (j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id))
          )
        )
        with check (false)
        """  # nosec B608 - fixed table names
    )


def upgrade() -> None:
    op.create_table(
        "business_invoice_refunds",
        *_base("business_invoice_refunds"),
        sa.Column("invoice_id", sa.String(80), nullable=False),
        sa.Column("payment_id", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_refund_id", sa.String(200), nullable=False),
        sa.Column("provider_payment_id", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("provider_amount", MONEY, nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("refunded_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("note", sa.String(300), nullable=False, server_default=""),
        sa.Column("recorded_by", sa.String(160), nullable=False),
        sa.Column("reversed_at", TS, nullable=True),
        _ref("business_invoice_refunds", "invoice_id", "business_invoices", cascade=True),
        # A payment with refunds is never removed: the refunds are its history.
        _ref("business_invoice_refunds", "payment_id", "business_invoice_payments", cascade=False),
        sa.CheckConstraint(_in("provider", ("moyasar", "stripe")), name="ck_business_invoice_refunds_provider"),
        sa.CheckConstraint(_in("status", ("recorded", "reversed")), name="ck_business_invoice_refunds_status"),
        sa.CheckConstraint("provider_amount > 0 and amount >= 0 and amount <= provider_amount", name="ck_business_invoice_refunds_amount"),
        # A provider refund is recorded once, whatever event or delivery carries it.
        sa.UniqueConstraint("tenant_id", "workspace_id", "provider", "provider_refund_id", name="uq_business_invoice_refunds_provider"),
    )
    op.create_index("ix_business_invoice_refunds_invoice", "business_invoice_refunds", ["tenant_id", "workspace_id", "invoice_id"])
    op.create_index("ix_business_invoice_refunds_payment", "business_invoice_refunds", ["tenant_id", "workspace_id", "payment_id"])

    op.create_table(
        "business_portal_sign_ins",
        *_base("business_portal_sign_ins"),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=False),
        sa.Column("access_id", sa.String(80), nullable=True),
        sa.Column("code_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS, nullable=True),
        sa.Column("session_hash", sa.String(64), nullable=True),
        sa.Column("session_expires_at", TS, nullable=True),
        sa.Column("ended_at", TS, nullable=True),
        _ref("business_portal_sign_ins", "access_id", "business_portal_access", cascade=True),
        sa.CheckConstraint(_in("status", ("pending", "unmatched", "signed_in", "failed", "ended")), name="ck_business_portal_sign_ins_status"),
        sa.CheckConstraint("attempts between 0 and 20", name="ck_business_portal_sign_ins_attempts"),
        sa.CheckConstraint("status <> 'signed_in' or (session_hash is not null and session_expires_at is not null)",
                           name="ck_business_portal_sign_ins_session"),
    )
    op.create_index("ix_business_portal_sign_ins_email", "business_portal_sign_ins", ["tenant_id", "workspace_id", "email_hash", "created_at"])
    op.create_index("ix_business_portal_sign_ins_ip", "business_portal_sign_ins", ["tenant_id", "workspace_id", "ip_hash", "created_at"])

    op.add_column("business_invoice_payments", sa.Column("provider_amount", MONEY, nullable=True))
    op.add_column("business_settings", sa.Column("portal_code_sign_in", sa.Boolean(), nullable=False, server_default=sa.false()))

    for table in TABLES:
        _enable_rls(table)
        _purge_policy(table)


def downgrade() -> None:
    # Only for an empty test database: production rolls back by restoring a backup.
    op.drop_column("business_settings", "portal_code_sign_in")
    op.drop_column("business_invoice_payments", "provider_amount")
    for table in TABLES:
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.execute(f"drop policy if exists tenant_isolation_{table} on {table}")
    op.drop_table("business_portal_sign_ins")
    op.drop_table("business_invoice_refunds")
