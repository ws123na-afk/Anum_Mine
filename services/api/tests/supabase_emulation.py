"""A local stand-in for a Supabase project shaped like TyrePulse, on real PostgreSQL.

Used by ``test_postgres_tyrepulse_reader.py`` to run the SQL the owner ships
(``infra/tyrepulse/supabase-anum-reader.sql`` and ``supabase-anum-reader-extend.sql``) against the same mechanics Supabase uses:

* roles ``anon``, ``authenticated``, ``service_role`` and the ``authenticator`` login that
  PostgREST connects as; an admin login (``tp_admin``) that, like Supabase's ``postgres``,
  is not a superuser, owns the public schema and may set only ``pgrst.*`` on roles (the
  allow-list Supabase's supautils applies, which is why ``request.jwt.claims`` is refused);
* ``auth.users`` and ``auth.uid()`` reading the verified JWT's ``sub`` from
  ``request.jwt.claims`` exactly as Supabase defines it;
* TyrePulse's org model: ``profiles.org_id``, SECURITY DEFINER ``app_current_org()``,
  ``is_super_admin()``, ``app_is_active()``, country scope helpers, permissive policies for
  active users and RESTRICTIVE org and country isolation (as in TyrePulse V42/V111/V551/V574);
* ``FakePostgrest``: Supabase Auth sign-in / refresh / user and PostgREST table reads,
  served by an ``httpx.MockTransport`` that runs each request the way PostgREST does
  (``authenticator`` -> ``set local role authenticated``, the JWT claims, the method and path,
  the pre-request function, GET in a READ ONLY transaction).

Every value is synthetic (``tyre_fixtures``); passwords and keys are the word "example".
"""

from __future__ import annotations

import json
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from tyre_fixtures import ORG, TABLES, build_rows

ORG_B = "00000000-0000-0000-0000-00000000000b"
ADMIN_ROLE = "tp_admin"
SIGNING_KEY = "example" * 6
PUBLISHABLE_KEY = "sb_publishable_example"
SECRET_KEY = "sb_secret_example"
PASSWORD = "example"
DATA_TABLES = ("vehicle_fleet", "tyre_records", "tyre_service_events", "tpms_readings", "suppliers")
# Operational tables the extend file adds (each has organisation_id), plus two it must not:
# ``drivers`` (personal data) and ``ksa_kms`` (no organisation_id, so it cannot be pinned).
OPERATION_TABLES = ("inspections", "inspection_schedules", "checklist_submissions", "work_orders",
                    "work_order_line_items", "parts_consumption", "odometer_logs", "engine_hours_logs", "sites")
EXCLUDED_TABLES = ("drivers", "ksa_kms")
NO_ORG_TABLES = ("ksa_kms",)
CLUSTER_ROLES = ("anum_reader", "authenticator", "anon", "authenticated", "service_role", ADMIN_ROLE)


@dataclass(frozen=True)
class User:
    id: str
    email: str
    org: str
    role: str
    super_admin: bool = False


USERS = {
    "reader": User(str(uuid.UUID(int=0xA1)), "anum-reader@example.com", ORG, "Reporter"),
    "staff": User(str(uuid.UUID(int=0xA2)), "staff@example.com", ORG, "Manager"),
    "other": User(str(uuid.UUID(int=0xB1)), "other@example.com", ORG_B, "Manager"),
    "owner": User(str(uuid.UUID(int=0xA3)), "owner@example.com", ORG, "Admin", super_admin=True),
}


def _column_type(column: str, values: list[Any]) -> str:
    if column == "organisation_id":
        return "uuid"
    sample = next((value for value in values if value is not None), None)
    if isinstance(sample, (dict, list)):
        return "jsonb"
    if isinstance(sample, bool):
        return "boolean"
    if isinstance(sample, (int, float)):
        return "double precision"
    return "text"


def table_rows() -> dict[str, list[dict[str, Any]]]:
    """Organisation A's fleet, a copy for organisation B, and one row without an organisation."""
    base = build_rows()
    base["drivers"] = [{"id": "fake-driver-1", "full_name": "example", "phone": "example"}]
    base["ksa_kms"] = [{"id": "fake-kms-1", "distance": 10.0}]
    rows: dict[str, list[dict[str, Any]]] = {}
    for table in (*DATA_TABLES, *OPERATION_TABLES, *EXCLUDED_TABLES):
        if table in NO_ORG_TABLES:
            rows[table] = [{**row, "country": "KSA"} for row in base[table]]
            continue
        own = [{**row, "organisation_id": ORG, "country": row.get("country") or "KSA"} for row in base.get(table, [])]
        if not own:
            own = [{"id": f"fake-{table}-1", "organisation_id": ORG, "country": "KSA"}]
        other = [{**row, "id": f"other-{row['id']}", "organisation_id": ORG_B} for row in own]
        orphan = [{**own[0], "id": f"orphan-{own[0]['id']}", "organisation_id": None}]
        rows[table] = own + other + orphan
    return rows


def _columns(table: str, rows: list[dict[str, Any]]) -> list[str]:
    names = dict.fromkeys(TABLES.get(table, []))
    for row in rows:
        names.update(dict.fromkeys(row))
    names.update(dict.fromkeys(("id", "country") if table in NO_ORG_TABLES else ("id", "organisation_id", "country")))
    return list(names)


def _superuser_setup(admin: psycopg.Connection) -> None:
    for role, options in (
        (ADMIN_ROLE, f"login createrole password '{PASSWORD}'"),
        ("anon", "nologin noinherit"),
        ("authenticated", "nologin noinherit"),
        ("service_role", "nologin noinherit bypassrls"),
        ("authenticator", f"login noinherit password '{PASSWORD}'"),
    ):
        exists = admin.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
        if not exists:
            admin.execute(sql.SQL("create role {} " + options).format(sql.Identifier(role)))  # nosec B608 - constants
    admin.execute(f"grant anon, authenticated, service_role, authenticator to {ADMIN_ROLE} with admin option")
    admin.execute("grant anon, authenticated, service_role to authenticator")
    # Supabase lets its admin set pgrst.* on roles, nothing else of the kind.
    admin.execute(f"grant set on parameter pgrst.db_pre_request to {ADMIN_ROLE}")


def _supabase_schema(conn: psycopg.Connection) -> None:
    conn.execute(f"alter schema public owner to {ADMIN_ROLE}")
    conn.execute("create schema auth")
    conn.execute("create table auth.users (id uuid primary key, email text unique not null)")
    conn.execute("""
        create function auth.uid() returns uuid language sql stable as $$
          select coalesce(
            nullif(current_setting('request.jwt.claim.sub', true), ''),
            (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
          )::uuid
        $$""")
    conn.execute(f"grant usage on schema auth to anon, authenticated, service_role, {ADMIN_ROLE}")
    conn.execute(f"grant select, references on auth.users to {ADMIN_ROLE}")
    for user in USERS.values():
        conn.execute("insert into auth.users (id, email) values (%s, %s)", (user.id, user.email))


def _tyrepulse_schema(conn: psycopg.Connection) -> None:
    """Run as the admin, like TyrePulse's migrations."""
    conn.execute("""
        create table public.profiles (
          id uuid primary key references auth.users (id) on delete cascade,
          org_id uuid, organisation_id uuid, role text not null default 'Reporter',
          is_super_admin boolean not null default false, approved boolean default true, locked boolean default false,
          country text[], sites text[])""")
    for user in USERS.values():
        conn.execute(
            "insert into public.profiles (id, org_id, organisation_id, role, is_super_admin, country, sites) "
            "values (%s, %s, %s, %s, %s, array['all'], array['ALL'])",
            (user.id, user.org, user.org, user.role, user.super_admin))
    conn.execute("""
        create function public.app_current_org() returns uuid language sql stable security definer
        set search_path = public as $$ select org_id from public.profiles where id = auth.uid() $$;
        create function public.is_super_admin() returns boolean language sql stable security definer
        set search_path = public as $$ select coalesce((select is_super_admin from public.profiles where id = auth.uid()), false) $$;
        create function public.app_is_active() returns boolean language sql stable security definer
        set search_path = public as $$
          select coalesce(approved, false) and not coalesce(locked, false) from public.profiles where id = auth.uid() $$;
        create function public.app_sees_all_countries() returns boolean language sql stable security definer
        set search_path = public as $$
          select coalesce((select exists (select 1 from unnest(p.country) x where lower(btrim(x)) = 'all')
                           from public.profiles p where p.id = auth.uid()), false) $$;
        create function public.app_country_scope() returns text[] language sql stable security definer
        set search_path = public as $$
          select coalesce((select array_agg(lower(btrim(x))) from public.profiles p, unnest(p.country) x
                           where p.id = auth.uid()), '{}'::text[]) $$;
        -- A SECURITY DEFINER function that writes: RLS never runs inside it (owner, no FORCE).
        create function public.purge_tyre_records() returns bigint language plpgsql security definer
        set search_path = public as $$
          declare n bigint; begin delete from public.tyre_records; get diagnostics n = row_count; return n; end $$;
    """)
    conn.execute("alter table public.profiles enable row level security")
    conn.execute("create policy profiles_self on public.profiles for select to authenticated using (id = (select auth.uid()))")

    rows = table_rows()
    for table in (*DATA_TABLES, *OPERATION_TABLES, *EXCLUDED_TABLES, "audit_events"):
        data = rows.get(table) or [{"id": "fake-audit-1", "organisation_id": ORG, "country": "KSA"}]
        columns = _columns(table, data)
        column_sql = sql.SQL(", ").join(
            sql.SQL("{} {}{}").format(
                sql.Identifier(column),
                sql.SQL(_column_type(column, [row.get(column) for row in data])),
                sql.SQL(" primary key" if column == "id" else
                        " default public.app_current_org()" if column == "organisation_id" else ""),
            )
            for column in columns
        )
        conn.execute(sql.SQL("create table public.{} ({})").format(sql.Identifier(table), column_sql))
        insert = sql.SQL("insert into public.{} ({}) values ({})").format(
            sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join(sql.Placeholder() * len(columns)))
        with conn.cursor() as cursor:
            cursor.executemany(insert, [[Jsonb(row[column]) if isinstance(row.get(column), (dict, list)) else row.get(column)
                                         for column in columns] for row in data])
        name = sql.Identifier(table)
        org_rule = ("organisation_id is null or organisation_id = (select public.app_current_org()) or (select public.is_super_admin())"
                    if table == "suppliers" else "true" if table in NO_ORG_TABLES else
                    "organisation_id = (select public.app_current_org()) or (select public.is_super_admin())")
        country_rule = ("country is null or (select public.is_super_admin()) or (select public.app_sees_all_countries()) "
                        "or lower(btrim(country)) = any (coalesce((select public.app_country_scope()), array[]::text[]))")
        for statement in (
            "alter table public.{t} enable row level security",
            "create policy active_select on public.{t} for select to authenticated using ((select public.app_is_active()))",
            "create policy role_insert on public.{t} for insert to authenticated with check ((select public.app_is_active()))",
            "create policy role_update on public.{t} for update to authenticated using ((select public.app_is_active()))",
            "create policy role_delete on public.{t} for delete to authenticated using ((select public.app_is_active()))",
            f"create policy org_isolation on public.{{t}} as restrictive for all to authenticated using ({org_rule}) with check ({org_rule})",
            f"create policy country_isolation on public.{{t}} as restrictive for all to authenticated using ({country_rule}) with check ({country_rule})",
        ):
            conn.execute(sql.SQL(statement).format(t=name))
    # Supabase's default grants.
    conn.execute("grant usage on schema public to anon, authenticated, service_role")
    conn.execute("grant select, insert, update, delete, truncate on all tables in schema public to anon, authenticated, service_role")
    conn.execute("grant execute on all functions in schema public to anon, authenticated, service_role")


def old_login_attempt(conn: psycopg.Connection) -> str:
    """What docs/tyre-planning.md used to ask for. Returns the SQLSTATE the claims line raises."""
    conn.execute(f"create role anum_reader with login password '{PASSWORD}' nobypassrls")
    conn.execute("alter role anum_reader set default_transaction_read_only = on")
    conn.execute("grant usage on schema public to anum_reader")
    conn.execute("grant select on public.vehicle_fleet, public.tyre_records, public.tyre_service_events, "
                 "public.tpms_readings, public.suppliers to anum_reader")
    try:
        with conn.transaction():
            conn.execute("""alter role anum_reader set request.jwt.claims = '{"sub": "x", "role": "authenticated"}'""")
    except psycopg.Error as exc:
        state = exc.sqlstate or "error"
    else:
        state = "accepted"
    conn.execute("grant execute on all functions in schema public to anum_reader")
    return state


@dataclass
class SupabaseProject:
    admin_conninfo: str           # superuser, for setup and teardown
    database: str
    host: str
    port: int

    def conninfo(self, user: str, dbname: str | None = None) -> str:
        return psycopg.conninfo.make_conninfo(host=self.host, port=self.port, user=user, password=PASSWORD,
                                              dbname=dbname or self.database)

    def connect(self, user: str = ADMIN_ROLE) -> psycopg.Connection:
        return psycopg.connect(self.conninfo(user), autocommit=True)


def create_project(superuser_conninfo: str, database: str) -> tuple[SupabaseProject, str]:
    params = psycopg.conninfo.conninfo_to_dict(superuser_conninfo)
    with psycopg.connect(superuser_conninfo, autocommit=True) as admin:
        drop_project_objects(admin, database)
        _superuser_setup(admin)
        admin.execute(sql.SQL("create database {} owner {}").format(sql.Identifier(database), sql.Identifier(ADMIN_ROLE)))
    project = SupabaseProject(superuser_conninfo, database, str(params.get("host") or "127.0.0.1"),
                              int(params.get("port") or 5432))
    with psycopg.connect(psycopg.conninfo.make_conninfo(superuser_conninfo, dbname=database), autocommit=True) as conn:
        _supabase_schema(conn)
    with project.connect() as conn:
        _tyrepulse_schema(conn)
        state = old_login_attempt(conn)
    return project, state


def drop_project_objects(admin: psycopg.Connection, database: str) -> None:
    admin.execute(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(database)))
    for role in CLUSTER_ROLES:
        if admin.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone():
            admin.execute(sql.SQL("drop owned by {} cascade").format(sql.Identifier(role)))
            admin.execute(sql.SQL("drop role {}").format(sql.Identifier(role)))


def run_file(project: SupabaseProject, path: str) -> list[str]:
    """Run a SQL file as the admin (the SQL Editor's ``postgres``); returns the notices."""
    notices: list[str] = []
    with project.connect() as conn:
        conn.add_notice_handler(lambda diag: notices.append(diag.message_primary or ""))
        with open(path, encoding="utf-8") as handle:
            conn.execute(handle.read())  # type: ignore[call-overload]  # the owner's file, run as written
    return notices


@dataclass
class FakePostgrest:
    """Supabase Auth + PostgREST for one project, backed by the real database."""

    project: SupabaseProject
    passwords: dict[str, str] = field(default_factory=lambda: {user.email: PASSWORD for user in USERS.values()})
    refresh_tokens: dict[str, str] = field(default_factory=dict)
    requests: list[tuple[str, str]] = field(default_factory=list)
    sign_ins: int = 0
    refreshes: int = 0
    token_lifetime: int = 3600

    def _token(self, user: User) -> dict[str, Any]:
        now = int(time.time())
        access = jwt.encode({"sub": user.id, "role": "authenticated", "aud": "authenticated", "email": user.email,
                             "iat": now, "exp": now + self.token_lifetime}, SIGNING_KEY, algorithm="HS256")
        refresh = secrets.token_hex(8)
        self.refresh_tokens[refresh] = user.email
        return {"access_token": access, "token_type": "bearer", "expires_in": self.token_lifetime,
                "refresh_token": refresh, "user": {"id": user.id, "email": user.email}}

    def _claims(self, request: httpx.Request) -> dict[str, Any] | None:
        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        if token in {PUBLISHABLE_KEY, ""}:
            return {"role": "anon"}
        try:
            return jwt.decode(token, SIGNING_KEY, algorithms=["HS256"], audience="authenticated")
        except jwt.PyJWTError:
            return None

    def handler(self, request: httpx.Request) -> httpx.Response:
        parts = urlsplit(str(request.url))
        self.requests.append((request.method, parts.path))
        if request.headers.get("apikey") != PUBLISHABLE_KEY:
            return httpx.Response(401, json={"message": "Invalid API key"})
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        if parts.path == "/auth/v1/token" and request.method == "POST":
            body = json.loads(request.content or b"{}")
            if query.get("grant_type") == "password":
                self.sign_ins += 1
                user = next((u for u in USERS.values() if u.email == body.get("email")), None)
                if user is None or self.passwords.get(user.email) != body.get("password"):
                    return httpx.Response(400, json={"error_code": "invalid_credentials", "msg": "Invalid login credentials"})
                return httpx.Response(200, json=self._token(user))
            if query.get("grant_type") == "refresh_token":
                self.refreshes += 1
                email = self.refresh_tokens.pop(str(body.get("refresh_token")), None)
                user = next((u for u in USERS.values() if u.email == email), None)
                if user is None:
                    return httpx.Response(400, json={"error_code": "refresh_token_not_found"})
                return httpx.Response(200, json=self._token(user))
            return httpx.Response(400, json={"error_code": "unsupported_grant_type"})
        claims = self._claims(request)
        if claims is None:
            return httpx.Response(401, json={"code": "PGRST301", "message": "JWT expired"})
        if parts.path == "/auth/v1/user" and request.method == "GET":
            return httpx.Response(200, json={"id": claims.get("sub")}) if claims.get("sub") else httpx.Response(401)
        if parts.path.startswith("/rest/v1/"):
            return self._postgrest(request.method, parts.path.removeprefix("/rest/v1"), query, claims)
        return httpx.Response(404)

    def _postgrest(self, method: str, path: str, query: dict[str, str], claims: dict[str, Any]) -> httpx.Response:
        role = "authenticated" if claims.get("role") == "authenticated" else "anon"
        with psycopg.connect(self.project.conninfo("authenticator")) as conn:
            try:
                if method in {"GET", "HEAD"}:
                    conn.execute("set transaction read only")
                conn.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
                conn.execute("select set_config('request.jwt.claims', %s, true), set_config('request.method', %s, true), "
                             "set_config('request.path', %s, true)", (json.dumps(claims), method, path))
                conn.execute("select anum_integration.pre_request()")
                table = path.strip("/")
                if not table:
                    return httpx.Response(200, json={"swagger": "2.0", "definitions": {}})
                if method != "GET":
                    return httpx.Response(405)
                columns = query.get("select", "*")
                select = sql.SQL("*") if columns == "*" else sql.SQL(", ").join(
                    sql.Identifier(column.strip('"')) for column in columns.split(","))
                # PostgREST's column=eq|gte|lte.value filters and order=column.desc, as ANUM sends them.
                operators = {"eq": "=", "gte": ">=", "lte": "<="}
                where, values = [], []
                for column, value in query.items():
                    if column in {"select", "limit", "offset", "order"}:
                        continue
                    op, _, wanted = value.partition(".")
                    where.append(sql.SQL("{}::text {} %s").format(sql.Identifier(column), sql.SQL(operators[op])))
                    values.append(wanted)
                order = sql.SQL("1")
                if "order" in query:
                    column, _, rest = query["order"].partition(".")
                    order = sql.SQL("{} {} nulls last").format(sql.Identifier(column),
                                                               sql.SQL("desc" if rest.startswith("desc") else "asc"))
                statement = sql.SQL("select {} from public.{}{} order by {} limit {} offset {}").format(
                    select, sql.Identifier(table),
                    sql.SQL(" where ") + sql.SQL(" and ").join(where) if where else sql.SQL(""), order,
                    sql.Literal(int(query.get("limit", "1000"))), sql.Literal(int(query.get("offset", "0"))))
                cursor = conn.execute(statement, values)
                names = [column.name for column in cursor.description or []]
                rows = [dict(zip(names, (str(v) if isinstance(v, uuid.UUID) else v for v in values)))
                        for values in cursor.fetchall()]
                return httpx.Response(200, json=rows)
            except psycopg.Error as exc:
                state = exc.sqlstate or ""
                status = {"PT403": 403, "42P01": 404, "42703": 400, "42501": 403, "25006": 405}.get(state, 500)
                return httpx.Response(status, json={"code": state, "message": "refused"})

    def client_factory(self) -> Callable[[], httpx.AsyncClient]:
        return lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.handler), timeout=10)


def as_user(project: SupabaseProject, user: User, method: str, path: str, statement: str,
            *, guard: bool = True) -> tuple[str, Any]:
    """One PostgREST-style request with raw SQL: returns ("ok", rows-or-rowcount) or ("error", sqlstate)."""
    with psycopg.connect(project.conninfo("authenticator")) as conn:
        try:
            if method in {"GET", "HEAD"}:
                conn.execute("set transaction read only")
            conn.execute("set local role authenticated")
            conn.execute("select set_config('request.jwt.claims', %s, true), set_config('request.method', %s, true), "
                         "set_config('request.path', %s, true)",
                         (json.dumps({"sub": user.id, "role": "authenticated"}), method, path))
            if guard:
                conn.execute("select anum_integration.pre_request()")
            cursor = conn.execute(statement)  # type: ignore[call-overload]  # test constants
            result: Any = cursor.fetchall() if cursor.description else cursor.rowcount
            conn.rollback()
            return "ok", result
        except psycopg.Error as exc:
            return "error", exc.sqlstate
