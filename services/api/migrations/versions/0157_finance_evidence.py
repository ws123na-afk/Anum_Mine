"""Business evidence links, the evidence threshold and exception notice claims (expand only).

docs/business-books.md#supporting-documents-and-evidence, docs/finance.md#exception-notices.

* ``vault_document_links.target_type`` also takes ``business_bill``, ``business_invoice``, ``business_adjustment``
  and ``business_journal``: a vault document of a business workspace attached as evidence to a bill, an invoice, a
  journal adjustment or a manual journal entry of that business. The check constraint is replaced by a wider one
  (every existing row still passes); nothing else in the table changes.
* ``business_book_settings.evidence_threshold`` (nullable): bills and adjustments at or above it, in the base
  currency, show "evidence missing" until a document is attached. Null means the flag is off.
* ``finance_notice_claims``: one row per person, notice kind, item and local day, claimed before an exception
  notice is sent and removed again when sending fails, so each item is noticed at most once a day. Forced RLS like
  ``0153``: ``tenant_isolation_<table>``, a restrictive ``owner_only_<table>`` policy (also admitting the tenant
  purge role, which can only delete) and ``lifecycle_purge_<table>``. No grant to a maintenance role.

Chained after ``0156_finance_reserve``.

Expand only. The previous release ignores the new column and table; it would fail to load a vault link of a new
type, so roll back only before a business document is attached (or delete those links first). Rollback:
``alembic downgrade 0156_finance_reserve`` restores the narrower check (after deleting rows of the new
types), drops the column and the table (only for an empty test workspace; production restores a backup).
"""

import sqlalchemy as sa
from alembic import op

revision = "0157_finance_evidence"
down_revision = "0156_finance_reserve"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
TABLE = "finance_notice_claims"
LINK_TYPES = ("vehicle", "person", "business", "purchase", "finance_transaction",
              "business_bill", "business_invoice", "business_adjustment", "business_journal")
OLD_LINK_TYPES = LINK_TYPES[:5]
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"


def _in(values: tuple[str, ...]) -> str:
    return "target_type in (" + ", ".join(f"'{v}'" for v in values) + ")"


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"create policy owner_only_{table} on {table} as restrictive "
               f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name)) with check ({OWNER_PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to anum_tenant_purger")


def upgrade() -> None:
    # A wider check: every row the old one admitted still passes, so validation cannot fail.
    op.execute("alter table vault_document_links drop constraint ck_vault_document_links_type")
    op.execute(f"alter table vault_document_links add constraint ck_vault_document_links_type check ({_in(LINK_TYPES)})")

    op.add_column("business_book_settings", sa.Column("evidence_threshold", sa.Numeric(24, 10), nullable=True))
    op.create_check_constraint("ck_business_book_settings_evidence_threshold", "business_book_settings",
                               "evidence_threshold is null or evidence_threshold >= 0")

    op.create_table(
        TABLE,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("item_key", sa.String(200), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_notice_claims_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "kind", "item_key", "day", name="uq_finance_notice_claims_item_day"),
        sa.CheckConstraint("kind in ('reconciliation_overdue', 'statement_missing', 'uncategorized_money', 'business_unposted', 'evidence_missing')",
                           name="ck_finance_notice_claims_kind"),
    )
    op.create_index("ix_finance_notice_claims_day", TABLE, ["tenant_id", "workspace_id", "owner_user_id", "day"])
    _rls(TABLE)
    op.execute("create policy lifecycle_purge_finance_notice_claims on finance_notice_claims for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = finance_notice_claims.tenant_id and j.status = 'purging' "
               "and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = finance_notice_claims.workspace_id)))) "
               "with check (false)")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute("drop policy if exists lifecycle_purge_finance_notice_claims on finance_notice_claims")
    op.drop_table(TABLE)
    op.drop_constraint("ck_business_book_settings_evidence_threshold", "business_book_settings", type_="check")
    op.drop_column("business_book_settings", "evidence_threshold")
    op.execute("delete from vault_document_links where target_type like 'business\\_%' escape '\\'")
    op.execute("alter table vault_document_links drop constraint ck_vault_document_links_type")
    op.execute(f"alter table vault_document_links add constraint ck_vault_document_links_type check ({_in(OLD_LINK_TYPES)})")
