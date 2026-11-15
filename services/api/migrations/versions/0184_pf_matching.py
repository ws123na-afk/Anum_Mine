"""Matching decisions and historical backfill runs for personal finance (expand only).

docs/finance-matching.md.

* ``finance_match_records``: one decision of the matching engine per person, canonical transaction and obligation
  target (a bill's due date, a Tamara installment's bill, a card, a bank loan): the outcome (auto linked, waiting for
  review, accepted, rejected, undone, stale), the evidence state (pending, provisional, verified), the reason code,
  the confidence, the evidence, the policy version, the link it made and the run that made it. Unique per pair, so a
  rerun updates the decision instead of adding one.
* ``finance_backfill_runs``: one historical reprocessing run (preview or apply) with its checkpoint (the last month
  finished), attempts, counts and error code. At most one running run per person (a partial unique index).
* ``finance_notice_claims.kind`` also takes ``match_review``: the daily notice that matches wait for review claims its
  day like the exception notices. The check is replaced by a wider one (every existing row still passes).

Both have forced RLS like ``0157``: ``tenant_isolation_<table>``, a restrictive ``owner_only_<table>`` policy (also
admitting the tenant purge role, which can only delete) and ``lifecycle_purge_<table>``. No grant to a maintenance role.

Chained after ``0183_calendar_choices``.

Expand only: the previous release ignores both tables. Rollback: ``alembic downgrade 0183_calendar_choices`` drops them
(only for an empty test workspace; production restores a backup). The finance links the engine made stay valid links
either way.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0184_pf_matching"
down_revision = "0183_calendar_choices"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
MATCHES = "finance_match_records"
RUNS = "finance_backfill_runs"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
OLD_NOTICE_KINDS = ("reconciliation_overdue", "statement_missing", "uncategorized_money", "business_unposted", "evidence_missing")
NOTICE_KINDS = (*OLD_NOTICE_KINDS, "match_review")


def _kinds(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _owned() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(80), primary_key=True),
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
    # A wider check (every existing row still passes): the daily "matches wait for review" notice claims its day too.
    op.execute("alter table finance_notice_claims drop constraint ck_finance_notice_claims_kind")
    op.execute(f"alter table finance_notice_claims add constraint ck_finance_notice_claims_kind check (kind in ({_kinds(NOTICE_KINDS)}))")

    op.create_table(
        MATCHES,
        *_owned(),
        sa.Column("canonical_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("dimension", sa.String(20), nullable=False),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(80), nullable=False),
        sa.Column("label", sa.String(160), nullable=False, server_default=""),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("posted_on", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("outcome", sa.String(14), nullable=False),
        sa.Column("evidence_state", sa.String(12), nullable=False),
        sa.Column("reason_code", sa.String(40), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("flags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("competitors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("run_id", sa.String(80), nullable=True),
        sa.Column("link_id", sa.String(80), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("decided_by", sa.String(160), nullable=True),
        sa.Column("decided_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_match_records_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "canonical_id", "target_id", name="uq_finance_match_records_pair"),
        sa.CheckConstraint("outcome in ('auto_linked', 'review', 'accepted', 'rejected', 'undone', 'stale')", name="ck_finance_match_records_outcome"),
        sa.CheckConstraint("evidence_state in ('pending', 'provisional', 'verified')", name="ck_finance_match_records_evidence_state"),
        sa.CheckConstraint("kind in ('installment', 'bill', 'card_repayment', 'bank_loan')", name="ck_finance_match_records_kind"),
        sa.CheckConstraint("confidence >= 0 and confidence <= 1", name="ck_finance_match_records_confidence"),
        sa.CheckConstraint("amount > 0", name="ck_finance_match_records_amount"),
    )
    op.create_index("ix_finance_match_records_outcome", MATCHES, ["tenant_id", "workspace_id", "owner_user_id", "outcome", "updated_at"])
    op.create_index("ix_finance_match_records_run", MATCHES, ["tenant_id", "workspace_id", "owner_user_id", "run_id"])
    _rls(MATCHES)
    op.execute("create policy lifecycle_purge_finance_match_records on finance_match_records for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = finance_match_records.tenant_id and j.status = 'purging' "
               "and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = finance_match_records.workspace_id)))) "
               "with check (false)")

    op.create_table(
        RUNS,
        *_owned(),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("start_on", sa.Date(), nullable=True),
        sa.Column("end_on", sa.Date(), nullable=True),
        sa.Column("cursor_on", sa.Date(), nullable=True),
        sa.Column("batches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("counts", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("undone_at", TS, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_backfill_runs_workspace"),
        sa.CheckConstraint("mode in ('preview', 'apply')", name="ck_finance_backfill_runs_mode"),
        sa.CheckConstraint("status in ('running', 'done', 'failed', 'undone')", name="ck_finance_backfill_runs_status"),
    )
    op.create_index("ix_finance_backfill_runs_started", RUNS, ["tenant_id", "workspace_id", "owner_user_id", "started_at"])
    op.create_index("uq_finance_backfill_runs_running", RUNS, ["tenant_id", "workspace_id", "owner_user_id"], unique=True,
                    postgresql_where=sa.text("status = 'running'"))
    _rls(RUNS)
    op.execute("create policy lifecycle_purge_finance_backfill_runs on finance_backfill_runs for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = finance_backfill_runs.tenant_id and j.status = 'purging' "
               "and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = finance_backfill_runs.workspace_id)))) "
               "with check (false)")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute("drop policy if exists lifecycle_purge_finance_backfill_runs on finance_backfill_runs")
    op.execute("drop policy if exists lifecycle_purge_finance_match_records on finance_match_records")
    op.drop_table(RUNS)
    op.drop_table(MATCHES)
    op.execute("delete from finance_notice_claims where kind = 'match_review'")
    op.execute("alter table finance_notice_claims drop constraint ck_finance_notice_claims_kind")
    op.execute(f"alter table finance_notice_claims add constraint ck_finance_notice_claims_kind check (kind in ({_kinds(OLD_NOTICE_KINDS)}))")
