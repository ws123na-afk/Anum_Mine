"""Personal debt overview, credit card terms, promises to pay and financial alert preferences (expand only).

docs/finance.md#debt-overview, #credit-cards, #financial-alerts.

* ``finance_debts`` gains eleven nullable columns: ``debt_type`` (what kind of obligation it is: ``personal_loan``,
  ``vehicle_finance``, ``mortgage``, ``credit_card``, ``employer_loan``, ``salary_advance``, ``bnpl``, ``informal`` or
  ``other``), the lender's own figure (``verified_balance`` with the date it was read, ``verified_on``), an amount the
  person disputes (``disputed_amount`` with ``dispute_note``), fees paid on it (``fees``), and a credit card's terms as
  printed on its statement: ``card_limit``, ``card_statement_day`` (the closing day, 1 to 31), ``card_statement_balance``,
  ``card_statement_minimum`` and ``card_statement_on``. A verified balance comes with its date; a statement balance with
  its statement date.
* ``finance_person_loans`` gains ``contact_id`` (the person in Contacts, optional), ``promises`` (jsonb, not null,
  default ``[]``: what the other person promised to pay and by when) and the balance both sides last agreed
  (``verified_balance`` with ``verified_on``).
* ``finance_notice_claims`` (``0157``) also takes the alerts' own claim kinds, ``alert:<kind>`` (at most 30 characters): its kind
  check is widened (the old kinds stay allowed), so each alert item is told once.
* ``finance_alert_settings``: one row per person with which financial alerts they want (jsonb). Forced RLS like
  ``0161``: ``tenant_isolation_<table>``, a restrictive ``owner_only_<table>`` policy (also admitting the tenant purge
  role, which can only delete) and ``lifecycle_purge_<table>``. No grant to a maintenance role.

Chained after ``0185_pf_budgets`` (written against ``0183_calendar_choices``; rechained at merge).

Expand only. The previous release ignores the new columns (the defaults fill them) and the new table. Rollback:
``alembic downgrade`` to the previous revision drops the table and the columns (only for an empty test workspace; production
restores a backup, because dropping them loses what the person entered).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0186_pf_debts"
down_revision = "0183_calendar_choices"  # the coordinator rechains it after 0185_pf_budgets
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(20, 4)
DEBTS = "finance_debts"
LOANS = "finance_person_loans"
ALERTS = "finance_alert_settings"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
DEBT_COLUMNS = (
    ("debt_type", sa.String(20)),
    ("verified_balance", MONEY),
    ("verified_on", sa.Date()),
    ("disputed_amount", MONEY),
    ("dispute_note", sa.String(300)),
    ("fees", MONEY),
    ("card_limit", MONEY),
    ("card_statement_day", sa.Integer()),
    ("card_statement_balance", MONEY),
    ("card_statement_minimum", MONEY),
    ("card_statement_on", sa.Date()),
)
DEBT_CHECKS = (
    ("ck_finance_debts_debt_type", "debt_type is null or debt_type in ('personal_loan', 'vehicle_finance', 'mortgage', 'credit_card', "
                                   "'employer_loan', 'salary_advance', 'bnpl', 'informal', 'other')"),
    ("ck_finance_debts_verified", "(verified_balance is null) = (verified_on is null) and (verified_balance is null or verified_balance >= 0)"),
    ("ck_finance_debts_disputed", "disputed_amount is null or disputed_amount > 0"),
    ("ck_finance_debts_fees", "fees is null or fees >= 0"),
    ("ck_finance_debts_card_limit", "card_limit is null or card_limit > 0"),
    ("ck_finance_debts_card_day", "card_statement_day is null or card_statement_day between 1 and 31"),
    ("ck_finance_debts_card_statement", "(card_statement_balance is null) = (card_statement_on is null) "
                                        "and (card_statement_balance is null or card_statement_balance >= 0)"),
    ("ck_finance_debts_card_minimum", "card_statement_minimum is null or card_statement_minimum >= 0"),
)
OLD_CLAIM_KINDS = "kind in ('reconciliation_overdue', 'statement_missing', 'uncategorized_money', 'business_unposted', 'evidence_missing')"
LOAN_COLUMNS = (
    ("contact_id", sa.String(80)),
    ("verified_balance", MONEY),
    ("verified_on", sa.Date()),
)


def upgrade() -> None:
    for name, kind in DEBT_COLUMNS:
        op.add_column(DEBTS, sa.Column(name, kind, nullable=True))
    for name, condition in DEBT_CHECKS:
        op.create_check_constraint(name, DEBTS, condition)

    for name, kind in LOAN_COLUMNS:
        op.add_column(LOANS, sa.Column(name, kind, nullable=True))
    op.add_column(LOANS, sa.Column("promises", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.create_check_constraint("ck_finance_person_loans_promises", LOANS, "jsonb_typeof(promises) = 'array'")
    op.create_check_constraint("ck_finance_person_loans_verified", LOANS,
                               "(verified_balance is null) = (verified_on is null) and (verified_balance is null or verified_balance >= 0)")

    op.drop_constraint("ck_finance_notice_claims_kind", "finance_notice_claims", type_="check")
    op.create_check_constraint("ck_finance_notice_claims_kind", "finance_notice_claims", f"{OLD_CLAIM_KINDS} or kind like 'alert:%'")

    op.create_table(
        ALERTS,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column("kinds", JSONB(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_finance_alert_settings_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", name="uq_finance_alert_settings_owner"),
        sa.CheckConstraint("jsonb_typeof(kinds) = 'object'", name="ck_finance_alert_settings_kinds"),
    )
    op.execute("alter table finance_alert_settings enable row level security")
    op.execute("alter table finance_alert_settings force row level security")
    op.execute(f"create policy tenant_isolation_finance_alert_settings on finance_alert_settings using ({PREDICATE}) with check ({PREDICATE})")
    op.execute("create policy owner_only_finance_alert_settings on finance_alert_settings as restrictive "
               f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name)) with check ({OWNER_PREDICATE})")
    op.execute("grant select (tenant_id, ctid, workspace_id), delete on finance_alert_settings to anum_tenant_purger")
    op.execute("create policy lifecycle_purge_finance_alert_settings on finance_alert_settings for all to anum_tenant_purger "
               "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
               "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = finance_alert_settings.tenant_id and j.status = 'purging' "
               "and (j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = finance_alert_settings.workspace_id)))) "
               "with check (false)")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute("drop policy if exists lifecycle_purge_finance_alert_settings on finance_alert_settings")
    op.drop_table(ALERTS)
    op.execute("delete from finance_notice_claims where kind like 'alert:%'")
    op.drop_constraint("ck_finance_notice_claims_kind", "finance_notice_claims", type_="check")
    op.create_check_constraint("ck_finance_notice_claims_kind", "finance_notice_claims", OLD_CLAIM_KINDS)
    op.drop_constraint("ck_finance_person_loans_verified", LOANS, type_="check")
    op.drop_constraint("ck_finance_person_loans_promises", LOANS, type_="check")
    op.drop_column(LOANS, "promises")
    for name, _kind in reversed(LOAN_COLUMNS):
        op.drop_column(LOANS, name)
    for name, _condition in reversed(DEBT_CHECKS):
        op.drop_constraint(name, DEBTS, type_="check")
    for name, _kind in reversed(DEBT_COLUMNS):
        op.drop_column(DEBTS, name)
