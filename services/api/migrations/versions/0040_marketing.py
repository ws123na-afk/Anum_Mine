"""Social media marketing (docs/marketing.md).

Four workspace tables under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id``:

* ``marketing_accounts``: connected Facebook Pages, Instagram professional accounts,
  LinkedIn members and organizations, X, TikTok and YouTube accounts. Access and refresh
  tokens are stored only as Fernet ciphertexts (``ANUM_SECRETS_KEY``).
* ``marketing_campaigns``: campaigns, including the owner-approved auto-post switch.
* ``marketing_posts``: one row per account and language: the copy, media references
  (files stay in ``workspace_files``), the schedule, the approval that authorised the
  publish, the platform's post id and the last pulled metrics.
* ``marketing_preferences``: the workspace's brand voice, time zone and default hashtags.

``anum_maintenance`` may *discover* due work and nothing else (migration 0011 pattern):
the id, scope, status and times of posts that are ``scheduled`` (the publisher then acts
inside each tenant's RLS context), and the scope of accounts holding a ciphertext (key
rotation, ``python -m anum_api.rotate_secrets``). It never reads copy or tokens.

Grant ``anum_maintenance`` to the API login ``WITH INHERIT FALSE, SET TRUE`` (as
``infra/helm/bootstrap-database.sql`` does).

Expand only: new tables and policies; the previous release ignores them.

Numbered 0040 on a parallel branch: re-chain ``down_revision`` onto the newest migration
when this lands (CLAUDE.md, parallel migrations).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0040_marketing"
down_revision = "0049_automation_triggers"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("marketing_posts", "marketing_campaigns", "marketing_accounts", "marketing_preferences")
PLATFORMS = ("facebook", "instagram", "linkedin", "x", "tiktok", "youtube")
POST_STATUSES = ("draft", "pending_approval", "scheduled", "publishing", "published", "sent_to_inbox", "failed", "cancelled")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column:
    if nullable:
        return sa.Column(name, sa.DateTime(timezone=True), nullable=True)
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _scope(table: str) -> list:  # type: ignore[type-arg]
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


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
        "marketing_accounts",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope("marketing_accounts"),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("handle", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("access_token_ciphertext", sa.Text(), nullable=True),
        sa.Column("refresh_token_ciphertext", sa.Text(), nullable=True),
        _timestamp("token_expires_at", nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _timestamp("last_checked_at", nullable=True),
        sa.Column("last_error", postgresql.JSONB(), nullable=True),
        sa.Column("connected_by", sa.String(160), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk("marketing_accounts"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "platform", "external_id", name="uq_marketing_accounts_external"),
        sa.CheckConstraint(_in("platform", PLATFORMS), name="ck_marketing_accounts_platform"),
        sa.CheckConstraint(_in("provider", ("meta", "linkedin", "x", "tiktok", "google")), name="ck_marketing_accounts_provider"),
        sa.CheckConstraint(_in("status", ("connected", "needs_reauth", "error", "revoked")), name="ck_marketing_accounts_status"),
    )

    op.create_table(
        "marketing_campaigns",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope("marketing_campaigns"),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("product", sa.String(200), nullable=False, server_default=""),
        sa.Column("brief", sa.Text(), nullable=False, server_default=""),
        sa.Column("languages", postgresql.JSONB(), nullable=False, server_default=sa.text("'[\"en\", \"ar\"]'::jsonb")),
        sa.Column("platforms", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("utm_campaign", sa.String(100), nullable=False, server_default=""),
        sa.Column("auto_post", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_post_accounts", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("auto_post_approved_by", sa.String(160), nullable=True),
        _timestamp("auto_post_enabled_at", nullable=True),
        sa.Column("auto_post_approval_id", sa.String(80), nullable=True),
        sa.Column("auto_post_task_id", sa.String(80), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk("marketing_campaigns"),
        sa.CheckConstraint(_in("status", ("draft", "active", "paused", "completed", "archived")), name="ck_marketing_campaigns_status"),
        sa.CheckConstraint("end_date is null or start_date is null or end_date >= start_date", name="ck_marketing_campaigns_dates"),
    )
    op.create_index("ix_marketing_campaigns_scope_created", "marketing_campaigns", ["tenant_id", "workspace_id", "created_at", "id"])

    op.create_table(
        "marketing_posts",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope("marketing_posts"),
        sa.Column("campaign_id", sa.String(80), nullable=True),
        sa.Column("group_id", sa.String(80), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("language", sa.String(5), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("hashtags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("link_url", sa.Text(), nullable=True),
        sa.Column("media", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        _timestamp("scheduled_at", nullable=True),
        _timestamp("next_attempt_at", nullable=True),
        _timestamp("published_at", nullable=True),
        sa.Column("external_id", sa.String(300), nullable=True),
        sa.Column("external_url", sa.String(1000), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", postgresql.JSONB(), nullable=True),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("approval_task_id", sa.String(80), nullable=True),
        sa.Column("approved_hash", sa.String(64), nullable=True),
        sa.Column("approved_by", sa.String(160), nullable=True),
        sa.Column("authorised_by", sa.String(20), nullable=True),
        sa.Column("metrics", postgresql.JSONB(), nullable=True),
        _timestamp("metrics_updated_at", nullable=True),
        sa.Column("ai_generated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("untrusted_source", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(160), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk("marketing_posts"),
        sa.CheckConstraint(_in("platform", PLATFORMS), name="ck_marketing_posts_platform"),
        sa.CheckConstraint(_in("status", POST_STATUSES), name="ck_marketing_posts_status"),
        sa.CheckConstraint(_in("language", ("en", "ar")), name="ck_marketing_posts_language"),
        sa.CheckConstraint("attempts >= 0", name="ck_marketing_posts_attempts"),
    )
    op.create_index("ix_marketing_posts_scope_created", "marketing_posts", ["tenant_id", "workspace_id", "created_at", "id"])
    op.create_index("ix_marketing_posts_scope_scheduled", "marketing_posts", ["tenant_id", "workspace_id", "scheduled_at"])
    op.create_index("ix_marketing_posts_scope_campaign", "marketing_posts", ["tenant_id", "workspace_id", "campaign_id"])
    # The publisher's discovery: scheduled posts by the time they are due.
    op.create_index(
        "ix_marketing_posts_due",
        "marketing_posts",
        [sa.text("coalesce(next_attempt_at, scheduled_at)")],
        postgresql_where=sa.text("status = 'scheduled'"),
    )

    op.create_table(
        "marketing_preferences",
        *_scope("marketing_preferences"),
        sa.Column("brand_voice", sa.Text(), nullable=False, server_default=""),
        sa.Column("timezone", sa.String(80), nullable=False, server_default="Asia/Riyadh"),
        sa.Column("default_hashtags_en", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("default_hashtags_ar", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("updated_by", sa.String(160), nullable=True),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_marketing_preferences"),
        _workspace_fk("marketing_preferences"),
    )

    for table in TABLES:
        _enable_rls(table)

    # Discovery only (ids, scope and times), never copy or tokens.
    op.execute(
        "grant select (id, tenant_id, workspace_id, status, scheduled_at, next_attempt_at) "
        "on marketing_posts to anum_maintenance"
    )
    op.execute("grant select (id, tenant_id, workspace_id) on marketing_accounts to anum_maintenance")
    # Permissive policies are OR-ed per role, so these widen access for this role only.
    op.execute(
        """
        create policy maintenance_scheduled_marketing_posts on marketing_posts
        for select to anum_maintenance
        using (status = 'scheduled')
        """
    )
    op.execute(
        """
        create policy maintenance_encrypted_marketing_tokens on marketing_accounts
        for select to anum_maintenance
        using (access_token_ciphertext is not null or refresh_token_ciphertext is not null)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_encrypted_marketing_tokens on marketing_accounts")
    op.execute("drop policy if exists maintenance_scheduled_marketing_posts on marketing_posts")
    for table in TABLES:
        op.drop_table(table)
