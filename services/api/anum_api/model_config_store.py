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


def credential_hint(secret: str | None) -> str | None:
    return f"...{secret[-4:]}" if secret else None


class StoredModelConfig(BaseModel):
    provider: str
    model: str
    base_url: str
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
        return (self.provider, self.model, self.base_url, self.credential_hint, self.updated_at)


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
    ) -> StoredModelConfig: ...


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
    ) -> StoredModelConfig:
        config = StoredModelConfig(
            provider=provider,
            model=model,
            base_url=base_url,
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
