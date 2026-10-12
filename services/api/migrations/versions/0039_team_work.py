"""Team work, department assistants and customer organisations.

Team work (docs/team-work.md), all under forced RLS on ``anum.tenant_id`` and
``anum.workspace_id`` like every workspace table:

* ``team_member_profiles``: display name, email, department, weekly capacity and time
  zone of a workspace member (what "ask Ahmed ..." resolves against).
* ``work_items``: assigned work (to a person, to ANUM or to a department assistant)
  with status, priority, due date, follow-up time and a version for optimistic writes.
* ``work_item_comments``, ``work_item_checklist`` and ``work_item_attachments``
  (attachments point at ``workspace_files`` by id; the bytes stay in object storage).
* ``team_activity``: the workspace's activity feed. Append-only: select and insert
  policies only, like ``audit_records``.
* ``user_notifications``: in-app notifications with their email and push delivery state.
  Anyone in the workspace may *insert* one (an assignment notifies someone else), but
  only the recipient (``anum.user_id``) can read, update or delete it.
* ``assistant_profiles``: department assistants (allowed tools, autonomy, instructions,
  routing keywords). ``tasks.assistant_id`` (nullable) names the assistant an agent task
  runs as; the runtime then narrows tool policy to that profile.

Customer organisations (docs/customer-organisations.md):

* ``tenant_branding`` (tenant-wide name and logo) and ``platform_organizations`` (the
  platform operator's registry: plan placeholder, first admin email), both under forced
  RLS on ``anum.tenant_id``.
* ``anum_platform_reader``: NOLOGIN, read-only, column grants only. Its policies admit
  ``platform_organizations`` rows whose ``operator_user_id`` is ``anum.user_id`` and, in
  the tables it counts, only rows of those tenants, and only id, status and timestamp
  columns: never titles, prompts, bodies or names of another tenant's data.
* ``anum_tenant_directory``: NOLOGIN, read-only. Lists the organisations (tenant id and
  name) and workspaces where ``anum.user_id`` has an active membership, for the tenant
  switcher.
* ``anum_maintenance`` gains discovery of overdue or follow-up-due work items and of
  notifications whose email is still pending (ids and timestamps only).

Deployments grant both new roles to the API login ``WITH INHERIT FALSE, SET TRUE``
(infra/helm/bootstrap-database.sql): their policies span tenants and must never apply to
the application role's own queries.

Expand only: new tables, one nullable column, new roles and policies. The previous
release ignores all of them.

Chained after ``0044_commands`` (the lead re-chains on landing).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0039_team_work"
down_revision = "0042_library"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"
WORKSPACE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
USER = "user_id = nullif(current_setting('anum.user_id', true), '')"
CALLER = "nullif(current_setting('anum.user_id', true), '')"

WORKSPACE_TABLES = (
    "team_member_profiles",
    "assistant_profiles",
    "work_items",
    "work_item_comments",
    "work_item_checklist",
    "work_item_attachments",
)
TENANT_TABLES = ("tenant_branding", "platform_organizations")
OPEN_STATUSES = "('todo', 'in_progress', 'blocked')"


def _scope_columns() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"],
        ["workspaces.tenant_id", "workspaces.id"],
        name=f"fk_{table}_workspace",
    )


def _item_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id", "work_item_id"],
        ["work_items.tenant_id", "work_items.workspace_id", "work_items.id"],
        name=f"fk_{table}_item",
        ondelete="CASCADE",
    )


def _force(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")


_CREATE_ROLE = {
    # NOLOGIN; when a DBA created it already (infra/helm/bootstrap-database.sql), no-op.
    "anum_platform_reader": """
        do $$
        begin
          if not exists (select 1 from pg_roles where rolname = 'anum_platform_reader') then
            create role anum_platform_reader nologin;
          end if;
        end
        $$
        """,
    "anum_tenant_directory": """
        do $$
        begin
          if not exists (select 1 from pg_roles where rolname = 'anum_tenant_directory') then
            create role anum_tenant_directory nologin;
          end if;
        end
        $$
        """,
}


def _count_policy(table: str) -> str:
    """The platform reader's policy on a counted table: rows of the caller's organisations."""
    if table not in {"workspace_memberships", "workspaces", "workspace_invitations", "tasks", "work_items"}:
        raise ValueError(table)
    return (
        "create policy platform_reader_counts on " + table + " for select to anum_platform_reader "  # nosec B608 - fixed table names
        "using (exists (select 1 from platform_organizations p where p.tenant_id = " + table + ".tenant_id "
        "and p.operator_user_id = nullif(current_setting('anum.user_id', true), '')))"
    )


def _role(name: str) -> None:
    op.execute(_CREATE_ROLE[name])
    op.execute(f"grant usage on schema public to {name}")


def upgrade() -> None:
    # Team work ----------------------------------------------------------------------
    op.create_table(
        "team_member_profiles",
        *_scope_columns(),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("department", sa.String(40), nullable=True),
        sa.Column("job_title", sa.String(120), nullable=True),
        sa.Column("capacity_hours", sa.Integer(), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "user_id", name="pk_team_member_profiles"),
        _workspace_fk("team_member_profiles"),
        sa.CheckConstraint("capacity_hours is null or capacity_hours between 0 and 168", name="ck_team_member_profiles_capacity"),
    )
    op.create_table(
        "assistant_profiles",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("department", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("allowed_tools", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("routing_keywords", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("inbound_channels", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("autonomy", sa.String(20), nullable=False, server_default="suggest"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_assistant_profiles"),
        _workspace_fk("assistant_profiles"),
        sa.CheckConstraint("autonomy in ('observe', 'suggest', 'act_low_risk')", name="ck_assistant_profiles_autonomy"),
    )
    op.create_table(
        "work_items",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("assignee_type", sa.String(12), nullable=True),
        sa.Column("assignee_id", sa.String(160), nullable=True),
        sa.Column("created_by_user_id", sa.String(160), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("follow_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimate_hours", sa.Float(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("source_detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("agent_task_id", sa.String(80), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_work_items"),
        _workspace_fk("work_items"),
        sa.CheckConstraint("status in ('todo', 'in_progress', 'blocked', 'done', 'cancelled')", name="ck_work_items_status"),
        sa.CheckConstraint("priority in ('low', 'normal', 'high', 'urgent')", name="ck_work_items_priority"),
        sa.CheckConstraint(
            "(assignee_type is null and assignee_id is null) or "
            "(assignee_type in ('user', 'assistant', 'anum') and assignee_id is not null)",
            name="ck_work_items_assignee",
        ),
        sa.CheckConstraint("source in ('manual', 'command', 'inbound', 'follow_up')", name="ck_work_items_source"),
        sa.CheckConstraint("estimate_hours is null or (estimate_hours >= 0 and estimate_hours <= 1000)", name="ck_work_items_estimate"),
    )
    op.create_index("ix_work_items_scope_updated", "work_items", ["tenant_id", "workspace_id", "updated_at", "id"])
    op.create_index("ix_work_items_scope_assignee", "work_items", ["tenant_id", "workspace_id", "assignee_type", "assignee_id", "status"])
    op.create_index("ix_work_items_open_due", "work_items", ["due_at"], postgresql_where=sa.text(f"status in {OPEN_STATUSES}"))
    op.create_table(
        "work_item_comments",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("work_item_id", sa.String(80), nullable=False),
        sa.Column("author_type", sa.String(12), nullable=False),
        sa.Column("author_id", sa.String(160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_work_item_comments"),
        _item_fk("work_item_comments"),
        sa.CheckConstraint("author_type in ('user', 'assistant', 'anum')", name="ck_work_item_comments_author"),
    )
    op.create_index("ix_work_item_comments_item", "work_item_comments", ["tenant_id", "workspace_id", "work_item_id", "created_at"])
    op.create_table(
        "work_item_checklist",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("work_item_id", sa.String(80), nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("done_by_user_id", sa.String(160), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_work_item_checklist"),
        _item_fk("work_item_checklist"),
    )
    op.create_index("ix_work_item_checklist_item", "work_item_checklist", ["tenant_id", "workspace_id", "work_item_id", "position"])
    op.create_table(
        "work_item_attachments",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("work_item_id", sa.String(80), nullable=False),
        sa.Column("file_id", sa.String(80), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("added_by_user_id", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_work_item_attachments"),
        _item_fk("work_item_attachments"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "work_item_id", "file_id", name="uq_work_item_attachments_file"),
    )
    op.create_table(
        "team_activity",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("work_item_id", sa.String(80), nullable=True),
        sa.Column("actor_type", sa.String(12), nullable=False),
        sa.Column("actor_id", sa.String(160), nullable=False),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_team_activity"),
        _workspace_fk("team_activity"),
        sa.CheckConstraint("actor_type in ('user', 'assistant', 'anum')", name="ck_team_activity_actor"),
    )
    op.create_index("ix_team_activity_scope_created", "team_activity", ["tenant_id", "workspace_id", "created_at", "id"])
    op.create_table(
        "user_notifications",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.String(1000), nullable=False),
        sa.Column("work_item_id", sa.String(80), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_status", sa.String(20), nullable=False),
        sa.Column("email_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("email_error", sa.String(300), nullable=True),
        sa.Column("push_status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_user_notifications"),
        _workspace_fk("user_notifications"),
        sa.CheckConstraint(
            "email_status in ('not_requested', 'pending', 'sent', 'failed', 'not_configured')",
            name="ck_user_notifications_email",
        ),
        sa.CheckConstraint(
            "push_status in ('not_requested', 'not_configured', 'queued', 'sent', 'failed')", name="ck_user_notifications_push"
        ),
    )
    op.create_index("ix_user_notifications_user_created", "user_notifications", ["tenant_id", "workspace_id", "user_id", "created_at"])
    op.add_column("tasks", sa.Column("assistant_id", sa.String(80), nullable=True))

    for table in WORKSPACE_TABLES:
        _force(table)
        op.execute(
            f"""
            create policy tenant_isolation_{table} on {table}
            using (
              {WORKSPACE}
            )
            with check (
              {WORKSPACE}
            )
            """
        )
    # Append-only feed: no update or delete policy, so with RLS forced those match nothing.
    _force("team_activity")
    op.execute(f"create policy team_activity_read on team_activity for select using ({WORKSPACE})")
    op.execute(f"create policy team_activity_insert on team_activity for insert with check ({WORKSPACE})")
    # Anyone in the workspace may notify a colleague; only the recipient reads or changes it.
    _force("user_notifications")
    op.execute(f"create policy user_notifications_insert on user_notifications for insert with check ({WORKSPACE})")
    op.execute(
        f"create policy user_notifications_select on user_notifications for select using ({WORKSPACE} and {USER})"
    )
    op.execute(
        f"create policy user_notifications_update on user_notifications for update "
        f"using ({WORKSPACE} and {USER}) with check ({WORKSPACE} and {USER})"
    )
    op.execute(
        f"create policy user_notifications_delete on user_notifications for delete using ({WORKSPACE} and {USER})"
    )

    # Customer organisations ------------------------------------------------------------
    op.create_table(
        "tenant_branding",
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id", name="fk_tenant_branding_tenant"), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("logo", sa.LargeBinary(), nullable=True),
        sa.Column("logo_content_type", sa.String(40), nullable=True),
        sa.Column("updated_by_user_id", sa.String(160), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenant_branding"),
        sa.CheckConstraint("logo is null or octet_length(logo) <= 262144", name="ck_tenant_branding_logo_size"),
        sa.CheckConstraint(
            "logo_content_type is null or logo_content_type in ('image/png', 'image/jpeg', 'image/webp')",
            name="ck_tenant_branding_logo_type",
        ),
    )
    op.create_table(
        "platform_organizations",
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id", name="fk_platform_organizations_tenant"), nullable=False),
        sa.Column("operator_user_id", sa.String(160), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("plan", sa.String(40), nullable=False, server_default="trial"),
        sa.Column("first_workspace_id", sa.String(80), nullable=False),
        sa.Column("first_admin_email", sa.String(254), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_platform_organizations"),
        sa.CheckConstraint("plan in ('trial', 'standard', 'enterprise')", name="ck_platform_organizations_plan"),
    )
    op.create_index("ix_platform_organizations_operator", "platform_organizations", ["operator_user_id"])
    for table in TENANT_TABLES:
        _force(table)
        op.execute(
            f"""
            create policy tenant_isolation_{table} on {table}
            using ({TENANT})
            with check ({TENANT})
            """
        )

    # Platform reader: counts and health of the operator's organisations, never content.
    _role("anum_platform_reader")
    op.execute(
        "grant select (tenant_id, operator_user_id, display_name, plan, first_workspace_id, created_at) "
        "on platform_organizations to anum_platform_reader"
    )
    op.execute(
        f"""
        create policy platform_reader_own_organizations on platform_organizations
        for select to anum_platform_reader
        using (operator_user_id = {CALLER})
        """
    )
    counted = {
        "workspace_memberships": "tenant_id, active",
        "workspaces": "tenant_id",
        "workspace_invitations": "tenant_id, status, expires_at",
        "tasks": "tenant_id, status, updated_at",
        "work_items": "tenant_id, status, due_at, updated_at",
    }
    for table, columns in counted.items():
        op.execute(f"grant select ({columns}) on {table} to anum_platform_reader")
        op.execute(_count_policy(table))

    # Tenant directory: the caller's own organisations, for the tenant switcher.
    _role("anum_tenant_directory")
    op.execute(
        "grant select (tenant_id, workspace_id, user_id, role, active) on workspace_memberships to anum_tenant_directory"
    )
    op.execute("grant select (id, name) on tenants to anum_tenant_directory")
    op.execute("grant select (tenant_id, id, name) on workspaces to anum_tenant_directory")
    op.execute("grant select (tenant_id, display_name) on tenant_branding to anum_tenant_directory")
    op.execute(
        f"""
        create policy tenant_directory_own_rows on workspace_memberships
        for select to anum_tenant_directory
        using (user_id = {CALLER} and active)
        """
    )
    op.execute(
        """
        create policy tenant_directory_tenants on tenants
        for select to anum_tenant_directory
        using (
          exists (
            select 1 from workspace_memberships m
            where m.tenant_id = tenants.id
              and m.user_id = nullif(current_setting('anum.user_id', true), '')
              and m.active
          )
        )
        """
    )
    op.execute(
        """
        create policy tenant_directory_workspaces on workspaces
        for select to anum_tenant_directory
        using (
          exists (
            select 1 from workspace_memberships m
            where m.tenant_id = workspaces.tenant_id
              and m.workspace_id = workspaces.id
              and m.user_id = nullif(current_setting('anum.user_id', true), '')
              and m.active
          )
        )
        """
    )
    op.execute(
        """
        create policy tenant_directory_branding on tenant_branding
        for select to anum_tenant_directory
        using (
          exists (
            select 1 from workspace_memberships m
            where m.tenant_id = tenant_branding.tenant_id
              and m.user_id = nullif(current_setting('anum.user_id', true), '')
              and m.active
          )
        )
        """
    )

    # Maintenance discovery: overdue and follow-up-due work, pending notification email.
    op.execute(
        "grant select (id, tenant_id, workspace_id, status, due_at, follow_up_at, reminder_sent_at) "
        "on work_items to anum_maintenance"
    )
    op.execute(
        f"""
        create policy maintenance_due_work_items on work_items
        for select to anum_maintenance
        using (
          status in {OPEN_STATUSES}
          and ((due_at <= now() and reminder_sent_at is null) or follow_up_at <= now())
        )
        """
    )
    op.execute(
        "grant select (id, tenant_id, workspace_id, user_id, email_status, email_attempts) "
        "on user_notifications to anum_maintenance"
    )
    op.execute(
        """
        create policy maintenance_pending_email on user_notifications
        for select to anum_maintenance
        using (email_status = 'pending' and email_attempts < 3)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_pending_email on user_notifications")
    op.execute("drop policy if exists maintenance_due_work_items on work_items")
    op.execute("drop policy if exists tenant_directory_own_rows on workspace_memberships")
    op.execute("drop policy if exists tenant_directory_tenants on tenants")
    op.execute("drop policy if exists tenant_directory_workspaces on workspaces")
    for table in ("workspace_memberships", "workspaces", "workspace_invitations", "tasks", "work_items"):
        op.execute(f"drop policy if exists platform_reader_counts on {table}")
    # The roles are cluster-wide and may be granted to logins, so they stay without
    # privileges in this database (like the other NOLOGIN roles).
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_platform_reader') then
            revoke all on workspace_memberships, workspaces, workspace_invitations, tasks from anum_platform_reader;
            revoke usage on schema public from anum_platform_reader;
          end if;
          if exists (select 1 from pg_roles where rolname = 'anum_tenant_directory') then
            revoke all on workspace_memberships, workspaces, tenants from anum_tenant_directory;
            revoke usage on schema public from anum_tenant_directory;
          end if;
        end
        $$
        """
    )
    op.drop_table("platform_organizations")
    op.drop_table("tenant_branding")
    op.drop_column("tasks", "assistant_id")
    for table in (
        "user_notifications",
        "team_activity",
        "work_item_attachments",
        "work_item_checklist",
        "work_item_comments",
        "work_items",
        "assistant_profiles",
        "team_member_profiles",
    ):
        op.drop_table(table)
