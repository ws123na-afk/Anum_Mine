"""Brevo email campaigns (docs/brevo.md).

Two workspace tables under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id``
(``tenant_isolation_<table>``, the 0040 pattern):

* ``brevo_connections``: one per workspace. The owner's Brevo API key only as a Fernet
  ciphertext (``api_key_ciphertext``), its last four characters, the account's own address
  (where test sends go) and the last check's outcome.
* ``brevo_campaign_drafts``: campaigns written in ANUM (name, subject, preview text, sender,
  HTML, lists) with the content hash a send approval binds, the approval, and the one Brevo
  campaign the draft owns. A partial unique index keeps one draft per Brevo campaign in a
  workspace, so an approved draft can never be sent through a second campaign.

Who may do what (owners manage, members write drafts, everyone reads) is the API's
permission check; the row policies keep every tenant and workspace apart.

Key rotation (``python -m anum_api.rotate_secrets``) finds the workspaces holding a key as
``anum_maintenance``: a column grant of ``tenant_id`` and ``workspace_id`` only (never the
ciphertext) and a select policy for that role. The rotation itself runs as the application
role inside each workspace's RLS context.

Expand only: new tables, one column grant, one policy. The previous release ignores them.

Numbered 0071 and chained after ``0070_business``, the head of ``main``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0071_brevo_campaigns"
down_revision = "0070_business"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("brevo_connections", "brevo_campaign_drafts")
TS = sa.DateTime(timezone=True)
DRAFT_STATUSES = ("draft", "pending_approval", "sending", "scheduled", "sent", "failed", "unknown")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _enable_rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{table} on {table}
        using (
          {PREDICATE}
        )
        with check (
          {PREDICATE}
        )
        """
    )


def upgrade() -> None:
    op.create_table(
        "brevo_connections",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=False),
        sa.Column("key_last4", sa.String(4), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="connected"),
        sa.Column("account_email", sa.String(254), nullable=False, server_default=""),
        sa.Column("account_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("company", sa.String(200), nullable=False, server_default=""),
        sa.Column("plan", sa.String(60), nullable=False, server_default=""),
        sa.Column("last_checked_at", TS, nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("connected_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_brevo_connections_workspace"),
        sa.CheckConstraint(_in("status", ("connected", "needs_key", "error")), name="ck_brevo_connections_status"),
        sa.CheckConstraint("length(api_key_ciphertext) > 0", name="ck_brevo_connections_ciphertext"),
    )
    op.create_table(
        "brevo_campaign_drafts",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("preview_text", sa.String(200), nullable=False, server_default=""),
        sa.Column("sender_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_name", sa.String(120), nullable=False),
        sa.Column("sender_email", sa.String(254), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("list_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("list_names", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("scheduled_at", TS, nullable=True),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("approval_task_id", sa.String(80), nullable=True),
        sa.Column("approved_hash", sa.String(64), nullable=True),
        sa.Column("approved_by", sa.String(160), nullable=True),
        sa.Column("brevo_campaign_id", sa.BigInteger(), nullable=True),
        sa.Column("send_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", postgresql.JSONB(), nullable=True),
        sa.Column("test_sent_at", TS, nullable=True),
        sa.Column("sent_at", TS, nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="person"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_brevo_campaign_drafts_workspace"),
        sa.CheckConstraint(_in("status", DRAFT_STATUSES), name="ck_brevo_campaign_drafts_status"),
        sa.CheckConstraint(_in("source", ("person", "assistant")), name="ck_brevo_campaign_drafts_source"),
        sa.CheckConstraint("jsonb_typeof(list_ids) = 'array' and jsonb_array_length(list_ids) between 1 and 10", name="ck_brevo_campaign_drafts_lists"),
    )
    op.create_index("ix_brevo_campaign_drafts_scope_updated", "brevo_campaign_drafts", ["tenant_id", "workspace_id", "updated_at"])
    # One draft per Brevo campaign: the idempotency of the approved send.
    op.create_index(
        "uq_brevo_campaign_drafts_campaign", "brevo_campaign_drafts", ["tenant_id", "workspace_id", "brevo_campaign_id"],
        unique=True, postgresql_where=sa.text("brevo_campaign_id is not null"),
    )
    for table in TABLES:
        _enable_rls(table)
    # Key rotation discovery: which workspaces hold a key, scope columns only.
    op.execute("grant select (tenant_id, workspace_id) on brevo_connections to anum_maintenance")
    op.execute(
        """
        create policy maintenance_brevo_rotation on brevo_connections
        for select to anum_maintenance
        using (true)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_brevo_rotation on brevo_connections")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on brevo_connections from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in reversed(TABLES):
        op.drop_table(table)
