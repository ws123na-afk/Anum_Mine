"""Per-user preferences (docs/memory.md#from-conversations, docs/multi-tenancy.md).

``user_preferences`` holds one row per tenant and user: today the "Remember things from
my chats" switch (``remember_conversations``), which turns conversation memory off for
that user in every workspace of the tenant. A missing row means the defaults (on). The
row is private to its user: the forced RLS policy checks ``anum.user_id`` as well as
``anum.tenant_id``, like conversations and voice sessions. No other role is granted
anything: nothing reads preferences across tenants.

Expand only: one new table; the previous release ignores it (and keeps remembering, as
before, until it is replaced).

Chained after ``0024_model_config_routes``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_user_preferences"
down_revision = "0024_model_config_routes"
branch_labels = None
depends_on = None

TABLE = "user_preferences"
USER_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and user_id = nullif(current_setting('anum.user_id', true), '')"
)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id", name="fk_user_preferences_tenant"), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("remember_conversations", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", name="pk_user_preferences"),
    )
    op.execute(f"alter table {TABLE} enable row level security")
    op.execute(f"alter table {TABLE} force row level security")
    op.execute(
        f"""
        create policy user_isolation_{TABLE} on {TABLE}
        using (
          {USER_PREDICATE}
        )
        with check (
          {USER_PREDICATE}
        )
        """
    )


def downgrade() -> None:
    op.drop_table(TABLE)
