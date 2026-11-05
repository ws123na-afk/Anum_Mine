"""The document vault (docs/documents.md#storage-and-isolation).

Vault documents are records on top of workspace files: the bytes, versions, malware scan, quota
and signed links stay in ``workspace_files`` and object storage. This migration adds:

* ``workspace_files.vault``: null for an ordinary file, ``hidden`` for a vault file, ``assistant``
  for a vault file whose owner allowed the assistant to read it. A restrictive policy hides
  ``hidden`` rows from every query that has not set ``anum.vault`` to ``on`` (only the vault's own
  unit of work does), so the Files list, retrieval, attachments and the previous release never see
  them. The purge and maintenance roles keep seeing them (organisation deletion, malware rescan).
* ``vault_settings``: one row per workspace (reminder days, trash days, OCR switches).
* ``vault_categories``: categories the workspace adds next to the built-in ones.
* ``vault_documents``: the document record (category, title, dates, issuer, country, tags,
  description, access class, encrypted identity or account number and its last four, receipt
  fields once confirmed, retention, trash). A second restrictive policy keeps a ``private``
  document visible only to its owner and the people named in ``vault_document_access``
  (``anum.user_id``), plus the vault's daily job (``anum.vault_job``) and the purge and
  maintenance roles.
* ``vault_document_access``: the people a private document is shared with.
* ``vault_document_links``: a document's links to a vehicle, person, business, purchase or a
  finance transaction (reference only, never a posting).
* ``vault_extractions``: OCR results waiting for the person to confirm them.
* ``vault_access_log``: who opened, downloaded, changed or shared a document (ids and codes only).
* ``vault_devices``: iPhone Shortcut tokens with the ``documents`` scope (hash at rest).

Every new table is under forced RLS with the tenant and workspace policy, gets the purge grants
and policy of ``0079_tenant_lifecycle`` and is listed in ``anum_api/tenant_lifecycle_tables.py``.
``anum_maintenance`` reads scope columns only (the daily job's discovery and ``rotate_secrets``).

Expand only: one nullable column, new tables and policies. The previous release ignores them and
cannot see hidden vault files. Numbered 0133 and chained after ``0132_work_attendance``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0133_document_vault"
down_revision = "0132_work_attendance"
branch_labels = None
depends_on = None

WORKSPACE_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = (
    "vault_settings",
    "vault_categories",
    "vault_documents",
    "vault_document_access",
    "vault_document_links",
    "vault_extractions",
    "vault_access_log",
    "vault_devices",
)
PURGER = "anum_tenant_purger"
MAINTENANCE = "anum_maintenance"
JOBS = "tenant_lifecycle_jobs"
TS = sa.DateTime(timezone=True)
CATEGORY = r"^([a-z_]{2,40}|vcat_[A-Za-z0-9_-]{4,60})$"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _force_rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{table} on {table}
        using (
          {WORKSPACE_PREDICATE}
        )
        with check (
          {WORKSPACE_PREDICATE}
        )
        """
    )


def _purge_policy(table: str) -> None:
    covers = f"(j.kind = 'organisation_deletion' or (j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id))"
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to {PURGER}")
    op.execute(
        f"""
        create policy lifecycle_purge_{table} on {table}
        for all to {PURGER}
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and exists (
            select 1 from {JOBS} j
            where j.tenant_id = {table}.tenant_id and j.status = 'purging' and {covers}
          )
        )
        with check (false)
        """  # nosec B608 - fixed table names
    )


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def _document_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id", "document_id"],
        ["vault_documents.tenant_id", "vault_documents.workspace_id", "vault_documents.id"],
        name=f"fk_{table}_document", ondelete="CASCADE",
    )


def _scope_columns() -> list[sa.Column]:
    return [sa.Column("tenant_id", sa.String(80), nullable=False), sa.Column("workspace_id", sa.String(80), nullable=False)]


def upgrade() -> None:
    # --- vault files inside workspace_files ------------------------------------------------
    op.add_column("workspace_files", sa.Column("vault", sa.String(10), nullable=True))
    op.create_check_constraint("ck_workspace_files_vault", "workspace_files", "vault is null or vault in ('hidden', 'assistant')")
    op.execute(f"grant select (vault) on workspace_files to {PURGER}")
    op.execute(f"grant select (vault) on workspace_files to {MAINTENANCE}")
    op.execute(
        f"""
        create policy vault_files_hidden on workspace_files
        as restrictive for all
        using (
          vault is null or vault = 'assistant'
          or current_setting('anum.vault', true) = 'on'
          or current_user in ('{PURGER}', '{MAINTENANCE}')
        )
        with check (
          vault is null or vault = 'assistant'
          or current_setting('anum.vault', true) = 'on'
        )
        """
    )

    op.create_table(
        "vault_settings",
        *_scope_columns(),
        sa.Column("reminder_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("trash_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("ocr_receipts", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ocr_identity_dates", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_vault_settings"),
        sa.CheckConstraint("reminder_days between 1 and 365", name="ck_vault_settings_reminder_days"),
        sa.CheckConstraint("trash_days between 1 and 365", name="ck_vault_settings_trash_days"),
        _workspace_fk("vault_settings"),
    )
    op.create_table(
        "vault_categories",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_vault_categories"),
        sa.CheckConstraint("length(btrim(label)) > 0", name="ck_vault_categories_label"),
        _workspace_fk("vault_categories"),
    )
    op.create_index("uq_vault_categories_label", "vault_categories", ["tenant_id", "workspace_id", sa.text("lower(label)")], unique=True)

    op.create_table(
        "vault_documents",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("file_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("access_class", sa.String(10), nullable=False, server_default="private"),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("issuer", sa.String(200), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("tags", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("description", sa.String(2000), nullable=False, server_default=""),
        # Identity or account number: Fernet (ANUM_SECRETS_KEY); lists show the last four only.
        sa.Column("number_ciphertext", sa.Text(), nullable=True),
        sa.Column("number_last4", sa.String(4), nullable=True),
        # Confirmed receipt fields (merchant, date, total, currency, tax, reference, items): data only, never a posting.
        sa.Column("receipt", JSONB(), nullable=True),
        sa.Column("assistant_may_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reminder_days", sa.Integer(), nullable=True),
        # Expiry reminders already sent, as "<expiry date>:<days before>" keys.
        sa.Column("reminders_sent", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("retention_until", sa.Date(), nullable=True),
        sa.Column("source", sa.String(10), nullable=False, server_default="web"),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deleted_at", TS, nullable=True),
        sa.Column("deleted_by", sa.String(160), nullable=True),
        sa.Column("purge_after", TS, nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_vault_documents"),
        sa.CheckConstraint(_in("access_class", ("private", "workspace")), name="ck_vault_documents_access_class"),
        sa.CheckConstraint(_in("status", ("active", "deleted")), name="ck_vault_documents_status"),
        sa.CheckConstraint(_in("source", ("web", "device", "api")), name="ck_vault_documents_source"),
        sa.CheckConstraint(f"category ~ '{CATEGORY}'", name="ck_vault_documents_category"),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_vault_documents_title"),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_vault_documents_sha256"),
        sa.CheckConstraint("country is null or country ~ '^[A-Z]{2}$'", name="ck_vault_documents_country"),
        sa.CheckConstraint("number_last4 is null or number_last4 ~ '^[A-Za-z0-9]{1,4}$'", name="ck_vault_documents_last4"),
        sa.CheckConstraint("(number_ciphertext is null) = (number_last4 is null)", name="ck_vault_documents_number"),
        sa.CheckConstraint("issue_date is null or expiry_date is null or issue_date <= expiry_date", name="ck_vault_documents_dates"),
        sa.CheckConstraint("reminder_days is null or reminder_days between 1 and 365", name="ck_vault_documents_reminder_days"),
        sa.CheckConstraint("(status = 'deleted') = (deleted_at is not null and purge_after is not null)", name="ck_vault_documents_trash"),
        sa.CheckConstraint("not assistant_may_read or access_class = 'workspace'", name="ck_vault_documents_assistant"),
        sa.CheckConstraint("row_version > 0", name="ck_vault_documents_row_version"),
        _workspace_fk("vault_documents"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "file_id"], ["workspace_files.tenant_id", "workspace_files.workspace_id", "workspace_files.id"],
            name="fk_vault_documents_file",
        ),
    )
    op.create_index("uq_vault_documents_file", "vault_documents", ["tenant_id", "workspace_id", "file_id"], unique=True)
    op.create_index("ix_vault_documents_created", "vault_documents", ["tenant_id", "workspace_id", "status", "created_at", "id"])
    op.create_index("ix_vault_documents_sha256", "vault_documents", ["tenant_id", "workspace_id", "content_sha256"])
    op.create_index(
        "ix_vault_documents_expiry", "vault_documents", ["tenant_id", "workspace_id", "expiry_date"],
        postgresql_where=sa.text("status = 'active' and expiry_date is not null"),
    )

    op.create_table(
        "vault_document_access",
        *_scope_columns(),
        sa.Column("document_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("granted_by", sa.String(160), nullable=False),
        sa.Column("granted_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "document_id", "user_id", name="pk_vault_document_access"),
        _document_fk("vault_document_access"),
    )
    op.create_index("ix_vault_document_access_user", "vault_document_access", ["tenant_id", "workspace_id", "user_id"])

    op.create_table(
        "vault_document_links",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("document_id", sa.String(80), nullable=False),
        sa.Column("target_type", sa.String(30), nullable=False),
        sa.Column("target_id", sa.String(160), nullable=False),
        sa.Column("label", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(10), nullable=False, server_default="confirmed"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_vault_document_links"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "document_id", "target_type", "target_id", name="uq_vault_document_links_target"),
        sa.CheckConstraint(
            _in("target_type", ("vehicle", "person", "business", "purchase", "finance_transaction")), name="ck_vault_document_links_type"
        ),
        sa.CheckConstraint(_in("status", ("proposed", "confirmed")), name="ck_vault_document_links_status"),
        _document_fk("vault_document_links"),
    )

    op.create_table(
        "vault_extractions",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("document_id", sa.String(80), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("engine", sa.String(80), nullable=False, server_default=""),
        sa.Column("languages", sa.String(40), nullable=False, server_default=""),
        sa.Column("fields", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("items", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("warnings", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("requested_by", sa.String(160), nullable=False),
        sa.Column("decided_by", sa.String(160), nullable=True),
        sa.Column("decided_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_vault_extractions"),
        sa.CheckConstraint(_in("kind", ("receipt", "dates")), name="ck_vault_extractions_kind"),
        sa.CheckConstraint(_in("status", ("queued", "pending", "confirmed", "rejected", "failed")), name="ck_vault_extractions_status"),
        sa.CheckConstraint("(status in ('confirmed', 'rejected')) = (decided_at is not null)", name="ck_vault_extractions_decided"),
        _document_fk("vault_extractions"),
    )
    op.create_index("ix_vault_extractions_document", "vault_extractions", ["tenant_id", "workspace_id", "document_id", "created_at"])
    op.create_index(
        "ix_vault_extractions_open", "vault_extractions", ["tenant_id", "workspace_id", "created_at"],
        postgresql_where=sa.text("status in ('queued', 'pending')"),
    )

    op.create_table(
        "vault_access_log",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("document_id", sa.String(80), nullable=False),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("detail", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_vault_access_log"),
        sa.CheckConstraint("action ~ '^[a-z_.]{2,40}$'", name="ck_vault_access_log_action"),
        _document_fk("vault_access_log"),
    )
    op.create_index("ix_vault_access_log_document", "vault_access_log", ["tenant_id", "workspace_id", "document_id", "created_at"])

    op.create_table(
        "vault_devices",
        *_scope_columns(),
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("hint", sa.String(8), nullable=False),
        sa.Column("scopes", JSONB(), nullable=False, server_default=sa.text("'[\"documents\"]'::jsonb")),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("last_seen_at", TS, nullable=True),
        sa.Column("last_error", sa.String(200), nullable=True),
        sa.Column("upload_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "id", name="pk_vault_devices"),
        sa.CheckConstraint(_in("status", ("active", "revoked")), name="ck_vault_devices_status"),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_vault_devices_hash"),
        sa.CheckConstraint("(status = 'revoked') = (revoked_at is not null)", name="ck_vault_devices_revoked"),
        _workspace_fk("vault_devices"),
    )

    for table in TABLES:
        _force_rls(table)
        _purge_policy(table)

    # A private document: its owner and the people it is shared with only.
    op.execute(f"grant select (id, access_class, owner_user_id) on vault_documents to {PURGER}")
    op.execute(f"grant select (document_id, user_id) on vault_document_access to {PURGER}")
    op.execute(
        "create policy vault_documents_private on vault_documents"  # nosec B608: fixed table and role names
        f"""
        as restrictive for all
        using (
          access_class = 'workspace'
          or owner_user_id = nullif(current_setting('anum.user_id', true), '')
          or current_setting('anum.vault_job', true) = 'on'
          or current_user in ('{PURGER}', '{MAINTENANCE}')
          or exists (
            select 1 from vault_document_access a
            where a.tenant_id = vault_documents.tenant_id and a.workspace_id = vault_documents.workspace_id
              and a.document_id = vault_documents.id
              and a.user_id = nullif(current_setting('anum.user_id', true), '')
          )
        )
        with check (
          access_class = 'workspace'
          or owner_user_id = nullif(current_setting('anum.user_id', true), '')
          or current_setting('anum.vault_job', true) = 'on'
        )
        """
    )

    # The daily job finds workspaces with due reminders, trash or retention; rotate_secrets finds
    # documents with a number. Scope and date columns only, never titles or numbers.
    op.execute(
        f"grant select (tenant_id, workspace_id, id, owner_user_id, access_class, status, expiry_date, purge_after, retention_until) "
        f"on vault_documents to {MAINTENANCE}"
    )
    op.execute(f"grant select (tenant_id, workspace_id, document_id, user_id) on vault_document_access to {MAINTENANCE}")
    op.execute(
        f"""
        create policy maintenance_vault_documents on vault_documents
        for select to {MAINTENANCE}
        using (true)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_vault_documents on vault_documents")
    op.execute("drop policy if exists vault_documents_private on vault_documents")
    for table in reversed(TABLES):
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
    for table in ("vault_devices", "vault_access_log", "vault_extractions", "vault_document_links", "vault_document_access",
                  "vault_documents", "vault_categories", "vault_settings"):
        op.drop_table(table)
    op.execute("drop policy if exists vault_files_hidden on workspace_files")
    op.drop_constraint("ck_workspace_files_vault", "workspace_files", type_="check")
    op.drop_column("workspace_files", "vault")
