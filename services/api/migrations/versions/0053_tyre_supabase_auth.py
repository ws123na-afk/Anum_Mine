"""Tyre planning: the ``supabase_auth`` connection kind (docs/tyre-planning.md#connecting).

A workspace may now connect TyrePulse as a dedicated Supabase user (sign-in through
Supabase Auth, reads through PostgREST with that user's JWT). Only the check on
``tyre_connections.kind`` changes: it accepts one more value. Rows, policies and grants are
untouched; no role is granted anything and nothing bypasses RLS.

Expand only: the previous release never writes the new value and keeps reading every
existing row. Downgrade fails while a workspace uses the new kind (the narrower check does
not validate), rather than silently deleting its connection.

Chained after ``0052_setup_plans``.
"""

from alembic import op

revision = "0053_tyre_supabase_auth"
down_revision = "0052_setup_plans"
branch_labels = None
depends_on = None

_OLD = "kind in ('supabase_postgres', 'supabase_rest', 'firestore')"
_NEW = "kind in ('supabase_auth', 'supabase_postgres', 'supabase_rest', 'firestore')"


def upgrade() -> None:
    # Widening a check: the new constraint accepts every row the old one did.
    op.execute("alter table tyre_connections drop constraint if exists ck_tyre_connections_kind")
    op.create_check_constraint("ck_tyre_connections_kind", "tyre_connections", _NEW)


def downgrade() -> None:
    # Re-adding the narrower check validates every row, so it fails (and nothing changes)
    # while a workspace still uses supabase_auth: reconnect those workspaces first.
    op.execute("alter table tyre_connections drop constraint if exists ck_tyre_connections_kind")
    op.create_check_constraint("ck_tyre_connections_kind", "tyre_connections", _OLD)
