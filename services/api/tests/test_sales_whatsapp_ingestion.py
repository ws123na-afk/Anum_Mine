"""Focused tests for verified WhatsApp to CRM ingestion behavior."""
from contextlib import contextmanager
from types import SimpleNamespace

from anum_api.schemas import TenantContext
from anum_api.whatsapp_store import Direction


def _context():
    return TenantContext(tenant_id="tenant-1", workspace_id="workspace-1", user_id="system:whatsapp", roles=[])


def _fake_wa(body="أحتاج تصميم موقع إلكتروني", direction=Direction.INBOUND, kind="text"):
    message = SimpleNamespace(
        id="message-1", conversation_id="conversation-1", body=body,
        direction=direction, kind=kind,
    )
    conversation = SimpleNamespace(id="conversation-1", contact_id="contact-1")
    class Store:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def get_message(self, message_id):
            return message if message_id == message.id else None
        def get_conversation(self, conversation_id):
            return conversation if conversation_id == conversation.id else None
    return Store()


def test_ignores_non_sales_messages(monkeypatch):
    from anum_api import sales_whatsapp_ingestion as importer
    monkeypatch.setattr(importer, "open_whatsapp_store", lambda context: _fake_wa("السلام عليكم"))
    result = importer.import_verified_whatsapp_enquiry(
        _context(), [{"message_id": "message-1", "conversation_id": "conversation-1"}]
    )
    assert result == {"created": 0, "duplicate": 0, "skipped": 1, "failed": 0}


def test_ignores_customer_opt_out(monkeypatch):
    from anum_api import sales_whatsapp_ingestion as importer
    monkeypatch.setattr(importer, "open_whatsapp_store", lambda context: _fake_wa("STOP"))
    result = importer.import_verified_whatsapp_enquiry(
        _context(), [{"message_id": "message-1", "conversation_id": "conversation-1"}]
    )
    assert result["created"] == 0
    assert result["skipped"] == 1


def test_rejects_event_without_stored_verified_message(monkeypatch):
    from anum_api import sales_whatsapp_ingestion as importer
    monkeypatch.setattr(importer, "open_whatsapp_store", lambda context: _fake_wa())
    result = importer.import_verified_whatsapp_enquiry(
        _context(), [{"message_id": "unrecognized", "conversation_id": "conversation-1"}]
    )
    assert result["created"] == 0
    assert result["skipped"] == 1


def test_duplicate_lead_uses_existing_conversation(monkeypatch):
    from anum_api import sales_whatsapp_ingestion as importer
    monkeypatch.setattr(importer, "open_whatsapp_store", lambda context: _fake_wa())
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    class Local:
        def begin(self): return Session()
    class Store:
        def __init__(self, *args): pass
        def find_by_inbound_message(self, _): return None
        def find_by_conversation(self, _): return object()
    monkeypatch.setattr(importer, "SessionLocal", Local())
    monkeypatch.setattr(importer, "set_tenant_context", lambda *a, **k: None)
    monkeypatch.setattr(importer, "SalesCRMStore", Store)
    result = importer.import_verified_whatsapp_enquiry(
        _context(), [{"message_id": "message-1", "conversation_id": "conversation-1"}]
    )
    assert result["duplicate"] == 1
    assert result["created"] == 0
