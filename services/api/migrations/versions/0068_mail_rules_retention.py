"""Email Center phase 6: mailbox settings, forwarding rules and background sync (docs/email.md#mailbox-settings).

* ``mail_account_settings``: per mailbox, how many days ANUM keeps the messages written in it
  once sent (``sent_retention_days``), unsent drafts (``draft_retention_days``) and AI labels
  (``label_retention_days``; empty keeps them while the mailbox is connected), and whether the
  server checks it in the background (``background_sync``). ``version`` goes up with every
  change; a change is applied only by the approved ``change_mail_settings`` call naming the
  version it was asked against. ``pending_approval_id`` names the change waiting for approval.
* ``mail_forward_rules``: forward new inbox mail that matches (sender and/or subject contains)
  to the addresses named. ``rule_hash`` is the hash of what the rule does; it forwards only
  while ``status = 'active'`` and ``approved_hash = rule_hash`` (set by the approved
  ``activate_mail_forward_rule`` call). ``last_uid`` is the newest inbox UID already looked at.
* ``mail_forward_log``: one row per message a rule forwarded (or tried to), unique per rule
  and UID, so a message is never forwarded twice. Ids, the UID and the Message-ID only.
* ``mail_sync_state``: the background sync's last counts (inbox unread and total), when it
  checked, when it is next due and the lease of the replica checking it now.

Row policies (all forced RLS with the tenant and workspace policy, plus restrictive ones):
settings and sync state are read by the mailbox's owner, the people it is shared with and
``anum_maintenance``, and written by the owner only; rules and their log are the owner's alone
(each tied to the mailbox and its owner by a foreign key, deleted with it).

Discovery as ``anum_maintenance`` (migration 0011's pattern, never BYPASSRLS):

* background sync: ``mail_accounts.status`` (to leave refused passwords alone) and the
  scheduling columns of the sync state and settings;
* the retention purge: the retention days of the settings, and ``account_id`` and
  ``owner_user_id`` of ``mail_drafts`` and ``mail_labels`` (who wrote something in which
  mailbox), only in mailboxes with a retention and only of rows the purge may delete (never
  a message waiting for approval, scheduled or being sent), never a subject, body, address or label. For that the restrictive owner policy of
  ``mail_labels`` also admits ``current_user = 'anum_maintenance'``, as ``mail_drafts`` does.

Each piece of work is then done as the application role in the person's own scope.

Expand only: new tables, column grants and policies; the previous release ignores them.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0068_mail_rules_retention"
down_revision = "0067_mail_sharing_contacts"
branch_labels = None
depends_on = None

NEW_TABLES = ("mail_account_settings", "mail_sync_state", "mail_forward_rules", "mail_forward_log")
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
ME = "nullif(current_setting('anum.user_id', true), '')"
OWNER = f"owner_user_id = {ME}"
LABEL_OWNER_WITH_MAINTENANCE = f"{OWNER}\n          or current_user = 'anum_maintenance'"
# Literal policy text: the caller has a share (any access) of the row's mailbox.
GRANTED_SETTINGS = (
    "exists (select 1 from mail_account_grants g where g.account_id = mail_account_settings.account_id"
    " and g.grantee_user_id = nullif(current_setting('anum.user_id', true), ''))"
)
GRANTED_SYNC = (
    "exists (select 1 from mail_account_grants g where g.account_id = mail_sync_state.account_id"
    " and g.grantee_user_id = nullif(current_setting('anum.user_id', true), ''))"
)
RETENTION_CHECK = "{column} is null or ({column} >= 1 and {column} <= 3650)"


def _scope() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace")


def _account_fk(table: str) -> sa.ForeignKeyConstraint:
    # The row's owner must be the mailbox's owner, in the same tenant and workspace.
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id", "account_id", "owner_user_id"],
        ["mail_accounts.tenant_id", "mail_accounts.workspace_id", "mail_accounts.id", "mail_accounts.owner_user_id"],
        name=f"fk_{table}_account",
        ondelete="CASCADE",
    )


def _times() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def _policy(name: str, table: str, *, command: str | None = None, using: str | None = None, check: str | None = None, restrictive: bool = True, to: str | None = None) -> None:
    parts = [f"create policy {name} on {table}"]
    if restrictive:
        parts.append("as restrictive")
    if command:
        parts.append(f"for {command}")
    if to:
        parts.append(f"to {to}")
    if using:
        parts.append(f"using (\n  {using}\n)")
    if check:
        parts.append(f"with check (\n  {check}\n)")
    op.execute("\n".join(parts))


def upgrade() -> None:
    op.create_table(
        "mail_account_settings",
        sa.Column("account_id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("sent_retention_days", sa.Integer(), nullable=True),
        sa.Column("draft_retention_days", sa.Integer(), nullable=True),
        sa.Column("label_retention_days", sa.Integer(), nullable=True),
        sa.Column("background_sync", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by", sa.String(160), nullable=True),
        # The change waiting for approval, if any (its task and approval), shown on the settings.
        sa.Column("pending_approval_id", sa.String(80), nullable=True),
        sa.Column("pending_task_id", sa.String(80), nullable=True),
        *_times(),
        _workspace_fk("mail_account_settings"),
        _account_fk("mail_account_settings"),
        *[
            sa.CheckConstraint(RETENTION_CHECK.format(column=column), name=f"ck_mail_account_settings_{column}")
            for column in ("sent_retention_days", "draft_retention_days", "label_retention_days")
        ],
    )
    op.create_table(
        "mail_sync_state",
        sa.Column("account_id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("inbox_unread", sa.Integer(), nullable=True),
        sa.Column("inbox_total", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        *_times(),
        _workspace_fk("mail_sync_state"),
        _account_fk("mail_sync_state"),
        sa.CheckConstraint("status in ('pending', 'ok', 'auth_failed', 'offline', 'error')", name="ck_mail_sync_state_status"),
    )
    op.create_index("ix_mail_sync_state_due", "mail_sync_state", ["next_sync_at"])
    op.create_table(
        "mail_forward_rules",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("from_contains", sa.String(200), nullable=False, server_default=""),
        sa.Column("subject_contains", sa.String(200), nullable=False, server_default=""),
        sa.Column("forward_to", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("rule_hash", sa.String(64), nullable=False),
        sa.Column("approved_hash", sa.String(64), nullable=True),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_uid", sa.BigInteger(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("forwarded_count", sa.Integer(), nullable=False, server_default="0"),
        *_times(),
        _workspace_fk("mail_forward_rules"),
        _account_fk("mail_forward_rules"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", "owner_user_id", name="uq_mail_forward_rules_scope_owner"),
        sa.CheckConstraint("status in ('pending_approval', 'active', 'paused')", name="ck_mail_forward_rules_status"),
        sa.CheckConstraint("from_contains <> '' or subject_contains <> ''", name="ck_mail_forward_rules_condition"),
        sa.CheckConstraint("status <> 'active' or (approved_hash is not null and approved_hash = rule_hash)", name="ck_mail_forward_rules_approved"),
    )
    op.create_index("ix_mail_forward_rules_account", "mail_forward_rules", ["account_id"])
    op.create_table(
        "mail_forward_log",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("rule_id", sa.String(80), nullable=False),
        sa.Column("uid", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _workspace_fk("mail_forward_log"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "rule_id", "owner_user_id"],
            ["mail_forward_rules.tenant_id", "mail_forward_rules.workspace_id", "mail_forward_rules.id", "mail_forward_rules.owner_user_id"],
            name="fk_mail_forward_log_rule", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("rule_id", "uid", name="uq_mail_forward_log_message"),
        sa.CheckConstraint("status in ('forwarding', 'forwarded', 'failed', 'unknown')", name="ck_mail_forward_log_status"),
    )
    op.create_index("ix_mail_forward_log_recent", "mail_forward_log", ["tenant_id", "workspace_id", "owner_user_id", "created_at"])

    for table in NEW_TABLES:
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
        _policy(f"tenant_isolation_{table}", table, using=PREDICATE, check=PREDICATE, restrictive=False)
    for table, granted in (("mail_account_settings", GRANTED_SETTINGS), ("mail_sync_state", GRANTED_SYNC)):
        _policy(f"owner_or_grantee_read_{table}", table, command="select", using=f"{OWNER}\n  or current_user = 'anum_maintenance'\n  or {granted}")
        _policy(f"owner_insert_{table}", table, command="insert", check=OWNER)
        _policy(f"owner_update_{table}", table, command="update", using=OWNER, check=OWNER)
        _policy(f"owner_delete_{table}", table, command="delete", using=OWNER)
        _policy(f"maintenance_read_{table}", table, command="select", to="anum_maintenance", using="true", restrictive=False)
    _policy("owner_only_mail_forward_rules", "mail_forward_rules", using=OWNER, check=OWNER)
    _policy("owner_only_mail_forward_log", "mail_forward_log", using=OWNER, check=OWNER)

    # Discovery: scheduling and retention columns only, never content.
    op.execute("grant select (status) on mail_accounts to anum_maintenance")
    op.execute(
        "grant select (account_id, tenant_id, workspace_id, owner_user_id, background_sync, "
        "sent_retention_days, draft_retention_days, label_retention_days) on mail_account_settings to anum_maintenance"
    )
    op.execute("grant select (account_id, tenant_id, workspace_id, owner_user_id, next_sync_at, lease_until) on mail_sync_state to anum_maintenance")
    op.execute("grant select (account_id) on mail_drafts to anum_maintenance")
    op.execute(
        """
        create policy maintenance_retention_mail_drafts on mail_drafts
        for select to anum_maintenance
        using (
          status in ('sent', 'draft', 'failed', 'unknown')
          and exists (
            select 1 from mail_account_settings s where s.account_id = mail_drafts.account_id
            and (s.sent_retention_days is not null or s.draft_retention_days is not null)
          )
        )
        """
    )
    op.execute(
        f"""
        alter policy owner_only_mail_labels on mail_labels
        using (
          {LABEL_OWNER_WITH_MAINTENANCE}
        )
        with check (
          {OWNER}
        )
        """
    )
    op.execute("grant select (account_id, owner_user_id) on mail_labels to anum_maintenance")
    op.execute(
        """
        create policy maintenance_retention_mail_labels on mail_labels
        for select to anum_maintenance
        using (
          exists (
            select 1 from mail_account_settings s where s.account_id = mail_labels.account_id and s.label_retention_days is not null
          )
        )
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_retention_mail_labels on mail_labels")
    op.execute("drop policy if exists maintenance_retention_mail_drafts on mail_drafts")
    op.execute(
        f"""
        alter policy owner_only_mail_labels on mail_labels
        using (
          {OWNER}
        )
        with check (
          {OWNER}
        )
        """
    )
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke select (account_id, owner_user_id) on mail_labels from anum_maintenance;
            revoke select (account_id) on mail_drafts from anum_maintenance;
            revoke select (status) on mail_accounts from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
