"""WhatsApp history retention against PostgreSQL (migration 0050, docs/whatsapp.md#retention).

Workspaces are discovered as ``anum_maintenance`` (scope of conversations at least a day
old, nothing else); each workspace is purged as the non-owner app role inside its RLS
context, where its own retention setting is read. Messages awaiting approval are kept.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.db.whatsapp_repository import SqlAlchemyWhatsAppStore
from anum_api.schemas import utc_now
from anum_api.secret_box import SecretCipher
from anum_api.settings import settings
from anum_api.whatsapp_retention import main as whatsapp_retention_main
from anum_api.whatsapp_retention import purge_whatsapp_history
from anum_api.whatsapp_store import (
    Conversation,
    Direction,
    Message,
    MessageStatus,
    WhatsAppAccount,
)

pytestmark = pytest.mark.database

NOW = utc_now()
CIPHER = SecretCipher([Fernet.generate_key()])


def _days_ago(days: int):  # type: ignore[no-untyped-def]
    return NOW - timedelta(days=days)


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None) -> sessionmaker[Session]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    return factory


def _account(store: SqlAlchemyWhatsAppStore, tenant_id: str, workspace_id: str, phone: str, days: int | None) -> None:
    store.save_account(
        WhatsAppAccount(
            tenant_id=tenant_id, workspace_id=workspace_id, phone_number_id=phone, business_account_id="2000000001",
            verify_token_hash="b" * 64, message_retention_days=days,
            created_at=NOW, updated_at=NOW, updated_by="owner",
        ),
        access_token="example-token", app_secret="example-secret",
    )


def _thread(
    store: SqlAlchemyWhatsAppStore,
    tenant_id: str,
    workspace_id: str,
    key: str,
    messages: list[tuple[int, MessageStatus]],
) -> None:
    """A contact and conversation created 200 days ago with messages ``days`` old."""
    contact = store.upsert_contact(f"9665{key}", f"Customer {key}", _days_ago(200), f"wac_{key}")
    last = min(days for days, _ in messages)
    store.add_conversation(
        Conversation(
            id=f"wcv_{key}", tenant_id=tenant_id, workspace_id=workspace_id, contact_id=contact.id, wa_id=contact.wa_id,
            last_message_at=_days_ago(last), last_message_preview="example preview",
            created_at=_days_ago(200), updated_at=_days_ago(last),
        )
    )
    for index, (days, status) in enumerate(messages):
        outbound = status != MessageStatus.RECEIVED
        store.add_message(
            Message(
                id=f"wam_{key}_{index}", tenant_id=tenant_id, workspace_id=workspace_id, conversation_id=f"wcv_{key}",
                wamid=f"wamid.{key}.{index}", direction=Direction.OUTBOUND if outbound else Direction.INBOUND,
                kind="text", body="example message", status=status, sent_by="owner" if outbound else None,
                timestamp=_days_ago(days), created_at=_days_ago(days), updated_at=_days_ago(days),
            )
        )


@pytest.fixture
def history(seed_scopes: None, app_session: Callable[..., Iterator[Session]]) -> None:
    # Workspace A keeps 30 days.
    context = tenant_context(TENANT_A, WORKSPACE_A)
    with app_session(context, commit=True) as session:
        store = SqlAlchemyWhatsAppStore(session, context, CIPHER)
        _account(store, TENANT_A, WORKSPACE_A, "1000000011", 30)
        _thread(store, TENANT_A, WORKSPACE_A, "a1", [(90, MessageStatus.RECEIVED), (10, MessageStatus.SENT)])
        _thread(store, TENANT_A, WORKSPACE_A, "a2", [(60, MessageStatus.RECEIVED), (55, MessageStatus.READ)])
        _thread(store, TENANT_A, WORKSPACE_A, "a3", [(60, MessageStatus.RECEIVED), (59, MessageStatus.AWAITING_APPROVAL)])
    # Workspace A2: number disconnected, history left; the deployment default applies.
    context = tenant_context(TENANT_A, WORKSPACE_A2)
    with app_session(context, commit=True) as session:
        _thread(SqlAlchemyWhatsAppStore(session, context, CIPHER), TENANT_A, WORKSPACE_A2, "b1", [(20, MessageStatus.RECEIVED)])
    # Tenant B chose to keep everything, whatever the deployment default.
    context = tenant_context(TENANT_B, WORKSPACE_B)
    with app_session(context, commit=True) as session:
        store = SqlAlchemyWhatsAppStore(session, context, CIPHER)
        _account(store, TENANT_B, WORKSPACE_B, "1000000012", 0)
        _thread(store, TENANT_B, WORKSPACE_B, "c1", [(400, MessageStatus.RECEIVED)])


def _remaining(database_engine: Engine) -> dict[str, list[str]]:
    with database_engine.connect() as connection:
        return {
            table: sorted(connection.execute(text(f"select id from {table}")).scalars())  # nosec B608 - fixed names
            for table in ("whatsapp_messages", "whatsapp_conversations", "whatsapp_contacts")
        }


def test_each_workspace_keeps_its_own_retention(
    history: None, app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    before = _remaining(database_engine)

    dry = purge_whatsapp_history(app_factory, default_days=7, now=NOW, dry_run=True, batch_size=1)
    assert (dry.workspaces_scanned, dry.messages, dry.conversations, dry.contacts) == (3, 5, 2, 2)
    assert _remaining(database_engine) == before

    kept = purge_whatsapp_history(app_factory, default_days=0, now=NOW)
    # Only workspace A has its own (30-day) retention; A2 follows the default (keep).
    assert (kept.messages, kept.conversations, kept.contacts, kept.workspaces_purged) == (4, 1, 1, 1)
    remaining = _remaining(database_engine)
    assert remaining["whatsapp_messages"] == ["wam_a1_1", "wam_a3_1", "wam_b1_0", "wam_c1_0"]
    # a2 lost every message: conversation and contact go too. a3 keeps the message awaiting approval.
    assert remaining["whatsapp_conversations"] == ["wcv_a1", "wcv_a3", "wcv_b1", "wcv_c1"]
    assert remaining["whatsapp_contacts"] == ["wac_a1", "wac_a3", "wac_b1", "wac_c1"]

    defaulted = purge_whatsapp_history(app_factory, default_days=7, now=NOW)
    assert (defaulted.messages, defaulted.conversations, defaulted.contacts) == (1, 1, 1)
    remaining = _remaining(database_engine)
    assert remaining["whatsapp_messages"] == ["wam_a1_1", "wam_a3_1", "wam_c1_0"]  # tenant B keeps everything
    assert "wcv_b1" not in remaining["whatsapp_conversations"]

    again = purge_whatsapp_history(app_factory, default_days=7, now=NOW)
    assert (again.messages, again.conversations, again.contacts) == (0, 0, 0)


def test_daily_retention_job_reports_whatsapp(
    history: None,
    app_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    from anum_api.voice_retention import main as voice_retention_main

    monkeypatch.setattr(db_session, "SessionLocal", app_factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    assert voice_retention_main(["--dry-run"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["whatsapp"]["dry_run"] is True and report["whatsapp"]["messages"] == 4
    assert whatsapp_retention_main([]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["messages"] == 4 and "example message" not in output


def test_maintenance_role_sees_only_scope_and_age_of_old_conversations(
    history: None, app_session: Callable[..., Iterator[Session]], database_engine: Engine
) -> None:
    context = tenant_context(TENANT_A, WORKSPACE_A)
    with app_session(context, commit=True) as session:
        _thread(SqlAlchemyWhatsAppStore(session, context, CIPHER), TENANT_A, WORKSPACE_A, "new", [(0, MessageStatus.RECEIVED)])
        session.execute(text("update whatsapp_conversations set created_at = now() where id = 'wcv_new'"))
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            rows = connection.execute(
                text("select tenant_id, workspace_id, created_at from whatsapp_conversations order by 1, 2")
            ).all()
        assert len(rows) == 5  # five old conversations; the one created today is not visible
        for statement in (
            "select id from whatsapp_conversations",
            "select summary from whatsapp_conversations",
            "select body from whatsapp_messages",
            "select profile_name from whatsapp_contacts",
            "delete from whatsapp_conversations",
            "select message_retention_days from whatsapp_accounts",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement))
