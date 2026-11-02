"""Let the tenant purge see IBKR broker rows (docs/organisation-lifecycle.md).

0122 put a restrictive ``owner_only_<table>`` policy on the four IBKR broker tables. A restrictive
policy applies to every role, so it also hid every row from ``anum_tenant_purger`` and an
organisation or workspace deletion would have left the broker settings, connections, orders and
events behind. Like the other owner-only tables (0079), the restrictive policy now also admits the
purge role; the purge role still reaches rows only through its own ``lifecycle_purge_<table>``
policy (a deletion job in ``purging`` for that tenant) and can never write (``with check (false)``).

Expand only: the previous release reads and writes as before.
"""

from alembic import op

revision = "0124_ibkr_purge_policy"
down_revision = "0123_selling_readiness"
branch_labels = None
depends_on = None

OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
TABLES = ("finance_broker_settings", "finance_broker_connections", "finance_broker_orders", "finance_broker_events")


def upgrade() -> None:
    for table in TABLES:
        op.execute(
            f"alter policy owner_only_{table} on {table} "
            f"using (({OWNER_PREDICATE}) or (current_user = 'anum_tenant_purger'::name))"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"alter policy owner_only_{table} on {table} using ({OWNER_PREDICATE})")
