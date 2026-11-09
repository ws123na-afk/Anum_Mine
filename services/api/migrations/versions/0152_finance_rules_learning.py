"""Finance rules and learning: versioned rules, merchant aliases, subcategories, suggestion decisions, rule batches
and automation settings (expand only).

docs/finance.md#rules-and-learning, #suggestions and #automation-policies; the November 2026 audit
(docs/audits/2026-11-09-finance-accounting-audit.md), classification, learning and automations.

* ``finance_rules``: one row per version of one of the owner's rules (``rule_id`` plus ``version``, unique): what it
  matches and what it gives as JSON (``content``), ``proposed``, ``approved`` or ``rejected``, its provenance
  (``owner``, ``learned``, ``imported``, ``agent``), the correction it was learned from, the payload hash the owner
  approved and who decided when. Versions are never overwritten; reverting adds a version.
* ``finance_merchant_aliases``: an owner's spelling of a merchant and the merchant it names (``alias_key``, the folded
  spelling, unique per owner).
* ``finance_subcategories``: the owner's subcategories under a built-in category (unique name per parent).
* ``finance_suggestion_decisions``: one decision per suggestion key per owner (unique), so of two accepts one wins and
  a rejected suggestion stays dismissed.
* ``finance_rule_batches``: each retroactive apply with the before and after values of every row it changed (JSON),
  so it can be undone as one.
* ``finance_automation_settings``: the owner's automation policy (JSON, off by default) and the day the last
  unresolved-suggestions notice was claimed for. One row per owner.

``finance_transactions`` gains a nullable ``subcategory_id``. The previous release ignores it and every new table.

Forced RLS like ``0147`` and ``0150`` on each new table: ``tenant_isolation_<table>``, a restrictive
``owner_only_<table>`` policy on ``owner_user_id = anum.user_id`` (also admitting the tenant purge role, which can
only delete) and ``lifecycle_purge_<table>``. No grant to a maintenance role: the daily job finds people through
``advisor_jobs.discover_people`` and acts as the application role in each person's scope.

Rollback: ``alembic downgrade 0151_business_ledger`` drops the tables and the column (only for an empty test
workspace; production restores a backup). Chained after ``0151_business_ledger``; a parallel branch may also add a
``0152``, renumbered at merge.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0152_finance_rules_learning"
down_revision = "0151_business_ledger"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
TABLES = (
    "finance_rules", "finance_merchant_aliases", "finance_subcategories", "finance_suggestion_decisions", "finance_rule_batches",
    "finance_automation_settings",
)
CATEGORIES = (
    "income", "cashback", "refunds", "transfer", "groceries", "dining", "food", "room_rent", "transport", "fuel", "utilities", "telecom", "housing",
    "shopping", "health", "education", "entertainment", "subscriptions", "travel", "insurance", "fees", "interest", "cash", "other",
)


def _owned() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _workspace(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "finance_rules", *_owned(),
        sa.Column("rule_id", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", sa.String(10), nullable=False),
        sa.Column("source_transaction_id", sa.String(80), nullable=True),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("decided_by", sa.String(160), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("decided_at", TS, nullable=True),
        sa.Column("reverted_from", sa.Integer(), nullable=True),
        _workspace("finance_rules"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "rule_id", "version", name="uq_finance_rules_version"),
        sa.CheckConstraint("version >= 1", name="ck_finance_rules_version"),
        sa.CheckConstraint(_in("status", ("proposed", "approved", "rejected")), name="ck_finance_rules_status"),
        sa.CheckConstraint(_in("provenance", ("owner", "learned", "imported", "agent")), name="ck_finance_rules_provenance"),
        sa.CheckConstraint("confidence >= 0 and confidence <= 1", name="ck_finance_rules_confidence"),
        sa.CheckConstraint("jsonb_typeof(content) = 'object'", name="ck_finance_rules_content"),
    )
    op.create_index("ix_finance_rules_owner", "finance_rules", ["tenant_id", "workspace_id", "owner_user_id", "rule_id"])
    op.create_table(
        "finance_merchant_aliases", *_owned(),
        sa.Column("alias", sa.String(80), nullable=False),
        sa.Column("alias_key", sa.String(160), nullable=False),
        sa.Column("merchant", sa.String(80), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        _workspace("finance_merchant_aliases"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "alias_key", name="uq_finance_merchant_aliases_key"),
    )
    op.create_table(
        "finance_subcategories", *_owned(),
        sa.Column("parent", sa.String(30), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("name_key", sa.String(120), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        _workspace("finance_subcategories"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "parent", "name_key", name="uq_finance_subcategories_name"),
        sa.CheckConstraint(_in("parent", CATEGORIES), name="ck_finance_subcategories_parent"),
    )
    op.create_table(
        "finance_suggestion_decisions", *_owned(),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("producer", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("decided_by", sa.String(160), nullable=False),
        sa.Column("decided_at", TS, nullable=False),
        sa.Column("batch_id", sa.String(80), nullable=True),
        _workspace("finance_suggestion_decisions"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "key", name="uq_finance_suggestion_decisions_key"),
        sa.CheckConstraint(_in("status", ("accepted", "rejected", "auto_applied")), name="ck_finance_suggestion_decisions_status"),
    )
    op.create_table(
        "finance_rule_batches", *_owned(),
        sa.Column("origin", sa.String(10), nullable=False),
        sa.Column("rule_id", sa.String(80), nullable=True),
        sa.Column("rule_version", sa.Integer(), nullable=True),
        sa.Column("suggestion_key", sa.String(200), nullable=True),
        sa.Column("changes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("undone_by", sa.String(160), nullable=True),
        sa.Column("undone_at", TS, nullable=True),
        _workspace("finance_rule_batches"),
        sa.CheckConstraint(_in("origin", ("owner", "policy")), name="ck_finance_rule_batches_origin"),
        sa.CheckConstraint(_in("status", ("applied", "undone")), name="ck_finance_rule_batches_status"),
        sa.CheckConstraint("jsonb_typeof(changes) = 'array'", name="ck_finance_rule_batches_changes"),
    )
    op.create_index("ix_finance_rule_batches_owner", "finance_rule_batches", ["tenant_id", "workspace_id", "owner_user_id", "created_at"])
    op.create_table(
        "finance_automation_settings", *_owned(),
        sa.Column("policy", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("notified_on", sa.String(10), nullable=True),
        sa.Column("updated_at", TS, nullable=True),
        sa.Column("updated_by", sa.String(160), nullable=True),
        _workspace("finance_automation_settings"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", name="uq_finance_automation_settings_owner"),
        sa.CheckConstraint("jsonb_typeof(policy) = 'object'", name="ck_finance_automation_settings_policy"),
    )
    for table in TABLES:
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
        op.execute(f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})")
        op.execute(f"create policy owner_only_{table} on {table} as restrictive "
                   f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name)) with check ({OWNER_PREDICATE})")
        op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to anum_tenant_purger")
        op.execute(f"create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger "
                   "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
                   f"and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = {table}.tenant_id and j.status = 'purging' "
                   f"and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
                   "with check (false)")
    op.add_column("finance_transactions", sa.Column("subcategory_id", sa.String(80), nullable=True))


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.drop_column("finance_transactions", "subcategory_id")
    for table in reversed(TABLES):
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.drop_table(table)
