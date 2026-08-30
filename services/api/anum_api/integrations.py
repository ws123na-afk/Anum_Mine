from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from enum import StrEnum
from time import perf_counter

import httpx
from pydantic import BaseModel, Field

from .settings import Settings
from .schemas import TenantContext


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
    credentials: CredentialMetadata
    probe: Probe = Field(exclude=True)


class IntegrationConfiguration(BaseModel):
    enabled: bool = True
    endpoint: str | None = Field(default=None, min_length=1, max_length=500)


class IntegrationConfigurationView(IntegrationConfiguration):
    id: str
    tenant_id: str
    workspace_id: str


class IntegrationRegistry:
    def __init__(self, definitions: Iterable[IntegrationDefinition]) -> None:
        items = list(definitions)
        self._definitions = {item.id: item for item in items}
        self._configurations: dict[tuple[str, str, str], IntegrationConfiguration] = {}
        if len(items) != len(self._definitions):
            raise ValueError("integration ids must be unique")

    async def health(self, context: TenantContext | None = None) -> list[IntegrationHealth]:
        return list(await asyncio.gather(*(self._check(self._effective(item, context)) for item in self._definitions.values())))

    def configure(self, integration_id: str, context: TenantContext, configuration: IntegrationConfiguration) -> IntegrationConfigurationView:
        if integration_id not in self._definitions:
            raise KeyError(integration_id)
        self._configurations[(context.tenant_id, context.workspace_id, integration_id)] = configuration
        return IntegrationConfigurationView(id=integration_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, **configuration.model_dump())

    def configuration(self, integration_id: str, context: TenantContext) -> IntegrationConfigurationView:
        definition = self._definitions.get(integration_id)
        if definition is None:
            raise KeyError(integration_id)
        value = self._configurations.get((context.tenant_id, context.workspace_id, integration_id), IntegrationConfiguration(enabled=definition.configured, endpoint=definition.endpoint))
        return IntegrationConfigurationView(id=integration_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, **value.model_dump())

    def _effective(self, definition: IntegrationDefinition, context: TenantContext | None) -> IntegrationDefinition:
        if context is None:
            return definition
        value = self._configurations.get((context.tenant_id, context.workspace_id, definition.id))
        return definition if value is None else definition.model_copy(update={"configured": value.enabled, "endpoint": value.endpoint or definition.endpoint})

    async def _check(self, definition: IntegrationDefinition) -> IntegrationHealth:
        if not definition.configured:
            return self._result(
                definition,
                IntegrationStatus.DISABLED,
                "Integration is not enabled for this environment.",
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
                credentials=CredentialMetadata(configured=bool(settings.nats_url), scopes=["events:publish"]),
                probe=lambda: tcp_probe(*_host_port(settings.nats_url, 4222)),
            ),
            IntegrationDefinition(
                id="temporal",
                name="Temporal",
                kind=IntegrationKind.WORKFLOW,
                endpoint=settings.temporal_target,
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
                name="MinIO S3",
                kind=IntegrationKind.OBJECT_STORAGE,
                endpoint=settings.s3_endpoint,
                credentials=CredentialMetadata(configured=bool(settings.s3_access_key), scopes=["objects:read", "objects:write"]),
                probe=lambda: http_probe(f"{settings.s3_endpoint.rstrip('/')}/minio/health/live"),
            ),
        ]
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
