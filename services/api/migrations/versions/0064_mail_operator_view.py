"""Email Center operator view: mail health per organisation, counts only (docs/email.md#operator-view).

* ``mail_ops_counters``: one row per tenant, workspace, UAE-independent UTC day and metric
  (``sent``, ``send_failed``, ``send_unknown``, ``send_rate_limited``,
  ``connect_rate_limited``, ``auth_failed``, ``server_offline``) with a count. No person,
  address, subject or body: the Email Center bumps it inside the caller's tenant context.
  Forced RLS on tenant and workspace like every table.
* ``anum_platform_reader`` (migration ``0039_team_work``) may read, for the organisations the
  operator runs and nothing else: ``mail_ops_counters`` (tenant, day, metric, count) and the
  ``tenant_id, status, last_checked_at`` columns of ``mail_accounts`` (never the address,
  display name, password or error text). For that, every restrictive policy on
  ``mail_accounts`` also admits ``current_user = 'anum_platform_reader'`` (reached only with
  ``SET LOCAL ROLE``; a login that merely inherits the role never is), the same pattern
  ``0060_email_center`` uses for ``anum_maintenance``. The predicate is extended in place, so
  whatever an earlier migration wrote there is kept.

Expand only: one new table, two grants and policies; the previous release ignores them.

Chained after ``0060_email_center``.
"""

from alembic import op
import sqlalchemy as sa

revision = "0064_mail_operator_view"
down_revision = "0060_email_center"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OPERATOR_ORGS = (
    "exists (select 1 from platform_organizations p where p.tenant_id = {table}.tenant_id "
    "and p.operator_user_id = nullif(current_setting('anum.user_id', true), ''))"
)
METRICS = ("sent", "send_failed", "send_unknown", "send_rate_limited", "connect_rate_limited", "auth_failed", "server_offline")


# Every restrictive policy on mail_accounts also admits the platform reader (kept as is when
# it already does); the downgrade strips the clause again from the deparsed predicate.
EXTEND_RESTRICTIVE = """
    do $$
    declare
      pol record;
    begin
      for pol in
        select policyname, qual from pg_policies
        where schemaname = current_schema() and tablename = 'mail_accounts' and permissive = 'RESTRICTIVE'
      loop
        if position('anum_platform_reader' in pol.qual) = 0 then
          execute format(
            'alter policy %I on mail_accounts using ((%s) or current_user = %L)',
            pol.policyname, pol.qual, 'anum_platform_reader'
          );
        end if;
      end loop;
    end
    $$
"""
STRIP_RESTRICTIVE = """
    do $$
    declare
      pol record;
      expr text;
    begin
      for pol in
        select policyname, qual from pg_policies
        where schemaname = current_schema() and tablename = 'mail_accounts' and permissive = 'RESTRICTIVE'
      loop
        expr := replace(pol.qual, ' OR (CURRENT_USER = ''anum_platform_reader''::name)', '');
        if expr <> pol.qual then
          execute format('alter policy %I on mail_accounts using (%s)', pol.policyname, expr);
        end if;
      end loop;
    end
    $$
"""


def _extend_restrictive(add: bool) -> None:
    """Add (or remove) the platform reader to every restrictive policy on mail_accounts."""
    op.execute(EXTEND_RESTRICTIVE if add else STRIP_RESTRICTIVE)


def upgrade() -> None:
    op.create_table(
        "mail_ops_counters",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "day", "metric", name="pk_mail_ops_counters"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"],
            name="fk_mail_ops_counters_workspace", ondelete="CASCADE",
        ),
        sa.CheckConstraint("metric in (" + ", ".join(f"'{m}'" for m in METRICS) + ")", name="ck_mail_ops_counters_metric"),
        sa.CheckConstraint("count >= 0", name="ck_mail_ops_counters_count"),
    )
    op.execute("alter table mail_ops_counters enable row level security")
    op.execute("alter table mail_ops_counters force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_mail_ops_counters on mail_ops_counters
        using (
          {PREDICATE}
        )
        with check (
          {PREDICATE}
        )
        """
    )
    op.execute("grant select (tenant_id, day, metric, count) on mail_ops_counters to anum_platform_reader")
    op.execute(
        "create policy platform_reader_counts on mail_ops_counters for select to anum_platform_reader using ("
        + OPERATOR_ORGS.format(table="mail_ops_counters") + ")"
    )
    op.execute("grant select (tenant_id, status, last_checked_at) on mail_accounts to anum_platform_reader")
    op.execute(
        "create policy platform_reader_counts on mail_accounts for select to anum_platform_reader using ("
        + OPERATOR_ORGS.format(table="mail_accounts") + ")"
    )
    _extend_restrictive(add=True)


def downgrade() -> None:
    # Best effort on the deparsed predicate; a clause left behind is harmless once the
    # reader's column grant and permissive policy are gone.
    _extend_restrictive(add=False)
    op.execute("drop policy if exists platform_reader_counts on mail_accounts")
    op.execute("revoke select (tenant_id, status, last_checked_at) on mail_accounts from anum_platform_reader")
    op.drop_table("mail_ops_counters")
