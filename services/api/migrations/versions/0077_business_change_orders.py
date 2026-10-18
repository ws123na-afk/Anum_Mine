"""Business: change orders and the client usage and cost ledger (docs/business.md).

Two workspace tables under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id``
(``tenant_isolation_<table>``, the 0040 pattern, as in ``0070_business``):

* ``business_change_orders``: a change to an accepted proposal and its project: the
  request, the scope delta as line items, the timeline impact in days, the internal cost
  delta, the status (draft, pending approval, approved, rejected, withdrawn), the version
  and hash it amends, its own content hash (the owner approves that exact hash), the
  computed price deltas and the proposal version an approval produced. Numbers are unique
  per project.
* ``business_usage_ledger``: labour hours and provider costs (AI usage from the model
  budget, hosting share, messaging, other) per client account and optionally per project,
  billable or not. A partial unique index keeps one imported row per account, project and
  external reference (an AI usage month is imported once).

One nullable column on ``business_proposal_versions``: ``change_order_id`` names the
approved change order that produced a version. The locked-version trigger from 0070 still
refuses any update of a locked version; a new version is a new row.

References carry the scope (``(tenant_id, workspace_id, <id>)`` composite foreign keys),
so a row can never point into another tenant or workspace. No role is granted anything.

Expand only: two new tables, one nullable column and indexes. The previous release
ignores them.

Numbered 0077 and chained after ``0074_brevo_campaigns``; sibling branches use 0075 and
0076, and the chain is renumbered at merge.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0077_business_change_orders"
down_revision = "0074_brevo_campaigns"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("business_change_orders", "business_usage_ledger")
TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(14, 2)
CURRENCIES = ("SAR", "AED", "USD")


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


def upgrade() -> None:
    op.add_column("business_proposal_versions", sa.Column("change_order_id", sa.String(80), nullable=True))

    op.create_table(
        "business_change_orders",
        *_base("business_change_orders"),
        sa.Column("project_id", sa.String(80), nullable=False),
        sa.Column("proposal_id", sa.String(80), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("request", sa.Text(), nullable=False, server_default=""),
        sa.Column("requested_by", sa.String(160), nullable=False, server_default=""),
        sa.Column("lines", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("timeline_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("internal_cost_delta", MONEY, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("base_hash", sa.String(64), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("subtotal_delta", MONEY, nullable=True),
        sa.Column("tax_delta", MONEY, nullable=True),
        sa.Column("total_delta", MONEY, nullable=True),
        sa.Column("result_version", sa.Integer(), nullable=True),
        sa.Column("submitted_by", sa.String(160), nullable=True),
        sa.Column("submitted_at", TS, nullable=True),
        sa.Column("decided_by", sa.String(160), nullable=True),
        sa.Column("decided_at", TS, nullable=True),
        sa.Column("decision_note", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_change_orders", "project_id", "business_projects", cascade=False),
        _ref("business_change_orders", "proposal_id", "business_proposals", cascade=False),
        _ref("business_change_orders", "account_id", "business_accounts", cascade=False),
        sa.UniqueConstraint("tenant_id", "workspace_id", "project_id", "number", name="uq_business_change_orders_number"),
        sa.CheckConstraint(_in("status", ("draft", "pending_approval", "approved", "rejected", "withdrawn")), name="ck_business_change_orders_status"),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_business_change_orders_currency"),
        sa.CheckConstraint("timeline_days between -3650 and 3650", name="ck_business_change_orders_timeline"),
        # Sent and decided change orders name the version and hash they were checked against.
        sa.CheckConstraint(
            "status in ('draft', 'withdrawn') or (base_version is not null and base_hash is not null and content_hash is not null)",
            name="ck_business_change_orders_bound",
        ),
        # An approved change order names who approved it, when, and the version it produced.
        sa.CheckConstraint(
            "status <> 'approved' or (decided_by is not null and decided_at is not null and result_version is not null)",
            name="ck_business_change_orders_approved",
        ),
    )
    op.create_index("ix_business_change_orders_project", "business_change_orders", ["tenant_id", "workspace_id", "project_id", "number"])
    op.create_index("ix_business_change_orders_scope_updated_at", "business_change_orders", ["tenant_id", "workspace_id", "updated_at", "id"])

    op.create_table(
        "business_usage_ledger",
        *_base("business_usage_ledger"),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("project_id", sa.String(80), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 2), nullable=False, server_default="1"),
        sa.Column("unit_cost", MONEY, nullable=False, server_default="0"),
        sa.Column("amount", MONEY, nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("billable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("external_ref", sa.String(160), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_usage_ledger", "account_id", "business_accounts", cascade=False),
        _ref("business_usage_ledger", "project_id", "business_projects", cascade=False),
        sa.CheckConstraint(_in("kind", ("labour", "ai_usage", "hosting", "messaging", "other")), name="ck_business_usage_ledger_kind"),
        sa.CheckConstraint(_in("source", ("manual", "model_budget")), name="ck_business_usage_ledger_source"),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_business_usage_ledger_currency"),
        sa.CheckConstraint("quantity > 0 and unit_cost >= 0 and amount >= 0", name="ck_business_usage_ledger_amounts"),
    )
    op.create_index("ix_business_usage_ledger_project", "business_usage_ledger", ["tenant_id", "workspace_id", "project_id", "occurred_on"])
    op.create_index("ix_business_usage_ledger_account", "business_usage_ledger", ["tenant_id", "workspace_id", "account_id", "occurred_on"])
    op.create_index("ix_business_usage_ledger_scope_updated_at", "business_usage_ledger", ["tenant_id", "workspace_id", "updated_at", "id"])
    op.create_index(
        "uq_business_usage_ledger_external", "business_usage_ledger",
        ["tenant_id", "workspace_id", "account_id", sa.text("coalesce(project_id, '')"), "external_ref"],
        unique=True, postgresql_where=sa.text("external_ref is not null"),
    )

    for table in TABLES:
        _enable_rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_column("business_proposal_versions", "change_order_id")
