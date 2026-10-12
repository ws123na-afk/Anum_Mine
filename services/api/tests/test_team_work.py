"""Team work management (in-memory backend): docs/team-work.md."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from anum_api import notifications as notifications_module
from anum_api.dependencies import memory_repository
from anum_api.files import file_store
from anum_api.main import app, store
from anum_api.onboarding import NotificationPreferences, _notifications
from anum_api.schemas import TenantContext, WorkspaceMembership, utc_now
from anum_api.settings import settings
from anum_api.team_store import memory_team_store
from anum_api.team_work import run_reminder_pass

TENANT = "tenant_team"
WORKSPACE = "workspace_team"


def h(user: str, roles: str = "member", workspace: str = WORKSPACE, tenant: str = TENANT) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": roles}


OWNER = h("owner_1", "owner")
AHMED = h("user_ahmed")
SARA = h("user_sara")
VERA = h("user_vera", "viewer")


def _clear() -> None:
    for name in ("tasks", "runs", "approvals", "events", "tenants", "workspaces", "memberships", "invitations", "audit_records"):
        getattr(store, name).clear()
    memory_team_store.clear()
    _notifications.clear()
    file_store.clear()


def _member(user: str, role: str, workspace: str = WORKSPACE) -> None:
    now = utc_now()
    memory_repository.save_membership(
        WorkspaceMembership(tenant_id=TENANT, workspace_id=workspace, user_id=user, role=role, created_at=now, updated_at=now)
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    monkeypatch.setattr(settings, "smtp_host", None)
    _clear()
    test_client = TestClient(app)
    assert test_client.post("/api/v1/tenants", headers=OWNER, json={"name": "Tyres Co"}).status_code == 201
    assert test_client.post("/api/v1/workspaces", headers=OWNER, json={"name": "Main"}).status_code == 201
    assert test_client.post("/api/v1/workspace-memberships/current", headers=OWNER).status_code == 201
    _member("user_ahmed", "member")
    _member("user_sara", "member")
    _member("user_vera", "viewer")
    for user, name, email in (("user_ahmed", "Ahmed Saleh", "ahmed@example.com"), ("user_sara", "Sara Ali", None), ("owner_1", "Omar Owner", None)):
        response = test_client.put(f"/api/v1/team/people/{user}", headers=OWNER,
                                   json={"display_name": name, "email": email, "timezone": "Asia/Riyadh", "capacity_hours": 40})
        assert response.status_code == 200, response.text
    try:
        yield test_client
    finally:
        _clear()


def create(client: TestClient, headers: dict[str, str] = OWNER, **body) -> dict:
    body.setdefault("title", "Call the supplier")
    response = client.post("/api/v1/work-items", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def inbox(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.get("/api/v1/notifications", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- people


def test_people_lists_active_members_with_profiles_and_who_can_be_assigned(client: TestClient) -> None:
    people = {p["user_id"]: p for p in client.get("/api/v1/team/people", headers=VERA).json()}
    assert people["user_ahmed"]["display_name"] == "Ahmed Saleh"
    assert people["user_ahmed"]["assignable"] is True
    assert people["user_vera"]["assignable"] is False
    assert people["user_vera"]["has_profile"] is False


def test_members_edit_only_their_own_profile(client: TestClient) -> None:
    own = client.put("/api/v1/team/people/user_ahmed", headers=AHMED, json={"display_name": "Ahmed S.", "timezone": "Asia/Dubai"})
    assert own.status_code == 200
    other = client.put("/api/v1/team/people/user_sara", headers=AHMED, json={"display_name": "Nope"})
    assert other.status_code == 403
    assert "own team profile" in other.json()["error"]["message"]
    bad = client.put("/api/v1/team/people/user_ahmed", headers=AHMED, json={"display_name": "A", "timezone": "Mars/Base"})
    assert bad.status_code == 422
    stranger = client.put("/api/v1/team/people/nobody", headers=OWNER, json={"display_name": "Ghost"})
    assert stranger.status_code == 404


# --------------------------------------------------------------------------- create and assign


def test_assigning_work_notifies_the_assignee_and_is_audited(client: TestClient) -> None:
    due = (utc_now() + timedelta(days=2)).isoformat()
    item = create(client, assignee={"type": "user", "id": "user_ahmed"}, due_at=due, priority="high", checklist=["Get quote", "Confirm date"])
    assert item["assignee_name"] == "Ahmed Saleh"
    assert item["created_by_name"] == "Omar Owner"
    assert item["checklist_total"] == 2 and item["checklist_done"] == 0
    assert item["version"] == 1 and item["can_edit"] is True

    ahmed = inbox(client, AHMED)
    assert ahmed["unread"] == 1
    note = ahmed["items"][0]
    assert note["kind"] == "work_assigned" and note["work_item_id"] == item["id"]
    assert "Omar Owner assigned you" in note["body"]
    # Email preference is off by default; push has no provider in this release.
    assert note["email_status"] == "not_requested" and note["push_status"] == "not_configured"
    # Nobody else sees Ahmed's notification.
    assert inbox(client, SARA)["items"] == []

    assert any(r.action == "work_item.create" and r.target == item["id"] for r in store.audit_records)
    assert any(e.type == "work_item.created" for e in store.events)
    assert any(e.type == "notification.created" and e.payload["recipient"] == "user_ahmed" for e in store.events)
    feed = client.get("/api/v1/team/activity", headers=SARA).json()["items"]
    assert feed[0]["summary"] == 'Omar Owner created "Call the supplier" for Ahmed Saleh'


def test_viewers_read_but_cannot_create_or_be_assigned(client: TestClient) -> None:
    denied = client.post("/api/v1/work-items", headers=VERA, json={"title": "x"})
    assert denied.status_code == 403
    assert "Viewers can read team work" in denied.json()["error"]["message"]
    refused = client.post("/api/v1/work-items", headers=OWNER, json={"title": "x", "assignee": {"type": "user", "id": "user_vera"}})
    assert refused.status_code == 422
    assert "viewer and cannot be assigned" in refused.json()["error"]["message"]
    unknown = client.post("/api/v1/work-items", headers=OWNER, json={"title": "x", "assignee": {"type": "user", "id": "ghost"}})
    assert unknown.status_code == 422
    naive = client.post("/api/v1/work-items", headers=OWNER, json={"title": "x", "due_at": "2026-10-15T17:00:00"})
    assert naive.status_code == 422
    create(client)
    assert client.get("/api/v1/work-items", headers=VERA).json()["total"] == 1


def test_edit_rules_creator_assignee_or_owner(client: TestClient) -> None:
    item = create(client, AHMED, assignee={"type": "user", "id": "user_sara"})
    by_sara = client.patch(f"/api/v1/work-items/{item['id']}", headers=SARA, json={"version": 1, "status": "in_progress"})
    assert by_sara.status_code == 200 and by_sara.json()["version"] == 2
    other = create(client, OWNER)
    blocked = client.patch(f"/api/v1/work-items/{other['id']}", headers=SARA, json={"version": 1, "title": "Mine now"})
    assert blocked.status_code == 403
    assert "workspace owner can change it" in blocked.json()["error"]["message"]
    assert client.delete(f"/api/v1/work-items/{item['id']}", headers=SARA).status_code == 403
    assert client.delete(f"/api/v1/work-items/{item['id']}", headers=AHMED).status_code == 204
    assert client.get(f"/api/v1/work-items/{item['id']}", headers=AHMED).status_code == 404


def test_stale_version_is_refused_with_a_reason(client: TestClient) -> None:
    item = create(client)
    assert client.patch(f"/api/v1/work-items/{item['id']}", headers=OWNER, json={"version": 1, "priority": "urgent"}).status_code == 200
    stale = client.patch(f"/api/v1/work-items/{item['id']}", headers=OWNER, json={"version": 1, "priority": "low"})
    assert stale.status_code == 409
    assert "Reload it" in stale.json()["error"]["message"]


def test_completing_notifies_the_creator_and_reassignment_notifies_the_new_assignee(client: TestClient) -> None:
    item = create(client, AHMED, assignee={"type": "user", "id": "user_sara"})
    moved = client.patch(f"/api/v1/work-items/{item['id']}", headers=AHMED,
                         json={"version": 1, "assignee": {"type": "user", "id": "owner_1"}})
    assert moved.status_code == 200
    assert inbox(client, OWNER)["items"][0]["kind"] == "work_assigned"
    done = client.patch(f"/api/v1/work-items/{item['id']}", headers=OWNER, json={"version": 2, "status": "done"})
    assert done.json()["completed_at"] is not None
    kinds = [n["kind"] for n in inbox(client, AHMED)["items"]]
    assert "work_completed" in kinds
    assert any(e.type == "work_item.completed" for e in store.events)


# --------------------------------------------------------------------------- lists


def test_my_tasks_overdue_search_and_paging(client: TestClient) -> None:
    past = (utc_now() - timedelta(hours=3)).isoformat()
    create(client, title="Overdue tyre count", assignee={"type": "user", "id": "user_ahmed"}, due_at=past)
    for n in range(5):
        create(client, title=f"Order batch {n}", assignee={"type": "user", "id": "user_sara"})
    mine = client.get("/api/v1/work-items?scope=mine", headers=AHMED).json()
    assert [i["title"] for i in mine["items"]] == ["Overdue tyre count"]
    assert mine["items"][0]["overdue"] is True
    overdue = client.get("/api/v1/work-items?overdue=true", headers=SARA).json()
    assert overdue["total"] == 1
    search = client.get("/api/v1/work-items?q=batch", headers=SARA).json()
    assert search["total"] == 5
    page = client.get("/api/v1/work-items?q=batch&limit=2&offset=2", headers=SARA).json()
    assert len(page["items"]) == 2 and page["next_offset"] == 4
    last = client.get("/api/v1/work-items?q=batch&limit=2&offset=4", headers=SARA).json()
    assert last["next_offset"] is None
    assert client.get("/api/v1/work-items?status=done", headers=SARA).json()["total"] == 0
    assert client.get("/api/v1/work-items?scope=bogus", headers=SARA).status_code == 422


def test_workload_counts_open_overdue_and_capacity(client: TestClient) -> None:
    past = (utc_now() - timedelta(hours=1)).isoformat()
    soon = (utc_now() + timedelta(days=2)).isoformat()
    create(client, assignee={"type": "user", "id": "user_ahmed"}, due_at=past, estimate_hours=10)
    create(client, assignee={"type": "user", "id": "user_ahmed"}, due_at=soon, estimate_hours=10)
    create(client, assignee={"type": "anum", "id": "anum"})
    create(client)
    load = client.get("/api/v1/work-items/workload", headers=VERA).json()
    rows = {r["assignee_id"]: r for r in load["rows"]}
    assert rows["user_ahmed"]["open"] == 2 and rows["user_ahmed"]["overdue"] == 1 and rows["user_ahmed"]["due_this_week"] == 1
    assert rows["user_ahmed"]["load_percent"] == 50
    assert rows["anum"]["name"] == "ANUM" and rows["anum"]["open"] == 1
    assert rows["user_sara"]["open"] == 0
    assert "user_vera" not in rows
    assert load["unassigned_open"] == 1
    assert load["rows"][0]["assignee_id"] == "user_ahmed"


# --------------------------------------------------------------------------- comments, checklist, attachments


def test_comments_notify_the_other_people_and_authors_delete_their_own(client: TestClient) -> None:
    item = create(client, AHMED, assignee={"type": "user", "id": "user_sara"})
    comment = client.post(f"/api/v1/work-items/{item['id']}/comments", headers=SARA, json={"body": "Supplier is closed today"})
    assert comment.status_code == 201 and comment.json()["author_name"] == "Sara Ali"
    assert inbox(client, AHMED)["items"][0]["kind"] == "work_comment"
    assert client.post(f"/api/v1/work-items/{item['id']}/comments", headers=VERA, json={"body": "hi"}).status_code == 403
    assert client.post(f"/api/v1/work-items/{item['id']}/comments", headers=SARA, json={"body": "   "}).status_code == 422
    cid = comment.json()["id"]
    assert client.delete(f"/api/v1/work-items/{item['id']}/comments/{cid}", headers=AHMED).status_code == 403
    assert client.delete(f"/api/v1/work-items/{item['id']}/comments/{cid}", headers=SARA).status_code == 204
    detail = client.get(f"/api/v1/work-items/{item['id']}", headers=AHMED).json()
    assert detail["comments"] == []


def test_checklist_ticks_are_recorded_and_counted(client: TestClient) -> None:
    item = create(client, AHMED)
    entry = client.post(f"/api/v1/work-items/{item['id']}/checklist", headers=AHMED, json={"label": "Check stock"}).json()
    ticked = client.patch(f"/api/v1/work-items/{item['id']}/checklist/{entry['id']}", headers=AHMED, json={"done": True}).json()
    assert ticked["done"] is True and ticked["done_by"] == "user_ahmed"
    listed = client.get("/api/v1/work-items", headers=AHMED).json()["items"][0]
    assert listed["checklist_total"] == 1 and listed["checklist_done"] == 1
    assert client.patch(f"/api/v1/work-items/{item['id']}/checklist/{entry['id']}", headers=SARA, json={"done": False}).status_code == 403
    assert client.delete(f"/api/v1/work-items/{item['id']}/checklist/{entry['id']}", headers=AHMED).status_code == 204
    assert client.delete(f"/api/v1/work-items/{item['id']}/checklist/{entry['id']}", headers=AHMED).status_code == 404


def test_attachments_link_workspace_files(client: TestClient) -> None:
    item = create(client, AHMED)
    upload = client.post("/api/v1/files", headers={**AHMED, "x-file-name": "quote.txt", "content-type": "text/plain"}, content=b"quote")
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["id"]
    attached = client.post(f"/api/v1/work-items/{item['id']}/attachments", headers=AHMED, json={"file_id": file_id})
    assert attached.status_code == 201 and attached.json()["file_name"] == "quote.txt"
    again = client.post(f"/api/v1/work-items/{item['id']}/attachments", headers=AHMED, json={"file_id": file_id})
    assert again.status_code == 409
    missing = client.post(f"/api/v1/work-items/{item['id']}/attachments", headers=AHMED, json={"file_id": "file_missing"})
    assert missing.status_code == 422
    # A file deleted from Files shows as unavailable instead of breaking the item.
    assert client.delete(f"/api/v1/files/{file_id}", headers=OWNER).status_code == 204
    detail = client.get(f"/api/v1/work-items/{item['id']}", headers=AHMED).json()
    assert detail["attachments"][0]["available"] is False


# --------------------------------------------------------------------------- commands


def test_command_preview_and_create(client: TestClient) -> None:
    preview = client.post("/api/v1/team/commands/parse", headers=SARA,
                          json={"text": "ANUM, ask Ahmed to call the supplier by Thursday"}).json()
    assert preview["understood"] is True
    assert preview["assignee"] == {"type": "user", "id": "user_ahmed", "name": "Ahmed Saleh"}
    assert preview["title"] == "Call the supplier"
    due = datetime.fromisoformat(preview["due_at"])
    assert due.weekday() == 3 and due.utcoffset() == timedelta(hours=3)  # Sara's Riyadh time
    created = client.post("/api/v1/team/commands", headers=SARA, json={"text": "ANUM, ask Ahmed to call the supplier by Thursday"})
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["source"] == "command" and item["assignee_id"] == "user_ahmed"
    assert inbox(client, AHMED)["items"][0]["kind"] == "work_assigned"


def test_command_explains_unknown_and_ambiguous_people(client: TestClient) -> None:
    unknown = client.post("/api/v1/team/commands/parse", headers=SARA, json={"text": "ask Khalid to count tyres"}).json()
    assert unknown["understood"] is False and "Nobody in this workspace is called Khalid" in unknown["problem"]
    client.put("/api/v1/team/people/user_sara", headers=SARA, json={"display_name": "Ahmed Nasser"})
    ambiguous = client.post("/api/v1/team/commands/parse", headers=SARA, json={"text": "ask Ahmed to count tyres"}).json()
    assert ambiguous["understood"] is False and len(ambiguous["candidates"]) == 2
    refused = client.post("/api/v1/team/commands", headers=SARA, json={"text": "ask Ahmed to count tyres"})
    assert refused.status_code == 422
    picked = client.post("/api/v1/team/commands", headers=SARA,
                         json={"text": "ask Ahmed to count tyres", "assignee": {"type": "user", "id": "user_ahmed"}})
    assert picked.status_code == 201
    nonsense = client.post("/api/v1/team/commands/parse", headers=SARA, json={"text": "the weather is nice"}).json()
    assert nonsense["understood"] is False and "ask Ahmed to call the supplier" in nonsense["problem"]


def test_anum_task_assigns_through_the_governed_tool(client: TestClient) -> None:
    task = client.post("/api/v1/tasks", headers=SARA, json={"title": "Assign", "prompt": "ANUM, ask Ahmed to call the supplier by Thursday"})
    assert task.status_code == 201
    run = client.post(f"/api/v1/tasks/{task.json()['id']}/run", headers=SARA)
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["task"]["status"] == "completed", body
    assert 'Assigned "Call the supplier" to Ahmed Saleh' in body["run"]["result"]
    proposal = next(s for s in body["run"]["steps"] if s["type"] == "tool_proposal")
    assert proposal["metadata"]["tool"] == "team.assign_task" and proposal["metadata"]["risk_level"] == "medium"
    items = client.get("/api/v1/work-items?scope=mine", headers=AHMED).json()["items"]
    assert items[0]["source"] == "command" and items[0]["created_by"] == "user_sara"
    assert inbox(client, AHMED)["items"][0]["kind"] == "work_assigned"


def test_anum_task_pauses_for_approval_when_medium_risk_needs_it(client: TestClient) -> None:
    assert client.put("/api/v1/approval-policy", headers=OWNER,
                      json={"two_person_rule": False, "medium_risk_requires_approval": True}).status_code == 200
    task = client.post("/api/v1/tasks", headers=SARA, json={"title": "Assign", "prompt": "ask Ahmed to call the supplier"}).json()
    body = client.post(f"/api/v1/tasks/{task['id']}/run", headers=SARA).json()
    assert body["approval"]["action"] == "team.assign_task"
    assert client.get("/api/v1/work-items", headers=SARA).json()["total"] == 0


# --------------------------------------------------------------------------- notifications


def test_notifications_read_and_read_all(client: TestClient) -> None:
    for n in range(3):
        create(client, title=f"Task {n}", assignee={"type": "user", "id": "user_ahmed"})
    page = client.get("/api/v1/notifications?limit=2", headers=AHMED).json()
    assert page["unread"] == 3 and len(page["items"]) == 2 and page["next_cursor"]
    rest = client.get(f"/api/v1/notifications?limit=2&cursor={page['next_cursor']}", headers=AHMED).json()
    assert len(rest["items"]) == 1
    first = page["items"][0]["id"]
    assert client.post(f"/api/v1/notifications/{first}/read", headers=SARA).status_code == 404
    assert client.post(f"/api/v1/notifications/{first}/read", headers=AHMED).json()["read_at"] is not None
    assert client.get("/api/v1/notifications?unread_only=true", headers=AHMED).json()["unread"] == 2
    assert client.post("/api/v1/notifications/read-all", headers=AHMED).json() == {"updated": 2}
    assert client.get("/api/v1/notifications?cursor=ntf_unknown", headers=AHMED).status_code == 400


def test_email_follows_the_recipients_preference_and_is_sent_after_commit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="user_ahmed", roles=["member"])
    _notifications.save(ctx, NotificationPreferences(email_enabled=True))
    create(client, title="Not configured", assignee={"type": "user", "id": "user_ahmed"})
    assert inbox(client, AHMED)["items"][0]["email_status"] == "not_configured"

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "anum@example.com")
    monkeypatch.setattr(notifications_module, "send_email", lambda to, subject, text: sent.append((to, subject)))
    create(client, title="Configured", assignee={"type": "user", "id": "user_ahmed"})
    assert sent == [("ahmed@example.com", "ANUM: New work: Configured")]
    assert inbox(client, AHMED)["items"][0]["email_status"] == "sent"

    # Sara has email on but no address: the row says so.
    ctx_sara = ctx.model_copy(update={"user_id": "user_sara"})
    _notifications.save(ctx_sara, NotificationPreferences(email_enabled=True))
    create(client, title="No address", assignee={"type": "user", "id": "user_sara"})
    assert inbox(client, SARA)["items"][0]["email_status"] == "not_requested"


def test_failed_email_is_retried_by_the_sweep_then_marked_failed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="user_ahmed", roles=["member"])
    _notifications.save(ctx, NotificationPreferences(email_enabled=True))
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "anum@example.com")

    def refuse(*_: object) -> None:
        raise notifications_module.EmailRefused("The SMTP server does not offer STARTTLS")

    monkeypatch.setattr(notifications_module, "send_email", refuse)
    create(client, assignee={"type": "user", "id": "user_ahmed"})
    note = inbox(client, AHMED)["items"][0]
    assert note["email_status"] == "pending" and note["email_attempts"] == 1
    run_reminder_pass()
    run_reminder_pass()
    note = inbox(client, AHMED)["items"][0]
    assert note["email_status"] == "failed" and note["email_attempts"] == 3
    assert "STARTTLS" in note["email_error"]


def test_unencrypted_smtp_is_refused_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "anum@example.com")
    monkeypatch.setattr(settings, "smtp_security", "none")
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(notifications_module.EmailRefused, match="only in local"):
        notifications_module.send_email("a@example.com", "s", "t")


# --------------------------------------------------------------------------- reminders and follow-ups


def test_overdue_reminder_is_sent_once_and_follow_up_comments_as_anum(client: TestClient) -> None:
    past = (utc_now() - timedelta(minutes=5)).isoformat()
    item = create(client, AHMED, title="Count stock", assignee={"type": "user", "id": "user_sara"}, due_at=past, follow_up_at=past)
    counts = run_reminder_pass()
    assert counts["reminded"] == 1 and counts["followed_up"] == 1
    assert run_reminder_pass() == {"reminded": 0, "followed_up": 0, "emails": 0}
    kinds = {n["kind"] for n in inbox(client, SARA)["items"]}
    assert {"work_overdue", "work_follow_up"} <= kinds
    assert "work_overdue" in {n["kind"] for n in inbox(client, AHMED)["items"]}
    detail = client.get(f"/api/v1/work-items/{item['id']}", headers=AHMED).json()
    assert detail["reminder_sent_at"] is not None and detail["follow_up_at"] is None
    assert detail["comments"][0]["author_type"] == "anum" and detail["comments"][0]["author_name"] == "ANUM"
    feed = [a["summary"] for a in client.get("/api/v1/team/activity", headers=AHMED).json()["items"]]
    assert 'ANUM sent an overdue reminder for "Count stock"' in feed
    # A new due date earns a new reminder.
    later = (utc_now() - timedelta(minutes=1)).isoformat()
    client.patch(f"/api/v1/work-items/{item['id']}", headers=AHMED, json={"version": detail["version"], "due_at": later})
    assert run_reminder_pass()["reminded"] == 1


def test_closed_work_gets_no_reminders(client: TestClient) -> None:
    past = (utc_now() - timedelta(minutes=5)).isoformat()
    item = create(client, AHMED, assignee={"type": "user", "id": "user_sara"}, due_at=past)
    client.patch(f"/api/v1/work-items/{item['id']}", headers=AHMED, json={"version": 1, "status": "cancelled"})
    assert run_reminder_pass()["reminded"] == 0


def test_activity_feed_pages_newest_first(client: TestClient) -> None:
    for n in range(4):
        create(client, title=f"Item {n}")
    # Four items plus the three profile updates of the fixture.
    first = client.get("/api/v1/team/activity?limit=5", headers=SARA).json()
    assert len(first["items"]) == 5 and first["next_cursor"]
    assert first["items"][0]["summary"].endswith('"Item 3"')
    rest = client.get(f"/api/v1/team/activity?limit=5&cursor={first['next_cursor']}", headers=SARA).json()
    assert len(rest["items"]) == 2 and rest["next_cursor"] is None
    assert client.get("/api/v1/team/activity?cursor=act_nope", headers=SARA).status_code == 400


def test_workspaces_are_isolated(client: TestClient) -> None:
    item = create(client)
    other = h("owner_1", "owner", workspace="workspace_team_two")
    assert client.post("/api/v1/workspaces", headers=other, json={"name": "Two"}).status_code == 201
    assert client.get(f"/api/v1/work-items/{item['id']}", headers=other).status_code == 404
    assert client.get("/api/v1/work-items", headers=other).json()["total"] == 0


def test_due_dates_need_offsets_and_reminder_naive_now_is_fine(client: TestClient) -> None:
    aware = datetime(2030, 1, 1, 9, tzinfo=timezone.utc).isoformat()
    assert client.post("/api/v1/work-items", headers=OWNER, json={"title": "Far", "due_at": aware}).status_code == 201


def test_team_notifications_become_content_free_phone_pushes_for_their_recipient(client: TestClient) -> None:
    from anum_api.push import TEXT, PushKind, intent_for, resolve_recipients

    item = create(client, assignee={"type": "user", "id": "user_ahmed"})
    event = next(e for e in store.events if e.type == "notification.created")
    intent = intent_for(event)
    assert intent is not None and intent.kind == PushKind.TEAM_WORK
    assert intent.data()["work_item_id"] == item["id"] and "Call the supplier" not in str(intent.data())
    assert "Call the supplier" not in " ".join(TEXT[PushKind.TEAM_WORK])
    assert resolve_recipients(intent) == ["user_ahmed"]
    # A recipient who is no longer an active member gets nothing.
    store.memberships[(TENANT, WORKSPACE, "user_ahmed")].active = False
    assert resolve_recipients(intent) == []


def test_plan_preview_and_runtime_choose_the_team_tool_the_same_way() -> None:
    # docs/command.md: the command plan preview uses choose_tool_call too, so it shows
    # team.assign_task for an assignment exactly when the runtime would run it.
    from anum_api.agent_planning import choose_tool_call

    call = choose_tool_call("ANUM, ask Ahmed to call the supplier by Thursday", [], "", {"team.assign_task", "anum.respond"})
    assert call.name == "team.assign_task" and call.arguments["command"].startswith("ANUM, ask Ahmed")
    # Without the tool registered (a worker without team work) it is an ordinary answer.
    assert choose_tool_call("ANUM, ask Ahmed to call the supplier by Thursday", [], "").name != "team.assign_task"
