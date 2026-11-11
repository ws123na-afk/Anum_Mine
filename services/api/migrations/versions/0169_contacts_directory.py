"""The Contacts directory (expand only). docs/contacts.md#data-model.

Five new tables inside one tenant and workspace:

* ``contacts``: one person (name, organisation, notes, where the record came from).
* ``contact_identifiers``: the person's phone numbers (E.164) and e-mail addresses (lower case),
  one row each; the same value may belong to two people while a merge proposal is open.
* ``contact_links``: the source record a person was built from (a Phone calls contact, a Business
  contact, a WhatsApp contact), one link per source record.
* ``contact_do_not_contact``: the workspace-wide block per channel (phone, email, whatsapp, all)
  with its reason, who set it and when. The sending paths of Phone calls, the Email Center,
  WhatsApp and Business outreach consult it.
* ``contact_merge_proposals``: two people who share a phone number or an address under different
  names, open until a person merges or dismisses them.

Row level security (forced on every table): ``tenant_isolation_<table>`` on tenant and workspace,
``lifecycle_purge_<table>`` and its grants (0079's pattern). Discovery for the daily directory
build, migration 0011's pattern, never BYPASSRLS: ``anum_maintenance`` may read ``tenant_id`` and
``workspace_id`` of ``call_contacts``, ``business_contacts`` and ``whatsapp_contacts`` (and nothing
else of them: no name, number or address) to find the workspaces that have contacts.

Expand only: new tables, policies and column grants. The previous release ignores them.
Chained after ``0168_files_folders_tags``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0169_contacts_directory"
down_revision = "0168_files_folders_tags"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"
SCOPE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
CHILDREN = ("contact_identifiers", "contact_links", "contact_do_not_contact", "contact_merge_proposals")
TABLES = ("contacts", *CHILDREN)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace")


def _person_fk(table: str, column: str = "contact_id") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], ["contacts.id"], name=f"fk_{table}_{column}", ondelete="CASCADE")


def _force_rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({SCOPE}) with check ({SCOPE})")


def _purge_policy(table: str) -> None:
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to {PURGER}")
    op.execute(
        f"create policy lifecycle_purge_{table} on {table} for all to {PURGER} "  # nosec B608: fixed table name
        f"using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') and exists ("
        f"select 1 from {JOBS} j where j.tenant_id = {table}.tenant_id and j.status = 'purging' and "
        f"(j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        f"with check (false)"
    )


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("organisation", sa.String(200), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("source", ("manual", "calls", "business", "whatsapp", "merge")), name="ck_contacts_source"),
        sa.CheckConstraint("length(name) > 0", name="ck_contacts_name"),
        _workspace_fk("contacts"),
    )
    op.create_index("ix_contacts_scope_name", "contacts", ["tenant_id", "workspace_id", "name"])
    op.create_index("ix_contacts_scope_updated", "contacts", ["tenant_id", "workspace_id", "updated_at"])

    op.create_table(
        "contact_identifiers",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("contact_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("value", sa.String(320), nullable=False),
        sa.Column("label", sa.String(320), nullable=False, server_default=""),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workspace_id", "contact_id", "kind", "value", name="uq_contact_identifiers_value"),
        sa.CheckConstraint(_in("kind", ("phone", "email")), name="ck_contact_identifiers_kind"),
        sa.CheckConstraint("(kind = 'phone' and value ~ '^\\+[0-9]{8,15}$') or (kind = 'email' and value = lower(value) and position('@' in value) > 1)",
                           name="ck_contact_identifiers_normalised"),
        _workspace_fk("contact_identifiers"),
        _person_fk("contact_identifiers"),
    )
    op.create_index("ix_contact_identifiers_lookup", "contact_identifiers", ["tenant_id", "workspace_id", "kind", "value"])

    op.create_table(
        "contact_links",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("contact_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workspace_id", "kind", "source_id", name="uq_contact_links_source"),
        sa.CheckConstraint(_in("kind", ("call_contact", "business_contact", "whatsapp_contact")), name="ck_contact_links_kind"),
        _workspace_fk("contact_links"),
        _person_fk("contact_links"),
    )
    op.create_index("ix_contact_links_contact", "contact_links", ["tenant_id", "workspace_id", "contact_id"])

    op.create_table(
        "contact_do_not_contact",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("contact_id", sa.String(80), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("set_by", sa.String(160), nullable=False),
        sa.Column("set_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workspace_id", "contact_id", "channel", name="uq_contact_dnc_channel"),
        sa.CheckConstraint(_in("channel", ("phone", "email", "whatsapp", "all")), name="ck_contact_dnc_channel"),
        _workspace_fk("contact_do_not_contact"),
        _person_fk("contact_do_not_contact"),
    )

    op.create_table(
        "contact_merge_proposals",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("contact_a_id", sa.String(80), nullable=False),
        sa.Column("contact_b_id", sa.String(80), nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("matched_value", sa.String(320), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("decided_by", sa.String(160), nullable=True),
        sa.Column("decided_at", TS, nullable=True),
        sa.UniqueConstraint("tenant_id", "workspace_id", "contact_a_id", "contact_b_id", name="uq_contact_merge_pair"),
        sa.CheckConstraint("contact_a_id < contact_b_id", name="ck_contact_merge_order"),
        sa.CheckConstraint(_in("reason", ("same_phone", "same_email")), name="ck_contact_merge_reason"),
        sa.CheckConstraint(_in("status", ("open", "dismissed", "merged")), name="ck_contact_merge_status"),
        sa.CheckConstraint("(status = 'open') = (decided_by is null)", name="ck_contact_merge_decided"),
        _workspace_fk("contact_merge_proposals"),
        _person_fk("contact_merge_proposals", "contact_a_id"),
        _person_fk("contact_merge_proposals", "contact_b_id"),
    )
    op.create_index("ix_contact_merge_open", "contact_merge_proposals", ["tenant_id", "workspace_id", "status", "created_at"])

    for table in TABLES:
        _force_rls(table)
        _purge_policy(table)

    # Discovery for the daily build: the scope of every source contact, nothing else of it.
    op.execute("grant select (tenant_id, workspace_id) on call_contacts to anum_maintenance")
    op.execute("create policy maintenance_contacts_scope_call_contacts on call_contacts for select to anum_maintenance using (true)")
    op.execute("grant select (tenant_id, workspace_id) on business_contacts to anum_maintenance")
    op.execute("create policy maintenance_contacts_scope_business_contacts on business_contacts for select to anum_maintenance using (true)")
    op.execute("grant select (tenant_id, workspace_id) on whatsapp_contacts to anum_maintenance")
    op.execute("create policy maintenance_contacts_scope_whatsapp_contacts on whatsapp_contacts for select to anum_maintenance using (true)")


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_contacts_scope_call_contacts on call_contacts")
    op.execute("drop policy if exists maintenance_contacts_scope_business_contacts on business_contacts")
    op.execute("drop policy if exists maintenance_contacts_scope_whatsapp_contacts on whatsapp_contacts")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke select (tenant_id, workspace_id) on call_contacts from anum_maintenance;
            revoke select (tenant_id, workspace_id) on business_contacts from anum_maintenance;
            revoke select (tenant_id, workspace_id) on whatsapp_contacts from anum_maintenance;
          end if;
        end $$
        """
    )
    for table in reversed(TABLES):
        op.drop_table(table)
