"""Integration health, per-workspace switches, health history and workspace credentials.

docs/tools-and-integrations.md. Integrations are defined by the operator's Settings: a
workspace can turn one off, test it, read its last failure and how to fix it, and, for an
integration that accepts one, store its own credential (Fernet ciphertext only,
``anum_api.secret_box``). A workspace never changes where ANUM connects (SSRF): probes and
tools always use the operator-configured endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from contextlib import AbstractContextManager
from datetime import datetime
from enum import StrEnum
from threading import RLock
from time import perf_counter
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from .audit import AuditRecord
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store
from .settings import Settings

logger = logging.getLogger("anum.integrations")

EXTERNAL_WEBHOOK_INTEGRATION_ID = "external-webhook"
PROBE_TIMEOUT_SECONDS = 2.5


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
    # An external system an agent tool calls (the REST webhook behind external.action).
    TOOL = "tool"
    EMAIL = "email"


class CredentialMetadata(BaseModel):
    configured: bool
    # environment (the operator's Settings), workspace (stored by an owner) or none.
    source: str = "environment"
    scopes: list[str] = Field(default_factory=list)
    expires_at: str | None = None


class WorkspaceCredentialView(BaseModel):
    """What the API ever shows of a stored credential: never the value."""

    integration_id: str
    configured: bool
    hint: str | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None


class IntegrationCheck(BaseModel):
    """The latest health check of an integration in a workspace and its latest failure."""

    status: IntegrationStatus
    detail: str = Field(max_length=500)
    latency_ms: int | None = Field(default=None, ge=0)
    checked_by: str
    checked_at: datetime
    last_error: str | None = Field(default=None, max_length=500)
    last_error_at: datetime | None = None


class StoredCredential(BaseModel):
    ciphertext: str
    hint: str
    updated_by: str
    updated_at: datetime


class IntegrationHealth(BaseModel):
    id: str
    name: str
    kind: IntegrationKind
    status: IntegrationStatus
    endpoint: str
    latency_ms: int | None = Field(default=None, ge=0)
    detail: str
    credentials: CredentialMetadata
    # Whether this workspace uses it (an owner's switch over the operator's default).
    enabled: bool = True
    # Whether the operator configured it for this deployment; when false no workspace
    # switch can turn it on.
    available: bool = True
    # What to do about a degraded or disabled integration, in words an operator can act on.
    remediation: str | None = None
    # The operator setting that configures it (for example ANUM_NATS_URL).
    environment_variable: str | None = None
    checked_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    accepts_workspace_credential: bool = False
    workspace_credential: WorkspaceCredentialView | None = None


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
    environment_variable: str | None = None
    # How to fix a failed health check.
    remediation: str | None = None
    # True when a workspace owner may store its own credential for it.
    accepts_workspace_credential: bool = False
    # Set internally when an owner turned it off for the workspace.
    workspace_disabled: bool = False


class IntegrationConfiguration(BaseModel):
    enabled: bool = True
    # Informational only: probes and tools never connect to a workspace-saved endpoint.
    endpoint: str | None = Field(default=None, min_length=1, max_length=500)


class IntegrationConfigurationView(IntegrationConfiguration):
    id: str
    tenant_id: str
    workspace_id: str


class IntegrationCredentialUpdate(BaseModel):
    # Printable ASCII without spaces: it is sent as a bearer token in an HTTP header.
    secret: str = Field(min_length=12, max_length=4096, pattern=r"^[\x21-\x7e]+$")


class IntegrationNotFoundError(KeyError):
    """No integration with that id is defined in this deployment."""


class CredentialNotAcceptedError(ValueError):
    """The integration's credentials come from the operator's Settings only."""


class IntegrationConfigurationStore(Protocol):
    """Per-workspace integration state: switches, health history, credentials, audit.

    The PostgreSQL store (``anum_api.db.workspace_settings_repository``) keeps them in
    the RLS-protected ``integration_configurations``, ``integration_health_checks`` and
    ``integration_credentials`` tables, and audits into ``audit_records`` in the same
    transaction.
    """

    def list_configurations(self, context: TenantContext) -> dict[str, IntegrationConfiguration]: ...
    def get(self, context: TenantContext, integration_id: str) -> IntegrationConfiguration | None: ...
    def save(
        self, context: TenantContext, integration_id: str, configuration: IntegrationConfiguration
    ) -> IntegrationConfiguration: ...
    def list_checks(self, context: TenantContext) -> dict[str, IntegrationCheck]: ...
    def save_check(self, context: TenantContext, integration_id: str, check: IntegrationCheck) -> IntegrationCheck: ...
    def list_credentials(self, context: TenantContext) -> dict[str, StoredCredential]: ...
    def get_credential(self, context: TenantContext, integration_id: str) -> StoredCredential | None: ...
    def save_credential(self, context: TenantContext, integration_id: str, credential: StoredCredential) -> StoredCredential: ...
    def delete_credential(self, context: TenantContext, integration_id: str) -> bool: ...
    def record_audit(self, record: AuditRecord) -> AuditRecord: ...


class InMemoryIntegrationConfigurationStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], IntegrationConfiguration] = {}
        self._checks: dict[tuple[str, str, str], IntegrationCheck] = {}
        self._credentials: dict[tuple[str, str, str], StoredCredential] = {}
        self._lock = RLock()

    @staticmethod
    def _scoped(items: dict[tuple[str, str, str], Any], context: TenantContext) -> dict[str, Any]:
        return {
            integration_id: value
            for (tenant_id, workspace_id, integration_id), value in items.items()
            if tenant_id == context.tenant_id and workspace_id == context.workspace_id
        }

    def list_configurations(self, context: TenantContext) -> dict[str, IntegrationConfiguration]:
        with self._lock:
            return self._scoped(self._items, context)

    def get(self, context: TenantContext, integration_id: str) -> IntegrationConfiguration | None:
        with self._lock:
            return self._items.get((context.tenant_id, context.workspace_id, integration_id))

    def save(
        self, context: TenantContext, integration_id: str, configuration: IntegrationConfiguration
    ) -> IntegrationConfiguration:
        with self._lock:
            self._items[(context.tenant_id, context.workspace_id, integration_id)] = configuration
        return configuration

    def list_checks(self, context: TenantContext) -> dict[str, IntegrationCheck]:
        with self._lock:
            return self._scoped(self._checks, context)

    def save_check(self, context: TenantContext, integration_id: str, check: IntegrationCheck) -> IntegrationCheck:
        with self._lock:
            self._checks[(context.tenant_id, context.workspace_id, integration_id)] = check
        return check

    def list_credentials(self, context: TenantContext) -> dict[str, StoredCredential]:
        with self._lock:
            return self._scoped(self._credentials, context)

    def get_credential(self, context: TenantContext, integration_id: str) -> StoredCredential | None:
        with self._lock:
            return self._credentials.get((context.tenant_id, context.workspace_id, integration_id))

    def save_credential(self, context: TenantContext, integration_id: str, credential: StoredCredential) -> StoredCredential:
        with self._lock:
            self._credentials[(context.tenant_id, context.workspace_id, integration_id)] = credential
        return credential

    def delete_credential(self, context: TenantContext, integration_id: str) -> bool:
        with self._lock:
            return self._credentials.pop((context.tenant_id, context.workspace_id, integration_id), None) is not None

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        # The same in-memory recorder the audit export reads.
        from .governance import governance_store

        return governance_store.record_audit(record)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._checks.clear()
            self._credentials.clear()


ConfigurationStoreOpener = Callable[[TenantContext], AbstractContextManager[IntegrationConfigurationStore]]


def credential_hint(secret: str) -> str:
    return f"…{secret[-4:]}"


def _audit(
    store: IntegrationConfigurationStore,
    context: TenantContext,
    action: str,
    integration_id: str,
    outcome: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    store.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=f"integration:{integration_id}",
            outcome=outcome,
            correlation_id=new_id("correlation"),
            created_at=utc_now(),
            metadata=metadata or {},
        )
    )


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

    def _definition(self, integration_id: str) -> IntegrationDefinition:
        definition = self._definitions.get(integration_id)
        if definition is None:
            raise IntegrationNotFoundError(integration_id)
        return definition

    async def health(self, context: TenantContext | None = None) -> list[IntegrationHealth]:
        """Probe every integration; with a context, apply and record the workspace's state."""
        if context is None:
            return list(await asyncio.gather(*(self._check(item) for item in self._definitions.values())))
        # Read the workspace state first; the probes run without holding a database session.
        with self._open_store(context) as store:
            configurations = store.list_configurations(context)
            checks = store.list_checks(context)
            credentials = store.list_credentials(context)
        results = list(await asyncio.gather(*(
            self._check(self._effective(item, configurations.get(item.id)))
            for item in self._definitions.values()
        )))
        try:
            recorded = self._record(context, results, checks)
        except Exception as exc:  # the live answer still stands; history is best effort here
            logger.warning(
                "integration_health_not_recorded error_class=%s",
                type(exc).__name__,
                extra={"anum_integration": {"event": "health_not_recorded", "error_class": type(exc).__name__}},
            )
            recorded = checks
        return [self._decorate(item, recorded.get(item.id), credentials.get(item.id)) for item in results]

    async def test(self, integration_id: str, context: TenantContext) -> IntegrationHealth:
        """Test one integration now (an owner's "Test connection"); recorded and audited."""
        definition = self._definition(integration_id)
        with self._open_store(context) as store:
            configuration = store.get(context, integration_id)
            previous = store.list_checks(context)
            credential = store.get_credential(context, integration_id)
        result = await self._check(self._effective(definition, configuration))
        recorded = self._record(context, [result], previous, audit_test=True)
        return self._decorate(result, recorded.get(integration_id), credential)

    def _record(
        self,
        context: TenantContext,
        results: list[IntegrationHealth],
        previous: dict[str, IntegrationCheck],
        *,
        audit_test: bool = False,
    ) -> dict[str, IntegrationCheck]:
        now = utc_now()
        recorded: dict[str, IntegrationCheck] = {}
        with self._open_store(context) as store:
            for result in results:
                before = previous.get(result.id)
                failed = result.status == IntegrationStatus.DEGRADED
                check = IntegrationCheck(
                    status=result.status,
                    detail=result.detail[:500],
                    latency_ms=result.latency_ms,
                    checked_by=context.user_id,
                    checked_at=now,
                    last_error=result.detail[:500] if failed else (before.last_error if before else None),
                    last_error_at=now if failed else (before.last_error_at if before else None),
                )
                recorded[result.id] = store.save_check(context, result.id, check)
                if audit_test:
                    _audit(
                        store, context, "integration.tested", result.id,
                        outcome="failed" if failed else "success",
                        metadata={"status": result.status.value, "latency_ms": result.latency_ms},
                    )
        return recorded

    @staticmethod
    def _decorate(
        result: IntegrationHealth, check: IntegrationCheck | None, credential: StoredCredential | None
    ) -> IntegrationHealth:
        update: dict[str, Any] = {}
        if check is not None:
            update.update(checked_at=check.checked_at, last_error=check.last_error, last_error_at=check.last_error_at)
        if result.accepts_workspace_credential:
            view = WorkspaceCredentialView(integration_id=result.id, configured=credential is not None)
            if credential is not None:
                view = view.model_copy(update={
                    "hint": credential.hint, "updated_by": credential.updated_by, "updated_at": credential.updated_at,
                })
                update["credentials"] = result.credentials.model_copy(update={"configured": True, "source": "workspace"})
            update["workspace_credential"] = view
        return result.model_copy(update=update)

    def configure(self, integration_id: str, context: TenantContext, configuration: IntegrationConfiguration) -> IntegrationConfigurationView:
        self._definition(integration_id)
        with self._open_store(context) as store:
            store.save(context, integration_id, configuration)
            _audit(store, context, "integration.configured", integration_id, metadata={"enabled": configuration.enabled})
        return IntegrationConfigurationView(id=integration_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, **configuration.model_dump())

    def configuration(self, integration_id: str, context: TenantContext) -> IntegrationConfigurationView:
        definition = self._definition(integration_id)
        with self._open_store(context) as store:
            stored = store.get(context, integration_id)
        value = stored or IntegrationConfiguration(enabled=definition.configured, endpoint=definition.endpoint)
        return IntegrationConfigurationView(id=integration_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, **value.model_dump())

    def set_credential(self, integration_id: str, context: TenantContext, secret: str) -> WorkspaceCredentialView:
        """Store a workspace credential, encrypted; the value is never returned or logged."""
        definition = self._definition(integration_id)
        if not definition.accepts_workspace_credential:
            raise CredentialNotAcceptedError(self._operator_credential_text(definition))
        from .secret_box import secret_cipher

        stored = StoredCredential(
            ciphertext=secret_cipher().encrypt(secret), hint=credential_hint(secret),
            updated_by=context.user_id, updated_at=utc_now(),
        )
        with self._open_store(context) as store:
            replaced = store.get_credential(context, integration_id) is not None
            store.save_credential(context, integration_id, stored)
            _audit(store, context, "integration.credential_set", integration_id, metadata={"replaced": replaced})
        return WorkspaceCredentialView(
            integration_id=integration_id, configured=True, hint=stored.hint,
            updated_by=stored.updated_by, updated_at=stored.updated_at,
        )

    def delete_credential(self, integration_id: str, context: TenantContext) -> bool:
        definition = self._definition(integration_id)
        if not definition.accepts_workspace_credential:
            raise CredentialNotAcceptedError(self._operator_credential_text(definition))
        with self._open_store(context) as store:
            removed = store.delete_credential(context, integration_id)
            if removed:
                _audit(store, context, "integration.credential_removed", integration_id)
        return removed

    def workspace_credential(self, integration_id: str) -> Callable[[TenantContext], str | None]:
        """A lookup for tools: the caller's workspace credential for ``integration_id``, decrypted."""

        def lookup(context: TenantContext) -> str | None:
            with self._open_store(context) as store:
                stored = store.get_credential(context, integration_id)
            if stored is None:
                return None
            from .secret_box import secret_cipher

            return secret_cipher().decrypt(stored.ciphertext)

        return lookup

    @staticmethod
    def _operator_credential_text(definition: IntegrationDefinition) -> str:
        where = f" ({definition.environment_variable} and related settings)" if definition.environment_variable else ""
        return f"{definition.name} uses the credentials the operator configured for this deployment{where}; a workspace cannot store its own."

    @staticmethod
    def _effective(definition: IntegrationDefinition, value: IntegrationConfiguration | None) -> IntegrationDefinition:
        # A workspace may only switch an integration on or off. Probes and the reported endpoint
        # always come from the operator's Settings: a tenant-saved endpoint is never connected to (SSRF).
        if value is None or (definition.disabled_detail and not definition.configured):
            # Nothing to switch: no override, or the operator has not configured it at all.
            return definition
        if value.enabled:
            # Turning it "on" never enables what the operator has not configured.
            return definition
        return definition.model_copy(
            update={
                "configured": False,
                "workspace_disabled": True,
                "disabled_detail": "Turned off for this workspace by an owner.",
            }
        )

    def switched_off(self, integration_id: str, context: TenantContext) -> bool:
        """True only when an owner turned ``integration_id`` off for the caller's workspace.

        Unlike ``configuration().enabled`` this ignores the operator default, for
        integrations whose setup is checked elsewhere (the mail providers).
        """
        if integration_id not in self._definitions:
            return False
        with self._open_store(context) as store:
            stored = store.get(context, integration_id)
        return stored is not None and not stored.enabled

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
            if definition.workspace_disabled:
                remediation = "A workspace owner can turn it back on here, under Integrations."
            elif definition.disabled_detail:
                remediation = None  # the detail already says what to set
            elif definition.environment_variable:
                remediation = f"The operator enables it with {definition.environment_variable} (docs/tools-and-integrations.md)."
            else:
                remediation = None
            return self._result(
                definition,
                IntegrationStatus.DISABLED,
                definition.disabled_detail or "Integration is not enabled for this environment.",
                remediation=remediation,
            )
        started = perf_counter()
        try:
            await asyncio.wait_for(definition.probe(), timeout=PROBE_TIMEOUT_SECONDS)
        except Exception as exc:
            # Only the exception class: messages can carry hosts, users or secrets.
            reason = (
                f"no answer within {PROBE_TIMEOUT_SECONDS:g} seconds"
                if isinstance(exc, TimeoutError) else type(exc).__name__
            )
            return self._result(
                definition,
                IntegrationStatus.DEGRADED,
                f"Health check failed: {reason}",
                round((perf_counter() - started) * 1000),
                remediation=definition.remediation,
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
        *,
        remediation: str | None = None,
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
            enabled=not definition.workspace_disabled,
            available=definition.configured or definition.workspace_disabled,
            remediation=remediation,
            environment_variable=definition.environment_variable,
            accepts_workspace_credential=definition.accepts_workspace_credential,
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
                environment_variable="ANUM_DATABASE_URL",
                disabled_detail=None if settings.repository_backend == "postgresql" else (
                    "Not used: this deployment keeps data in memory (ANUM_REPOSITORY_BACKEND=memory). "
                    "Set ANUM_REPOSITORY_BACKEND=postgresql and ANUM_DATABASE_URL for durable storage."
                ),
                remediation="Check that PostgreSQL is running, reachable from the API and accepts the login in ANUM_DATABASE_URL; then run the migrations (docs/runbooks.md).",
            ),
            IntegrationDefinition(
                id="keycloak",
                name="Keycloak OIDC",
                kind=IntegrationKind.IDENTITY,
                endpoint=settings.keycloak_issuer,
                credentials=CredentialMetadata(configured=bool(settings.keycloak_issuer), scopes=["openid"]),
                probe=lambda: http_probe(f"{settings.keycloak_issuer.rstrip('/')}/.well-known/openid-configuration"),
                environment_variable="ANUM_KEYCLOAK_ISSUER",
                remediation="Check that Keycloak answers <issuer>/.well-known/openid-configuration from the API's network and that ANUM_KEYCLOAK_ISSUER names the realm (docs/identity.md).",
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
                environment_variable="ANUM_NATS_URL",
                disabled_detail=None if settings.event_bus == "nats" else (
                    "Not used: events stay in the API process (ANUM_EVENT_BUS=memory). "
                    "Set ANUM_EVENT_BUS=nats and ANUM_NATS_URL to publish through NATS JetStream."
                ),
                remediation="Check that the NATS server at ANUM_NATS_URL is running with JetStream and reachable from the API (docs/events.md).",
            ),
            IntegrationDefinition(
                id="temporal",
                name="Temporal",
                kind=IntegrationKind.WORKFLOW,
                endpoint=settings.temporal_target,
                configured=settings.runtime_backend == "temporal",
                credentials=CredentialMetadata(configured=bool(settings.temporal_target), scopes=["workflow:execute"]),
                probe=lambda: tcp_probe(*_host_port(settings.temporal_target, 7233)),
                environment_variable="ANUM_TEMPORAL_TARGET",
                disabled_detail=None if settings.runtime_backend == "temporal" else (
                    "Not used: runs execute inside the API (ANUM_RUNTIME_BACKEND=inline). "
                    "Set ANUM_RUNTIME_BACKEND=temporal and ANUM_TEMPORAL_TARGET to run them on Temporal workers."
                ),
                remediation="Check that the Temporal frontend at ANUM_TEMPORAL_TARGET is running and reachable from the API (docs/agent-runtime.md).",
            ),
            IntegrationDefinition(
                id="valkey",
                name="Valkey",
                kind=IntegrationKind.CACHE,
                endpoint=settings.valkey_url,
                credentials=CredentialMetadata(configured=bool(settings.valkey_url), scopes=["cache:read", "cache:write"]),
                probe=lambda: tcp_probe(*_host_port(settings.valkey_url, 6379), payload=b"*1\r\n$4\r\nPING\r\n"),
                environment_variable="ANUM_VALKEY_URL",
                remediation="Check that Valkey at ANUM_VALKEY_URL is running and reachable from the API.",
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
                environment_variable="ANUM_S3_ENDPOINT",
                remediation="Check that the S3 endpoint in ANUM_S3_ENDPOINT answers from the API's network and that ANUM_S3_ACCESS_KEY is set (docs/files.md).",
            ),
            searxng_integration(settings),
            external_webhook_integration(settings),
            *email_integrations(),
        ]
    )


def email_integrations() -> list[IntegrationDefinition]:
    """The three mail providers behind the unified inbox (anum_api/email_inbox.py, docs/email.md).

    Configured when the operator set the provider's OAuth client (``ANUM_EMAIL_*``). A
    workspace owner can switch a provider off here: connecting, syncing, reading and the
    email tools then refuse that provider's mailboxes in that workspace.
    """
    from .email_inbox import email_settings, integration_id, provider_client, provider_problems, provider_scopes
    from .email_providers import PROVIDER_NAMES, EmailProvider

    definitions = []
    for provider in EmailProvider:
        problems = provider_problems(provider, email_settings)
        host = provider_client(provider, email_settings).api_host if not problems else {
            EmailProvider.GMAIL: "gmail.googleapis.com",
            EmailProvider.OUTLOOK: "graph.microsoft.com",
            EmailProvider.ZOHO: f"mail.zoho.{email_settings.zoho_data_centre}",
        }[provider]

        def probe(host: str = host) -> Awaitable[None]:
            return tcp_probe(host, 443)

        definitions.append(
            IntegrationDefinition(
                id=integration_id(provider),
                name=PROVIDER_NAMES[provider],
                kind=IntegrationKind.EMAIL,
                endpoint=f"https://{host}",
                configured=not problems,
                disabled_detail=None if not problems else (
                    "Not set up: the server needs " + ", ".join(problems) + " (docs/email.md)."
                ),
                credentials=CredentialMetadata(
                    configured=not problems, source="oauth", scopes=provider_scopes(provider)
                ),
                probe=probe,
            )
        )
    return definitions


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
        environment_variable="ANUM_SEARXNG_URL",
        remediation="Check that SearXNG answers <ANUM_SEARXNG_URL>/healthz on the internal network (the searxng service in the compose stacks).",
    )


def external_webhook_integration(settings: Settings) -> IntegrationDefinition:
    """The REST endpoint behind the ``external.action`` tool (``anum_api.integration_tools``).

    The operator sets the URL (``ANUM_EXTERNAL_WEBHOOK_URL``); a workspace owner may store
    the workspace's own bearer token for it, used instead of ``ANUM_EXTERNAL_WEBHOOK_API_KEY``.
    The health check only opens a TCP connection: posting would run the action.
    """
    raw = (settings.external_webhook_url or "").strip()
    parsed = urlparse(raw) if raw else None
    host = parsed.hostname if parsed else None
    valid = bool(parsed and parsed.scheme in {"http", "https"} and host)
    port = (parsed.port if parsed and parsed.port else (443 if parsed and parsed.scheme == "https" else 80)) if valid else 0
    problem = (
        "Not configured: set ANUM_EXTERNAL_WEBHOOK_URL to the HTTPS endpoint external.action posts to; "
        "until then the tool answers from a built-in mock."
    ) if not raw else "Not configured: ANUM_EXTERNAL_WEBHOOK_URL must be an absolute http(s) URL."

    async def probe() -> None:
        if not valid or host is None:
            raise RuntimeError("external webhook is not configured")
        await tcp_probe(host, port)

    return IntegrationDefinition(
        id=EXTERNAL_WEBHOOK_INTEGRATION_ID,
        name="External REST webhook",
        kind=IntegrationKind.TOOL,
        # Host and scheme only: the path and query may carry tokens.
        endpoint=f"{parsed.scheme}://{host}" if valid and parsed else "not configured",
        configured=valid,
        disabled_detail=None if valid else problem,
        credentials=CredentialMetadata(
            configured=bool(settings.external_webhook_api_key),
            source="environment" if settings.external_webhook_api_key else "none",
            scopes=["external.action"],
        ),
        probe=probe,
        environment_variable="ANUM_EXTERNAL_WEBHOOK_URL",
        remediation="Check that the webhook host accepts connections from the API's network (firewall, DNS, TLS port) and that ANUM_EXTERNAL_WEBHOOK_URL is right.",
        accepts_workspace_credential=True,
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
