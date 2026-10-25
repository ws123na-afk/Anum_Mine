"""Every tenant table, for the organisation export and purge (docs/organisation-lifecycle.md).

Each entry names a table with a ``tenant_id`` column, the domain folder its rows go to in
an export, the column that holds its workspace (``None`` for organisation-wide tables,
``"id"`` for ``workspaces`` itself) and, for the two tables whose rows point at bytes in
object storage, the column holding the object key.

``tests/test_tenant_lifecycle.py`` replays the migrations and fails when a table with a
``tenant_id`` column is missing here (or listed here but gone); the PostgreSQL test does
the same against ``information_schema``. A new tenant table therefore needs an entry here
and, in its migration, the purge policy and grants of migration ``0079_tenant_lifecycle``.

The order is a safe delete order (children before parents) used by the in-memory backend
and as the fallback; with PostgreSQL the purge orders tables from the foreign keys in the
catalog, so a new foreign key never breaks it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TenantTable:
    name: str
    domain: str
    workspace_column: str | None = "workspace_id"
    storage_key_column: str | None = None


# The job table of this feature: never exported, never purged by the purge role (the
# application role changes it, and the deletion job row stays as the receipt).
LIFECYCLE_JOBS_TABLE = "tenant_lifecycle_jobs"
# The receipt the platform console lists after a deletion (ids, dates and counts only):
# never exported, never purged.
DELETED_ORGANIZATIONS_TABLE = "deleted_organizations"
RECEIPT_TABLES = frozenset({LIFECYCLE_JOBS_TABLE, DELETED_ORGANIZATIONS_TABLE})

# Organisation-wide tables (no workspace_id): a workspace deletion leaves them alone.
_TENANT_WIDE = frozenset(
    {
        "agent_emergency_stops",
        "billing_plans",
        "billing_settings",
        "billing_accounts",
        "billing_subscriptions",
        "billing_seats",
        "billing_usage",
        "billing_invoices",
        "billing_invoice_lines",
        "billing_credit_notes",
        "billing_payments",
        "billing_number_sequences",
        "billing_webhook_events",
        "billing_signups",
        "approval_rules",
        "autonomy_guardrails",
        "contact_suppressions",
        "finance_exec_global",
        "home_profiles",
        "marketplace_packages",
        "memory_governance",
        "platform_organizations",
        "policy_packs",
        "role_templates",
        "routing_targets",
        "setup_plan_checks",
        "skill_publisher_keys",
        "skill_signing_settings",
        "skill_versions",
        "tenant_branding",
        "user_preferences",
        LIFECYCLE_JOBS_TABLE,
        DELETED_ORGANIZATIONS_TABLE,
    }
)

_DOMAINS = (
    ("app_call", "calls"),
    ("call_", "calls"),
    ("mail_", "mail"),
    ("email_", "mail"),
    ("finance_", "finance"),
    ("business_", "business"),
    ("billing_", "billing"),
    ("brevo_", "marketing"),
    ("marketing_", "marketing"),
    ("sales_", "sales"),
    ("contact_suppressions", "marketing"),
    ("commerce_", "commerce"),
    ("tyre_", "tyre"),
    ("office_", "office"),
    ("whatsapp_", "whatsapp"),
    ("autonomy_", "autonomy"),
    ("workspace_autonomy", "autonomy"),
    ("learning_", "learning"),
    ("agent_meeting_", "meetings"),
    ("meeting", "meetings"),
    ("kb_", "library"),
    ("reports", "library"),
    ("favorites", "library"),
    ("work_item", "team"),
    ("team_", "team"),
    ("user_notifications", "team"),
    ("assistant_profiles", "team"),
    ("agent_profiles", "agents"),
    ("agent_cost_caps", "agents"),
    ("agent_usage_monthly", "agents"),
    ("agent_emergency_stops", "agents"),
    ("agent_memories", "agents"),
    ("memor", "memory"),
    ("retrieval_", "memory"),
    ("conversation", "conversations"),
    ("voice_", "voice"),
    ("automation_", "automation"),
    ("developer_", "developer"),
    ("home_", "home"),
    ("command", "commands"),
    ("quick_commands", "commands"),
    ("calendar_", "calendar"),
    ("setup_plan", "setup"),
    ("integration_", "integrations"),
    ("workspace_file", "files"),
    ("audit_records", "audit"),
    ("domain_events", "audit"),
    ("tasks", "tasks"),
    ("agent_run", "tasks"),
    ("approval", "tasks"),
    ("workspace_approval", "tasks"),
    ("skill_", "governance"),
    ("policy_packs", "governance"),
    ("role_templates", "governance"),
    ("marketplace_", "governance"),
    ("routing_targets", "governance"),
    ("model_", "governance"),
    ("workspace_model", "governance"),
    ("notification_preferences", "settings"),
    ("push_devices", "settings"),
    ("user_preferences", "settings"),
    ("tenant_branding", "organisation"),
    ("platform_organizations", "organisation"),
    ("workspace", "organisation"),
    (LIFECYCLE_JOBS_TABLE, "organisation"),
    (DELETED_ORGANIZATIONS_TABLE, "organisation"),
)


def domain_of(table: str) -> str:
    """The export folder of ``table``: the first matching prefix above, else ``other``."""
    for prefix, domain in _DOMAINS:
        if table.startswith(prefix):
            return domain
    return "other"


# Children before parents. Keep every tenant table here (the tests check it).
_ORDER = (
    "billing_invoice_lines",
    "billing_credit_notes",
    "billing_payments",
    "billing_seats",
    "billing_invoices",
    "billing_subscriptions",
    "billing_usage",
    "billing_accounts",
    "billing_plans",
    "billing_settings",
    "billing_number_sequences",
    "billing_webhook_events",
    "billing_signups",
    "app_call_signals",
    "app_calls",
    "call_ai_requests",
    "call_provider_accounts",
    "call_opt_outs",
    "call_log",
    "call_contacts",
    "mail_forward_log",
    "mail_forward_rules",
    "mail_sync_state",
    "mail_account_settings",
    "mail_ops_counters",
    "mail_labels",
    "mail_attachments",
    "mail_drafts",
    "mail_contacts",
    "mail_identities",
    "mail_account_grants",
    "mail_accounts",
    "finance_agent_decisions",
    "finance_strategy_runs",
    "finance_strategies",
    "finance_market_bars",
    "finance_instruments",
    "finance_live_incidents",
    "finance_live_widenings",
    "finance_exec_global",
    "finance_exec_settings",
    "finance_exec_intents",
    "finance_paper_orders",
    "finance_paper_portfolios",
    "finance_prices",
    "finance_fx_rates",
    "finance_connections",
    "finance_guardian_changes",
    "finance_guardian_versions",
    "finance_plans",
    "finance_transactions",
    "finance_imports",
    "finance_holdings",
    "finance_bills",
    "finance_debts",
    "finance_goals",
    "finance_accounts",
    "finance_settings",
    "business_outbox",
    "business_replies",
    "business_observations",
    "business_pipeline_runs",
    "business_target_profiles",
    "business_outreach_settings",
    "business_portal_decisions",
    "business_portal_access",
    "business_payment_notices",
    "business_credit_notes",
    "business_invoice_payments",
    "business_invoices",
    "business_invoice_counters",
    "business_usage_ledger",
    "business_change_orders",
    "business_ticket_replies",
    "business_support_tickets",
    "business_research_runs",
    "business_drafts",
    "business_milestones",
    "business_projects",
    "business_proposal_versions",
    "business_proposals",
    "business_opportunities",
    "business_activities",
    "business_inferences",
    "business_evidence",
    "business_leads",
    "business_contacts",
    "business_accounts",
    "business_offers",
    "business_products",
    "business_settings",
    "sales_consent_events",
    "sales_followups",
    "sales_lead_events",
    "sales_quotes",
    "sales_leads",
    "sales_automation_settings",
    "brevo_campaign_drafts",
    "brevo_connections",
    "contact_suppressions",
    "commerce_order_lines",
    "commerce_orders",
    "commerce_listings",
    "commerce_customers",
    "commerce_returns",
    "commerce_fees",
    "commerce_alerts",
    "commerce_connectors",
    "commerce_products",
    "commerce_settings",
    "tyre_reports",
    "tyre_alerts",
    "tyre_snapshots",
    "tyre_connections",
    "commands",
    "quick_commands",
    "whatsapp_messages",
    "whatsapp_conversations",
    "whatsapp_contacts",
    "whatsapp_accounts",
    "email_drafts",
    "email_messages",
    "email_oauth_requests",
    "email_accounts",
    "autonomy_recipients",
    "autonomy_actions",
    "autonomy_proposals",
    "autonomy_rules",
    "workspace_autonomy_settings",
    "autonomy_guardrails",
    "learning_suggestions",
    "learning_notes",
    "learning_routines",
    "learning_preferences",
    "learning_signals",
    "learning_profiles",
    "agent_meeting_usage_monthly",
    "agent_meeting_budgets",
    "agent_meeting_files",
    "agent_meeting_attendance",
    "agent_meeting_output_links",
    "agent_meeting_outputs",
    "agent_meeting_agenda",
    "agent_meeting_events",
    "agent_meeting_turn_leases",
    "agent_meeting_turns",
    "agent_meeting_participants",
    "agent_meeting_sessions",
    "meeting_transcript_segments",
    "meetings",
    "meeting_connector_accounts",
    "calendar_events",
    "calendar_connections",
    "calendar_oauth_requests",
    "demo_records",
    "home_insight_states",
    "home_pins",
    "home_profiles",
    "kb_items",
    "kb_collections",
    "reports",
    "favorites",
    "user_notifications",
    "team_activity",
    "work_item_attachments",
    "work_item_checklist",
    "work_item_comments",
    "work_items",
    "assistant_profiles",
    "team_member_profiles",
    "platform_organizations",
    "tenant_branding",
    "marketing_posts",
    "marketing_campaigns",
    "marketing_accounts",
    "marketing_preferences",
    "agent_usage_monthly",
    "agent_cost_caps",
    "agent_emergency_stops",
    "agent_memories",
    "setup_plan_checks",
    "setup_plan_items",
    "executive_grants",
    "campus_workspace_profiles",
    "office_agent_provisions",
    "office_desk_staffing",
    "office_create_keys",
    "office_desks",
    "office_rooms",
    "office_floors",
    "office_buildings",
    "agent_profiles",
    "retrieval_chunks",
    "retrieval_sources",
    "conversation_turns",
    "conversations",
    "voice_transcript_segments",
    "voice_sessions",
    "developer_requests",
    "developer_projects",
    "automation_runs",
    "automation_triggers",
    "automation_schedules",
    "automation_workflows",
    "model_usage_monthly",
    "model_budgets",
    "workspace_approval_policies",
    "notification_preferences",
    "push_devices",
    "user_preferences",
    "workspace_file_versions",
    "workspace_files",
    "integration_credentials",
    "integration_health_checks",
    "integration_configurations",
    "routing_targets",
    "marketplace_installs",
    "marketplace_packages",
    "memory_governance",
    "approval_rules",
    "role_templates",
    "policy_packs",
    "skill_installations",
    "skill_versions",
    "skill_publisher_keys",
    "skill_signing_settings",
    "audit_records",
    "workspace_invitations",
    "workspace_model_configs",
    "memories",
    "domain_events",
    "approval_approvers",
    "approvals",
    "agent_run_steps",
    "agent_runs",
    "tasks",
    "workspace_memberships",
    "workspaces",
    LIFECYCLE_JOBS_TABLE,
    DELETED_ORGANIZATIONS_TABLE,
)

_STORAGE_KEYS = {
    "workspace_files": "storage_key",
    "workspace_file_versions": "storage_key",
    "mail_attachments": "storage_key",
}


def _entry(name: str) -> TenantTable:
    if name == "workspaces":
        workspace_column: str | None = "id"
    elif name in _TENANT_WIDE:
        workspace_column = None
    else:
        workspace_column = "workspace_id"
    return TenantTable(name, domain_of(name), workspace_column, _STORAGE_KEYS.get(name))


TENANT_TABLES: tuple[TenantTable, ...] = tuple(_entry(name) for name in _ORDER)
BY_NAME = {table.name: table for table in TENANT_TABLES}

# What the purge role deletes, and what the export reads: every tenant table except the
# job table (kept as the receipt and changed only by the application role).
PURGED_TABLES: tuple[TenantTable, ...] = tuple(t for t in TENANT_TABLES if t.name not in RECEIPT_TABLES)
EXPORTED_TABLES: tuple[TenantTable, ...] = PURGED_TABLES

# Removed before anything else, so nobody can open the organisation (or workspace) while
# the rest is deleted.
ACCESS_TABLES = ("workspace_invitations", "workspace_memberships")

if len(BY_NAME) != len(_ORDER):  # pragma: no cover - a duplicate is a coding error
    raise RuntimeError("tenant_lifecycle_tables lists a table twice")
