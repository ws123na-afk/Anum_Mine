"""Business refunds started from ANUM, provider refunds ANUM could not match, and dismissed receipt matches (expand only).

docs/business.md#refunds-from-anum, docs/business.md#refunds-anum-could-not-match, docs/documents.md#receipt-matches.

Three workspace tables under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id`` (``tenant_isolation_<table>``,
the pattern of ``0177_business_refunds_otp``), each with the ``anum_tenant_purger`` grants and purge policy of
``0079_tenant_lifecycle`` and listed in ``anum_api/tenant_lifecycle_tables.py``:

* ``business_refund_requests``: a refund of a Business invoice's card payment asked for from ANUM. It waits for an
  owner's approval (``approval_id``, the ``payload_hash`` they are shown); the approved call refunds through Moyasar or
  Stripe with the request's own key and is recorded as a ``business_invoice_refunds`` row (``refund_id``). One request
  per form submit (unique idempotency key per workspace); its amount is held while it is open.
* ``business_refund_reviews``: a refund the provider's verified webhook reported for a card payment ANUM never
  recorded, kept once per provider refund for an owner to check (``open`` or ``checked`` with a note).
* ``vault_match_dismissals``: a receipt match the person said is wrong (a payment or a purchase), remembered per
  document so it is not proposed again until someone restores it. Removed with its document.

Chained after ``0177_business_refunds_otp``. Expand only: the previous release never reads or writes the new tables, so
it keeps working. Rollback never reverses it in production.
"""

import sqlalchemy as sa
from alembic import op

revision = "0178_refund_requests_vault"
down_revision = "0177_business_refunds_otp"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("business_refund_requests", "business_refund_reviews", "vault_match_dismissals")
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
    """Deletion of the organisation or of one workspace may remove these rows (0177's ``_purge_policy``)."""
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
        "business_refund_requests",
        *_base("business_refund_requests"),
        sa.Column("invoice_id", sa.String(80), nullable=False),
        sa.Column("payment_id", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("payload_hash", sa.String(128), nullable=True),
        sa.Column("provider_refund_id", sa.String(200), nullable=True),
        sa.Column("refund_id", sa.String(80), nullable=True),
        sa.Column("detail", sa.String(500), nullable=False, server_default=""),
        sa.Column("requested_by", sa.String(160), nullable=False),
        sa.Column("decided_by", sa.String(160), nullable=True),
        sa.Column("decided_at", TS, nullable=True),
        _ref("business_refund_requests", "invoice_id", "business_invoices", cascade=True),
        # A payment with a refund request is kept: the request is part of its history.
        _ref("business_refund_requests", "payment_id", "business_invoice_payments", cascade=False),
        sa.CheckConstraint(_in("provider", ("moyasar", "stripe")), name="ck_business_refund_requests_provider"),
        sa.CheckConstraint(
            _in("status", ("waiting_approval", "processing", "pending_provider", "succeeded", "failed", "rejected", "cancelled")),
            name="ck_business_refund_requests_status",
        ),
        sa.CheckConstraint("amount > 0", name="ck_business_refund_requests_amount"),
        # One request per form submit.
        sa.UniqueConstraint("tenant_id", "workspace_id", "idempotency_key", name="uq_business_refund_requests_key"),
    )
    op.create_index("ix_business_refund_requests_invoice", "business_refund_requests", ["tenant_id", "workspace_id", "invoice_id"])
    op.create_index("ix_business_refund_requests_payment", "business_refund_requests", ["tenant_id", "workspace_id", "payment_id", "status"])
    op.create_index("ix_business_refund_requests_provider", "business_refund_requests", ["tenant_id", "workspace_id", "provider", "provider_refund_id"])

    op.create_table(
        "business_refund_reviews",
        *_base("business_refund_reviews"),
        sa.Column("invoice_id", sa.String(80), nullable=True),
        sa.Column("invoice_number", sa.String(40), nullable=False, server_default=""),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_refund_id", sa.String(200), nullable=False),
        sa.Column("provider_payment_id", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("note", sa.String(300), nullable=False, server_default=""),
        sa.Column("checked_by", sa.String(160), nullable=True),
        sa.Column("checked_at", TS, nullable=True),
        _ref("business_refund_reviews", "invoice_id", "business_invoices", cascade=True),
        sa.CheckConstraint(_in("provider", ("moyasar", "stripe")), name="ck_business_refund_reviews_provider"),
        sa.CheckConstraint(_in("status", ("open", "checked")), name="ck_business_refund_reviews_status"),
        sa.CheckConstraint("amount > 0", name="ck_business_refund_reviews_amount"),
        # A provider refund is kept once, whatever event or delivery carries it.
        sa.UniqueConstraint("tenant_id", "workspace_id", "provider", "provider_refund_id", name="uq_business_refund_reviews_provider"),
    )
    op.create_index("ix_business_refund_reviews_status", "business_refund_reviews", ["tenant_id", "workspace_id", "status", "created_at"])

    op.create_table(
        "vault_match_dismissals",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("document_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("target_id", sa.String(160), nullable=False),
        sa.Column("label", sa.String(200), nullable=False, server_default=""),
        sa.Column("dismissed_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_vault_match_dismissals"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "document_id", "kind", "target_id", name="uq_vault_match_dismissals_target"),
        sa.CheckConstraint(_in("kind", ("payment", "purchase")), name="ck_vault_match_dismissals_kind"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "document_id"],
            ["vault_documents.tenant_id", "vault_documents.workspace_id", "vault_documents.id"],
            name="fk_vault_match_dismissals_document", ondelete="CASCADE",
        ),
    )

    for table in TABLES:
        _enable_rls(table)
        _purge_policy(table)


def downgrade() -> None:
    # Only for an empty test database: production rolls back by restoring a backup.
    for table in TABLES:
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.execute(f"drop policy if exists tenant_isolation_{table} on {table}")
    op.drop_table("vault_match_dismissals")
    op.drop_table("business_refund_reviews")
    op.drop_table("business_refund_requests")
