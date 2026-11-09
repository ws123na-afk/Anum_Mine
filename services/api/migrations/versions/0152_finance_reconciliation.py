"""Bank and card reconciliation sessions, and journal entries that must have postings (expand only).

docs/finance.md#reconciliation-sessions.

* ``finance_reconciliations``: one per account and statement period. The statement's opening and closing balance
  (from an imported statement or typed by the owner), a card's amount and minimum due, the status (``open`` or
  ``completed``), who completed it and when, the figures and what was matched at that moment (``snapshot``), a
  fingerprint of the ledger rows of the period (a later edit inside the period shows the reconciliation as changed)
  and the history of completions and reopenings with their reasons (``history``). One per account and period.
* ``finance_reconciliation_items``: the owner's resolutions inside a session: a statement row matched to a ledger
  record, a ledger record attached as confirmed by the statement, a ledger record marked not on this statement
  (pending), a pair accepted as a reversal, and a correcting entry the owner approved. Partial unique indexes keep a
  statement row and a ledger record in at most one live resolution per session, so two requests that match the same
  row at the same moment cannot both win.

Both tables are under forced RLS like ``0147`` and ``0150``: ``tenant_isolation_<table>``, a restrictive
``owner_only_<table>`` policy on ``owner_user_id = anum.user_id`` (also admitting the tenant purge role, which can
only delete) and ``lifecycle_purge_<table>``. No grant to a maintenance role.

Audit B7 (docs/audits/2026-11-09-finance-accounting-audit.md): the personal journal's balance trigger fired on
posting inserts and deletes only, so an entry stored with no postings at all was never checked. A deferred
constraint trigger on ``finance_journal_entries`` now refuses, at commit, an entry that still exists with fewer
than two postings. The function runs as the invoker (no SECURITY DEFINER over forced-RLS tables).

Expand only: two new tables and a new trigger. The previous release never reads the tables and never writes an
entry without postings. Rollback: ``alembic downgrade 0151_business_ledger`` drops the trigger and the tables (only
for an empty test workspace; production restores a backup, and the trigger can be dropped on its own).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0152_finance_reconciliation"
down_revision = "0151_business_ledger"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(20, 4)
TABLES = ("finance_reconciliations", "finance_reconciliation_items")
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"


def _owned() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"create policy owner_only_{table} on {table} as restrictive "
               f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name)) with check ({OWNER_PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to anum_tenant_purger")


def upgrade() -> None:
    op.create_table(
        "finance_reconciliations",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("statement_opening", MONEY, nullable=True),
        sa.Column("statement_closing", MONEY, nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("import_id", sa.String(80), nullable=True),
        sa.Column("amount_due", MONEY, nullable=True),
        sa.Column("minimum_due", MONEY, nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("completed_at", TS, nullable=True),
        sa.Column("completed_by", sa.String(160), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("history", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_reconciliations_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "account_id", "period_start", "period_end",
                            name="uq_finance_reconciliations_period"),
        sa.CheckConstraint("period_end >= period_start and period_end - period_start <= 400", name="ck_finance_reconciliations_period"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_finance_reconciliations_currency"),
        sa.CheckConstraint("source in ('import', 'typed')", name="ck_finance_reconciliations_source"),
        sa.CheckConstraint("status in ('open', 'completed')", name="ck_finance_reconciliations_status"),
        sa.CheckConstraint("(status = 'completed') = (completed_at is not null and completed_by is not null)", name="ck_finance_reconciliations_completed"),
        sa.CheckConstraint("jsonb_typeof(snapshot) = 'object' and jsonb_typeof(history) = 'array'", name="ck_finance_reconciliations_json"),
        sa.CheckConstraint("version >= 0", name="ck_finance_reconciliations_version"),
    )
    op.create_index("ix_finance_reconciliations_account", "finance_reconciliations",
                    ["tenant_id", "workspace_id", "owner_user_id", "account_id", "period_end"])

    op.create_table(
        "finance_reconciliation_items",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("reconciliation_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("statement_ref", sa.String(120), nullable=True),
        sa.Column("transaction_id", sa.String(80), nullable=True),
        sa.Column("other_transaction_id", sa.String(80), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=True),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("undone_at", TS, nullable=True),
        sa.Column("undone_by", sa.String(160), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_reconciliation_items_workspace"),
        sa.ForeignKeyConstraint(["reconciliation_id"], ["finance_reconciliations.id"], name="fk_finance_reconciliation_items_session", ondelete="CASCADE"),
        sa.CheckConstraint("kind in ('match', 'attach', 'pending', 'reversal', 'correction')", name="ck_finance_reconciliation_items_kind"),
        sa.CheckConstraint("status in ('active', 'undone')", name="ck_finance_reconciliation_items_status"),
        sa.CheckConstraint("(status = 'undone') = (undone_at is not null)", name="ck_finance_reconciliation_items_undone"),
        sa.CheckConstraint("statement_ref is not null or transaction_id is not null", name="ck_finance_reconciliation_items_target"),
        sa.CheckConstraint("kind <> 'reversal' or (transaction_id is not null and other_transaction_id is not null)",
                           name="ck_finance_reconciliation_items_pair"),
    )
    op.create_index("ix_finance_reconciliation_items_session", "finance_reconciliation_items",
                    ["tenant_id", "workspace_id", "owner_user_id", "reconciliation_id"])
    # One live resolution per statement row and per ledger record in a session: a second request that resolves the same
    # row at the same moment fails on these, whatever the application checked before.
    op.execute("create unique index uq_finance_reconciliation_items_row on finance_reconciliation_items "
               "(tenant_id, workspace_id, owner_user_id, reconciliation_id, statement_ref) where status = 'active' and statement_ref is not null")
    op.execute("create unique index uq_finance_reconciliation_items_txn on finance_reconciliation_items "
               "(tenant_id, workspace_id, owner_user_id, reconciliation_id, transaction_id) where status = 'active' and transaction_id is not null")
    op.execute("create unique index uq_finance_reconciliation_items_other on finance_reconciliation_items "
               "(tenant_id, workspace_id, owner_user_id, reconciliation_id, other_transaction_id) "
               "where status = 'active' and other_transaction_id is not null")

    for table in TABLES:
        _rls(table)
    op.execute("create policy lifecycle_purge_finance_reconciliations on finance_reconciliations for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = finance_reconciliations.tenant_id and j.status = 'purging' "
               "and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = finance_reconciliations.workspace_id)))) "
               "with check (false)")
    op.execute("create policy lifecycle_purge_finance_reconciliation_items on finance_reconciliation_items for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = finance_reconciliation_items.tenant_id and j.status = 'purging' "
               "and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = finance_reconciliation_items.workspace_id)))) "
               "with check (false)")

    # Audit B7: an entry must keep at least two postings, checked when the transaction commits. An entry deleted in the
    # same transaction (privacy deletion, tenant purge) is no longer there and is not checked.
    op.execute("""
        create function finance_journal_entry_has_postings() returns trigger language plpgsql as $$
        begin
            if exists (select 1 from finance_journal_entries e where e.id = new.id)
               and (select count(*) from finance_postings p where p.entry_id = new.id) < 2 then
                raise exception 'finance journal entry % needs at least two postings', new.id using errcode = '23514';
            end if;
            return null;
        end $$
    """)
    op.execute("""
        create constraint trigger finance_journal_entry_has_postings after insert on finance_journal_entries
        deferrable initially deferred for each row execute function finance_journal_entry_has_postings()
    """)


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute("drop trigger if exists finance_journal_entry_has_postings on finance_journal_entries")
    op.execute("drop function if exists finance_journal_entry_has_postings()")
    for table in reversed(TABLES):
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.drop_table(table)
