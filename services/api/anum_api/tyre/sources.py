"""Read-only connectors to the owner's tyre data (docs/tyre-planning.md#connecting).

Three kinds, each with ``test``, ``discover`` (tables or collections and their columns)
and ``fetch`` (the mapped columns of one table, capped at
``ANUM_TYRE_MAX_ROWS_PER_ENTITY`` rows):

* ``supabase_postgres``: a PostgreSQL connection string for a read-only login (the
  owner creates it; see the doc). Every session is ``default_transaction_read_only`` with
  a statement timeout, so even a login that could write never does. Outside local the
  host must resolve to public addresses only (or be in ``ANUM_TYRE_SOURCE_ALLOWED_HOSTS``),
  the port must be 5432 or 6543 (Supabase's pooler) and TLS is required; the connection
  is made to the address that was checked (``hostaddr``), so DNS rebinding cannot redirect it.
* ``supabase_rest``: the project URL and an API key, read through PostgREST
  (``/rest/v1``). Calls go through the same pinned egress guard as model endpoints.
* ``firestore``: a Google service account (JSON key) with the Cloud Datastore Viewer
  role; a signed JWT is exchanged for a token at Google's fixed token endpoint and
  documents are read through the Firestore REST API. Only Google's own hosts are called.

Secrets (the connection string, the API key, the service account key) are decrypted
only for the call and never logged, returned or put into error messages.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx
import jwt

from ..model_egress import (
    LOCAL_ENVIRONMENTS,
    EgressPolicy,
    ModelEndpoint,
    PinnedEgressTransport,
    UnsafeModelEndpointError,
    normalize_host,
    parse_allowed_hosts,
    resolve_pinned,
)
from ..settings import settings
from .mapping import SourceColumn, SourceTable
from .model import SourceKind

FIRESTORE_HOST = "firestore.googleapis.com"
GOOGLE_SIGN_IN_URL = "https://oauth2.googleapis.com/token"
FIRESTORE_SCOPE = "https://www.googleapis.com/auth/datastore"
POSTGRES_PORTS = frozenset({5432, 6543})
MAX_TABLES = 200
FIRESTORE_SAMPLE = 25
PAGE_SIZE = 1000
_SAFE_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TyreSourceError(Exception):
    """The source could not be read. ``message`` is safe to show to the owner."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        self.message = message
        self.hint = hint
        super().__init__(message)


@dataclass(frozen=True)
class FetchResult:
    rows: list[dict[str, Any]]
    cut: bool


def _policy() -> EgressPolicy:
    try:
        allowed = parse_allowed_hosts(settings.tyre_source_allowed_hosts)
    except ValueError as exc:
        raise TyreSourceError(f"ANUM_TYRE_SOURCE_ALLOWED_HOSTS is invalid: {exc}") from None
    return EgressPolicy(
        enforce=settings.environment.strip().lower() not in LOCAL_ENVIRONMENTS,
        allowed_hosts=allowed,
    )


def _timeout() -> float:
    return float(settings.tyre_source_timeout_seconds)


def _max_rows() -> int:
    return int(settings.tyre_max_rows_per_entity)


def _split_table(name: str, default_schema: str = "public") -> tuple[str, str]:
    schema, _, table = name.rpartition(".")
    return (schema or default_schema), table


# --- Supabase PostgreSQL ----------------------------------------------------------------


@dataclass(frozen=True)
class PostgresTarget:
    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str

    @property
    def display(self) -> str:
        return f"{self.user}@{self.host}:{self.port}/{self.database}"


_WEAK_SSL = {"disable", "allow", "prefer"}


def parse_postgres_url(value: str) -> PostgresTarget:
    """``postgresql://user:password@host:port/database?sslmode=require``."""
    try:
        parts = urlsplit(value.strip())
        port = parts.port or 5432
    except ValueError:
        raise TyreSourceError("The connection string is not a valid PostgreSQL URL.") from None
    if parts.scheme not in {"postgres", "postgresql"} or not parts.hostname:
        raise TyreSourceError("The connection string must start with postgresql:// and name a host.")
    if not parts.username or parts.password is None:
        raise TyreSourceError("The connection string must include the read-only user and its password.")
    params = dict(item.split("=", 1) for item in parts.query.split("&") if "=" in item)
    unknown = set(params) - {"sslmode"}
    if unknown:
        raise TyreSourceError("Only the sslmode option is accepted in the connection string.")
    sslmode = params.get("sslmode", "require")
    enforce = settings.environment.strip().lower() not in LOCAL_ENVIRONMENTS
    if enforce and sslmode in _WEAK_SSL:
        raise TyreSourceError("Use sslmode=require (or stricter): the connection must be encrypted.")
    database = unquote(parts.path.lstrip("/")) or "postgres"
    return PostgresTarget(
        host=normalize_host(parts.hostname),
        port=port,
        database=database,
        user=unquote(parts.username),
        password=unquote(parts.password),
        sslmode=sslmode,
    )


async def _postgres_address(target: PostgresTarget) -> str | None:
    """Check the host; returns the address to dial (None when not enforced)."""
    policy = _policy()
    if not policy.enforce:
        return None
    allow_listed = policy.allows_host(target.host, target.port, 5432)
    if target.port not in POSTGRES_PORTS and not allow_listed:
        raise TyreSourceError("Use port 5432 or 6543 (Supabase's pooler).")
    endpoint = ModelEndpoint(scheme="postgresql", host=target.host, port=target.port, allow_listed=allow_listed)
    try:
        return await resolve_pinned(endpoint)
    except UnsafeModelEndpointError:
        raise TyreSourceError(
            "The database host must be a public address. An operator can allow a private host "
            "with ANUM_TYRE_SOURCE_ALLOWED_HOSTS."
        ) from None


Connector = Callable[..., Any]


def _psycopg_connect(**kwargs: Any) -> Any:
    import psycopg

    return psycopg.connect(**kwargs)


# Replaced by tests with a fake; looked up at call time.
postgres_connect: Connector = _psycopg_connect


class SupabasePostgresSource:
    kind = SourceKind.SUPABASE_POSTGRES

    def __init__(self, connection_string: str, schema: str = "public") -> None:
        self.target = parse_postgres_url(connection_string)
        self.schema = schema or "public"

    def _connect_kwargs(self, address: str | None) -> dict[str, Any]:
        timeout_ms = int(_timeout() * 1000)
        kwargs: dict[str, Any] = {
            "host": self.target.host,
            "port": self.target.port,
            "dbname": self.target.database,
            "user": self.target.user,
            "password": self.target.password,
            "sslmode": self.target.sslmode,
            "connect_timeout": max(2, int(_timeout())),
            "application_name": "anum-tyre-planning",
            "options": f"-c default_transaction_read_only=on -c statement_timeout={timeout_ms}",
        }
        if address:
            kwargs["hostaddr"] = address
        return kwargs

    async def _run(self, work: Callable[[Any], Any]) -> Any:
        address = await _postgres_address(self.target)
        kwargs = self._connect_kwargs(address)

        def call() -> Any:
            try:
                connection = postgres_connect(**kwargs)
            except Exception as exc:  # psycopg.OperationalError and friends
                raise TyreSourceError(_postgres_problem(exc)) from None
            try:
                with connection.transaction():
                    return work(connection)
            except TyreSourceError:
                raise
            except Exception as exc:
                raise TyreSourceError(_postgres_problem(exc)) from None
            finally:
                connection.close()

        try:
            return await asyncio.wait_for(asyncio.to_thread(call), timeout=_timeout() * 3)
        except TimeoutError:
            raise TyreSourceError(f"The database did not answer within {int(_timeout() * 3)} seconds.") from None

    async def test(self) -> str:
        def work(connection: Any) -> str:
            row = connection.execute("select current_user, current_setting('transaction_read_only')").fetchone()
            return f"Connected as {row[0]} (read-only session: {row[1]})."

        return str(await self._run(work))

    async def discover(self) -> list[SourceTable]:
        schema = self.schema

        def work(connection: Any) -> list[SourceTable]:
            rows = connection.execute(
                """
                select c.table_name, c.column_name, c.data_type,
                       coalesce(cls.reltuples, -1)::bigint,
                       has_table_privilege(format('%%I.%%I', c.table_schema, c.table_name), 'INSERT, UPDATE, DELETE')
                from information_schema.columns c
                join information_schema.tables t
                  on t.table_schema = c.table_schema and t.table_name = c.table_name
                left join pg_namespace ns on ns.nspname = c.table_schema
                left join pg_class cls on cls.relnamespace = ns.oid and cls.relname = c.table_name
                where c.table_schema = %s and t.table_type in ('BASE TABLE', 'VIEW')
                order by c.table_name, c.ordinal_position
                """,
                (schema,),
            ).fetchall()
            tables: dict[str, SourceTable] = {}
            for table_name, column, data_type, estimate, writable in rows:
                if table_name not in tables:
                    if len(tables) >= MAX_TABLES:
                        break
                    tables[table_name] = SourceTable(
                        name=table_name if schema == "public" else f"{schema}.{table_name}",
                        rows=int(estimate) if estimate is not None and estimate >= 0 else None,
                        writable=bool(writable),
                    )
                tables[table_name].columns.append(SourceColumn(name=column, type=data_type))
            return list(tables.values())

        return list(await self._run(work))

    async def fetch(self, table: str, columns: list[str]) -> FetchResult:
        from psycopg import sql

        schema, name = _split_table(table, self.schema)
        limit = _max_rows()
        statement = sql.SQL("select {columns} from {table} limit {limit}").format(
            columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            table=sql.Identifier(schema, name),
            limit=sql.Literal(limit + 1),
        )

        def work(connection: Any) -> FetchResult:
            cursor = connection.execute(statement)
            rows = [dict(zip(columns, values)) for values in cursor.fetchmany(limit + 1)]
            return FetchResult(rows=rows[:limit], cut=len(rows) > limit)

        return await self._run(work)  # type: ignore[no-any-return]


def _postgres_problem(exc: Exception) -> str:
    text = str(exc).lower()
    if "read-only transaction" in text:
        return "The statement tried to write; ANUM's session is read-only, so nothing was changed."
    if "password authentication failed" in text or "authentication" in text:
        return "The database refused the user or password."
    if "does not exist" in text and "database" in text:
        return "The database name in the connection string does not exist."
    if "permission denied" in text:
        return "The read-only login may not read this table: grant it SELECT (see the setup steps)."
    if "does not exist" in text:
        return "A mapped table or column does not exist: run discovery again and fix the mapping."
    if "timeout" in text or "timed out" in text or "canceling statement" in text:
        return "The database did not answer in time."
    if "ssl" in text:
        return "The database did not accept an encrypted connection."
    return f"The database could not be read ({type(exc).__name__})."


# --- Supabase REST (PostgREST) ------------------------------------------------------------


def normalize_supabase_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        raise TyreSourceError("The project URL is not valid.") from None
    if not parts.hostname or parts.scheme not in {"https", "http"}:
        raise TyreSourceError("Use the project URL, for example https://<project-ref>.supabase.co.")
    if parts.username or parts.password or parts.query:
        raise TyreSourceError("The project URL must not carry credentials or a query.")
    policy = _policy()
    if policy.enforce and parts.scheme != "https" and not policy.allows_host(normalize_host(parts.hostname), parts.port or 80, 80):
        raise TyreSourceError("The project URL must use HTTPS.")
    base = f"{parts.scheme}://{parts.netloc}"
    return base.rstrip("/")


HttpClientFactory = Callable[[], httpx.AsyncClient]


def _guarded_client() -> httpx.AsyncClient:
    policy = _policy()
    if not policy.enforce:
        return httpx.AsyncClient(timeout=_timeout(), follow_redirects=False)
    return httpx.AsyncClient(
        timeout=_timeout(), follow_redirects=False, trust_env=False, transport=PinnedEgressTransport(policy)
    )


def _google_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_timeout(), follow_redirects=False)


# Replaced by tests with clients on an httpx.MockTransport.
rest_client_factory: HttpClientFactory = _guarded_client
google_client_factory: HttpClientFactory = _google_client


def _http_problem(status: int, service: str) -> str:
    if status in (401, 403):
        return f"{service} refused the key (HTTP {status}). Check the key and that its role may read these tables."
    if status == 404:
        return f"{service} has no such table or project (HTTP 404)."
    if status == 429:
        return f"{service} is rate limiting requests (HTTP 429); try again in a minute."
    return f"{service} answered HTTP {status}."


def _column_selector(column: str) -> str:
    return column if _SAFE_COLUMN.match(column) else '"' + column.replace('"', "") + '"'


class SupabaseRestSource:
    kind = SourceKind.SUPABASE_REST

    def __init__(self, url: str, api_key: str, schema: str = "public") -> None:
        self.base = normalize_supabase_url(url)
        self.api_key = api_key.strip()
        self.schema = schema or "public"
        if not self.api_key:
            raise TyreSourceError("Enter the API key.")

    def _headers(self, schema: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.api_key,
            "authorization": f"Bearer {self.api_key}",
            "accept-encoding": "identity",
        }
        if (schema or self.schema) != "public":
            headers["accept-profile"] = schema or self.schema
        return headers

    async def _get(self, path: str, *, params: dict[str, str] | None = None, headers: dict[str, str]) -> httpx.Response:
        try:
            async with rest_client_factory() as client:
                response = await client.get(f"{self.base}{path}", params=params, headers=headers)
        except UnsafeModelEndpointError:
            raise TyreSourceError(
                "The project URL must be a public address. An operator can allow a private host with "
                "ANUM_TYRE_SOURCE_ALLOWED_HOSTS."
            ) from None
        except httpx.TimeoutException:
            raise TyreSourceError(f"Supabase did not answer within {int(_timeout())} seconds.") from None
        except httpx.HTTPError as exc:
            raise TyreSourceError(f"Supabase could not be reached ({type(exc).__name__}).") from None
        if response.status_code >= 400:
            raise TyreSourceError(_http_problem(response.status_code, "Supabase"))
        return response

    async def test(self) -> str:
        await self._get("/rest/v1/", headers={**self._headers(), "accept": "application/openapi+json"})
        return "Supabase accepted the key."

    async def discover(self) -> list[SourceTable]:
        response = await self._get("/rest/v1/", headers={**self._headers(), "accept": "application/openapi+json"})
        try:
            definitions = response.json().get("definitions") or {}
        except ValueError:
            definitions = {}
        if not definitions:
            raise TyreSourceError(
                "Supabase returned no table list for this key.",
                hint="Use a key whose role may read the tables (see the setup steps), or connect with PostgreSQL.",
            )
        tables = []
        for name, definition in list(definitions.items())[:MAX_TABLES]:
            properties = (definition or {}).get("properties") or {}
            tables.append(
                SourceTable(
                    name=name if self.schema == "public" else f"{self.schema}.{name}",
                    columns=[
                        SourceColumn(name=column, type=str(spec.get("format") or spec.get("type") or "unknown"))
                        for column, spec in properties.items()
                    ],
                )
            )
        return tables

    async def fetch(self, table: str, columns: list[str]) -> FetchResult:
        schema, name = _split_table(table, self.schema)
        limit = _max_rows()
        rows: list[dict[str, Any]] = []
        select = ",".join(_column_selector(column) for column in columns)
        offset = 0
        while len(rows) <= limit:
            page = min(PAGE_SIZE, limit + 1 - len(rows))
            response = await self._get(
                f"/rest/v1/{quote(name, safe='')}",
                params={"select": select, "limit": str(page), "offset": str(offset)},
                headers=self._headers(schema),
            )
            try:
                batch = response.json()
            except ValueError:
                raise TyreSourceError("Supabase returned something that is not JSON.") from None
            if not isinstance(batch, list):
                raise TyreSourceError("Supabase returned an unexpected shape for a table read.")
            rows.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < page:
                break
            offset += len(batch)
        return FetchResult(rows=rows[:limit], cut=len(rows) > limit)


# --- Firestore ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceAccount:
    project_id: str
    client_email: str
    private_key: str
    private_key_id: str | None


def parse_service_account(value: str) -> ServiceAccount:
    try:
        data = json.loads(value)
    except ValueError:
        raise TyreSourceError("The service account key must be the JSON file Google gave you.") from None
    if not isinstance(data, dict) or data.get("type") != "service_account":
        raise TyreSourceError("The JSON is not a service account key (type must be service_account).")
    missing = [key for key in ("project_id", "client_email", "private_key") if not data.get(key)]
    if missing:
        raise TyreSourceError("The service account key is missing: " + ", ".join(missing) + ".")
    token_uri = data.get("token_uri", GOOGLE_SIGN_IN_URL)
    if token_uri != GOOGLE_SIGN_IN_URL:
        raise TyreSourceError("The service account key names an unexpected token endpoint.")
    if "PRIVATE KEY" not in str(data["private_key"]):
        raise TyreSourceError("The service account key has no private key.")
    return ServiceAccount(
        project_id=str(data["project_id"]),
        client_email=str(data["client_email"]),
        private_key=str(data["private_key"]),
        private_key_id=data.get("private_key_id"),
    )


def decode_firestore_value(value: dict[str, Any]) -> Any:
    if "nullValue" in value:
        return None
    for key in ("stringValue", "booleanValue", "timestampValue", "bytesValue"):
        if key in value:
            return value[key]
    if "integerValue" in value:
        try:
            return int(value["integerValue"])
        except (TypeError, ValueError):
            return None
    if "doubleValue" in value:
        return value["doubleValue"]
    if "referenceValue" in value:
        return str(value["referenceValue"]).rsplit("/", 1)[-1]
    if "geoPointValue" in value:
        return None
    if "mapValue" in value:
        fields = (value["mapValue"] or {}).get("fields") or {}
        return {key: decode_firestore_value(item) for key, item in fields.items()}
    if "arrayValue" in value:
        return [decode_firestore_value(item) for item in (value["arrayValue"] or {}).get("values") or []]
    return None


def flatten(document: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in document.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


def decode_document(document: dict[str, Any]) -> dict[str, Any]:
    fields = document.get("fields") or {}
    decoded = flatten({key: decode_firestore_value(value) for key, value in fields.items()})
    name = str(document.get("name", ""))
    decoded["__id__"] = name.rsplit("/", 1)[-1]
    return decoded


def _firestore_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    return "string"


class FirestoreSource:
    kind = SourceKind.FIRESTORE

    def __init__(self, service_account_json: str, database: str = "(default)") -> None:
        self.account = parse_service_account(service_account_json)
        if not re.fullmatch(r"\(default\)|[a-z][a-z0-9-]{3,62}", database or "(default)"):
            raise TyreSourceError("The Firestore database id is not valid.")
        self.database = database or "(default)"
        self._token: tuple[str, float] | None = None

    @property
    def root(self) -> str:
        return (
            f"https://{FIRESTORE_HOST}/v1/projects/{quote(self.account.project_id, safe='')}"
            f"/databases/{quote(self.database, safe='()')}/documents"
        )

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        now = int(time.time())
        claims = {"iss": self.account.client_email, "scope": FIRESTORE_SCOPE, "aud": GOOGLE_SIGN_IN_URL,
                  "iat": now, "exp": now + 3600}
        headers = {"kid": self.account.private_key_id} if self.account.private_key_id else None
        try:
            assertion = jwt.encode(claims, self.account.private_key, algorithm="RS256", headers=headers)
        except Exception:
            raise TyreSourceError("The service account's private key could not sign a request.") from None
        response = await client.post(
            GOOGLE_SIGN_IN_URL,
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
        )
        if response.status_code >= 400:
            raise TyreSourceError(_http_problem(response.status_code, "Google sign-in"))
        token = response.json().get("access_token")
        if not token:
            raise TyreSourceError("Google sign-in returned no access token.")
        self._token = (str(token), now + int(response.json().get("expires_in", 3600)))
        return str(token)

    async def _call(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with google_client_factory() as client:
                token = await self._access_token(client)
                response = await client.request(method, url, headers={"authorization": f"Bearer {token}"}, **kwargs)
        except httpx.TimeoutException:
            raise TyreSourceError(f"Firestore did not answer within {int(_timeout())} seconds.") from None
        except httpx.HTTPError as exc:
            raise TyreSourceError(f"Firestore could not be reached ({type(exc).__name__}).") from None
        if response.status_code >= 400:
            raise TyreSourceError(_http_problem(response.status_code, "Firestore"))
        try:
            body = response.json()
        except ValueError:
            raise TyreSourceError("Firestore returned something that is not JSON.") from None
        return body if isinstance(body, dict) else {}

    async def test(self) -> str:
        await self._call("POST", f"{self.root}:listCollectionIds", json={"pageSize": 1})
        return f"Signed in as {self.account.client_email}."

    async def discover(self) -> list[SourceTable]:
        body = await self._call("POST", f"{self.root}:listCollectionIds", json={"pageSize": MAX_TABLES})
        tables = []
        for collection in (body.get("collectionIds") or [])[:MAX_TABLES]:
            sample = await self._call("GET", f"{self.root}/{quote(str(collection), safe='')}",
                                      params={"pageSize": str(FIRESTORE_SAMPLE)})
            documents = [decode_document(item) for item in sample.get("documents") or []]
            columns: dict[str, str] = {}
            for document in documents:
                for key, value in document.items():
                    if value is not None:
                        columns.setdefault(key, _firestore_type(value))
                    else:
                        columns.setdefault(key, "unknown")
            tables.append(
                SourceTable(
                    name=str(collection),
                    columns=[SourceColumn(name=name, type=kind) for name, kind in columns.items()],
                    rows=len(documents),
                )
            )
        return tables

    async def fetch(self, table: str, columns: list[str]) -> FetchResult:
        limit = _max_rows()
        rows: list[dict[str, Any]] = []
        token: str | None = None
        path = "/".join(quote(part, safe="") for part in table.strip("/").split("/"))
        while len(rows) <= limit:
            params: list[tuple[str, str]] = [("pageSize", "300")]
            params += [("mask.fieldPaths", column) for column in columns if column != "__id__"]
            if token:
                params.append(("pageToken", token))
            body = await self._call("GET", f"{self.root}/{path}", params=params)
            rows.extend(decode_document(item) for item in body.get("documents") or [])
            token = body.get("nextPageToken")
            if not token:
                break
        return FetchResult(rows=rows[:limit], cut=len(rows) > limit)


TyreSource = SupabasePostgresSource | SupabaseRestSource | FirestoreSource


def open_source(kind: SourceKind, *, endpoint: str, secret: str, schema: str = "public") -> TyreSource:
    if kind == SourceKind.SUPABASE_POSTGRES:
        return SupabasePostgresSource(secret, schema)
    if kind == SourceKind.SUPABASE_REST:
        return SupabaseRestSource(endpoint, secret, schema)
    return FirestoreSource(secret, endpoint or "(default)")


SourceOpener = Callable[..., TyreSource]
AsyncWork = Callable[[TyreSource], Awaitable[Any]]
