"""AI labels on mail in PostgreSQL (migration ``0063_mail_labels``, docs/email.md#ai-on-email).

The API runs as the non-owner ``anum_test_app`` role against the fake mail server and a scripted
model. Labels are private to the person who made them (forced RLS plus the restrictive owner
policy), one row per message (upsert), and go with their mailbox.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import APP_ROLE
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_mail_ai import ScriptedModel
from test_mail_center import ADDRESS, PASSWORD
from test_postgres_mail_outgoing import A, headers
from test_postgres_mail_outgoing import client, fake  # noqa: F401 (fixtures)

from anum_api import email_inbox

pytestmark = pytest.mark.database


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    gateway = ScriptedModel()
    email_inbox.set_model_gateway(gateway)
    yield gateway
    email_inbox.set_model_gateway(None)


def _count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text("select count(*) from mail_labels")).scalar_one())


def test_labels_are_private_one_per_message_and_go_with_the_mailbox(client: TestClient, model: ScriptedModel, database_engine: Engine) -> None:  # noqa: F811
    account = client.post("/api/v1/mail/accounts", headers=A, json={"address": ADDRESS, "password": PASSWORD}).json()
    ref = {"account_id": account["id"], "folder": "INBOX", "uid": 1}
    assert client.post("/api/v1/mail/ai/classify", headers=A, json=ref).status_code == 200
    assert client.post("/api/v1/mail/ai/classify", headers=A, json=ref).status_code == 200
    assert _count(database_engine) == 1
    shown = client.get("/api/v1/mail/ai/labels", headers=A, params={"account_id": account["id"], "folder": "INBOX", "uids": "1"}).json()
    assert shown == [{"uid": 1, "category": "customer", "priority": "high", "reason": "A customer wants a quote soon.", "source": "model"}]
    # Another person in the same workspace sees no row, even reading the table directly as the app role.
    other = headers("someone_else")
    assert client.get("/api/v1/mail/ai/labels", headers=other, params={"account_id": account["id"], "folder": "INBOX", "uids": "1"}).status_code == 404
    with database_engine.begin() as connection:
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true), set_config('anum.user_id', 'someone_else', true)"),
                           {"t": A["x-tenant-id"], "w": A["x-workspace-id"]})
        assert connection.execute(text("select count(*) from mail_labels")).scalar_one() == 0
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(text(
                    "insert into mail_labels (id, tenant_id, workspace_id, owner_user_id, account_id, folder, uid, category, priority, source) "
                    "values ('maillabel_x', :t, :w, 'owner_test', :a, 'INBOX', 2, 'order', 'low', 'rules')"
                ), {"t": A["x-tenant-id"], "w": A["x-workspace-id"], "a": account["id"]})
    assert client.delete(f"/api/v1/mail/accounts/{account['id']}", headers=A).status_code == 204
    assert _count(database_engine) == 0
