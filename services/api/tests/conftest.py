from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from anum_api.db.repository import SqlAlchemyRepository
from anum_api.schemas import TenantContext
from anum_api.settings import settings


API_ROOT = Path(__file__).parents[1]
APP_ROLE = "anum_test_app"
FIXED_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

TENANT_A = "tenant_test_a"
TENANT_B = "tenant_test_b"
WORKSPACE_A = "workspace_test_a"
WORKSPACE_A2 = "workspace_test_a2"
WORKSPACE_B = "workspace_test_b"

TABLES_IN_DELETE_ORDER = (
    "demo_records",
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
    "business_usage_ledger",
    "business_change_orders",
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
    "meeting_transcript_segments",
    "meetings",
    "meeting_connector_accounts",
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
    "agent_memories",
    "setup_plan_checks",
    "setup_plan_items",
    "agent_profiles",
    "retrieval_chunks",
    "retrieval_sources",
    "conversation_turns",
    "conversations",
    "voice_transcript_segments",
    "voice_sessions",
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
    "tenants",
)


@pytest.fixture(autouse=True)
def _fresh_rate_limit_budget() -> Iterator[None]:
    """Give each test its own rate-limit budget.

    The API app is a module-level singleton shared by the whole suite, which sends far
    more requests per minute than one real client may. The limiter stays enabled; it is
    only emptied between tests so tests do not consume each other's budget.
    """
    main = sys.modules.get("anum_api.main")
    limiter = getattr(main.app.state, "rate_limiter", None) if main is not None else None
    if limiter is not None:
        limiter.reset()
    yield


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Live provider tests spend money and need a real key: they run only when the -m
    # expression names them (the Live model workflow). Deselected, never skipped, so the
    # no-skip integration job and normal runs do not report them at all.
    if "live_model" not in (config.getoption("markexpr", "") or ""):
        live = [item for item in items if "live_model" in item.keywords]
        if live:
            config.hook.pytest_deselected(items=live)
            items[:] = [item for item in items if "live_model" not in item.keywords]

    if os.getenv("ANUM_TEST_DATABASE_URL"):
        return

    skip = pytest.mark.skip(reason="ANUM_TEST_DATABASE_URL is not configured")
    for item in items:
        if "database" in item.keywords:
            item.add_marker(skip)


def tenant_context(
    tenant_id: str = TENANT_A,
    workspace_id: str = WORKSPACE_A,
) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id="user_test",
        roles=["owner"],
    )


def _alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def run_migration(database_url: str, direction: str, revision: str) -> None:
    original_url = settings.database_url
    settings.database_url = database_url
    try:
        migration_command = command.upgrade if direction == "upgrade" else command.downgrade
        migration_command(_alembic_config(database_url), revision)
    finally:
        settings.database_url = original_url


def grant_app_role(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                do $$
                begin
                    if not exists (select 1 from pg_roles where rolname = '{APP_ROLE}') then
                        create role {APP_ROLE} nologin;
                    end if;
                end
                $$
                """
            )
        )
        connection.execute(text(f"grant {APP_ROLE} to current_user"))
        connection.execute(text(f"grant usage on schema public to {APP_ROLE}"))
        connection.execute(
            text(f"grant select, insert, update, delete on all tables in schema public to {APP_ROLE}")
        )


@pytest.fixture(scope="session")
def test_database_url() -> str:
    value = os.getenv("ANUM_TEST_DATABASE_URL")
    if not value:
        pytest.skip("ANUM_TEST_DATABASE_URL is not configured")
    return value


@pytest.fixture(scope="session")
def database_engine(test_database_url: str) -> Iterator[Engine]:
    run_migration(test_database_url, "downgrade", "base")
    run_migration(test_database_url, "upgrade", "head")
    engine = create_engine(test_database_url, poolclass=NullPool, pool_pre_ping=True)
    grant_app_role(engine)
    yield engine
    engine.dispose()


def _clean_database(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(f"truncate table {', '.join(TABLES_IN_DELETE_ORDER)} cascade"))


@pytest.fixture
def clean_database(database_engine: Engine) -> Iterator[None]:
    _clean_database(database_engine)
    yield
    _clean_database(database_engine)


@pytest.fixture
def seed_scopes(database_engine: Engine, clean_database: None) -> None:
    with database_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into tenants (id, name) values
                    (:tenant_a, 'Tenant A'),
                    (:tenant_b, 'Tenant B')
                """
            ),
            {"tenant_a": TENANT_A, "tenant_b": TENANT_B},
        )

    workspaces = (
        (TENANT_A, WORKSPACE_A, "Workspace A"),
        (TENANT_A, WORKSPACE_A2, "Workspace A2"),
        (TENANT_B, WORKSPACE_B, "Workspace B"),
    )
    for tenant_id, workspace_id, name in workspaces:
        with database_engine.begin() as connection:
            connection.execute(text(f"set local role {APP_ROLE}"))
            connection.execute(
                text("select set_config('anum.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            connection.execute(
                text("select set_config('anum.workspace_id', :workspace_id, true)"),
                {"workspace_id": workspace_id},
            )
            connection.execute(
                text(
                    """
                    insert into workspaces (id, tenant_id, name)
                    values (:workspace_id, :tenant_id, :name)
                    """
                ),
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "name": name},
            )


SessionFactory = Callable[[TenantContext, bool], Iterator[Session]]


@pytest.fixture
def app_session(database_engine: Engine) -> Callable[..., Iterator[Session]]:
    @contextmanager
    def factory(
        context: TenantContext | None = None,
        *,
        commit: bool = False,
    ) -> Iterator[Session]:
        connection = database_engine.connect()
        transaction = connection.begin()
        session = Session(bind=connection, expire_on_commit=False)
        try:
            connection.execute(text(f"set local role {APP_ROLE}"))
            if context is not None:
                connection.execute(
                    text("select set_config('anum.tenant_id', :tenant_id, true)"),
                    {"tenant_id": context.tenant_id},
                )
                connection.execute(
                    text("select set_config('anum.workspace_id', :workspace_id, true)"),
                    {"workspace_id": context.workspace_id},
                )
            yield session
            session.flush()
            if commit:
                transaction.commit()
            else:
                transaction.rollback()
        except Exception:
            if transaction.is_active:
                transaction.rollback()
            raise
        finally:
            session.close()
            connection.close()

    return factory


@pytest.fixture
def repository_factory(
    app_session: Callable[..., Iterator[Session]],
) -> Callable[..., Iterator[SqlAlchemyRepository]]:
    @contextmanager
    def factory(
        context: TenantContext | None = None,
        *,
        commit: bool = False,
    ) -> Iterator[SqlAlchemyRepository]:
        with app_session(context, commit=commit) as session:
            yield SqlAlchemyRepository(session, created_by_user_id="user_test")

    return factory


@pytest.fixture(autouse=True)
def _quiet_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The evaluation load guard reads the real machine; tests pretend it is idle unless a
    test sets a reading itself (docs/finance-trading-intel.md#load-guard)."""
    from anum_api.finance.intel import load_guard

    monkeypatch.setattr(load_guard, "read_load", lambda: None)
    monkeypatch.setattr(load_guard, "read_free_memory", lambda: None)
