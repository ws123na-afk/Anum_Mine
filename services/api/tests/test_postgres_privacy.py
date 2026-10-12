"""Download my data and Delete my data in PostgreSQL (docs/privacy.md).

The API runs as the non-owner ``anum_test_app`` role under forced RLS. Each user gets a
conversation, a private memory with an index row and chunk, a voice transcript,
notification preferences and a task; the tests prove one user's export and deletion
see and touch only that user's rows in that tenant and workspace.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from anum_api.conversations import conversation_store
from anum_api.db import session as db_session
from anum_api.db.memory_repository import SqlAlchemyMemoryRepository
from anum_api.db.privacy_repository import SqlAlchemyPrivacyStore
from anum_api.db.retrieval_repository import SqlAlchemyRetrievalStore
from anum_api.main import app
from anum_api.memory import MemoryNote, MemoryProvenance, RetentionKind, RetentionPolicy
from anum_api.retrieval import ChunkInput, IndexStatus, RetrievalSource, SourceType
from anum_api.schemas import TenantContext, new_id, utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database


def headers(user_id: str = "user_me", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "member"}


ME = headers()
COLLEAGUE = headers("user_colleague")
OTHER_WORKSPACE = headers(workspace_id=WORKSPACE_A2)
OTHER_TENANT = headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)
EVERYONE = (ME, COLLEAGUE, OTHER_WORKSPACE, OTHER_TENANT)
PRIVATE_TABLES = (
    "conversations",
    "conversation_turns",
    "memories",
    "retrieval_sources",
    "retrieval_chunks",
    "voice_sessions",
    "voice_transcript_segments",
    "notification_preferences",
    "learning_signals",
    "learning_profiles",
    "learning_preferences",
)


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    conversation_store.clear()
    yield factory
    conversation_store.clear()


@pytest.fixture
def client(app_factory: sessionmaker[Session]) -> TestClient:
    return TestClient(app)


def _context(hdrs: dict[str, str]) -> TenantContext:
    return TenantContext(tenant_id=hdrs["x-tenant-id"], workspace_id=hdrs["x-workspace-id"], user_id=hdrs["x-user-id"], roles=["member"])


def _private_memory(factory: sessionmaker[Session], hdrs: dict[str, str], content: str) -> str:
    context = _context(hdrs)
    now = utc_now()
    with factory() as session:
        db_session.set_tenant_context(session, context.tenant_id, context.workspace_id, user_id=context.user_id)
        note = SqlAlchemyMemoryRepository(session).create(
            MemoryNote(
                id=new_id("memory"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                task_id=None,
                content=content,
                provenance=MemoryProvenance(source_type="conversation", source_id="conv_x", created_by_user_id=context.user_id, created_at=now),
                retention=RetentionPolicy(kind=RetentionKind.INDEFINITE),
                created_at=now,
                owner_user_id=context.user_id,
            )
        )
        SqlAlchemyRetrievalStore(session).save_source(
            context,
            RetrievalSource(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                source_type=SourceType.MEMORY,
                source_id=note.id,
                content_sha256="0" * 64,
                status=IndexStatus.INDEXED,
                embedding_model="anum-test",
                indexed_at=now,
                owner_user_id=context.user_id,
            ),
            [ChunkInput(id=new_id("chunk"), chunk_index=0, content=content, content_sha256="0" * 64, embedding=[0.1, 0.2, 0.3])],
        )
        session.commit()
    return note.id


def _seed(client: TestClient, factory: sessionmaker[Session], hdrs: dict[str, str], label: str) -> dict[str, str]:
    conversation_id = client.post("/api/v1/conversations", headers=hdrs, json={}).json()["id"]
    assert client.post(f"/api/v1/conversations/{conversation_id}/ask", headers=hdrs, json={"content": f"{label} question"}).status_code == 200
    memory_id = _private_memory(factory, hdrs, f"{label} prefers tea")
    session_id = client.post("/api/v1/voice/sessions", headers=hdrs, json={"retention": "permanent"}).json()["id"]
    assert client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript", headers=hdrs, json={"text": f"{label} spoken", "client_sequence": 1}
    ).status_code == 201
    assert client.put("/api/v1/notification-preferences", headers=hdrs, json={"email_enabled": True}).status_code == 200
    # What ANUM learned (docs/learning.md): one edited draft (a signal and the derived
    # profile) and one learned preference, recorded while learning is on (the chat memory
    # switch is per tenant, so an earlier seed of the same user turned it off).
    assert client.put("/api/v1/me/preferences", headers=hdrs, json={"remember_conversations": True}).status_code == 200
    learned = client.post(
        "/api/v1/learning/feedback",
        headers=hdrs,
        json={"surface": "email", "original": f"Hello, {label} draft", "final": f"Hi, {label} draft as sent"},
    )
    assert learned.status_code == 200 and learned.json()["recorded"] is True, learned.text
    assert client.post("/api/v1/learning/preferences", headers=hdrs, json={"text": f"{label} keeps it short"}).status_code == 201
    assert client.put("/api/v1/me/preferences", headers=hdrs, json={"remember_conversations": False}).status_code == 200
    task = client.post("/api/v1/tasks", headers=hdrs, json={"title": f"{label} task", "prompt": f"{label} summarize notes"})
    assert task.status_code == 201, task.text
    return {"conversation": conversation_id, "memory": memory_id, "voice": session_id, "task": task.json()["id"]}


def _preference_rows(engine: Engine) -> set[tuple[str, str]]:
    """user_preferences rows (one per tenant and user, migration 0025), read below RLS."""
    with engine.connect() as connection:
        rows = connection.execute(text("select tenant_id, user_id from user_preferences"))
        return {(row.tenant_id, row.user_id) for row in rows}


def _rows_per_user(engine: Engine) -> dict[tuple[str, str, str], dict[str, int]]:
    """Row counts per (tenant, workspace, user) read as the table owner, below RLS."""
    found: dict[tuple[str, str, str], dict[str, int]] = {}
    owner_column = {"memories": "owner_user_id", "retrieval_sources": "owner_user_id"}
    with engine.connect() as connection:
        for table in PRIVATE_TABLES:
            if table == "retrieval_chunks":
                statement = (
                    "select s.tenant_id, s.workspace_id, s.owner_user_id as user_id, count(*) from retrieval_chunks c "
                    "join retrieval_sources s on (s.tenant_id, s.workspace_id, s.source_type, s.source_id) = "
                    "(c.tenant_id, c.workspace_id, c.source_type, c.source_id) group by 1, 2, 3"
                )
            else:
                column = owner_column.get(table, "user_id")
                statement = f"select tenant_id, workspace_id, {column} as user_id, count(*) from {table} group by 1, 2, 3"  # nosec B608 - fixed table names
            for tenant_id, workspace_id, user_id, count in connection.execute(text(statement)):
                found.setdefault((tenant_id, workspace_id, user_id), {})[table] = count
    return found


def _key(hdrs: dict[str, str]) -> tuple[str, str, str]:
    return (hdrs["x-tenant-id"], hdrs["x-workspace-id"], hdrs["x-user-id"])


def test_export_reads_only_my_rows_under_rls(client: TestClient, app_factory: sessionmaker[Session]) -> None:
    seeded = {label: _seed(client, app_factory, hdrs, label) for label, hdrs in zip(("mine", "colleague", "elsewhere", "foreign"), EVERYONE)}

    response = client.get("/api/v1/privacy/export", headers=ME)
    assert response.status_code == 200
    export = json.loads(response.content)
    assert [c["id"] for c in export["conversations"]] == [seeded["mine"]["conversation"]]
    assert [t["content"] for t in export["conversations"][0]["turns"]][0] == "mine question"
    assert [m["id"] for m in export["memories"]["private"]] == [seeded["mine"]["memory"]]
    assert export["voice_sessions"][0]["transcript"][0]["text"] == "mine spoken"
    assert [t["id"] for t in export["tasks_created"]] == [seeded["mine"]["task"]]
    assert export["notification_preferences"]["email_enabled"] is True
    assert export["user_preferences"]["remember_conversations"] is False
    for other in ("colleague", "elsewhere", "foreign"):
        assert other not in response.content.decode()

    summary = client.get("/api/v1/privacy", headers=ME).json()
    assert summary["private"] == {
        "conversations": 1,
        "conversation_turns": 2,
        "private_memories": 1,
        "retrieval_index_entries": 1,
        "voice_sessions": 1,
        "voice_transcript_segments": 1,
        "notification_preferences": 1,
        "user_preferences": 1,
        "push_devices": 0,
        "learning_signals": 1,
        "learned_items": 2,
        "meetings": 0,
        "meeting_transcript_segments": 0,
        "meeting_recordings": 0,
        "meeting_connections": 0,
        "commands": 0,
        "quick_commands": 0,
        "favorites": 0,
        "private_collections": 0,
        "private_reports": 0,
        "email_mailboxes": 0,
        "email_messages": 0,
        "email_drafts": 0,
        "team_notifications": 0,
        "team_profile": 0,
        "home_items": 0,
    }
    assert summary["shared"]["tasks_created"] == 1


def test_summary_reports_my_own_chat_memory_switch_under_rls(client: TestClient, app_factory: sessionmaker[Session]) -> None:
    """The summary reads the caller's preference row inside its RLS unit of work, and the
    tenant's memory governance in its own: the state matches GET /api/v1/me/preferences."""

    def state(hdrs: dict[str, str]) -> str:
        summary = client.get("/api/v1/privacy", headers=hdrs)
        assert summary.status_code == 200, summary.text
        value = summary.json()["model_provider"]["conversation_memory"]
        assert value == client.get("/api/v1/me/preferences", headers=hdrs).json()["conversation_memory"]
        return value

    assert state(ME) == "on"
    assert client.put("/api/v1/me/preferences", headers=ME, json={"remember_conversations": False}).status_code == 200
    assert state(ME) == "off_by_user"
    # Same tenant, same workspace and same user elsewhere: the switch is per user and tenant.
    assert (state(COLLEAGUE), state(OTHER_WORKSPACE), state(OTHER_TENANT)) == ("on", "off_by_user", "on")

    assert client.put(
        "/api/v1/organization/memory-governance",
        headers={**ME, "x-user-roles": "owner,org_admin"},
        json={"default_retention_days": 30, "allow_permanent_retention": True, "require_provenance": True, "allowed_source_types": ["task_message"]},
    ).status_code == 200
    assert client.put("/api/v1/me/preferences", headers=ME, json={"remember_conversations": True}).status_code == 200
    assert (state(ME), state(OTHER_TENANT)) == ("off_for_organization", "on")


def test_export_in_each_workspace_of_one_tenant_holds_only_that_workspace(
    client: TestClient, app_factory: sessionmaker[Session]
) -> None:
    """The same user in two workspaces of one tenant: each export and summary covers its own
    workspace only; the tenant-wide user preference appears in both."""
    first = _seed(client, app_factory, ME, "alpha")
    second = _seed(client, app_factory, OTHER_WORKSPACE, "bravo")

    for hdrs, mine, theirs, other_label in ((ME, first, second, "bravo"), (OTHER_WORKSPACE, second, first, "alpha")):
        response = client.get("/api/v1/privacy/export", headers=hdrs)
        assert response.status_code == 200, response.text
        export = json.loads(response.content)
        assert export["identity"]["workspace_id"] == hdrs["x-workspace-id"]
        assert [c["id"] for c in export["conversations"]] == [mine["conversation"]]
        assert [m["id"] for m in export["memories"]["private"]] == [mine["memory"]]
        assert [v["id"] for v in export["voice_sessions"]] == [mine["voice"]]
        assert [t["id"] for t in export["tasks_created"]] == [mine["task"]]
        assert export["notification_preferences"]["email_enabled"] is True
        # Tenant-wide: "Remember things from my chats" is one row for every workspace.
        assert export["user_preferences"]["remember_conversations"] is False
        text_body = response.content.decode()
        assert other_label not in text_body
        assert not any(value in text_body for value in theirs.values())

        summary = client.get("/api/v1/privacy", headers=hdrs).json()
        assert summary["workspace_id"] == hdrs["x-workspace-id"]
        assert (summary["private"]["conversations"], summary["private"]["private_memories"]) == (1, 1)
        assert (summary["private"]["retrieval_index_entries"], summary["private"]["user_preferences"]) == (1, 1)
        assert summary["shared"]["tasks_created"] == 1


def test_the_store_needs_the_user_context_to_see_private_rows(client: TestClient, app_factory: sessionmaker[Session]) -> None:
    _seed(client, app_factory, ME, "mine")
    with app_factory() as session:
        # Tenant and workspace only: RLS hides every user-private row.
        db_session.set_tenant_context(session, TENANT_A, WORKSPACE_A)
        counts, _ = SqlAlchemyPrivacyStore(session, _context(ME)).counts()
        assert counts.conversations == counts.private_memories == counts.voice_sessions == counts.user_preferences == 0
        session.rollback()
    with app_factory() as session:
        # Another user's context sees none of mine either.
        db_session.set_tenant_context(session, TENANT_A, WORKSPACE_A, user_id="user_colleague")
        counts, _ = SqlAlchemyPrivacyStore(session, _context(ME)).counts()
        assert counts.conversations == counts.private_memories == counts.voice_sessions == counts.user_preferences == 0
        # notification_preferences is scoped by tenant and workspace in RLS; the store's
        # explicit user filter is what keeps it to the caller (it is only ever opened
        # with the caller's own context, privacy.open_privacy_store).
        session.rollback()


def test_delete_removes_my_private_rows_and_nobody_elses(
    client: TestClient, app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    seeded = {label: _seed(client, app_factory, hdrs, label) for label, hdrs in zip(("mine", "colleague", "elsewhere", "foreign"), EVERYONE)}
    before = _rows_per_user(database_engine)
    assert _preference_rows(database_engine) == {(TENANT_A, "user_me"), (TENANT_A, "user_colleague"), (TENANT_B, "user_me")}
    assert before[_key(ME)] == {table: (2 if table == "conversation_turns" else 1) for table in PRIVATE_TABLES}

    assert client.post("/api/v1/privacy/delete", headers=ME, json={"confirm": "nope"}).status_code == 422
    assert _rows_per_user(database_engine) == before

    response = client.post("/api/v1/privacy/delete", headers=ME, json={"confirm": "DELETE MY DATA"})
    assert response.status_code == 200, response.text
    assert response.json()["deleted"]["retrieval_index_entries"] == 1
    assert response.json()["kept"]["tasks_created"] == 1

    after = _rows_per_user(database_engine)
    assert _key(ME) not in after
    # One preference row per user and tenant: mine in tenant A goes; the colleague's and
    # the same user id's row in tenant B stay.
    assert _preference_rows(database_engine) == {(TENANT_A, "user_colleague"), (TENANT_B, "user_me")}
    for hdrs in (COLLEAGUE, OTHER_WORKSPACE, OTHER_TENANT):
        assert after[_key(hdrs)] == before[_key(hdrs)]

    # Shared records stay: my task is still there for the workspace.
    assert client.get(f"/api/v1/tasks/{seeded['mine']['task']}", headers=COLLEAGUE).status_code == 200
    with database_engine.connect() as connection:
        audit = connection.execute(
            text("select actor, target, outcome, metadata from audit_records where action = 'privacy.delete'")
        ).one()
    assert (audit.actor, audit.target, audit.outcome) == ("user_me", "user_me", "success")
    assert audit.metadata["deleted"]["conversations"] == 1
    assert "mine" not in json.dumps(audit.metadata)


def test_export_audit_is_written_in_the_users_workspace(client: TestClient, app_factory: sessionmaker[Session], database_engine: Engine) -> None:
    _seed(client, app_factory, ME, "mine")
    assert client.get("/api/v1/privacy/export", headers=ME).status_code == 200
    with database_engine.connect() as connection:
        rows = connection.execute(
            text("select tenant_id, workspace_id, actor, outcome, metadata from audit_records where action = 'privacy.export'")
        ).all()
    assert [(r.tenant_id, r.workspace_id, r.actor, r.outcome) for r in rows] == [(TENANT_A, WORKSPACE_A, "user_me", "success")]
    assert rows[0].metadata["sections"]["conversations"] == 1
    assert "mine" not in json.dumps(rows[0].metadata)


# -- email, WhatsApp and autonomy rows (migrations 0033 to 0035) ----------------------------


def _seed_modules(factory: sessionmaker[Session], hdrs: dict[str, str], label: str) -> None:
    from anum_api.autonomy import AutonomousAction
    from anum_api.db.autonomy_repository import SqlAlchemyAutonomyStore
    from anum_api.db.email_repository import SqlAlchemyEmailStore
    from anum_api.db.session import set_tenant_context
    from anum_api.db.whatsapp_repository import SqlAlchemyWhatsAppStore
    from anum_api.email_inbox import EmailAccount, EmailDraft, EmailMessage
    from anum_api.secret_box import SecretCipher
    from anum_api.whatsapp_store import Conversation, Message

    context, now = _context(hdrs), utc_now()
    scope = {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id}
    user = context.user_id
    with factory() as session:
        set_tenant_context(session, context.tenant_id, context.workspace_id, user_id=user)
        email = SqlAlchemyEmailStore(session, context)
        for suffix, visibility in (("private", "private"), ("shared", "workspace")):
            email.add_account(
                EmailAccount(
                    id=f"mbox_{label}_{suffix}", **scope, provider="zoho", address=f"{label}.{suffix}@example.com",
                    connected_by=user, visibility=visibility, created_at=now, updated_at=now,
                )
            )
        email.upsert_messages([
            EmailMessage(
                id=f"msg_{label}_{suffix}", **scope, account_id=f"mbox_{label}_{suffix}", provider_message_id=f"p_{suffix}",
                thread_id="t1", subject=f"{label} {suffix} subject", received_at=now, created_at=now, updated_at=now,
            )
            for suffix in ("private", "shared")
        ])
        for draft_id, account, status in (("unsent", "shared", "draft"), ("sent", "shared", "sent"), ("private", "private", "draft")):
            email.add_draft(
                EmailDraft(
                    id=f"draft_{label}_{draft_id}", **scope, account_id=f"mbox_{label}_{account}", body=f"{label} body",
                    status=status, created_by=user, created_at=now, updated_at=now,
                )
            )
        whatsapp = SqlAlchemyWhatsAppStore(session, context, SecretCipher.from_settings())
        contact = whatsapp.upsert_contact(f"9665{len(label)}{label[:3].encode().hex()}", "Customer", now, f"wac_{label}")
        whatsapp.add_conversation(
            Conversation(
                id=f"wcv_{label}", **scope, contact_id=contact.id, wa_id=contact.wa_id, last_message_at=now,
                created_at=now, updated_at=now,
            )
        )
        whatsapp.add_message(
            Message(
                id=f"wam_{label}", **scope, conversation_id=f"wcv_{label}", wamid=f"wamid.{label}", direction="outbound",
                kind="text", body=f"{label} whatsapp reply", status="sent", sent_by=user, timestamp=now,
                created_at=now, updated_at=now,
            )
        )
        SqlAlchemyAutonomyStore(session).add_action(
            AutonomousAction(
                id=f"act_{label}", **scope, task_id="task_x", run_id="run_x", tool="email.send", category="email",
                action_kind="send", level="act_and_notify", pattern_key="email.send:x", summary=f"{label} sent a reply",
                requested_by=user, undo_token="undo-token-placeholder", created_at=now,
            )
        )
        session.commit()


def _module_rows(engine: Engine) -> dict[str, list[str]]:
    with engine.connect() as connection:
        return {
            table: sorted(connection.execute(text(f"select id from {table}")).scalars())  # nosec B608 - fixed names
            for table in ("email_accounts", "email_messages", "email_drafts", "whatsapp_messages", "autonomy_actions")
        }


def test_email_whatsapp_and_autonomy_under_rls(
    client: TestClient, app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    _seed_modules(app_factory, ME, "mine")
    _seed_modules(app_factory, COLLEAGUE, "colleague")
    _seed_modules(app_factory, OTHER_TENANT, "foreign")

    summary = client.get("/api/v1/privacy", headers=ME).json()
    assert (summary["private"]["email_mailboxes"], summary["private"]["email_messages"], summary["private"]["email_drafts"]) == (1, 1, 2)
    assert (
        summary["shared"]["email_mailboxes_connected"], summary["shared"]["email_drafts_sent"],
        summary["shared"]["whatsapp_messages_sent"], summary["shared"]["autonomy_actions_requested"],
    ) == (1, 1, 1, 1)

    export = client.get("/api/v1/privacy/export", headers=ME)
    body = json.loads(export.content)
    assert [m["subject"] for m in body["email"]["private_mailbox_messages"]] == ["mine private subject"]
    assert [m["body"] for m in body["whatsapp_messages_sent"]] == ["mine whatsapp reply"]
    assert "undo_token" not in body["autonomy_actions_requested"][0]
    assert "colleague" not in export.text and "foreign" not in export.text and "undo-token-placeholder" not in export.text

    response = client.post("/api/v1/privacy/delete", headers=ME, json={"confirm": "DELETE MY DATA"})
    assert response.status_code == 200, response.text
    assert response.json()["deleted"]["email_drafts"] == 2
    rows = _module_rows(database_engine)
    assert rows["email_accounts"] == [
        "mbox_colleague_private", "mbox_colleague_shared", "mbox_foreign_private", "mbox_foreign_shared", "mbox_mine_shared",
    ]
    assert "msg_mine_private" not in rows["email_messages"] and "msg_mine_shared" in rows["email_messages"]
    assert [d for d in rows["email_drafts"] if d.startswith("draft_mine")] == ["draft_mine_sent"]
    assert "wam_mine" in rows["whatsapp_messages"] and "act_mine" in rows["autonomy_actions"]
    assert len([d for d in rows["email_drafts"] if d.startswith("draft_colleague")]) == 3


def test_team_notifications_and_profile_are_deleted_under_rls(
    client: TestClient, app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    from anum_api.db.session import set_tenant_context
    from anum_api.db.team_repository import SqlAlchemyTeamStore
    from anum_api.team_models import MemberProfile, UserNotification

    now = utc_now()
    for hdrs in (ME, COLLEAGUE):
        context = _context(hdrs)
        with app_factory() as session:
            set_tenant_context(session, context.tenant_id, context.workspace_id, user_id=context.user_id)
            store = SqlAlchemyTeamStore(session)
            store.save_profile(context, MemberProfile(user_id=context.user_id, display_name=f"{context.user_id} name", updated_at=now))
            store.add_notification(
                context,
                UserNotification(id=f"note_{context.user_id}", user_id=context.user_id, kind="assigned", title="t", body="b", created_at=now),
            )
            session.commit()

    summary = client.get("/api/v1/privacy", headers=ME).json()
    assert (summary["private"]["team_notifications"], summary["private"]["team_profile"]) == (1, 1)
    export = json.loads(client.get("/api/v1/privacy/export", headers=ME).content)
    assert export["team_work"]["profile"]["display_name"] == "user_me name"
    assert [item["id"] for item in export["team_work"]["notifications"]] == ["note_user_me"]
    deleted = client.post("/api/v1/privacy/delete", headers=ME, json={"confirm": "DELETE MY DATA"}).json()
    assert (deleted["deleted"]["team_notifications"], deleted["deleted"]["team_profile"]) == (1, 1)
    with database_engine.connect() as connection:
        notifications = connection.execute(text("select user_id from user_notifications order by 1")).scalars().all()
        profiles = connection.execute(text("select user_id from team_member_profiles order by 1")).scalars().all()
    assert notifications == ["user_colleague"] and profiles == ["user_colleague"]
