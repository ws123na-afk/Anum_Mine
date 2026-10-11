"""At most one queued or coding developer request per tenant and workspace (docs/developer-mode.md).

``POST /api/v1/developer/requests`` admits a request atomically (a per-scope advisory lock,
then the checks and the insert in one transaction, before anything is sent to GitHub).
This partial unique index enforces the same "one active request" rule in the database, so
no writer, including a previous release during a rollout, can store a second one.

Before the index is built, any scope that already has more than one active request (the
race this revision closes) keeps its newest one; the others are marked failed with a
reason. ``developer_requests`` is under forced RLS and the migration login sets no tenant
context, so that one update runs with RLS not forced for the owner, as in
``0007_event_outbox``, inside the migration transaction (which also restores it). No role
is granted anything and no application login ever bypasses the policy.

Built ``CONCURRENTLY`` (outside the migration transaction) so writes continue during the
build; an invalid index left by an interrupted build is dropped first. Expand only: the
previous release keeps working (its rare racing insert is refused instead of stored).

Numbered 0023 on a parallel branch: re-chain ``down_revision`` onto the newest migration
when this lands (CLAUDE.md, parallel migrations).
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_developer_one_active"
down_revision = "0022_developer_requests"
branch_labels = None
depends_on = None

TABLE = "developer_requests"
INDEX = "uq_developer_requests_one_active"
ACTIVE = "('queued', 'coding')"
SUPERSEDED = "Not started: another request for this workspace was already queued or coding."


def upgrade() -> None:
    op.execute(f"alter table {TABLE} no force row level security")
    op.execute(
        sa.text(
            """
            update developer_requests as stale
               set status = 'failed', failure_reason = :reason, updated_at = now()
              from (
                select id,
                       row_number() over (
                         partition by tenant_id, workspace_id order by created_at desc, id desc
                       ) as position
                  from developer_requests
                 where status in ('queued', 'coding')
              ) as ranked
             where stale.id = ranked.id and ranked.position > 1
            """
        ).bindparams(reason=SUPERSEDED)
    )
    op.execute(f"alter table {TABLE} force row level security")
    with op.get_context().autocommit_block():
        invalid = not op.get_context().as_sql and op.get_bind().execute(
            sa.text("select not indisvalid from pg_index where indexrelid = to_regclass(:name)"),
            {"name": INDEX},
        ).scalar()
        if invalid:
            op.execute(f"drop index concurrently if exists {INDEX}")
        op.execute(
            f"create unique index concurrently if not exists {INDEX} "
            f"on {TABLE} (tenant_id, workspace_id) where status in {ACTIVE}"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"drop index concurrently if exists {INDEX}")
