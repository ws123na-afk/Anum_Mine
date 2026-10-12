"""What ANUM learned (docs/learning.md).

Six tables hold what ANUM learns from one user in one workspace: feedback signals
(ratings, edited drafts, approval decisions, retries), the derived style profile with
the user's own overrides and the learning switch, learned preferences (instructions),
detected routines, weekly improvement notes and their suggestions. Every row is private
to its user, like conversations: the forced RLS policy checks ``anum.tenant_id``,
``anum.workspace_id`` and ``anum.user_id``. No other role is granted anything: nothing
reads learning data across tenants or users.

Expand only: new tables; the previous release ignores them.

Chained after ``0035_email_inbox``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_learning"
down_revision = "0035_email_inbox"
branch_labels = None
depends_on = None

TABLES = (
    "learning_profiles",
    "learning_signals",
    "learning_preferences",
    "learning_routines",
    "learning_notes",
    "learning_suggestions",
)

USER_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')\n"
    "          and user_id = nullif(current_setting('anum.user_id', true), '')"
)


def _scope() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column:
    if nullable:
        return sa.Column(name, sa.DateTime(timezone=True), nullable=True)
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _json(name: str, default: str = "'{}'::jsonb") -> sa.Column:
    return sa.Column(name, postgresql.JSONB(), nullable=False, server_default=sa.text(default))


def upgrade() -> None:
    op.create_table(
        "learning_profiles",
        *_scope(),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        _json("derived"),
        _json("overrides"),
        _timestamp("derived_at", nullable=True),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "user_id", name="pk_learning_profiles"),
        _workspace_fk("learning_profiles"),
    )
    op.create_table(
        "learning_signals",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("surface", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.String(120), nullable=True),
        sa.Column("skill", sa.String(120), nullable=True),
        sa.Column("tool", sa.String(120), nullable=True),
        sa.Column("contact", sa.String(160), nullable=True),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("original_text", sa.Text(), nullable=True),
        sa.Column("final_text", sa.Text(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        _json("metadata"),
        _timestamp("created_at"),
        _workspace_fk("learning_signals"),
        sa.CheckConstraint(
            "kind in ('rating_up', 'rating_down', 'draft_accepted', 'draft_edited', "
            "'approval_approved', 'approval_rejected', 'retry')",
            name="ck_learning_signals_kind",
        ),
        sa.CheckConstraint(
            "surface in ('chat', 'email', 'whatsapp', 'task', 'approval', 'automation')",
            name="ck_learning_signals_surface",
        ),
    )
    op.create_index(
        "ix_learning_signals_scope_created", "learning_signals", ["tenant_id", "workspace_id", "user_id", "created_at"]
    )
    op.create_index(
        "ix_learning_signals_subject", "learning_signals", ["tenant_id", "workspace_id", "user_id", "surface", "subject_id"]
    )
    op.create_table(
        "learning_preferences",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("scope", sa.String(200), nullable=False, server_default="all"),
        sa.Column("text", sa.String(300), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="user"),
        sa.Column("suggestion_id", sa.String(80), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk("learning_preferences"),
        sa.CheckConstraint("source in ('user', 'suggestion')", name="ck_learning_preferences_source"),
    )
    op.create_index(
        "ix_learning_preferences_scope_created",
        "learning_preferences",
        ["tenant_id", "workspace_id", "user_id", "created_at"],
    )
    op.create_table(
        "learning_routines",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("fingerprint", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("cadence", sa.String(10), nullable=False),
        sa.Column("cron", sa.String(120), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False, server_default="UTC"),
        _json("steps", "'[]'::jsonb"),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("weeks_observed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        _timestamp("last_seen_at", nullable=True),
        sa.Column("workflow_id", sa.String(80), nullable=True),
        sa.Column("schedule_id", sa.String(80), nullable=True),
        _timestamp("detected_at"),
        _timestamp("decided_at", nullable=True),
        _workspace_fk("learning_routines"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "user_id", "fingerprint", name="uq_learning_routines_fingerprint"),
        sa.CheckConstraint(
            "status in ('proposed', 'accepted', 'confirmed', 'dismissed')", name="ck_learning_routines_status"
        ),
        sa.CheckConstraint("cadence in ('daily', 'weekly')", name="ck_learning_routines_cadence"),
    )
    op.create_table(
        "learning_notes",
        *_scope(),
        sa.Column("week", sa.String(10), nullable=False),
        _timestamp("period_start"),
        _timestamp("period_end"),
        _json("summary"),
        _timestamp("generated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "user_id", "week", name="pk_learning_notes"),
        _workspace_fk("learning_notes"),
    )
    op.create_table(
        "learning_suggestions",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("week", sa.String(10), nullable=False),
        sa.Column("fingerprint", sa.String(200), nullable=False),
        sa.Column("rule", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.String(1000), nullable=False),
        sa.Column("target", sa.String(200), nullable=True),
        _json("change"),
        _json("evidence"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _timestamp("decided_at", nullable=True),
        _workspace_fk("learning_suggestions"),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "user_id", "fingerprint", name="uq_learning_suggestions_fingerprint"
        ),
        sa.CheckConstraint("kind in ('instruction', 'template', 'autonomy')", name="ck_learning_suggestions_kind"),
        sa.CheckConstraint("status in ('open', 'applied', 'dismissed')", name="ck_learning_suggestions_status"),
    )
    op.create_index(
        "ix_learning_suggestions_scope_week", "learning_suggestions", ["tenant_id", "workspace_id", "user_id", "week"]
    )
    for table in TABLES:
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
        op.execute(
            f"""
            create policy user_isolation_{table} on {table}
            using (
              {USER_PREDICATE}
            )
            with check (
              {USER_PREDICATE}
            )
            """
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
