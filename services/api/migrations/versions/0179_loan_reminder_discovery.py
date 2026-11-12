"""Loan reminders reach people who have no Finance account (expand only).

docs/finance.md#lent-and-borrowed.

The daily loan reminders found people only through an active ``finance_accounts`` row, so someone who records
money lent or borrowed but never added an account got no reminder. Discovery now also reads, as
``anum_maintenance`` (migration 0057's and 0143's pattern, never BYPASSRLS), the scope columns
(``tenant_id``, ``workspace_id``, ``owner_user_id``) and the three reminder columns (``remind``, ``due_on``,
``closed_on``) of ``finance_person_loans`` and nothing else: no name, amount, currency or note. A select policy
for the role admits only open loans with a due date and reminders on, and the restrictive owner policy also
admits the session whose ``current_user`` is ``anum_maintenance`` (a login that merely inherits the role never
is). The work then runs as the application role in each person's own scope, as before.

Chained after ``0178_refund_requests_vault``. Expand only: a column grant, a policy and a widened restrictive
policy that only the discovery role can use; the previous release ignores them. Rollback never reverses it in
production.
"""

from alembic import op

revision = "0179_loan_reminder_discovery"
down_revision = "0178_refund_requests_vault"
branch_labels = None
depends_on = None

OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"


def upgrade() -> None:
    op.execute("grant select (tenant_id, workspace_id, owner_user_id, remind, due_on, closed_on) on finance_person_loans to anum_maintenance")
    op.execute("create policy maintenance_loan_reminders on finance_person_loans for select to anum_maintenance "
               "using (remind and due_on is not null and closed_on is null)")
    op.execute(f"alter policy owner_only_finance_person_loans on finance_person_loans using (({OWNER_PREDICATE}) "
               "or (current_user = 'anum_tenant_purger'::name) or (current_user = 'anum_maintenance'::name))")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute(f"alter policy owner_only_finance_person_loans on finance_person_loans using (({OWNER_PREDICATE}) "
               "or (current_user = 'anum_tenant_purger'::name))")
    op.execute("drop policy if exists maintenance_loan_reminders on finance_person_loans")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke select (tenant_id, workspace_id, owner_user_id, remind, due_on, closed_on) on finance_person_loans from anum_maintenance;
          end if;
        end
        $$
        """
    )
