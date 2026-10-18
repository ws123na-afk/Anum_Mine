"""Email Center phase 3 (part A): shared mailboxes, sender identities and contacts (docs/email.md#email-center).

* ``mail_account_grants``: the owner of a mailbox shares it with another member of the
  workspace, to read (``read``) or to read and send (``send``). A grant names the account's
  owner and is tied to the account by a foreign key on ``(tenant, workspace, id, owner)``,
  so nobody can write a grant for a mailbox that is not theirs.
* ``mail_identities``: the sender addresses (aliases) and names a mailbox may send as,
  each with an optional signature; one may be the default.
* ``mail_contacts``: each person's own address book.
* ``mail_drafts.identity_id``: which identity a message is sent as (empty: the mailbox address).

Row policies (all tables forced RLS, the standard tenant and workspace policy, plus):

* ``mail_accounts``: the restrictive owner policy is split by command. Reading admits the
  owner, ``anum_maintenance`` (key rotation, as before), ``anum_platform_reader`` (the operator
  view of ``0064_mail_operator_view``, as before) and people the owner granted access;
  inserting, updating and deleting stay owner only.
* ``mail_account_grants``: the owner and the grantee see the row; only the owner writes it
  (the grantee may delete their own grant: leaving a shared mailbox).
* ``mail_identities``: the owner, and people granted ``send``, see the row; only the owner writes it.
* ``mail_contacts``: owner only.

The grants policy never reads ``mail_accounts`` (no policy recursion). ``anum_maintenance`` and
``anum_platform_reader`` get ``select (account_id, grantee_user_id)`` on grants because the
reading policy of ``mail_accounts`` looks there for every role that reads mailboxes.

Expand only: new tables, one nullable column, one extra unique constraint and replaced
policies whose reading side is wider; the previous release keeps filtering by owner.

Chained after ``0064_mail_operator_view`` (which made every restrictive policy on
``mail_accounts`` admit ``anum_platform_reader``; the new reading policy keeps that).
"""

import sqlalchemy as sa
from alembic import op

revision = "0066_mail_sharing_contacts"
down_revision = "0064_mail_operator_view"
branch_labels = None
depends_on = None

NEW_TABLES = ("mail_account_grants", "mail_identities", "mail_contacts")
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
ME = "nullif(current_setting('anum.user_id', true), '')"
OWNER = f"owner_user_id = {ME}"
# Literal policy text (no string building): the caller has a share of the row's mailbox.
GRANTED = (
    "exists (select 1 from mail_account_grants g where g.account_id = mail_accounts.id"
    " and g.grantee_user_id = nullif(current_setting('anum.user_id', true), ''))"
)
SEND_GRANTED = (
    "exists (select 1 from mail_account_grants g where g.account_id = mail_identities.account_id"
    " and g.grantee_user_id = nullif(current_setting('anum.user_id', true), '') and g.access = 'send')"
)
# The policy as 0064_mail_operator_view left it, restored on downgrade (0064's own downgrade
# then strips the platform reader clause again).
OLD_ACCOUNT_OWNER = f"({OWNER} or current_user = 'anum_maintenance') or current_user = 'anum_platform_reader'"


def _scope() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def _account_fk(table: str) -> sa.ForeignKeyConstraint:
    # The row's owner must be the mailbox's owner, in the same tenant and workspace.
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id", "account_id", "owner_user_id"],
        ["mail_accounts.tenant_id", "mail_accounts.workspace_id", "mail_accounts.id", "mail_accounts.owner_user_id"],
        name=f"fk_{table}_account",
        ondelete="CASCADE",
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def _policy(name: str, table: str, *, command: str | None = None, using: str | None = None, check: str | None = None, restrictive: bool = True) -> None:
    parts = [f"create policy {name} on {table}"]
    if restrictive:
        parts.append("as restrictive")
    if command:
        parts.append(f"for {command}")
    if using:
        parts.append(f"using (\n  {using}\n)")
    if check:
        parts.append(f"with check (\n  {check}\n)")
    op.execute("\n".join(parts))


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_mail_accounts_scope_owner", "mail_accounts", ["tenant_id", "workspace_id", "id", "owner_user_id"]
    )
    op.create_table(
        "mail_account_grants",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("grantee_user_id", sa.String(160), nullable=False),
        sa.Column("access", sa.String(10), nullable=False),
        *_timestamps(),
        _workspace_fk("mail_account_grants"),
        _account_fk("mail_account_grants"),
        sa.UniqueConstraint("account_id", "grantee_user_id", name="uq_mail_account_grants_grantee"),
        sa.CheckConstraint("access in ('read', 'send')", name="ck_mail_account_grants_access"),
        sa.CheckConstraint("grantee_user_id <> owner_user_id", name="ck_mail_account_grants_not_owner"),
    )
    op.create_index("ix_mail_account_grants_grantee", "mail_account_grants", ["tenant_id", "workspace_id", "grantee_user_id"])
    op.create_table(
        "mail_identities",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("address", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("signature", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        _workspace_fk("mail_identities"),
        _account_fk("mail_identities"),
        sa.UniqueConstraint("account_id", "address", name="uq_mail_identities_address"),
    )
    op.create_index(
        "uq_mail_identities_default", "mail_identities", ["account_id"], unique=True, postgresql_where=sa.text("is_default")
    )
    op.create_table(
        "mail_contacts",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("company", sa.String(200), nullable=False, server_default=""),
        sa.Column("phone", sa.String(60), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
        _workspace_fk("mail_contacts"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "email", name="uq_mail_contacts_email"),
    )
    op.create_index("ix_mail_contacts_owner_name", "mail_contacts", ["tenant_id", "workspace_id", "owner_user_id", "name", "id"])
    op.add_column(
        "mail_drafts",
        sa.Column(
            "identity_id", sa.String(80),
            sa.ForeignKey("mail_identities.id", ondelete="SET NULL", name="fk_mail_drafts_identity"), nullable=True,
        ),
    )

    for table in NEW_TABLES:
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
        _policy(f"tenant_isolation_{table}", table, using=PREDICATE, check=PREDICATE, restrictive=False)
    _policy(
        "owner_or_grantee_mail_account_grants", "mail_account_grants",
        using=f"{OWNER} or grantee_user_id = {ME}", check=OWNER,
    )
    _policy("owner_or_sender_read_mail_identities", "mail_identities", command="select", using=f"{OWNER} or {SEND_GRANTED}")
    _policy("owner_insert_mail_identities", "mail_identities", command="insert", check=OWNER)
    _policy("owner_update_mail_identities", "mail_identities", command="update", using=OWNER, check=OWNER)
    _policy("owner_delete_mail_identities", "mail_identities", command="delete", using=OWNER)
    _policy("owner_only_mail_contacts", "mail_contacts", using=OWNER, check=OWNER)

    # Mailboxes: reading widens to grantees; every write stays with the owner.
    op.execute("drop policy owner_only_mail_accounts on mail_accounts")
    _policy(
        "owner_or_grantee_read_mail_accounts", "mail_accounts", command="select",
        using=f"{OWNER}\n  or current_user = 'anum_maintenance'\n  or current_user = 'anum_platform_reader'\n  or {GRANTED}",
    )
    _policy("owner_insert_mail_accounts", "mail_accounts", command="insert", check=OWNER)
    _policy("owner_update_mail_accounts", "mail_accounts", command="update", using=OWNER, check=OWNER)
    _policy("owner_delete_mail_accounts", "mail_accounts", command="delete", using=OWNER)
    # Every role that reads mailboxes evaluates the reading policy, so each needs these two columns
    # (they see no share rows: the tenant and owner policies of the shares still apply).
    op.execute("grant select (account_id, grantee_user_id) on mail_account_grants to anum_maintenance")
    op.execute("grant select (account_id, grantee_user_id) on mail_account_grants to anum_platform_reader")


def downgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on mail_account_grants from anum_maintenance;
          end if;
          if exists (select 1 from pg_roles where rolname = 'anum_platform_reader') then
            revoke all on mail_account_grants from anum_platform_reader;
          end if;
        end
        $$
        """
    )
    for name in ("owner_or_grantee_read_mail_accounts", "owner_insert_mail_accounts", "owner_update_mail_accounts", "owner_delete_mail_accounts"):
        op.execute(f"drop policy if exists {name} on mail_accounts")
    _policy("owner_only_mail_accounts", "mail_accounts", using=OLD_ACCOUNT_OWNER, check=OWNER)
    op.drop_column("mail_drafts", "identity_id")
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
    op.drop_constraint("uq_mail_accounts_scope_owner", "mail_accounts", type_="unique")
