"""Headquarters tables: every office, meeting, presence and executive table must be tenant-safe.

The set of tables is discovered from the catalog by prefix, so a table added by a later headquarters
migration (office presence, executive grants, meeting tools) is checked without editing this file.
Run like CI: from the repository root, ``python -m pytest services/api -m database``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.database

HQ_PREFIXES = ("office_", "agent_meeting", "executive_", "hq_")
# Cross-tenant jobs reach these tables only through the narrow NOLOGIN purge role (docs/multi-tenancy.md).
ALLOWED_EXTRA_ROLES = {"anum_tenant_purger"}


def _hq_tables(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("select relname from pg_class where relnamespace = 'public'::regnamespace and relkind = 'r' order by relname")
        ).scalars()
        return [name for name in rows if name.startswith(HQ_PREFIXES)]


def test_the_headquarters_table_set_is_not_empty(database_engine: Engine) -> None:
    tables = _hq_tables(database_engine)
    assert {"office_buildings", "office_floors", "office_rooms", "office_desks", "office_create_keys"} <= set(tables)
    assert {"agent_meeting_sessions", "agent_meeting_participants", "agent_meeting_turns"} <= set(tables)


def test_every_headquarters_table_has_forced_rls(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        state = {
            row.relname: (row.relrowsecurity, row.relforcerowsecurity)
            for row in connection.execute(
                text("select relname, relrowsecurity, relforcerowsecurity from pg_class where relname = any(:names)"),
                {"names": _hq_tables(database_engine)},
            )
        }
    assert state, "no headquarters tables found"
    assert {name: flags for name, flags in state.items() if flags != (True, True)} == {}


def test_every_headquarters_table_policy_pins_tenant_and_workspace(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        policies = connection.execute(
            text(
                """
                select tablename, policyname, roles::text as roles, qual, with_check
                from pg_policies where schemaname = 'public' and tablename = any(:names)
                """
            ),
            {"names": _hq_tables(database_engine)},
        ).all()
    by_table: dict[str, list] = {}
    for policy in policies:
        by_table.setdefault(policy.tablename, []).append(policy)
    assert set(by_table) == set(_hq_tables(database_engine)), "a headquarters table has no policy at all"
    for table, rows in by_table.items():
        # Policies for every role (the app role) must check both the tenant and the workspace, for reads and writes.
        for policy in rows:
            roles = {r.strip() for r in policy.roles.strip("{}").split(",") if r.strip()}
            if roles and roles <= ALLOWED_EXTRA_ROLES:
                continue
            assert "anum.tenant_id" in (policy.qual or ""), f"{table}.{policy.policyname} reads without the tenant check"
            assert "anum.workspace_id" in (policy.qual or ""), f"{table}.{policy.policyname} reads without the workspace check"
            assert "anum.tenant_id" in (policy.with_check or ""), f"{table}.{policy.policyname} writes without the tenant check"
            assert "anum.workspace_id" in (policy.with_check or ""), f"{table}.{policy.policyname} writes without the workspace check"
