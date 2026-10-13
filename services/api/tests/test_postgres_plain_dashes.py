"""Migration 0055_plain_dashes against PostgreSQL: stored names lose their long dashes.

Rows are seeded the way an earlier setup plan left them ("GROUP, em dash, Chief of
Staff"), then the migration's ``upgrade`` runs twice: as the test login (a superuser in CI)
and as a non-superuser table owner under forced RLS, the way ``anum_migrator`` runs it in
production. Audit records, ids, instructions and timestamps are never touched.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from sqlalchemy import Connection, Engine, text

pytestmark = pytest.mark.database

EM, EN = chr(0x2014), chr(0x2013)
DASHES = (EM, EN, chr(0x2015))
OWNER_ROLE = "anum_test_plain_dashes_owner"
USER = "user_plain_dashes"


def _migration() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0055_plain_dashes.py"
    spec = importlib.util.spec_from_file_location("migration_0055_plain_dashes", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _migration()


def _upgrade(connection: Connection) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        MIGRATION.upgrade()


def _seed(connection: Connection) -> None:
    rows = {
        "workspace": f"TYRE {EM} Tyre Pulse",
        "pack": f"BASE {EM} Scope & Evidence",
        "taken": f"FLEET {EM} Safety & Readings",
        "rule": f"Prices and stock {EM} owner approves",
        "agent": f"GROUP {EM} Chief of Staff",
        "command": f"Sales {EM} Due Follow-ups",
        "workflow": f"GALLERY {EM} COD Confirmation",
    }
    connection.execute(text("insert into tenants (id, name) values (:a, 'Tenant A'), (:b, 'Tenant B')"), {"a": TENANT_A, "b": TENANT_B})
    connection.execute(
        text("insert into workspaces (id, tenant_id, name) values (:w, :t, :name), (:wb, :tb, 'Workspace B')"),
        {"w": WORKSPACE_A, "t": TENANT_A, "name": rows["workspace"], "wb": WORKSPACE_B, "tb": TENANT_B},
    )
    connection.execute(
        text(
            "insert into policy_packs (id, tenant_id, name, description, version, active, rules, created_by) values "
            "('pack_base', :a, :pack, :description, 1, true, '[]', 'system'), "
            "('pack_taken', :b, :taken, '', 1, true, '[]', 'system'), "
            "('pack_new', :b, 'Fleet: Safety & Readings', '', 1, true, '[]', 'system')"
        ),
        {"a": TENANT_A, "b": TENANT_B, "pack": rows["pack"], "taken": rows["taken"],
         "description": f"Scope {EM} and evidence for 10{EN}15 days"},
    )
    connection.execute(
        text("insert into approval_rules (id, tenant_id, name, action_pattern, minimum_approvers) values ('rule_prices', :a, :rule, 'commerce.*', 1)"),
        {"a": TENANT_A, "rule": rows["rule"]},
    )
    connection.execute(
        text(
            "insert into agent_profiles (tenant_id, workspace_id, id, owner_user_id, name, icon, color, category, description, "
            "instructions, model_route, knowledge_mode, status, sharing, created_at, updated_at) values "
            "(:a, :w, 'agent_chief', :user, :agent, 'bot', 'blue', 'work', :description, :instructions, 'fast', 'none', "
            "'active', 'private', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z')"
        ),
        {"a": TENANT_A, "w": WORKSPACE_A, "user": USER, "agent": rows["agent"],
         "description": f"Runs the day {EM} briefly", "instructions": f"Keep {EM} as written"},
    )
    connection.execute(
        text("insert into quick_commands (id, tenant_id, workspace_id, user_id, title, text, category) values ('qc_due', :a, :w, :user, :title, :body, 'work')"),
        {"a": TENANT_A, "w": WORKSPACE_A, "user": USER, "title": rows["command"], "body": f"Due {EM} today"},
    )
    connection.execute(
        text("insert into automation_workflows (id, tenant_id, workspace_id, name, steps, status, version, created_by) values ('wf_cod', :a, :w, :name, '[]', 'disabled', 1, 'system')"),
        {"a": TENANT_A, "w": WORKSPACE_A, "name": rows["workflow"]},
    )
    connection.execute(
        text(
            "insert into audit_records (id, tenant_id, workspace_id, correlation_id, actor, action, target, outcome, created_at) "
            "values ('audit_dash', :a, :w, 'corr', 'system', 'setup_plan.apply', :target, 'success', now())"
        ),
        {"a": TENANT_A, "w": WORKSPACE_A, "target": rows["agent"]},
    )


def _state(connection: Connection) -> dict[str, object]:
    def one(sql: str) -> object:
        return connection.execute(text(sql)).scalar_one()

    return {
        "workspace": one(f"select name from workspaces where id = '{WORKSPACE_A}'"),
        "pack": one("select name from policy_packs where id = 'pack_base'"),
        "pack_description": one("select description from policy_packs where id = 'pack_base'"),
        "taken": one("select name from policy_packs where id = 'pack_taken'"),
        "rule": one("select name from approval_rules where id = 'rule_prices'"),
        "agent": one("select name from agent_profiles where id = 'agent_chief'"),
        "agent_description": one("select description from agent_profiles where id = 'agent_chief'"),
        "agent_instructions": one("select instructions from agent_profiles where id = 'agent_chief'"),
        "agent_updated": str(one("select updated_at from agent_profiles where id = 'agent_chief'")),
        "command": one("select title from quick_commands where id = 'qc_due'"),
        "command_text": one("select text from quick_commands where id = 'qc_due'"),
        "workflow": one("select name from automation_workflows where id = 'wf_cod'"),
        "audit": one("select target from audit_records where id = 'audit_dash'"),
    }


def _assert_plain(state: dict[str, object]) -> None:
    assert state["workspace"] == "Tyre Pulse"
    assert state["pack"] == "Base: Scope & Evidence"
    assert state["pack_description"] == "Scope, and evidence for 10-15 days"
    assert state["taken"] == f"FLEET {EM} Safety & Readings"  # the new name was taken: kept
    assert state["rule"] == "Prices and stock: owner approves"
    assert state["agent"] == "Group: Chief of Staff"
    assert state["agent_description"] == "Runs the day, briefly"
    assert state["command"] == "Sales: Due Follow-ups"
    assert state["workflow"] == "Gallery: COD Confirmation"
    # Not display names: left exactly as stored.
    assert state["agent_instructions"] == f"Keep {EM} as written"
    assert state["command_text"] == f"Due {EM} today"
    assert state["audit"] == f"GROUP {EM} Chief of Staff"


@pytest.fixture
def seeded(database_engine: Engine, clean_database: None) -> Iterator[Engine]:
    with database_engine.begin() as connection:
        _seed(connection)
    yield database_engine
    with database_engine.begin() as connection:
        for table in ("audit_records", "automation_workflows", "quick_commands", "agent_profiles", "approval_rules", "policy_packs"):
            connection.execute(text(f"delete from {table} where tenant_id in (:a, :b)"), {"a": TENANT_A, "b": TENANT_B})


def test_the_migration_renames_display_names_and_nothing_else(seeded: Engine) -> None:
    with seeded.begin() as connection:
        before = _state(connection)
        _upgrade(connection)
        after = _state(connection)
    _assert_plain(after)
    assert after["agent_updated"] == before["agent_updated"]
    # Idempotent: a second run finds nothing to change.
    with seeded.begin() as connection:
        _upgrade(connection)
        assert _state(connection) == after
        # The discovery policy never outlives the migration.
        assert connection.execute(text("select count(*) from pg_policies where policyname = :p"), {"p": MIGRATION.POLICY}).scalar_one() == 0


def test_the_migration_works_for_a_non_superuser_owner_under_forced_rls(seeded: Engine) -> None:
    """Production migrates as ``anum_migrator``: it owns the tables but RLS is forced."""
    connection = seeded.connect()
    transaction = connection.begin()
    try:
        exists = connection.execute(text("select 1 from pg_roles where rolname = :r"), {"r": OWNER_ROLE}).first()
        if not exists:
            connection.execute(text(f"create role {OWNER_ROLE} nologin nosuperuser nobypassrls"))
        connection.execute(text(f"grant {OWNER_ROLE} to current_user"))
        connection.execute(text(f"grant usage on schema public to {OWNER_ROLE}"))
        for table in MIGRATION.TABLES:
            connection.execute(text(f"alter table {table} owner to {OWNER_ROLE}"))
        connection.execute(text(f"set local role {OWNER_ROLE}"))
        # Forced RLS: without a context the owner sees no row.
        assert connection.execute(text("select count(*) from policy_packs")).scalar_one() == 0
        _upgrade(connection)
        connection.execute(text("reset role"))
        _assert_plain(_state(connection))
    finally:
        transaction.rollback()  # ownership (and this run's renames) go back
        connection.close()


def test_name_rules_match_the_blueprint_renames() -> None:
    import json

    blueprint = Path(__file__).resolve().parents[1] / "anum_api" / "blueprints" / "shahzeb_business_group.json"
    data = json.loads(blueprint.read_text(encoding="utf-8"))
    checked = 0
    for section, items in data.items():
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            for former in item.get("former_names") or []:
                table = "workspaces" if section == "workspaces" else ""
                assert MIGRATION.plain_name(former, table=table) == (item.get("name") or item.get("title"))
                checked += 1
    assert checked > 80
    assert not any(dash in MIGRATION.plain_text(f"a {EM} b 1{EN}2 c") for dash in DASHES)
