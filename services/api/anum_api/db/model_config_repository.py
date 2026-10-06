from datetime import datetime

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from anum_api.model_config_store import (
    StoredModelConfig,
    WorkspaceNotProvisionedError,
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
    ) -> StoredModelConfig:
        workspace = self.session.scalar(
            select(WorkspaceRecord.id).where(
                WorkspaceRecord.tenant_id == context.tenant_id,
                WorkspaceRecord.id == context.workspace_id,
            )
        )
        if workspace is None:
            raise WorkspaceNotProvisionedError(context.workspace_id)

        ciphertext = self.cipher.encrypt(api_key) if api_key else None
        record = self._record(context)
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
        record.api_key_ciphertext = ciphertext
        record.credential_hint = credential_hint(api_key)
        record.updated_by_user_id = context.user_id
        record.updated_at = updated_at
        self.session.flush()
        return StoredModelConfig(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=SecretStr(api_key) if api_key else None,
            credential_configured=ciphertext is not None,
            credential_hint=record.credential_hint,
            updated_at=updated_at,
            updated_by_user_id=context.user_id,
        )

    def _record(self, context: TenantContext) -> WorkspaceModelConfigRecord | None:
        return self.session.scalar(
            select(WorkspaceModelConfigRecord).where(
                WorkspaceModelConfigRecord.tenant_id == context.tenant_id,
                WorkspaceModelConfigRecord.workspace_id == context.workspace_id,
            )
        )
