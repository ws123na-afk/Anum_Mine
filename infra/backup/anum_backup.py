#!/usr/bin/env python3
"""ANUM PostgreSQL backup, restore and restore drill (docs/runbooks.md#backup-and-restore).

Subcommands (each prints a JSON report on stdout and exits non-zero on failure):

``backup``   Logical dump in pg_dump custom format plus a manifest (row counts per
             table and per tenant, RLS flags, policies, extensions, Alembic revision),
             taken from one consistent snapshot.
``restore``  Restore a dump into a new scratch database whose name starts with
             ``anum_restore_``. It never touches an existing database.
``verify``   Check a restored database against its manifest: revision, extensions,
             policies, RLS enabled and forced, exact row counts per table and per
             tenant, and that RLS still isolates tenants for a non-superuser role.
``drill``    backup -> restore -> verify -> drop, with timings (the restore drill).

Tenant safety:

* The dump is the whole database: every tenant, in one file. It runs as a role that
  bypasses RLS (superuser or BYPASSRLS) and refuses to run otherwise, because a
  role subject to RLS would silently produce a partial backup.
* Files are created with mode 0600 in a 0700 directory. Store them only in
  encrypted, access-controlled storage (see the runbook); they hold every tenant's data.
* Restores always go to a fresh ``anum_restore_*`` database, never over a live one,
  and keep RLS policies, FORCE ROW LEVEL SECURITY and grants. Recovering one tenant
  means copying that tenant's rows out of the scratch database (runbook), never a
  partial restore into production.
* Connection strings are passed to pg_dump/pg_restore through PG* environment
  variables, never on a command line, and are never printed.

Requirements: Python 3.11+, psycopg 3 (an API dependency) and PostgreSQL client tools
(pg_dump, pg_restore) at least as new as the server. ``--pg-bin`` or ``ANUM_PG_BIN``
points at their directory when they are not on PATH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

MANIFEST_FORMAT = 1
RESTORE_PREFIX = "anum_restore_"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_ROLE_REFERENCE = re.compile(r"\b(?:OWNER TO|TO|FROM)\s+(\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_$]*)\s*;")
_BUILTIN_ROLES = {"public", "current_user", "session_user", "current_role"}
DEFAULT_PG_BIN_DIRS = ("/usr/lib/postgresql/16/bin", "/usr/lib/postgresql/17/bin")


class DrillError(RuntimeError):
    """A check failed; the message is safe to print (no credentials, no row data)."""


# --------------------------------------------------------------------------- helpers


def libpq_url(url: str) -> str:
    """Accept SQLAlchemy URLs (``postgresql+psycopg://``) as well as libpq ones."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url.strip())


def with_database(url: str, database: str) -> str:
    params = conninfo_to_dict(libpq_url(url))
    params["dbname"] = database
    return make_conninfo(**params)


def pg_environment(url: str) -> dict[str, str]:
    """PG* variables for the client tools, so no secret appears in a process list."""
    params = conninfo_to_dict(libpq_url(url))
    mapping = {
        "host": "PGHOST",
        "port": "PGPORT",
        "user": "PGUSER",
        "password": "PGPASSWORD",  # nosec B105
        "dbname": "PGDATABASE",
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
    }
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    for name, variable in mapping.items():
        value = params.get(name)
        if value:
            env[variable] = str(value)
    return env


def find_tool(name: str, pg_bin: str | None) -> str:
    candidates = [pg_bin, os.environ.get("ANUM_PG_BIN")]
    for directory in candidates:
        if directory:
            path = Path(directory) / name
            if path.is_file():
                return str(path)
    found = shutil.which(name)
    if found:
        return found
    for directory in DEFAULT_PG_BIN_DIRS:
        path = Path(directory) / name
        if path.is_file():
            return str(path)
    raise DrillError(f"{name} not found: install the PostgreSQL client tools or pass --pg-bin")


def run_tool(command: list[str], url: str, *, stdout: Any = None) -> subprocess.CompletedProcess[str]:
    # Fixed tool path and argument list, never a shell.
    result = subprocess.run(  # nosec B603
        command,
        env=pg_environment(url),
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # pg_dump/pg_restore errors name objects, not row data or credentials.
        raise DrillError(f"{Path(command[0]).name} failed: {result.stderr.strip()[-2000:]}")
    return result


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def write_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------------- inventory


@dataclass(frozen=True)
class TableInfo:
    name: str
    rls: bool
    force_rls: bool
    has_tenant: bool
    has_workspace: bool


def list_tables(connection: psycopg.Connection) -> list[TableInfo]:
    rows = connection.execute(
        """
        select c.relname,
               c.relrowsecurity,
               c.relforcerowsecurity,
               exists (select 1 from information_schema.columns col
                       where col.table_schema = 'public' and col.table_name = c.relname
                         and col.column_name = 'tenant_id'),
               exists (select 1 from information_schema.columns col
                       where col.table_schema = 'public' and col.table_name = c.relname
                         and col.column_name = 'workspace_id')
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind in ('r', 'p')
        order by c.relname
        """
    ).fetchall()
    return [TableInfo(*row) for row in rows]


def inventory(connection: psycopg.Connection) -> dict[str, Any]:
    """Counts and security settings of the public schema, as the caller sees them."""
    tables: dict[str, Any] = {}
    for table in list_tables(connection):
        if not _IDENTIFIER.match(table.name):
            raise DrillError(f"unexpected table name {table.name!r}")
        identifier = sql.Identifier(table.name)
        rows = connection.execute(sql.SQL("select count(*) from {}").format(identifier)).fetchone()[0]
        entry: dict[str, Any] = {
            "rows": int(rows),
            "rls": bool(table.rls),
            "force_rls": bool(table.force_rls),
            "tenant_scoped": bool(table.has_tenant),
        }
        if table.has_tenant:
            entry["by_tenant"] = {
                str(tenant): int(count)
                for tenant, count in connection.execute(
                    sql.SQL(
                        "select tenant_id, count(*) from {} group by tenant_id order by tenant_id"
                    ).format(identifier)
                ).fetchall()
            }
        tables[table.name] = entry
    policies = sorted(
        f"{table}.{policy}"
        for table, policy in connection.execute(
            "select tablename, policyname from pg_policies where schemaname = 'public'"
        ).fetchall()
    )
    extensions = sorted(row[0] for row in connection.execute("select extname from pg_extension"))
    revision = None
    if "alembic_version" in tables:
        found = connection.execute("select version_num from alembic_version").fetchone()
        revision = found[0] if found else None
    return {
        "alembic_revision": revision,
        "extensions": extensions,
        "policies": policies,
        "tables": tables,
    }


# --------------------------------------------------------------------------- backup


def backup(database_url: str, out_dir: Path, *, pg_bin: str | None = None) -> dict[str, Any]:
    pg_dump = find_tool("pg_dump", pg_bin)
    directory = private_directory(out_dir)
    stamp = utc_stamp()
    dump_path = directory / f"anum-{stamp}.dump"
    manifest_path = directory / f"anum-{stamp}.manifest.json"
    started = time.monotonic()
    with psycopg.connect(libpq_url(database_url), autocommit=False) as connection:
        role = connection.execute(
            "select rolsuper or rolbypassrls from pg_roles where rolname = current_user"
        ).fetchone()
        if not role or not role[0]:
            raise DrillError(
                "the backup role must bypass RLS (superuser or BYPASSRLS); "
                "a role subject to RLS would produce a partial backup"
            )
        connection.rollback()
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.read_only = True
        snapshot = connection.execute("select pg_export_snapshot()").fetchone()[0]
        contents = inventory(connection)  # same snapshot as the dump
        server_version = connection.execute("show server_version").fetchone()[0]
        # The dump shares the exported snapshot while this transaction stays open.
        previous_umask = os.umask(0o077)
        try:
            run_tool(
                [
                    pg_dump,
                    "--format=custom",
                    "--compress=6",
                    f"--snapshot={snapshot}",
                    "--no-password",
                    f"--file={dump_path}",
                ],
                database_url,
            )
        finally:
            os.umask(previous_umask)
        connection.rollback()
    dump_path.chmod(0o600)
    tool_version = run_tool([pg_dump, "--version"], database_url).stdout.strip()
    manifest = {
        "format": MANIFEST_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dump_file": dump_path.name,
        "sha256": sha256_of(dump_path),
        "size_bytes": dump_path.stat().st_size,
        "server_version": server_version,
        "pg_dump_version": tool_version,
        **contents,
    }
    write_private(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {
        "dump": str(dump_path),
        "manifest": str(manifest_path),
        "size_bytes": manifest["size_bytes"],
        "tables": len(contents["tables"]),
        "rows": sum(table["rows"] for table in contents["tables"].values()),
        "seconds": round(time.monotonic() - started, 3),
    }


# --------------------------------------------------------------------------- restore


def referenced_roles(dump_path: Path, *, pg_bin: str | None, url: str) -> set[str]:
    pg_restore = find_tool("pg_restore", pg_bin)
    script = run_tool([pg_restore, "--schema-only", "--file=-", str(dump_path)], url).stdout
    roles = set()
    for match in _ROLE_REFERENCE.finditer(script):
        name = match.group(1).strip('"')
        if name.lower() not in _BUILTIN_ROLES:
            roles.add(name)
    return roles


def restore(
    admin_url: str,
    dump_path: Path,
    target: str,
    *,
    pg_bin: str | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    if not target.startswith(RESTORE_PREFIX) or not _IDENTIFIER.match(target):
        raise DrillError(f"restore target must be a new database named {RESTORE_PREFIX}<suffix>")
    if manifest_path is not None:
        expected = json.loads(manifest_path.read_text())["sha256"]
        if sha256_of(dump_path) != expected:
            raise DrillError("dump checksum does not match its manifest")
    pg_restore = find_tool("pg_restore", pg_bin)
    started = time.monotonic()
    with psycopg.connect(libpq_url(admin_url), autocommit=True) as connection:
        if connection.execute("select 1 from pg_database where datname = %s", (target,)).fetchone():
            raise DrillError(f"database {target} already exists; restores never overwrite")
        # Roles are cluster-wide and not in the dump. Create the missing ones as
        # NOLOGIN so owners, grants and the relay role's policies restore unchanged.
        created_roles = []
        for role in sorted(referenced_roles(dump_path, pg_bin=pg_bin, url=admin_url)):
            if not connection.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone():
                connection.execute(sql.SQL("create role {} nologin").format(sql.Identifier(role)))
                created_roles.append(role)
        connection.execute(sql.SQL("create database {}").format(sql.Identifier(target)))
    run_tool(
        [
            pg_restore,
            "--exit-on-error",
            "--no-password",
            f"--dbname={target}",
            str(dump_path),
        ],
        with_database(admin_url, target),
    )
    return {
        "database": target,
        "created_roles": created_roles,
        "seconds": round(time.monotonic() - started, 3),
    }


def drop_database(admin_url: str, target: str) -> None:
    if not target.startswith(RESTORE_PREFIX) or not _IDENTIFIER.match(target):
        raise DrillError("only anum_restore_* scratch databases can be dropped by this tool")
    with psycopg.connect(libpq_url(admin_url), autocommit=True) as connection:
        connection.execute(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(target)))


# --------------------------------------------------------------------------- verify


@contextmanager
def probe_role(connection: psycopg.Connection) -> Iterator[str]:
    """A throwaway non-superuser role without BYPASSRLS that can read every table."""
    name = f"anum_restore_probe_{os.getpid()}"
    identifier = sql.Identifier(name)
    connection.execute(sql.SQL("create role {} nologin nosuperuser nobypassrls").format(identifier))
    try:
        connection.execute(sql.SQL("grant usage on schema public to {}").format(identifier))
        connection.execute(sql.SQL("grant select on all tables in schema public to {}").format(identifier))
        yield name
    finally:
        connection.execute(sql.SQL("drop owned by {}").format(identifier))
        connection.execute(sql.SQL("drop role {}").format(identifier))


def _as_probe(connection: psycopg.Connection, probe: str, scope: tuple[str, str] | None,
              query: sql.Composable) -> int:
    with connection.transaction(force_rollback=True):
        connection.execute(sql.SQL("set local role {}").format(sql.Identifier(probe)))
        if scope is not None:
            connection.execute("select set_config('anum.tenant_id', %s, true)", (scope[0],))
            connection.execute("select set_config('anum.workspace_id', %s, true)", (scope[1],))
        return int(connection.execute(query).fetchone()[0])


def check_rls_isolation(connection: psycopg.Connection, tables: list[TableInfo]) -> dict[str, Any]:
    """RLS must still hide every row without a tenant context and every other tenant's
    rows with one. Checked as a role that is not the owner, superuser or BYPASSRLS."""
    rls_tables = [table for table in tables if table.rls and table.has_tenant]
    if not rls_tables:
        raise DrillError("no RLS-protected tenant tables found in the restored database")
    scoped = [table for table in rls_tables if table.has_workspace]
    scope = None
    for table in scoped:
        found = connection.execute(
            sql.SQL(
                "select tenant_id, workspace_id from {} where workspace_id is not null "
                "group by tenant_id, workspace_id order by count(*) desc limit 1"
            ).format(sql.Identifier(table.name))
        ).fetchone()
        if found:
            scope = (str(found[0]), str(found[1]))
            break
    if scope is None:
        raise DrillError("the restored database has no tenant rows to test RLS with")

    results: dict[str, Any] = {"scope_tenant": scope[0], "tables": {}}
    visible_total = 0
    with probe_role(connection) as probe:
        for table in rls_tables:
            identifier = sql.Identifier(table.name)
            without_context = _as_probe(
                connection, probe, None, sql.SQL("select count(*) from {}").format(identifier)
            )
            visible = _as_probe(
                connection, probe, scope, sql.SQL("select count(*) from {}").format(identifier)
            )
            foreign = _as_probe(
                connection,
                probe,
                scope,
                sql.SQL("select count(*) from {} where tenant_id is distinct from {}").format(
                    identifier, sql.Literal(scope[0])
                ),
            )
            if without_context != 0:
                raise DrillError(f"RLS leak: {table.name} shows rows without a tenant context")
            if foreign != 0:
                raise DrillError(f"RLS leak: {table.name} shows another tenant's rows")
            visible_total += visible
            results["tables"][table.name] = {"without_context": 0, "visible_in_scope": visible, "other_tenants": 0}
    if visible_total == 0:
        raise DrillError("RLS probe saw no rows in its own tenant scope; grants or policies are broken")
    results["visible_in_scope"] = visible_total
    return results


def verify(database_url: str, manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("format") != MANIFEST_FORMAT:
        raise DrillError("unsupported manifest format")
    started = time.monotonic()
    problems: list[str] = []
    with psycopg.connect(libpq_url(database_url), autocommit=True) as connection:
        actual = inventory(connection)
        for key in ("alembic_revision", "extensions", "policies"):
            if actual[key] != manifest[key]:
                problems.append(f"{key} differs: expected {manifest[key]!r}, found {actual[key]!r}")
        expected_tables, actual_tables = manifest["tables"], actual["tables"]
        if set(expected_tables) != set(actual_tables):
            problems.append(
                f"tables differ: missing {sorted(set(expected_tables) - set(actual_tables))}, "
                f"unexpected {sorted(set(actual_tables) - set(expected_tables))}"
            )
        for name, expected in expected_tables.items():
            found = actual_tables.get(name)
            if found is None:
                continue
            for field in ("rows", "rls", "force_rls", "by_tenant"):
                if expected.get(field) != found.get(field):
                    problems.append(f"{name}.{field}: expected {expected.get(field)!r}, found {found.get(field)!r}")
            if expected.get("tenant_scoped") and not (found["rls"] and found["force_rls"]):
                problems.append(f"{name} holds tenant data without enabled and forced RLS")
        if problems:
            raise DrillError("; ".join(problems))
        isolation = check_rls_isolation(connection, list_tables(connection))
    return {
        "database": conninfo_to_dict(libpq_url(database_url)).get("dbname"),
        "alembic_revision": actual["alembic_revision"],
        "tables": len(actual_tables),
        "rows": sum(table["rows"] for table in actual_tables.values()),
        "tenants": len({tenant for table in actual_tables.values() for tenant in table.get("by_tenant", {})}),
        "rls_isolation": isolation,
        "seconds": round(time.monotonic() - started, 3),
    }


# --------------------------------------------------------------------------- drill


def drill(
    database_url: str,
    admin_url: str,
    work_dir: Path,
    *,
    pg_bin: str | None = None,
    keep: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    backed_up = backup(database_url, work_dir, pg_bin=pg_bin)
    target = f"{RESTORE_PREFIX}drill_{utc_stamp().lower()}_{os.getpid()}"
    restored: dict[str, Any] | None = None
    try:
        restored = restore(
            admin_url,
            Path(backed_up["dump"]),
            target,
            pg_bin=pg_bin,
            manifest_path=Path(backed_up["manifest"]),
        )
        verified = verify(with_database(admin_url, target), Path(backed_up["manifest"]))
    finally:
        if restored is not None and not keep:
            drop_database(admin_url, target)
    return {
        "result": "passed",
        "backup": backed_up,
        "restore": restored,
        "verify": verified,
        "kept_database": target if keep else None,
        # Measured restore time objective for this data volume (restore + verify).
        "rto_seconds_measured": round(restored["seconds"] + verified["seconds"], 3),
        "total_seconds": round(time.monotonic() - started, 3),
    }


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pg-bin", help="directory holding pg_dump and pg_restore")
    commands = parser.add_subparsers(dest="command", required=True)

    p_backup = commands.add_parser("backup", help="dump a database with its manifest")
    p_backup.add_argument("--database-url", default=os.environ.get("ANUM_BACKUP_DATABASE_URL"))
    p_backup.add_argument("--out", type=Path, required=True)

    p_restore = commands.add_parser("restore", help="restore into a new anum_restore_* database")
    p_restore.add_argument("--admin-url", default=os.environ.get("ANUM_RESTORE_ADMIN_URL"))
    p_restore.add_argument("--dump", type=Path, required=True)
    p_restore.add_argument("--manifest", type=Path)
    p_restore.add_argument("--target", required=True)

    p_verify = commands.add_parser("verify", help="check a restored database against a manifest")
    p_verify.add_argument("--database-url", default=os.environ.get("ANUM_RESTORE_DATABASE_URL"))
    p_verify.add_argument("--manifest", type=Path, required=True)

    p_drill = commands.add_parser("drill", help="backup, restore, verify and drop")
    p_drill.add_argument("--database-url", default=os.environ.get("ANUM_BACKUP_DATABASE_URL"))
    p_drill.add_argument("--admin-url", default=os.environ.get("ANUM_RESTORE_ADMIN_URL"))
    p_drill.add_argument("--work-dir", type=Path, required=True)
    p_drill.add_argument("--keep", action="store_true", help="keep the scratch database")

    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            report = backup(_required(args.database_url, "--database-url"), args.out, pg_bin=args.pg_bin)
        elif args.command == "restore":
            report = restore(
                _required(args.admin_url, "--admin-url"),
                args.dump,
                args.target,
                pg_bin=args.pg_bin,
                manifest_path=args.manifest,
            )
        elif args.command == "verify":
            report = verify(_required(args.database_url, "--database-url"), args.manifest)
        else:
            report = drill(
                _required(args.database_url, "--database-url"),
                _required(args.admin_url or args.database_url, "--admin-url"),
                args.work_dir,
                pg_bin=args.pg_bin,
                keep=args.keep,
            )
    except (DrillError, psycopg.Error) as exc:
        # psycopg messages name objects and SQL states, never the password.
        print(json.dumps({"result": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _required(value: str | None, flag: str) -> str:
    if not value:
        raise DrillError(f"{flag} (or its environment variable) is required")
    return value


if __name__ == "__main__":
    sys.exit(main())
