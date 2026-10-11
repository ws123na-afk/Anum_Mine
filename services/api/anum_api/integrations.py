from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from contextlib import AbstractContextManager
from enum import StrEnum
from threading import RLock
from time import perf_counter
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

from .schemas import TenantContext
from .scoped_store import open_scoped_store
from .settings import Settings


class IntegrationStatus(StrEnum):
    CONNECTED = "connected"
    DEGRADED = "degraded"
    CONFIGURED = "configured"
    DISABLED = "disabled"


class IntegrationKind(StrEnum):
    DATABASE = "database"
    IDENTITY = "identity"
    EVENT_BUS = "event_bus"
    WORKFLOW = "workflow"
    CACHE = "cache"
    OBJECT_STORAGE = "object_storage"
    SEARCH = "search"


class CredentialMetadata(BaseModel):
    configured: bool
    source: str = "environment"
    scopes: list[str] = Field(default_factory=list)
    expires_at: str | None = None


class IntegrationHealth(BaseModel):
    id: str
    name: str
    kind: IntegrationKind
    status: IntegrationStatus
    endpoint: str
    latency_ms: int | None = Field(default=None, ge=0)
    detail: str
    credentials: CredentialMetadata


Probe = Callable[[], Awaitable[None]]


class IntegrationDefinition(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    id: str
    name: str
    kind: IntegrationKind
    endpoint: str
    configured: bool = True
    # Shown instead of the generic text when the operator has not configured it.
    disabled_detail: str | None = None
    credentials: CredentialMetadata
    probe: Probe = Field(exclude=True)


class IntegrationConfiguration(BaseModel):
    enabled: bool = True
    endpoint: str | None = Field(default=None, min_length=1, max_length=500)


class IntegrationConfigurationView(IntegrationConfiguration):
    id: str
    tenant_id: str
    workspace_id: str


class IntegrationConfigurationStore(Protocol):
    """Per-workspace integration overrides (enabled flag and endpoint).

    The PostgreSQL store (``anum_api.db.workspace_settings_repository``) keeps them in
    the RLS-protected ``integration_configurations`` table.
    """

    def list_configurations(self, context: TenantContext) -> dict[str, IntegrationConfiguration]: ...
    def get(self, context: TenantContext, integration_id: str) -> IntegrationConfiguration | None: ...
    def save(
        self, context: TenantContext, integration_id: str, configuration: IntegrationConfiguration
    ) -> IntegrationConfiguration: ...


class InMemoryIntegrationConfigurationStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], IntegrationConfiguration] = {}
        self._lock = RLock()

    def list_configurations(self, context: TenantContext) -> dict[str, IntegrationConfiguration]:
        with self._lock:
            return {
                integration_id: value
                for (tenant_id, workspace_id, integration_id), value in self._items.items()
                if tenant_id == context.tenant_id and workspace_id == context.workspace_id
            }

    def get(self, context: TenantContext, integration_id: str) -> IntegrationConfiguration | None:
        with self._lock:
            return self._items.get((context.tenant_id, context.workspace_id, integration_id))

    def save(
        self, context: TenantContext, integration_id: str, configuration: IntegrationConfiguration
    ) -> IntegrationConfiguration:
        with self._lock:
            self._items[(context.tenant_id, context.workspace_id, integration_id)] = configuration
        return configuration

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


ConfigurationStoreOpener = Callable[[TenantContext], AbstractContextManager[IntegrationConfigurationStore]]


class IntegrationRegistry:
    def __init__(
        self,
        definitions: Iterable[IntegrationDefinition],
        configuration_store: ConfigurationStoreOpener | None = None,
    ) -> None:
        items = list(definitions)
        self._definitions = {item.id: item for item in items}
        if len(items) != len(self._definitions):
            raise ValueError("integration ids must be unique")
        self.memory_configurations = InMemoryIntegrationConfigurationStore()
        self._open_store = configuration_store or self._open_default_store

    def _open_default_store(self, context: TenantContext) -> AbstractContextManager[IntegrationConfigurationStore]:
        """Chosen by ``ANUM_REPOSITORY_BACKEND`` like the other control-plane stores."""

        def sql_store(session):  # type: ignore[no-untyped-def]
            from .db.workspace_settings_repository import SqlAlchemyIntegrationConfigurationStore

            return SqlAlchemyIntegrationConfigurationStore(session)

        return open_scoped_store(context, self.memory_configurations, sql_store)

    async def health(self, context: TenantContext | None = None) -> list[IntegrationHealth]:
        configurations: dict[str, IntegrationConfiguration] = {}
        if context is not None:
            # Read the overrides first; the probes run without holding a database session.
            with self._open_store(context) as store:
                configurations = store.list_configurations(context)
        return list(await asyncio.gather(*(
            self._check(self._effective(item, configurations.get(item.id)))
            for item in self._definitions.values()
        )))

    def configure(self, integration_id: str, context: TenantContext, configuration: IntegrationConfiguration) -> IntegrationConfigurationView:
        if integration_id not in self._definitions:
            raise KeyError(integration_id)
        with self._open_store(context) as store:
            store.save(context, integration_id, configuration)
        return IntegrationConfigurationView(id=integration_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, **configuration.model_dump())

    def configuration(self, integration_id: str, context: TenantContext) -> IntegrationConfigurationView:
        definition = self._definitions.get(integration_id)
        if definition is None:
            raise KeyError(integration_id)
        with self._open_store(context) as store:
            stored = store.get(context, integration_id)
        value = stored or IntegrationConfiguration(enabled=definition.configured, endpoint=definition.endpoint)
        return IntegrationConfigurationView(id=integration_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, **value.model_dump())

    @staticmethod
    def _effective(definition: IntegrationDefinition, value: IntegrationConfiguration | None) -> IntegrationDefinition:
        # A workspace may only switch an integration on or off. Probes and the reported endpoint
        # always come from the operator's Settings: a tenant-saved endpoint is never connected to (SSRF).
        if value is None or (definition.disabled_detail and not definition.configured):
            # Nothing to switch: no override, or the operator has not configured it at all.
            return definition
        if value.enabled:
            return definition.model_copy(update={"configured": True})
        return definition.model_copy(
            update={"configured": False, "disabled_detail": "Turned off for this workspace by an owner."}
        )

    def workspace_enabled(self, integration_id: str) -> Callable[[TenantContext], bool]:
        """A check for tools: is ``integration_id`` on for the caller's workspace?

        Reads the workspace override in the caller's tenant scope; without one the
        operator's setting applies. Only the enabled flag matters to tools: a saved
        endpoint is never connected to (SSRF).
        """

        def check(context: TenantContext) -> bool:
            return self.configuration(integration_id, context).enabled

        return check

    async def _check(self, definition: IntegrationDefinition) -> IntegrationHealth:
        if not definition.configured:
            return self._result(
                definition,
                IntegrationStatus.DISABLED,
                definition.disabled_detail or "Integration is not enabled for this environment.",
            )
        started = perf_counter()
        try:
            await asyncio.wait_for(definition.probe(), timeout=2.5)
        except Exception as exc:
            return self._result(
                definition,
                IntegrationStatus.DEGRADED,
                f"Health check failed: {type(exc).__name__}",
                round((perf_counter() - started) * 1000),
            )
        return self._result(
            definition,
            IntegrationStatus.CONNECTED,
            "Health check passed.",
            round((perf_counter() - started) * 1000),
        )

    @staticmethod
    def _result(
        definition: IntegrationDefinition,
        status: IntegrationStatus,
        detail: str,
        latency_ms: int | None = None,
    ) -> IntegrationHealth:
        return IntegrationHealth(
            id=definition.id,
            name=definition.name,
            kind=definition.kind,
            status=status,
            endpoint=definition.endpoint,
            latency_ms=latency_ms,
            detail=detail,
            credentials=definition.credentials,
        )


async def http_probe(url: str) -> None:
    async with httpx.AsyncClient(timeout=2) as client:
        response = await client.get(url)
        response.raise_for_status()


async def tcp_probe(host: str, port: int, payload: bytes | None = None) -> None:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        if payload:
            writer.write(payload)
            await writer.drain()
            await asyncio.wait_for(reader.read(64), timeout=1)
    finally:
        writer.close()
        await writer.wait_closed()


def default_integration_registry(settings: Settings) -> IntegrationRegistry:
    return IntegrationRegistry(
        [
            IntegrationDefinition(
                id="postgresql",
                name="PostgreSQL + pgvector",
                kind=IntegrationKind.DATABASE,
                endpoint=_safe_endpoint(settings.database_url),
                configured=settings.repository_backend == "postgresql",
                credentials=CredentialMetadata(configured=True, scopes=["tenant-data"]),
                probe=lambda: _postgres_probe(settings.database_url),
            ),
            IntegrationDefinition(
                id="keycloak",
                name="Keycloak OIDC",
                kind=IntegrationKind.IDENTITY,
                endpoint=settings.keycloak_issuer,
                credentials=CredentialMetadata(configured=bool(settings.keycloak_issuer), scopes=["openid"]),
                probe=lambda: http_probe(f"{settings.keycloak_issuer.rstrip('/')}/.well-known/openid-configuration"),
            ),
            IntegrationDefinition(
                id="nats",
                name="NATS JetStream",
                kind=IntegrationKind.EVENT_BUS,
                endpoint=settings.nats_url,
                # Probed only where the API publishes to it; otherwise it is reported as disabled.
                configured=settings.event_bus == "nats",
                credentials=CredentialMetadata(configured=bool(settings.nats_url), scopes=["events:publish"]),
                probe=lambda: tcp_probe(*_host_port(settings.nats_url, 4222)),
            ),
            IntegrationDefinition(
                id="temporal",
                name="Temporal",
                kind=IntegrationKind.WORKFLOW,
                endpoint=settings.temporal_target,
                configured=settings.runtime_backend == "temporal",
                credentials=CredentialMetadata(configured=bool(settings.temporal_target), scopes=["workflow:execute"]),
                probe=lambda: tcp_probe(*_host_port(settings.temporal_target, 7233)),
            ),
            IntegrationDefinition(
                id="valkey",
                name="Valkey",
                kind=IntegrationKind.CACHE,
                endpoint=settings.valkey_url,
                credentials=CredentialMetadata(configured=bool(settings.valkey_url), scopes=["cache:read", "cache:write"]),
                probe=lambda: tcp_probe(*_host_port(settings.valkey_url, 6379), payload=b"*1\r\n$4\r\nPING\r\n"),
            ),
            IntegrationDefinition(
                id="minio",
                # The id stays "minio" so stored workspace configurations keep working.
                name="S3 object storage",
                kind=IntegrationKind.OBJECT_STORAGE,
                endpoint=settings.s3_endpoint,
                credentials=CredentialMetadata(configured=bool(settings.s3_access_key), scopes=["objects:read", "objects:write"]),
                # Vendor-neutral: S3 has no standard health URL, so check the endpoint answers.
                probe=lambda: tcp_probe(*_host_port(settings.s3_endpoint, 443 if settings.s3_endpoint.startswith("https") else 80)),
            ),
            searxng_integration(settings),
        ]
    )


def searxng_integration(settings: Settings) -> IntegrationDefinition:
    """Health of the private SearXNG behind the ``web_search`` tool (anum_api/web_search.py)."""
    from .web_search import SEARXNG_INTEGRATION_ID, SearxngUrlError, normalize_searxng_url

    raw = settings.searxng_url.strip()
    base: str | None = None
    problem = (
        "Web search is off: set ANUM_SEARXNG_URL to the internal SearXNG address "
        "(http://searxng:8080 in the compose stacks) to enable the web_search tool."
    )
    if raw:
        try:
            base = normalize_searxng_url(raw)
        except SearxngUrlError as exc:
            problem = f"Web search is off: {exc}."

    async def probe() -> None:
        if base is None:
            raise RuntimeError("SearXNG is not configured")
        await http_probe(f"{base}/healthz")

    return IntegrationDefinition(
        id=SEARXNG_INTEGRATION_ID,
        name="SearXNG web search",
        kind=IntegrationKind.SEARCH,
        endpoint=base or "not configured",
        configured=base is not None,
        disabled_detail=None if base else problem,
        credentials=CredentialMetadata(configured=base is not None, source="none", scopes=["web_search:read"]),
        probe=probe,
    )


async def _postgres_probe(database_url: str) -> None:
    def check() -> None:
        from sqlalchemy import create_engine, text

        engine = create_engine(database_url, pool_pre_ping=True, connect_args={"connect_timeout": 2})
        try:
            with engine.connect() as connection:
                connection.execute(text("select 1"))
        finally:
            engine.dispose()

    await asyncio.to_thread(check)


def _host_port(endpoint: str, default_port: int) -> tuple[str, int]:
    value = endpoint.split("://", 1)[-1].split("/", 1)[0]
    host, separator, port = value.partition(":")
    return host, int(port) if separator else default_port


def _safe_endpoint(database_url: str) -> str:
    if "@" not in database_url:
        return database_url
    scheme, location = database_url.split("://", 1)
    return f"{scheme}://***@{location.split('@', 1)[1]}"
