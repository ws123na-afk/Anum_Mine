"""Every table of the migrated schema, checked against the tenant isolation model.

``test_postgres_rls.py`` checks a fixed list of tables; this test reads the catalogue, so a
table added by a later migration is covered without anyone remembering to list it
(docs/security.md#pre-sale-review-november-2026, docs/multi-tenancy.md):

* every table in ``public`` has row-level security enabled and forced and at least one
  policy (only Alembic's own version table is exempt);
* every permissive policy that applies to every role (``TO public``, so to the application
  login) is bounded by the tenant setting ``anum.tenant_id``; owner-only and similar
  narrowing policies must be ``AS RESTRICTIVE``;
* the narrow cross-tenant roles are NOLOGIN and none of them, nor the application role,
  bypasses row-level security;
* no function in ``public`` is ``SECURITY DEFINER``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from conftest import APP_ROLE

pytestmark = pytest.mark.database

# Tables without tenant rows. Each needs a reason.
NO_RLS_TABLES = {"alembic_version": "Alembic's schema version; no tenant data."}
NARROW_ROLES = {
    "anum_outbox_relay",
    "anum_maintenance",
    "anum_membership_reader",
    "anum_platform_reader",
    "anum_tenant_directory",
    "anum_tenant_purger",
    "anum_executive_reader",
}


def test_every_table_has_forced_row_level_security_and_a_policy(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                select c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       (select count(*) from pg_policies p
                         where p.schemaname = 'public' and p.tablename = c.relname) as policies
                from pg_class c
                where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p')
                """
            )
        ).all()
    assert len(rows) > 200, "the catalogue query found too few tables"
    missing = sorted(
        row.relname
        for row in rows
        if row.relname not in NO_RLS_TABLES and not (row.relrowsecurity and row.relforcerowsecurity and row.policies > 0)
    )
    assert not missing, f"Tables without forced row-level security and a policy: {missing}"
    assert set(NO_RLS_TABLES) <= {row.relname for row in rows}


def test_permissive_policies_for_every_role_are_bounded_by_the_tenant(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                select tablename, policyname, cmd, coalesce(qual, '') as qual, coalesce(with_check, '') as with_check
                from pg_policies
                where schemaname = 'public' and permissive = 'PERMISSIVE' and roles = '{public}'::name[]
                """
            )
        ).all()
    assert rows
    unbounded = []
    for row in rows:
        reads = row.cmd != "INSERT"
        writes = row.cmd in {"INSERT", "UPDATE", "ALL"}
        if reads and "anum.tenant_id" not in row.qual:
            unbounded.append(f"{row.tablename}.{row.policyname} (using)")
        if writes and "anum.tenant_id" not in (row.with_check or row.qual):
            unbounded.append(f"{row.tablename}.{row.policyname} (with check)")
    assert not unbounded, (
        "Permissive policies for every role that are not bounded by anum.tenant_id (make them "
        f"AS RESTRICTIVE, or add the tenant condition): {sorted(unbounded)}"
    )


def test_cross_tenant_roles_are_narrow_and_nothing_bypasses_rls(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        roles = {
            row.rolname: row
            for row in connection.execute(
                text("select rolname, rolcanlogin, rolbypassrls, rolsuper from pg_roles where rolname = any(:names)"),
                {"names": sorted(NARROW_ROLES | {APP_ROLE})},
            )
        }
        definers = connection.execute(
            text("select proname from pg_proc where pronamespace = 'public'::regnamespace and prosecdef")
        ).scalars().all()
    assert NARROW_ROLES <= set(roles), sorted(NARROW_ROLES - set(roles))
    for name, role in roles.items():
        assert not role.rolbypassrls and not role.rolsuper, name
        if name in NARROW_ROLES:
            assert not role.rolcanlogin, f"{name} must be NOLOGIN"
    assert definers == [], f"SECURITY DEFINER functions in public: {definers}"
