"""Per-workspace model configuration storage.

``InMemoryModelConfigStore`` is the local and test default. With
``ANUM_REPOSITORY_BACKEND=postgresql`` configurations live in the RLS-protected
``workspace_model_configs`` table, with the provider API key encrypted at rest
(``anum_api.db.model_config_repository``). Every call is scoped by an explicit
``TenantContext``; the PostgreSQL store sets the tenant and workspace session context
before touching the table, so RLS applies.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import Protocol

from pydantic import BaseModel, SecretStr

from .schemas import TenantContext
from .settings import settings

KEYLESS_PROVIDERS = frozenset({"mock", "ollama"})


class WorkspaceNotProvisionedError(LookupError):
    """The workspace must exist (onboarding complete) before a model can be saved."""


class StoredKeyNotReusableError(ValueError):
    """A save asked to keep the saved API key (``keep_api_key``) but none may be reused.

    ``reason`` is ``"missing"`` (no key is saved) or ``"changed"`` (the provider or base
    URL differs from the saved one: the saved key is never sent to a new host).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def check_key_reusable(*, saved_provider: str, saved_base_url: str, provider: str, base_url: str) -> None:
    """Raise :class:`StoredKeyNotReusableError` unless a saved key may serve this save:
    the provider and the (normalized) base URL are both unchanged."""
    if saved_provider != provider or saved_base_url != base_url:
        raise StoredKeyNotReusableError("changed")


def credential_hint(secret: str | None) -> str | None:
    return f"...{secret[-4:]}" if secret else None


class ModelRouting(BaseModel):
    """Per-workspace model routing (``anthropic`` only), mirroring the server's
    ``ANUM_MODEL_FAST_NAME``/``CAPABLE_NAME``/``FAST_EFFORT``/``CAPABLE_EFFORT``.
    ``None`` means not set: the saved ``model`` serves that route and no effort is sent."""

    fast_model: str | None = None
    capable_model: str | None = None
    fast_effort: str | None = None
    capable_effort: str | None = None


class StoredModelConfig(BaseModel):
    provider: str
    model: str
    base_url: str
    routing: ModelRouting = ModelRouting()
    # Present only when a store was asked for the secret; never serialized to clients.
    api_key: SecretStr | None = None
    credential_configured: bool = False
    credential_hint: str | None = None
    updated_at: datetime
    updated_by_user_id: str | None = None

    @property
    def usable(self) -> bool:
        return self.provider in KEYLESS_PROVIDERS or self.credential_configured

    @property
    def fingerprint(self) -> tuple[object, ...]:
        """Identity of a saved configuration, used to invalidate cached gateways."""
        return (
            self.provider,
            self.model,
            self.base_url,
            self.routing.model_dump_json(),
            self.credential_hint,
            self.updated_at,
        )


class ModelConfigStore(Protocol):
    def get(self, context: TenantContext, *, include_secret: bool = True) -> StoredModelConfig | None: ...

    def save(
        self,
        context: TenantContext,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: str | None,
        updated_at: datetime,
        routing: ModelRouting | None = None,
        keep_api_key: bool = False,
    ) -> StoredModelConfig:
        """Replace the workspace's config. With ``keep_api_key`` (and no ``api_key``) the
        saved encrypted key is kept, only when provider and base URL are unchanged;
        otherwise :class:`StoredKeyNotReusableError`."""
        ...


class InMemoryModelConfigStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], StoredModelConfig] = {}
        self._lock = RLock()

    def get(self, context: TenantContext, *, include_secret: bool = True) -> StoredModelConfig | None:
        with self._lock:
            config = self._items.get((context.tenant_id, context.workspace_id))
        if config is None or include_secret:
            return config
        return config.model_copy(update={"api_key": None})

    def save(
        self,
        context: TenantContext,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: str | None,
        updated_at: datetime,
        routing: ModelRouting | None = None,
        keep_api_key: bool = False,
    ) -> StoredModelConfig:
        with self._lock:
            if keep_api_key and not api_key:
                saved = self._items.get((context.tenant_id, context.workspace_id))
                if saved is None or saved.api_key is None:
                    raise StoredKeyNotReusableError("missing")
                check_key_reusable(
                    saved_provider=saved.provider,
                    saved_base_url=saved.base_url,
                    provider=provider,
                    base_url=base_url,
                )
                api_key = saved.api_key.get_secret_value()
            return self._store(context, provider, model, base_url, api_key, updated_at, routing)

    def _store(
        self,
        context: TenantContext,
        provider: str,
        model: str,
        base_url: str,
        api_key: str | None,
        updated_at: datetime,
        routing: ModelRouting | None,
    ) -> StoredModelConfig:
        config = StoredModelConfig(
            provider=provider,
            model=model,
            base_url=base_url,
            routing=routing or ModelRouting(),
            api_key=SecretStr(api_key) if api_key else None,
            credential_configured=bool(api_key),
            credential_hint=credential_hint(api_key),
            updated_at=updated_at,
            updated_by_user_id=context.user_id,
        )
        with self._lock:
            self._items[(context.tenant_id, context.workspace_id)] = config
        return config

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


memory_model_config_store = InMemoryModelConfigStore()


@contextmanager
def open_model_config_store(context: TenantContext) -> Iterator[ModelConfigStore]:
    """A store for one tenant/workspace unit of work, chosen by the repository backend."""
    if settings.repository_backend == "memory":
        yield memory_model_config_store
        return
    if settings.repository_backend != "postgresql":
        raise RuntimeError(f"Unsupported repository backend: {settings.repository_backend}")

    from .db.model_config_repository import SqlAlchemyModelConfigStore
    from .db.session import SessionLocal, set_tenant_context
    from .secret_box import secret_cipher

    session = SessionLocal()
    try:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        yield SqlAlchemyModelConfigStore(session, secret_cipher())
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
