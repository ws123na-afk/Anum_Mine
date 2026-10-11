from datetime import datetime

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from anum_api.model_config_store import (
    ModelRouting,
    StoredKeyNotReusableError,
    StoredModelConfig,
    WorkspaceNotProvisionedError,
    check_key_reusable,
    credential_hint,
)
from anum_api.schemas import TenantContext
from anum_api.secret_box import SecretCipher

from .models import Workspace as WorkspaceRecord
from .models import WorkspaceModelConfigRecord


class SqlAlchemyModelConfigStore:
    """PostgreSQL model configuration store.

    The session must already carry the tenant and workspace RLS context
    (``set_tenant_context``); queries also filter on both explicitly.
    """

    def __init__(self, session: Session, cipher: SecretCipher) -> None:
        self.session = session
        self.cipher = cipher

    def get(self, context: TenantContext, *, include_secret: bool = True) -> StoredModelConfig | None:
        record = self._record(context)
        if record is None:
            return None
        api_key = None
        if include_secret and record.api_key_ciphertext:
            api_key = SecretStr(self.cipher.decrypt(record.api_key_ciphertext))
        return StoredModelConfig(
            provider=record.provider,
            model=record.model,
            base_url=record.base_url,
            routing=ModelRouting(
                fast_model=record.fast_model,
                capable_model=record.capable_model,
                fast_effort=record.fast_effort,
                capable_effort=record.capable_effort,
            ),
            api_key=api_key,
            credential_configured=record.api_key_ciphertext is not None,
            credential_hint=record.credential_hint,
            updated_at=record.updated_at,
            updated_by_user_id=record.updated_by_user_id,
        )

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
        routing = routing or ModelRouting()
        workspace = self.session.scalar(
            select(WorkspaceRecord.id).where(
                WorkspaceRecord.tenant_id == context.tenant_id,
                WorkspaceRecord.id == context.workspace_id,
            )
        )
        if workspace is None:
            raise WorkspaceNotProvisionedError(context.workspace_id)

        keep = keep_api_key and not api_key
        # Keeping the key locks the row, so the checked provider and base URL and the
        # kept ciphertext are one consistent pair even against a concurrent save.
        record = self._record(context, lock=keep)
        if keep:
            if record is None or record.api_key_ciphertext is None:
                raise StoredKeyNotReusableError("missing")
            check_key_reusable(
                saved_provider=record.provider,
                saved_base_url=record.base_url,
                provider=provider,
                base_url=base_url,
            )
            ciphertext: str | None = record.api_key_ciphertext
            hint = record.credential_hint
            api_key = self.cipher.decrypt(record.api_key_ciphertext)
        else:
            ciphertext = self.cipher.encrypt(api_key) if api_key else None
            hint = credential_hint(api_key)
        if record is None:
            record = WorkspaceModelConfigRecord(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                created_at=updated_at,
            )
            self.session.add(record)
        record.provider = provider
        record.model = model
        record.base_url = base_url
        record.fast_model = routing.fast_model
        record.capable_model = routing.capable_model
        record.fast_effort = routing.fast_effort
        record.capable_effort = routing.capable_effort
        record.api_key_ciphertext = ciphertext
        record.credential_hint = hint
        record.updated_by_user_id = context.user_id
        record.updated_at = updated_at
        self.session.flush()
        return StoredModelConfig(
            provider=provider,
            model=model,
            base_url=base_url,
            routing=routing,
            api_key=SecretStr(api_key) if api_key else None,
            credential_configured=ciphertext is not None,
            credential_hint=record.credential_hint,
            updated_at=updated_at,
            updated_by_user_id=context.user_id,
        )

    def _record(self, context: TenantContext, *, lock: bool = False) -> WorkspaceModelConfigRecord | None:
        query = select(WorkspaceModelConfigRecord).where(
            WorkspaceModelConfigRecord.tenant_id == context.tenant_id,
            WorkspaceModelConfigRecord.workspace_id == context.workspace_id,
        )
        return self.session.scalar(query.with_for_update() if lock else query)
