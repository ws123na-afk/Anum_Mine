"""The document vault in a person's privacy export and deletion (docs/documents.md#privacy).

* Export: the caller's own documents (metadata, the masked number, confirmed receipt fields, the
  people they shared private documents with) and their iPhone Shortcut devices without token
  hashes. The files' bytes are downloaded from the vault itself; the full numbers stay encrypted.
* Deletion: the caller's private documents (rows, file rows and bytes), their devices and the
  shares other people gave them go; workspace documents they added stay, as workspace files do.
"""

from __future__ import annotations

from typing import Any

from ..schemas import TenantContext, utc_now
from .models import masked_number
from .store import open_vault_unit


def counts(context: TenantContext) -> tuple[int, int, int]:
    """(private documents, devices, workspace documents) of the caller."""
    with open_vault_unit(context) as unit:
        docs = unit.store.owned_by(context, context.user_id)
        devices = unit.store.devices(context, context.user_id)
    return sum(1 for d in docs if d.access_class == "private"), len(devices), sum(1 for d in docs if d.access_class == "workspace")


def export(context: TenantContext) -> dict[str, Any]:
    with open_vault_unit(context) as unit:
        docs = unit.store.owned_by(context, context.user_id)
        out = []
        for doc in docs:
            data = doc.model_dump(mode="json", exclude={"number_ciphertext", "tenant_id", "workspace_id"})
            data["number_masked"] = masked_number(doc.number_last4)
            data.pop("number_last4", None)
            data["shared_with"] = [grant.user_id for grant in unit.store.access(context, doc.id)]
            data["links"] = [link.model_dump(mode="json") for link in unit.store.links(context, doc.id)]
            out.append(data)
        devices = [d.model_dump(mode="json", exclude={"token_hash"}) for d in unit.store.devices(context, context.user_id)]
    return {"documents": out, "devices": devices}


def delete_private(context: TenantContext) -> tuple[int, int]:
    """Delete the caller's private documents and devices; returns (documents, devices)."""
    from .service import delete_objects, purge_document

    with open_vault_unit(context) as unit:
        docs = [d for d in unit.store.owned_by(context, context.user_id) if d.access_class == "private"]
        devices = unit.store.devices(context, context.user_id)
    removed = 0
    for doc in docs:
        with open_vault_unit(context) as unit:
            current = unit.store.get_document(context, doc.id)
            if current is None:
                continue
            keys = purge_document(unit, context, current)
        delete_objects(context, doc.file_id, keys)
        removed += 1
    with open_vault_unit(context, job=True) as unit:
        for device in devices:
            unit.store.save_device(context, device.model_copy(update={"status": "revoked", "revoked_at": device.revoked_at or utc_now()}))
    return removed, len(devices)


__all__ = ["counts", "delete_private", "export"]
