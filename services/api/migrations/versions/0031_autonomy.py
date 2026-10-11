"""Autonomy policy: guardrails, workspace settings, rules, proposals, activity (docs/autonomy.md).

* ``autonomy_guardrails``: one row per tenant with the organization's floors (spending
  limit, blocked recipients and domains, category ceilings, the most a workspace may let
  run on its own, daily caps, quiet hours) as JSON. Tenant-level, like approval rules.
* ``workspace_autonomy_settings``: one row per workspace (levels per category,
  thresholds, approved templates, its own caps, quiet hours and blocked lists).
* ``autonomy_rules``: the level for one exact pattern (a tool, optionally a template).
* ``autonomy_proposals``: "ask once, remember" counters and proposals per pattern.
* ``autonomy_actions``: every action ANUM took on its own, with its undo token.
* ``autonomy_recipients``: recipients already contacted, as SHA-256 digests only.

Every table is under forced RLS on ``anum.tenant_id`` (and ``anum.workspace_id`` for the
workspace tables); no role bypasses it and nothing is granted to a cross-tenant role.

Expand only: new tables; the previous release ignores them and keeps asking for every
high-risk action, as before.

Chained after ``0030_integration_health``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0031_autonomy"
down_revision = "0030_integration_health"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"
WORKSPACE_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
LEVELS = "('ask_always', 'act_and_notify', 'act_silently')"
WORKSPACE_TABLES = (
    "workspace_autonomy_settings",
    "autonomy_rules",
    "autonomy_proposals",
    "autonomy_actions",
    "autonomy_recipients",
)


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def _scope_columns() -> list[sa.Column]:
    return [sa.Column("tenant_id", sa.String(80), nullable=False), sa.Column("workspace_id", sa.String(80), nullable=False)]


def _force_rls(table: str, predicate: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{table} on {table}
        using (
          {predicate}
        )
        with check (
          {predicate}
        )
        """
    )


def upgrade() -> None:
    op.create_table(
        "autonomy_guardrails",
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id", name="fk_autonomy_guardrails_tenant"), nullable=False),
        sa.Column("settings", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_autonomy_guardrails"),
    )
    op.create_table(
        "workspace_autonomy_settings",
        *_scope_columns(),
        sa.Column("settings", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_workspace_autonomy_settings"),
        _workspace_fk("workspace_autonomy_settings"),
    )
    op.create_table(
        "autonomy_rules",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("pattern_key", sa.String(320), nullable=False),
        sa.Column("tool", sa.String(160), nullable=False),
        sa.Column("template", sa.String(120), nullable=True),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("action_kind", sa.String(20), nullable=False),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_autonomy_rules"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "pattern_key", name="uq_autonomy_rules_pattern"),
        sa.CheckConstraint(f"level in {LEVELS}", name="ck_autonomy_rules_level"),
        sa.CheckConstraint("source in ('manual', 'proposal', 'tighten')", name="ck_autonomy_rules_source"),
        _workspace_fk("autonomy_rules"),
    )
    op.create_table(
        "autonomy_proposals",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("pattern_key", sa.String(320), nullable=False),
        sa.Column("tool", sa.String(160), nullable=False),
        sa.Column("template", sa.String(120), nullable=True),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("action_kind", sa.String(20), nullable=False),
        sa.Column("proposed_level", sa.String(20), nullable=False),
        sa.Column("approvals", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_by", sa.String(160), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_autonomy_proposals"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "pattern_key", name="uq_autonomy_proposals_pattern"),
        sa.CheckConstraint(f"proposed_level in {LEVELS}", name="ck_autonomy_proposals_level"),
        sa.CheckConstraint(
            "status in ('collecting', 'pending', 'accepted', 'declined')", name="ck_autonomy_proposals_status"
        ),
        _workspace_fk("autonomy_proposals"),
    )
    op.create_table(
        "autonomy_actions",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("task_id", sa.String(80), nullable=False),
        sa.Column("run_id", sa.String(80), nullable=False),
        sa.Column("tool", sa.String(160), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("action_kind", sa.String(20), nullable=False),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("pattern_key", sa.String(320), nullable=False),
        sa.Column("template", sa.String(120), nullable=True),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("target", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("requested_by", sa.String(160), nullable=False),
        sa.Column("undo_tool", sa.String(160), nullable=True),
        sa.Column("undo_arguments", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("undo_token", sa.String(80), nullable=True),
        sa.Column("undo_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_by", sa.String(160), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undo_error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_autonomy_actions"),
        sa.CheckConstraint(f"level in {LEVELS}", name="ck_autonomy_actions_level"),
        sa.CheckConstraint("status in ('done', 'undone')", name="ck_autonomy_actions_status"),
        _workspace_fk("autonomy_actions"),
    )
    op.create_index(
        "ix_autonomy_actions_feed", "autonomy_actions", ["tenant_id", "workspace_id", "created_at", "id"]
    )
    op.create_table(
        "autonomy_recipients",
        *_scope_columns(),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("recipient_hash", sa.String(64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "category", "recipient_hash", name="pk_autonomy_recipients"),
        _workspace_fk("autonomy_recipients"),
    )
    _force_rls("autonomy_guardrails", TENANT_PREDICATE)
    for table in WORKSPACE_TABLES:
        _force_rls(table, WORKSPACE_PREDICATE)


def downgrade() -> None:
    for table in reversed(WORKSPACE_TABLES):
        op.drop_table(table)
    op.drop_table("autonomy_guardrails")
