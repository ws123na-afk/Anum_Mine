"""Headquarters tables: every office, meeting, presence and executive table must be tenant-safe.

The set of tables is discovered from the catalog by prefix, so a table added by a later headquarters
migration (office presence, executive grants, meeting tools) is checked without editing this file.
Run like CI: from the repository root, ``python -m pytest services/api -m database``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.database

HQ_PREFIXES = ("office_", "agent_meeting", "executive_", "campus_", "hq_")
# Cross-tenant jobs reach these tables only through the narrow NOLOGIN purge role (docs/multi-tenancy.md).
ALLOWED_EXTRA_ROLES = {"anum_tenant_purger"}
# The retention job finds work across tenants through anum_maintenance (migration 0120, owner decision O5): its
# policies may span tenants, but they must be read only and the role may read only the scope columns.
DISCOVERY_ROLE = "anum_maintenance"
DISCOVERY_COLUMNS = {"tenant_id", "workspace_id"}


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
                select tablename, policyname, cmd, roles::text as roles, qual, with_check
                from pg_policies where schemaname = 'public' and tablename = any(:names)
                """
            ),
            {"names": _hq_tables(database_engine)},
        ).all()
    by_table: dict[str, list] = {}
    for policy in policies:
        by_table.setdefault(policy.tablename, []).append(policy)
    assert set(by_table) == set(_hq_tables(database_engine)), "a headquarters table has no policy at all"
    discovery_tables: set[str] = set()
    for table, rows in by_table.items():
        assert any("public" in _roles(p) for p in rows), f"{table} has no policy for the application role"
        for policy in rows:
            roles = _roles(policy)
            if roles and roles <= ALLOWED_EXTRA_ROLES:
                continue  # the purge role: tenant lifecycle job only, write check is false
            if roles == {DISCOVERY_ROLE}:
                assert policy.cmd == "SELECT", f"{table}.{policy.policyname} lets {DISCOVERY_ROLE} write"
                discovery_tables.add(table)
                continue
            if "public" in roles:
                # The application role: tenant and workspace, for reads and for writes.
                for part in (policy.qual, policy.with_check):
                    assert "anum.tenant_id" in (part or ""), f"{table}.{policy.policyname} lacks the tenant check"
                    assert "anum.workspace_id" in (part or ""), f"{table}.{policy.policyname} lacks the workspace check"
            else:
                # A narrow NOLOGIN reader (for example the executive grant reader): read only, still pinned to one tenant.
                assert policy.cmd == "SELECT", f"{table}.{policy.policyname} lets a narrow role write"
                assert "anum.tenant_id" in (policy.qual or ""), f"{table}.{policy.policyname} is not pinned to a tenant"
    with database_engine.connect() as connection:
        granted = connection.execute(
            text(
                """
                select table_name, column_name from information_schema.column_privileges
                where grantee = :role and table_name = any(:names)
                """
            ),
            {"role": DISCOVERY_ROLE, "names": sorted(discovery_tables)},
        ).all()
        whole = connection.execute(
            text("select table_name from information_schema.role_table_grants where grantee = :role and table_name = any(:names)"),
            {"role": DISCOVERY_ROLE, "names": sorted(discovery_tables)},
        ).scalars().all()
    assert whole == [], f"{DISCOVERY_ROLE} holds a table-wide grant on {whole}"
    assert {column for _, column in granted} <= DISCOVERY_COLUMNS, granted


def _roles(policy) -> set[str]:  # type: ignore[no-untyped-def]
    return {r.strip() for r in policy.roles.strip("{}").split(",") if r.strip()}


def test_narrow_policy_roles_cannot_log_in_or_bypass_rls(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        names = set(
            connection.execute(
                text("select roles::text from pg_policies where schemaname = 'public' and tablename = any(:names)"),
                {"names": _hq_tables(database_engine)},
            ).scalars()
        )
        role_names = {r.strip() for value in names for r in value.strip("{}").split(",") if r.strip()} - {"public"}
        rows = connection.execute(
            text("select rolname, rolcanlogin, rolbypassrls, rolsuper from pg_roles where rolname = any(:roles)"),
            {"roles": sorted(role_names)},
        ).all()
    assert {row.rolname for row in rows} == role_names
    for row in rows:
        assert not row.rolcanlogin, f"{row.rolname} can log in"
        assert not row.rolbypassrls, f"{row.rolname} bypasses RLS"
        assert not row.rolsuper, f"{row.rolname} is a superuser"
