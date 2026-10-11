"""Forced row-level security on ``tenants`` (docs/multi-tenancy.md#tenants-table).

``tenants`` was the one table without RLS: the application role could list every
organization's id, name and status. Its policy now admits only the row whose ``id`` is
``anum.tenant_id``, the same setting every request, job and worker transaction already
sets (``set_tenant_context``), for reads and writes alike, so ``POST /api/v1/tenants`` and
onboarding can still create the caller's own tenant row.

No narrow role is granted anything here: the cross-tenant jobs (outbox relay, retention
purge, scheduler, key rotation) find their work through other tables and never read
``tenants``. Foreign keys to ``tenants`` keep working because PostgreSQL does not apply
row security to referential integrity checks.

Expand only: no column or table changes. The previous release reads ``tenants`` only for
the tenant in its RLS context, so it runs unchanged against this schema.

Chained after ``0019_conversation_retention``.
"""

from alembic import op

revision = "0020_tenants_rls"
down_revision = "0019_conversation_retention"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "id = nullif(current_setting('anum.tenant_id', true), '')"


def upgrade() -> None:
    op.execute("alter table tenants enable row level security")
    op.execute("alter table tenants force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_tenants on tenants
        using ({TENANT_PREDICATE})
        with check ({TENANT_PREDICATE})
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists tenant_isolation_tenants on tenants")
    op.execute("alter table tenants no force row level security")
    op.execute("alter table tenants disable row level security")
