"""PostgreSQL store for push devices (migration ``0027_push_devices``).

The session's RLS context names the tenant, the workspace and the user
(``open_scoped_store(..., user_scoped=True)``); every statement repeats all three, so
another user's phone is neither read nor written even if the policy were missing.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from anum_api.push import PushPlatform, StoredPushDevice
from anum_api.schemas import TenantContext

from .models import PushDeviceRecord
from .scope import require_workspace


def _device(record: PushDeviceRecord) -> StoredPushDevice:
    return StoredPushDevice(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        user_id=record.user_id,
        platform=PushPlatform(record.platform),
        label=record.label,
        token_ciphertext=record.token_ciphertext,
        token_sha256=record.token_sha256,
        token_hint=record.token_hint,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_sent_at=record.last_sent_at,
    )


class SqlAlchemyPushDeviceStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _scope(context: TenantContext) -> tuple:  # type: ignore[type-arg]
        return (
            PushDeviceRecord.tenant_id == context.tenant_id,
            PushDeviceRecord.workspace_id == context.workspace_id,
            PushDeviceRecord.user_id == context.user_id,
        )

    def list(self, context: TenantContext) -> list[StoredPushDevice]:
        records = self.session.scalars(
            select(PushDeviceRecord)
            .where(*self._scope(context))
            .order_by(PushDeviceRecord.created_at, PushDeviceRecord.id)
        ).all()
        return [_device(record) for record in records]

    def find_by_digest(self, context: TenantContext, digest: str) -> StoredPushDevice | None:
        record = self.session.scalar(
            select(PushDeviceRecord).where(*self._scope(context), PushDeviceRecord.token_sha256 == digest)
        )
        return _device(record) if record is not None else None

    def save(self, device: StoredPushDevice) -> StoredPushDevice:
        require_workspace(self.session, device.tenant_id, device.workspace_id)
        record = self.session.get(PushDeviceRecord, device.id)
        if record is None:
            record = PushDeviceRecord(
                id=device.id,
                tenant_id=device.tenant_id,
                workspace_id=device.workspace_id,
                user_id=device.user_id,
            )
            self.session.add(record)
        record.platform = device.platform.value
        record.label = device.label
        record.token_ciphertext = device.token_ciphertext
        record.token_sha256 = device.token_sha256
        record.token_hint = device.token_hint
        record.created_at = device.created_at
        record.updated_at = device.updated_at
        record.last_sent_at = device.last_sent_at
        self.session.flush()
        return device

    def delete(self, context: TenantContext, device_id: str) -> bool:
        result = self.session.execute(
            delete(PushDeviceRecord).where(*self._scope(context), PushDeviceRecord.id == device_id)
        )
        return bool(result.rowcount)

    def mark_sent(self, context: TenantContext, device_ids: Sequence[str], at: datetime) -> None:
        if not device_ids:
            return
        self.session.execute(
            update(PushDeviceRecord)
            .where(*self._scope(context), PushDeviceRecord.id.in_(list(device_ids)))
            .values(last_sent_at=at)
        )
