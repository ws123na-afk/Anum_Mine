"""Business: the internal sales MVP (docs/business.md).

Sixteen workspace tables under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id``
(``tenant_isolation_<table>``, the 0040 pattern):

* ``business_products`` and ``business_offers``: the catalog. Products and service packages
  with their maturity; offers are priced versions with cost assumptions (one active version
  per product, unique version numbers).
* ``business_accounts`` and ``business_contacts``: organizations and the people at them
  that were lawfully obtained, with a consent basis and a do-not-contact flag. One account
  per website domain, one contact per e-mail address in a workspace (duplicate detection).
* ``business_leads``: the lead state machine, the six-criterion score and the time each
  state was first reached (the funnel reads them). At most one open lead per account and
  product.
* ``business_evidence`` (public source facts: URL, title, date, excerpt, confidence) and
  ``business_inferences`` (conclusions with the evidence they rest on), kept apart.
* ``business_activities``: the activity trail, idempotent per key.
* ``business_opportunities``: the pipeline, with value and probability as assumptions.
* ``business_proposals`` and ``business_proposal_versions``: versions with a content hash;
  the accepted version, its hash and the approval that accepted it.
* ``business_projects`` and ``business_milestones``: delivery tracking from an accepted
  proposal (one project per proposal).
* ``business_drafts``: outbound message drafts. There is no send path in this release.
* ``business_research_runs`` and ``business_settings``: the research skill's runs (the daily
  caps count them) and the workspace's caps.

References between these tables carry the scope (``(tenant_id, workspace_id, <id>)``
composite foreign keys), so a row can never point into another tenant or workspace even
though a foreign key check itself ignores row policies. No role is granted anything new and
no discovery role reads these tables.

Expand only: new tables. The previous release ignores them.

Numbered 0070 and chained after ``0069_mail_writers_uidvalidity``, the head of ``main``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0070_business"
down_revision = "0069_mail_writers_uidvalidity"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = (
    "business_settings",
    "business_products",
    "business_offers",
    "business_accounts",
    "business_contacts",
    "business_leads",
    "business_evidence",
    "business_inferences",
    "business_activities",
    "business_opportunities",
    "business_proposals",
    "business_proposal_versions",
    "business_projects",
    "business_milestones",
    "business_drafts",
    "business_research_runs",
)
TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(14, 2)
PCT = sa.Numeric(5, 2)
CURRENCIES = ("SAR", "AED", "USD")
SOURCES = ("manual", "csv_import", "research", "referral", "inbound")
CONFIDENCE = ("low", "medium", "high")
LEAD_STATES = ("discovered", "researched", "qualified", "contacted", "replied", "meeting", "offered", "won", "lost", "suppressed")
OPEN_LEAD_STATES = ("discovered", "researched", "qualified", "contacted", "replied", "meeting", "offered")
STAGES = ("discovery", "qualified", "demo", "quote", "negotiation", "won", "lost")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _jsonb(name: str, default: str = "'[]'::jsonb") -> sa.Column:
    return sa.Column(name, postgresql.JSONB(), nullable=False, server_default=sa.text(default))


def _base(table: str) -> list:  # type: ignore[type-arg]
    return [
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"),
        # The target of the scoped foreign keys below.
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name=f"uq_{table}_scope_id"),
    ]


def _ref(table: str, column: str, target: str, *, cascade: bool) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id", column],
        [f"{target}.tenant_id", f"{target}.workspace_id", f"{target}.id"],
        name=f"fk_{table}_{column}",
        ondelete="CASCADE" if cascade else "RESTRICT",
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


def _scope_index(table: str, column: str = "updated_at") -> None:
    op.create_index(f"ix_{table}_scope_{column}", table, ["tenant_id", "workspace_id", column, "id"])


def upgrade() -> None:
    op.create_table(
        "business_settings",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("research_runs_per_day", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("research_searches_per_day", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("updated_by", sa.String(160), nullable=True),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_business_settings_workspace"),
        sa.CheckConstraint("research_runs_per_day between 0 and 100", name="ck_business_settings_runs"),
        sa.CheckConstraint("research_searches_per_day between 0 and 500", name="ck_business_settings_searches"),
    )

    op.create_table(
        "business_products",
        *_base("business_products"),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("name_ar", sa.String(160), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("maturity", sa.String(20), nullable=False),
        _jsonb("integrations"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.CheckConstraint(_in("kind", ("product", "service_package")), name="ck_business_products_kind"),
        sa.CheckConstraint(_in("maturity", ("idea", "prototype", "pilot", "live", "retired")), name="ck_business_products_maturity"),
    )
    _scope_index("business_products")

    op.create_table(
        "business_offers",
        *_base("business_offers"),
        sa.Column("product_id", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("price_type", sa.String(20), nullable=False),
        sa.Column("price_min", MONEY, nullable=True),
        sa.Column("price_max", MONEY, nullable=True),
        _jsonb("deliverables"),
        _jsonb("limitations"),
        _jsonb("cost", "'{}'::jsonb"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("activated_at", TS, nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_offers", "product_id", "business_products", cascade=True),
        sa.UniqueConstraint("tenant_id", "workspace_id", "product_id", "version", name="uq_business_offers_version"),
        sa.CheckConstraint(_in("status", ("draft", "active", "retired")), name="ck_business_offers_status"),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_business_offers_currency"),
        sa.CheckConstraint(_in("price_type", ("fixed", "from", "range", "hourly", "quote")), name="ck_business_offers_price_type"),
        sa.CheckConstraint("version >= 1", name="ck_business_offers_version"),
        sa.CheckConstraint("price_min is null or price_min >= 0", name="ck_business_offers_price_min"),
        sa.CheckConstraint("price_max is null or price_min is null or price_max >= price_min", name="ck_business_offers_price_range"),
    )
    op.create_index(
        "uq_business_offers_one_active", "business_offers", ["tenant_id", "workspace_id", "product_id"],
        unique=True, postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "business_accounts",
        *_base("business_accounts"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_key", sa.String(200), nullable=False),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("domain", sa.String(253), nullable=True),
        sa.Column("industry", sa.String(120), nullable=False, server_default=""),
        sa.Column("country", sa.String(80), nullable=False, server_default=""),
        sa.Column("city", sa.String(80), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("owner_id", sa.String(160), nullable=False),
        sa.Column("do_not_contact", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dnc_reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.CheckConstraint(_in("source", SOURCES), name="ck_business_accounts_source"),
    )
    _scope_index("business_accounts")
    op.create_index("ix_business_accounts_name_key", "business_accounts", ["tenant_id", "workspace_id", "name_key"])
    op.create_index(
        "uq_business_accounts_domain", "business_accounts", ["tenant_id", "workspace_id", "domain"],
        unique=True, postgresql_where=sa.text("domain is not null"),
    )

    op.create_table(
        "business_contacts",
        *_base("business_contacts"),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("role_title", sa.String(160), nullable=False, server_default=""),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("profile_url", sa.String(500), nullable=True),
        sa.Column("consent_basis", sa.String(40), nullable=False),
        sa.Column("consent_note", sa.String(500), nullable=False, server_default=""),
        sa.Column("consent_recorded_at", TS, nullable=True),
        sa.Column("do_not_contact", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dnc_reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_contacts", "account_id", "business_accounts", cascade=True),
        sa.CheckConstraint(
            _in("consent_basis", ("unknown", "consent_given", "inbound_inquiry", "existing_relationship", "referral", "public_business_channel")),
            name="ck_business_contacts_consent",
        ),
        sa.CheckConstraint(_in("source", SOURCES), name="ck_business_contacts_source"),
    )
    op.create_index("ix_business_contacts_account", "business_contacts", ["tenant_id", "workspace_id", "account_id"])
    op.create_index(
        "uq_business_contacts_email", "business_contacts", ["tenant_id", "workspace_id", "email"],
        unique=True, postgresql_where=sa.text("email is not null"),
    )

    lead_stamps = [sa.Column(name, TS, nullable=True) for name in (
        "last_verified_at", "researched_at", "qualified_at", "contacted_at", "replied_at", "meeting_at", "offered_at", "won_at", "lost_at", "suppressed_at",
    )]
    op.create_table(
        "business_leads",
        *_base("business_leads"),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("contact_id", sa.String(80), nullable=True),
        sa.Column("product_id", sa.String(80), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("owner_id", sa.String(160), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        _jsonb("score", "'{}'::jsonb"),
        sa.Column("score_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("score_known", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_step", sa.String(300), nullable=False, server_default=""),
        sa.Column("lost_reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("suppressed_reason", sa.String(300), nullable=False, server_default=""),
        *lead_stamps,
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_leads", "account_id", "business_accounts", cascade=False),
        sa.CheckConstraint(_in("state", LEAD_STATES), name="ck_business_leads_state"),
        sa.CheckConstraint(_in("source", SOURCES), name="ck_business_leads_source"),
        sa.CheckConstraint("score_total between 0 and 30", name="ck_business_leads_score_total"),
        sa.CheckConstraint("score_known between 0 and 6", name="ck_business_leads_score_known"),
    )
    _scope_index("business_leads")
    _scope_index("business_leads", "created_at")
    op.create_index("ix_business_leads_account", "business_leads", ["tenant_id", "workspace_id", "account_id"])
    op.execute(
        "create unique index uq_business_leads_open on business_leads "
        "(tenant_id, workspace_id, account_id, coalesce(product_id, '')) "
        f"where {_in('state', OPEN_LEAD_STATES)}"
    )

    op.create_table(
        "business_evidence",
        *_base("business_evidence"),
        sa.Column("lead_id", sa.String(80), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_key", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("published_on", sa.Date(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.String(10), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("research_run_id", sa.String(80), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_evidence", "lead_id", "business_leads", cascade=True),
        sa.UniqueConstraint("tenant_id", "workspace_id", "lead_id", "url_key", name="uq_business_evidence_url"),
        sa.CheckConstraint(_in("confidence", CONFIDENCE), name="ck_business_evidence_confidence"),
        sa.CheckConstraint(_in("source", SOURCES), name="ck_business_evidence_source"),
        sa.CheckConstraint("url ~ '^https?://'", name="ck_business_evidence_url"),
    )

    op.create_table(
        "business_inferences",
        *_base("business_inferences"),
        sa.Column("lead_id", sa.String(80), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("criterion", sa.String(40), nullable=True),
        sa.Column("suggested_score", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.String(10), nullable=False),
        _jsonb("basis_evidence_ids"),
        sa.Column("generator", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reviewed_by", sa.String(160), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_inferences", "lead_id", "business_leads", cascade=True),
        sa.CheckConstraint(_in("confidence", CONFIDENCE), name="ck_business_inferences_confidence"),
        sa.CheckConstraint(_in("status", ("proposed", "accepted", "rejected")), name="ck_business_inferences_status"),
        sa.CheckConstraint(_in("generator", ("human", "template", "model")), name="ck_business_inferences_generator"),
        sa.CheckConstraint("suggested_score is null or suggested_score between 0 and 5", name="ck_business_inferences_score"),
    )

    op.create_table(
        "business_activities",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("lead_id", sa.String(80), nullable=True),
        sa.Column("account_id", sa.String(80), nullable=True),
        sa.Column("opportunity_id", sa.String(80), nullable=True),
        sa.Column("proposal_id", sa.String(80), nullable=True),
        sa.Column("project_id", sa.String(80), nullable=True),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        _jsonb("metadata", "'{}'::jsonb"),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_business_activities_workspace"),
    )
    op.create_index("ix_business_activities_lead", "business_activities", ["tenant_id", "workspace_id", "lead_id", "created_at"])
    op.create_index("ix_business_activities_scope_created", "business_activities", ["tenant_id", "workspace_id", "created_at", "id"])
    op.create_index(
        "uq_business_activities_key", "business_activities", ["tenant_id", "workspace_id", "idempotency_key"],
        unique=True, postgresql_where=sa.text("idempotency_key is not null"),
    )

    op.create_table(
        "business_opportunities",
        *_base("business_opportunities"),
        sa.Column("lead_id", sa.String(80), nullable=True),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("product_id", sa.String(80), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("value_amount", MONEY, nullable=True),
        sa.Column("probability_pct", PCT, nullable=True),
        sa.Column("assumption_note", sa.String(500), nullable=False, server_default=""),
        sa.Column("next_step", sa.String(300), nullable=False, server_default=""),
        sa.Column("expected_decision_on", sa.Date(), nullable=True),
        sa.Column("last_interaction_at", TS, nullable=True),
        sa.Column("lost_reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("won_at", TS, nullable=True),
        sa.Column("lost_at", TS, nullable=True),
        sa.Column("owner_id", sa.String(160), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_opportunities", "account_id", "business_accounts", cascade=False),
        sa.CheckConstraint(_in("stage", STAGES), name="ck_business_opportunities_stage"),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_business_opportunities_currency"),
        sa.CheckConstraint("value_amount is null or value_amount >= 0", name="ck_business_opportunities_value"),
        sa.CheckConstraint("probability_pct is null or probability_pct between 0 and 100", name="ck_business_opportunities_probability"),
    )
    _scope_index("business_opportunities")
    _scope_index("business_opportunities", "created_at")

    op.create_table(
        "business_proposals",
        *_base("business_proposals"),
        sa.Column("opportunity_id", sa.String(80), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("accepted_version", sa.Integer(), nullable=True),
        sa.Column("accepted_hash", sa.String(64), nullable=True),
        sa.Column("accepted_by", sa.String(160), nullable=True),
        sa.Column("accepted_at", TS, nullable=True),
        sa.Column("requested_hash", sa.String(64), nullable=True),
        sa.Column("requested_version", sa.Integer(), nullable=True),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("approval_task_id", sa.String(80), nullable=True),
        sa.Column("decision_note", sa.String(500), nullable=False, server_default=""),
        sa.Column("sent_at", TS, nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_proposals", "opportunity_id", "business_opportunities", cascade=False),
        sa.CheckConstraint(_in("status", ("draft", "pending_approval", "accepted", "withdrawn")), name="ck_business_proposals_status"),
        # An accepted proposal names the version, hash, person and time that accepted it.
        sa.CheckConstraint(
            "status <> 'accepted' or (accepted_version is not null and accepted_hash is not null and accepted_by is not null and accepted_at is not null)",
            name="ck_business_proposals_accepted",
        ),
    )
    _scope_index("business_proposals")

    op.create_table(
        "business_proposal_versions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("proposal_id", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("scope", sa.Text(), nullable=False, server_default=""),
        _jsonb("deliverables"),
        _jsonb("exclusions"),
        _jsonb("assumptions"),
        _jsonb("acceptance_criteria"),
        _jsonb("line_items"),
        _jsonb("milestones"),
        sa.Column("tax_rate_pct", PCT, nullable=False, server_default="0"),
        sa.Column("tax_note", sa.String(300), nullable=False, server_default=""),
        sa.Column("internal_cost", MONEY, nullable=False, server_default="0"),
        sa.Column("payment_terms", sa.Text(), nullable=False, server_default=""),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_business_proposal_versions_workspace"),
        _ref("business_proposal_versions", "proposal_id", "business_proposals", cascade=True),
        sa.UniqueConstraint("tenant_id", "workspace_id", "proposal_id", "version", name="uq_business_proposal_versions_version"),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_business_proposal_versions_currency"),
    )
    # A locked version never changes: the accepted one is immutable in the database too.
    op.execute(
        """
        create function business_proposal_version_locked() returns trigger
        language plpgsql security invoker set search_path = pg_catalog, public as $$
        begin
          if old.locked then
            raise exception 'business proposal version % is locked', old.id using errcode = 'check_violation';
          end if;
          return new;
        end;
        $$
        """
    )
    op.execute(
        "create trigger business_proposal_versions_locked before update on business_proposal_versions "
        "for each row when (old.locked) execute function business_proposal_version_locked()"
    )

    op.create_table(
        "business_projects",
        *_base("business_projects"),
        sa.Column("proposal_id", sa.String(80), nullable=False),
        sa.Column("proposal_version", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.String(80), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("contract_total", MONEY, nullable=False, server_default="0"),
        sa.Column("owner_id", sa.String(160), nullable=False),
        sa.Column("start_on", sa.Date(), nullable=True),
        sa.Column("contract_limits", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_projects", "proposal_id", "business_proposals", cascade=False),
        sa.UniqueConstraint("tenant_id", "workspace_id", "proposal_id", name="uq_business_projects_proposal"),
        sa.CheckConstraint(_in("status", ("planned", "active", "on_hold", "delivered", "closed")), name="ck_business_projects_status"),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_business_projects_currency"),
    )
    _scope_index("business_projects")

    op.create_table(
        "business_milestones",
        *_base("business_milestones"),
        sa.Column("project_id", sa.String(80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("amount", MONEY, nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("acceptance_criteria", sa.String(500), nullable=False, server_default=""),
        sa.Column("accepted_by", sa.String(160), nullable=True),
        sa.Column("accepted_at", TS, nullable=True),
        sa.Column("paid_amount", MONEY, nullable=True),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        _ref("business_milestones", "project_id", "business_projects", cascade=True),
        sa.CheckConstraint(_in("status", ("planned", "in_progress", "submitted", "accepted", "blocked")), name="ck_business_milestones_status"),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_business_milestones_currency"),
        sa.CheckConstraint("paid_amount is null or paid_amount >= 0", name="ck_business_milestones_paid"),
    )
    op.create_index("ix_business_milestones_project", "business_milestones", ["tenant_id", "workspace_id", "project_id", "position"])

    op.create_table(
        "business_drafts",
        *_base("business_drafts"),
        sa.Column("lead_id", sa.String(80), nullable=False),
        sa.Column("contact_id", sa.String(80), nullable=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("generator", sa.String(20), nullable=False),
        sa.Column("untrusted_source", sa.Boolean(), nullable=False, server_default=sa.false()),
        _jsonb("issues"),
        sa.Column("approved_hash", sa.String(64), nullable=True),
        sa.Column("approved_by", sa.String(160), nullable=True),
        sa.Column("approved_at", TS, nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_drafts", "lead_id", "business_leads", cascade=True),
        sa.CheckConstraint(_in("channel", ("email", "whatsapp", "linkedin", "call_script")), name="ck_business_drafts_channel"),
        sa.CheckConstraint(_in("language", ("en", "ar")), name="ck_business_drafts_language"),
        sa.CheckConstraint(_in("status", ("draft", "approved", "discarded")), name="ck_business_drafts_status"),
        sa.CheckConstraint(_in("generator", ("template", "model", "human")), name="ck_business_drafts_generator"),
    )

    op.create_table(
        "business_research_runs",
        *_base("business_research_runs"),
        sa.Column("lead_id", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("queries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("results", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_added", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inferences_added", sa.Integer(), nullable=False, server_default="0"),
        _jsonb("steps"),
        sa.Column("error", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_research_runs", "lead_id", "business_leads", cascade=True),
        sa.CheckConstraint(_in("status", ("succeeded", "partial", "failed", "blocked")), name="ck_business_research_runs_status"),
    )
    _scope_index("business_research_runs", "created_at")

    for table in TABLES:
        _enable_rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("drop function if exists business_proposal_version_locked()")
