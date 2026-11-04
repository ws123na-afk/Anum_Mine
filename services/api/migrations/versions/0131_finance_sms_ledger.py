"""iPhone SMS ingestion and the double-entry journal (expand only). docs/finance-sms.md.

* ``finance_sms_settings``: one row per person and workspace (the on switch, ledger kind,
  retention, confirmed senders, card and account numbers, AI help consent), JSON with a version.
* ``finance_sms_devices``: device credentials. ``token_hash`` is the SHA-256 of the token's
  secret; the token itself is shown once and never stored.
* ``finance_sms_messages``: every forwarded or pasted message: masked text only, and only until
  ``body_purge_at`` (refused messages keep no text at all), what was read, the status and the
  review reason. ``idem_key`` is unique per person: a retried delivery finds its first row.
* ``finance_sms_rules``: rules the person taught from a corrected message.
* ``finance_journal_entries`` and ``finance_postings``: the journal. A constraint trigger
  (deferred to commit) refuses any entry whose postings do not sum to zero per currency, and
  another refuses any update of a posting: corrections are reversing entries.

Every table is under forced RLS with ``tenant_isolation_<table>``, a restrictive
``owner_only_<table>`` policy on ``owner_user_id = anum.user_id`` and the tenant purge policy
(``lifecycle_purge_<table>``), like ``0122_finance_ibkr_live``.

Expand only: new tables. The previous release ignores them. Chained after ``0130_action_browser``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0131_finance_sms_ledger"
down_revision = "0130_action_browser"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
TABLES = ("finance_sms_settings", "finance_sms_devices", "finance_sms_messages", "finance_sms_rules", "finance_journal_entries", "finance_postings")
TS = sa.DateTime(timezone=True)


def _owned() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace")


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"create policy owner_only_{table} on {table} as restrictive using ({OWNER_PREDICATE}) with check ({OWNER_PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to anum_tenant_purger")
    purge_policy = (f"create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger "  # nosec B608: fixed table name
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)")
    op.execute(purge_policy)


def upgrade() -> None:
    op.create_table(
        "finance_sms_settings",
        *_owned(),
        sa.Column("data", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "owner_user_id", name="pk_finance_sms_settings"),
        _workspace_fk("finance_sms_settings"),
        sa.CheckConstraint("version >= 0", name="ck_finance_sms_settings_version"),
    )

    op.create_table(
        "finance_sms_devices",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_hint", sa.String(8), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("rotated_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("last_seen_at", TS, nullable=True),
        sa.Column("last_ok_at", TS, nullable=True),
        sa.Column("last_error", sa.String(40), nullable=True),
        sa.Column("last_error_at", TS, nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        _workspace_fk("finance_sms_devices"),
        sa.CheckConstraint("status in ('active', 'paused', 'revoked')", name="ck_finance_sms_devices_status"),
        sa.CheckConstraint("char_length(token_hash) = 64", name="ck_finance_sms_devices_hash"),
        sa.CheckConstraint("(status = 'revoked') = (revoked_at is not null)", name="ck_finance_sms_devices_revoked"),
    )
    op.create_index("ix_finance_sms_devices_owner", "finance_sms_devices", ["tenant_id", "workspace_id", "owner_user_id"])

    op.create_table(
        "finance_sms_messages",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("device_id", sa.String(80), nullable=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("idem_key", sa.String(64), nullable=False),
        sa.Column("received_at", TS, nullable=False),
        sa.Column("sender", sa.String(60), nullable=False),
        sa.Column("bank_key", sa.String(40), nullable=True),
        sa.Column("body_masked", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_purge_at", TS, nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(40), nullable=True),
        sa.Column("parsed", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("rule", sa.String(100), nullable=False, server_default="none"),
        sa.Column("entry_id", sa.String(80), nullable=True),
        sa.Column("duplicate_of", sa.String(80), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("observed_balance", sa.Numeric(20, 4), nullable=True),
        sa.Column("observed_currency", sa.String(3), nullable=True),
        sa.Column("observed_account_id", sa.String(80), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_sms_messages"),
        sa.CheckConstraint("source in ('device', 'paste', 'test')", name="ck_finance_sms_messages_source"),
        sa.CheckConstraint("status in ('posted', 'review', 'duplicate', 'rejected', 'dismissed', 'failed')", name="ck_finance_sms_messages_status"),
        # A refused message (one-time code, password, promotion) never keeps its text.
        sa.CheckConstraint("status <> 'rejected' or body_masked = ''", name="ck_finance_sms_messages_rejected"),
        sa.CheckConstraint("body_masked = '' or body_purge_at is not null", name="ck_finance_sms_messages_retention"),
        sa.CheckConstraint("char_length(body_masked) <= 1200", name="ck_finance_sms_messages_length"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "idem_key", name="uq_finance_sms_messages_idem"),
    )
    op.create_index("ix_finance_sms_messages_owner", "finance_sms_messages", ["tenant_id", "workspace_id", "owner_user_id", "status", "received_at"])
    op.create_index("ix_finance_sms_messages_device", "finance_sms_messages", ["tenant_id", "workspace_id", "owner_user_id", "device_id", "created_at"])

    op.create_table(
        "finance_sms_rules",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("sender", sa.String(60), nullable=False),
        sa.Column("bank_key", sa.String(40), nullable=True),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("category", sa.String(40), nullable=True),
        sa.Column("account_id", sa.String(80), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("from_message_id", sa.String(80), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_sms_rules"),
        sa.CheckConstraint("status in ('proposed', 'active', 'disabled')", name="ck_finance_sms_rules_status"),
        sa.CheckConstraint("char_length(pattern) <= 4000", name="ck_finance_sms_rules_pattern"),
    )
    op.create_index("ix_finance_sms_rules_owner", "finance_sms_rules", ["tenant_id", "workspace_id", "owner_user_id"])

    op.create_table(
        "finance_journal_entries",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("posted_on", sa.Date(), nullable=False),
        sa.Column("occurred_at", TS, nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("category", sa.String(40), nullable=True),
        sa.Column("merchant", sa.String(60), nullable=True),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("source_ref", sa.String(80), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("reverses_id", sa.String(80), nullable=True),
        sa.Column("reversed_by_id", sa.String(80), nullable=True),
        sa.Column("original_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("original_currency", sa.String(3), nullable=True),
        sa.Column("fx_rate", sa.Numeric(24, 10), nullable=True),
        sa.Column("fx_fee", sa.Numeric(20, 4), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("reference", sa.String(30), nullable=True),
        sa.Column("reconciled_at", TS, nullable=True),
        sa.Column("reconciled_ref", sa.String(200), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_journal_entries"),
        sa.CheckConstraint("source in ('sms', 'correction', 'reversal', 'clearing')", name="ck_finance_journal_entries_source"),
        sa.CheckConstraint("status in ('posted', 'reversed')", name="ck_finance_journal_entries_status"),
        sa.CheckConstraint("(status = 'reversed') = (reversed_by_id is not null)", name="ck_finance_journal_entries_reversed"),
        sa.CheckConstraint("(original_amount is null) = (original_currency is null)", name="ck_finance_journal_entries_fx"),
    )
    op.create_index("ix_finance_journal_entries_owner", "finance_journal_entries", ["tenant_id", "workspace_id", "owner_user_id", "posted_on"])
    op.create_index("ix_finance_journal_entries_fp", "finance_journal_entries", ["tenant_id", "workspace_id", "owner_user_id", "fingerprint"])

    op.create_table(
        "finance_postings",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("entry_id", sa.String(80), nullable=False),
        sa.Column("ledger_account", sa.String(120), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=True),
        sa.Column("transaction_id", sa.String(80), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_postings"),
        sa.ForeignKeyConstraint(["entry_id"], ["finance_journal_entries.id"], name="fk_finance_postings_entry", ondelete="CASCADE"),
        sa.CheckConstraint("amount <> 0", name="ck_finance_postings_nonzero"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_finance_postings_currency"),
        sa.CheckConstraint(
            "ledger_account ~ '^(asset|liability|income|expense|equity|suspense):[A-Za-z0-9_-]{1,100}$'", name="ck_finance_postings_account"),
    )
    op.create_index("ix_finance_postings_entry", "finance_postings", ["entry_id"])
    op.create_index("ix_finance_postings_owner", "finance_postings", ["tenant_id", "workspace_id", "owner_user_id", "ledger_account"])

    # Every entry balances per currency, checked when the transaction commits.
    op.execute("""
        create function finance_postings_balanced() returns trigger language plpgsql as $$
        declare
            target text := coalesce(new.entry_id, old.entry_id);
        begin
            if exists (select 1 from finance_journal_entries e where e.id = target)
               and exists (select 1 from finance_postings p where p.entry_id = target group by p.currency having sum(p.amount) <> 0) then
                raise exception 'finance journal entry % does not balance', target using errcode = '23514';
            end if;
            if exists (select 1 from finance_journal_entries e where e.id = target)
               and (select count(*) from finance_postings p where p.entry_id = target) < 2 then
                raise exception 'finance journal entry % needs at least two postings', target using errcode = '23514';
            end if;
            return null;
        end $$
    """)
    op.execute("""
        create constraint trigger finance_postings_balanced after insert or delete on finance_postings
        deferrable initially deferred for each row execute function finance_postings_balanced()
    """)
    # Postings are written once: a correction is a reversing entry, never an edit.
    op.execute("""
        create function finance_postings_immutable() returns trigger language plpgsql as $$
        begin
            raise exception 'finance postings cannot be changed; post a reversing entry' using errcode = '55000';
        end $$
    """)
    op.execute("create trigger finance_postings_immutable before update on finance_postings for each row execute function finance_postings_immutable()")

    for table in TABLES:
        _rls(table)


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute("drop trigger if exists finance_postings_immutable on finance_postings")
    op.execute("drop trigger if exists finance_postings_balanced on finance_postings")
    for table in reversed(TABLES):
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.drop_table(table)
    op.execute("drop function if exists finance_postings_immutable()")
    op.execute("drop function if exists finance_postings_balanced()")
