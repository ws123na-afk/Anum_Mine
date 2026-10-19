"""Calendar sync (docs/calendar.md#sync) against fake Google Calendar and Microsoft Graph APIs.

Every provider call goes to an ``httpx.MockTransport``; nothing leaves the process.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from anum_api import calendar_sync, main
from anum_api.authorization import ROLE_PERMISSIONS, Permission, Role
from anum_api.calendar_api import calendar_backend
from anum_api.calendar_providers import GoogleCalendarClient, MicrosoftCalendarClient, google_event, graph_event
from anum_api.calendar_sync import calendar_settings, invitation_hash, sync_backend
from anum_api.email_inbox import email_settings
from anum_api.email_providers import MailProviderError, OAuthClient
from anum_api.schemas import utc_now
from anum_api.store import store

TENANT = "tenant_calsync"
WORKSPACE = "workspace_calsync"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_cs", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_cs", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_cs", "x-user-roles": "viewer"}

client = TestClient(main.app)


def stamp(hours: float) -> str:
    return (utc_now().replace(microsecond=0) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def google_item(item_id: str, title: str, hours: float, *, updated: str | None = None, etag: str = '"e1"', **extra: Any) -> dict[str, Any]:
    return {
        "id": item_id,
        "status": "confirmed",
        "etag": etag,
        "summary": title,
        "updated": updated or stamp(-1),
        "start": {"dateTime": stamp(hours)},
        "end": {"dateTime": stamp(hours + 1)},
        "organizer": {"email": "owner@example.com", "self": True},
        "attendees": [
            {"email": "Sajid@Example.com", "displayName": "Sajid Kamboh", "responseStatus": "accepted"},
            {"email": "room-1@resource.example.com", "resource": True},
        ],
        **extra,
    }


def graph_item(item_id: str, title: str, hours: float, *, modified: str | None = None, change_key: str = "ck1", **extra: Any) -> dict[str, Any]:
    return {
        "id": item_id,
        "subject": title,
        "changeKey": change_key,
        "lastModifiedDateTime": modified or stamp(-1),
        "start": {"dateTime": stamp(hours).removesuffix("Z") + ".0000000", "timeZone": "UTC"},
        "end": {"dateTime": stamp(hours + 1).removesuffix("Z") + ".0000000", "timeZone": "UTC"},
        "isOrganizer": True,
        "organizer": {"emailAddress": {"address": "owner@contoso.example"}},
        "attendees": [{"emailAddress": {"address": "vinay@contoso.example", "name": "Vinay Kumar"}, "status": {"response": "tentativelyAccepted"}, "type": "required"}],
        "onlineMeeting": {"joinUrl": "https://teams.example.com/meet/abc"},
        **extra,
    }


class FakeCalendars:
    """Google Calendar v3 and Microsoft Graph, as far as sync uses them."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.token_requests: list[dict[str, list[str]]] = []
        self.revoked: list[str] = []
        self.refresh_fails = False
        self.write_status = 200
        # Google
        self.google_items: list[dict[str, Any]] = [google_item("g1", "Fleet review", 5), google_item("g2", "Supplier call", 30)]
        self.google_changes: list[dict[str, Any]] = []
        self.google_requests: list[httpx.Request] = []
        self.google_writes: list[dict[str, Any]] = []
        self.google_query: list[dict[str, str]] = []
        self.google_expire_token = False
        # Microsoft
        self.graph_items: list[dict[str, Any]] = [graph_item("m1", "Teams stand-up", 6)]
        self.graph_changes: list[dict[str, Any]] = []
        self.graph_writes: list[dict[str, Any]] = []
        self.graph_calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        self.calls.append(f"{request.method} {host}{path}")
        if path.endswith("/token") and request.method == "POST":
            return self._token(request)
        if host == "oauth2.googleapis.com" and path == "/revoke":
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200, json={})
        if host == "openidconnect.googleapis.com":
            return httpx.Response(200, json={"email": "Owner@Example.com", "name": "Owner One"})
        if host == "www.googleapis.com":
            return self._google(request)
        if host == "graph.microsoft.com":
            return self._graph(request)
        return httpx.Response(404, json={"error": {"message": f"unexpected {request.method} {request.url}"}})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        self.token_requests.append(form)
        prefix = "google" if request.url.host == "oauth2.googleapis.com" else "graph"
        if form["grant_type"] == ["refresh_token"]:
            if self.refresh_fails:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
            return httpx.Response(200, json={"access_token": f"{prefix}-access-2", "expires_in": 3600})
        assert form["code"] == ["example-code"]
        return httpx.Response(200, json={"access_token": f"{prefix}-access-1", "refresh_token": f"{prefix}-refresh-1", "expires_in": 3600, "scope": "calendar"})

    def _google(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Bearer google-access-")
        params = dict(request.url.params)
        path = request.url.path.removeprefix("/calendar/v3/calendars/primary/events")
        if request.method == "GET" and path == "":
            self.google_query.append(params)
            if params.get("syncToken") == "S1" and self.google_expire_token:
                self.google_expire_token = False
                return httpx.Response(410, json={"error": {"message": "Sync token is no longer valid"}})
            if params.get("syncToken"):
                items, token = self.google_changes, "S2"
                self.google_changes = []
            else:
                items, token = self.google_items, "S1"
            return httpx.Response(200, json={"items": items, "nextSyncToken": token})
        if self.write_status >= 400:
            return httpx.Response(self.write_status, json={"error": {"message": "Backend Error"}})
        if request.method == "POST":
            body = json.loads(request.content)
            self.google_writes.append({"method": "POST", "params": params, **body})
            return httpx.Response(200, json={**google_item("g_new", body["summary"], 40, etag='"n1"'), "summary": body["summary"], "attendees": []})
        if request.method == "PATCH":
            body = json.loads(request.content)
            self.google_writes.append({"method": "PATCH", "id": path.removeprefix("/"), "params": params, **body})
            return httpx.Response(200, json={**google_item(path.removeprefix("/"), body["summary"], 5, etag='"e9"'), "summary": body["summary"]})
        if request.method == "DELETE":
            self.google_writes.append({"method": "DELETE", "id": path.removeprefix("/"), "params": params})
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"message": "unexpected"}})

    def _graph(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Bearer graph-access-")
        path = request.url.path.removeprefix("/v1.0/me")
        params = dict(request.url.params)
        self.graph_calls.append(f"{request.method} {path}")
        if path == "":
            return httpx.Response(200, json={"mail": "owner@contoso.example", "displayName": "Owner Two"})
        if path == "/calendarView/delta":
            if "$deltatoken" in params:
                items, link = self.graph_changes, "d2"
                self.graph_changes = []
            elif "$skiptoken" in params:
                return httpx.Response(200, json={"value": [graph_item("m2", "Page two", 8)], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/calendarView/delta?$deltatoken=d1"})
            else:
                assert "startDateTime" in params and "endDateTime" in params
                return httpx.Response(200, json={"value": self.graph_items, "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/calendarView/delta?$skiptoken=p2"})
            return httpx.Response(200, json={"value": items, "@odata.deltaLink": f"https://graph.microsoft.com/v1.0/me/calendarView/delta?$deltatoken={link}"})
        if self.write_status >= 400:
            return httpx.Response(self.write_status, json={"error": {"message": "Backend Error"}})
        if request.method == "POST" and path == "/events":
            body = json.loads(request.content)
            self.graph_writes.append({"method": "POST", **body})
            return httpx.Response(201, json=graph_item("m_new", body["subject"], 40, change_key="ckn", attendees=[]))
        if request.method == "PATCH":
            body = json.loads(request.content)
            self.graph_writes.append({"method": "PATCH", "id": path.removeprefix("/events/"), **body})
            return httpx.Response(200, json=graph_item(path.removeprefix("/events/"), body["subject"], 6, change_key="ck9"))
        if request.method in {"DELETE", "POST"}:
            self.graph_writes.append({"method": request.method, "path": path})
            return httpx.Response(204 if request.method == "DELETE" else 202)
        return httpx.Response(404, json={"error": {"message": "unexpected"}})


@pytest.fixture(autouse=True)
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeCalendars]:
    cal = FakeCalendars()
    monkeypatch.setattr(calendar_sync, "calendar_transport", httpx.MockTransport(cal))
    monkeypatch.setattr(calendar_settings, "google_client_id", "example-google-client")
    monkeypatch.setattr(calendar_settings, "google_client_secret", SecretStr("example"))
    monkeypatch.setattr(calendar_settings, "microsoft_client_id", "example-azure-client")
    monkeypatch.setattr(calendar_settings, "microsoft_client_secret", SecretStr("example"))
    monkeypatch.setattr(calendar_settings, "public_api_url", "https://api.shop.example")
    monkeypatch.setattr(calendar_settings, "web_url", "https://app.shop.example")
    calendar_backend.clear()
    sync_backend.clear()
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
        collection.clear()
    yield cal
    calendar_backend.clear()
    sync_backend.clear()


def connect(provider: str = "google", who: dict[str, str] = OWNER) -> dict[str, Any]:
    started = client.post(f"/api/v1/calendar/sync/oauth/{provider}/start", headers=who)
    assert started.status_code == 200, started.text
    query = parse_qs(urlsplit(started.json()["authorization_url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    back = client.get("/api/v1/calendar/sync/oauth/callback", params={"state": query["state"][0], "code": "example-code"}, follow_redirects=False)
    assert back.status_code == 303
    location = urlsplit(back.headers["location"])
    assert location.netloc == "app.shop.example" and location.fragment == "calendar"
    nonce = parse_qs(location.query)["calendar_connect"][0]
    done = client.post("/api/v1/calendar/sync/oauth/complete", headers=who, json={"connection": nonce})
    assert done.status_code == 201, done.text
    return done.json()


def events(**params: Any) -> list[dict[str, Any]]:
    response = client.get("/api/v1/calendar/events", headers=OWNER, params={"start": stamp(-24 * 40), "end": stamp(24 * 50), "include_cancelled": "true", **params})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def by_title(title: str) -> dict[str, Any]:
    return next(e for e in events() if e["title"] == title)


def sync(connection_id: str) -> dict[str, Any]:
    response = client.post(f"/api/v1/calendar/sync/connections/{connection_id}/sync", headers=OWNER)
    assert response.status_code == 200, response.text
    return response.json()


def stored(connection_id: str) -> calendar_sync.CalendarConnection:
    return sync_backend.connections[connection_id]


def new_event(title: str = "Tyre cost review", **overrides: Any) -> dict[str, Any]:
    start = utc_now().replace(microsecond=0) + timedelta(hours=40)
    body = {"title": title, "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat(), **overrides}
    response = client.post("/api/v1/calendar/events", headers=MEMBER, json=body)
    assert response.status_code == 201, response.text
    return response.json()


# -- setup and permissions ------------------------------------------------------------------------


def test_setup_names_missing_settings_and_hides_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    setup = client.get("/api/v1/calendar/sync", headers=VIEWER).json()
    assert setup["can_manage"] is False and setup["connections"] == []
    assert {p["provider"]: p["configured"] for p in setup["providers"]} == {"google": True, "microsoft": True}
    assert setup["redirect_uri"] == "https://api.shop.example/api/v1/calendar/sync/oauth/callback"
    assert "example" not in json.dumps(setup).replace("example.com", "").replace("shop.example", "").replace("example-", "")
    monkeypatch.setattr(calendar_settings, "google_client_id", "")
    monkeypatch.setattr(calendar_settings, "google_client_secret", None)
    monkeypatch.setattr(email_settings, "google_client_id", "")
    monkeypatch.setattr(email_settings, "google_client_secret", None)
    google = next(p for p in client.get("/api/v1/calendar/sync", headers=OWNER).json()["providers"] if p["provider"] == "google")
    assert google["configured"] is False and google["missing"] == ["ANUM_CALENDAR_GOOGLE_CLIENT_ID", "ANUM_CALENDAR_GOOGLE_CLIENT_SECRET"]
    refused = client.post("/api/v1/calendar/sync/oauth/google/start", headers=OWNER)
    assert refused.status_code == 409 and "ANUM_CALENDAR_GOOGLE_CLIENT_ID" in refused.json()["error"]["message"]


def test_the_inbox_oauth_app_is_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendar_settings, "microsoft_client_id", "")
    monkeypatch.setattr(calendar_settings, "microsoft_client_secret", None)
    assert calendar_sync.provider_problems(calendar_sync.CalendarProvider.MICROSOFT)
    monkeypatch.setattr(email_settings, "microsoft_client_id", "example-mail-client")
    monkeypatch.setattr(email_settings, "microsoft_client_secret", SecretStr("example"))
    assert calendar_sync.provider_problems(calendar_sync.CalendarProvider.MICROSOFT) == []


def test_only_owners_connect_and_everyone_can_read_the_status() -> None:
    assert client.post("/api/v1/calendar/sync/oauth/google/start", headers=MEMBER).status_code == 403
    connection = connect()
    assert client.get("/api/v1/calendar/sync", headers=VIEWER).json()["connections"][0]["id"] == connection["id"]
    assert client.post(f"/api/v1/calendar/sync/connections/{connection['id']}/sync", headers=MEMBER).status_code == 403
    assert client.delete(f"/api/v1/calendar/sync/connections/{connection['id']}", headers=MEMBER).status_code == 403
    assert Permission.CALENDAR_MANAGE in ROLE_PERMISSIONS[Role.OWNER]
    # Another workspace sees no connection.
    other = {**OWNER, "x-workspace-id": "workspace_elsewhere"}
    assert client.get("/api/v1/calendar/sync", headers=other).json()["connections"] == []
    assert client.post(f"/api/v1/calendar/sync/connections/{connection['id']}/sync", headers=other).status_code == 404


def test_callback_refuses_a_bad_state_and_a_second_use() -> None:
    back = client.get("/api/v1/calendar/sync/oauth/callback", params={"state": "nonsense", "code": "x"}, follow_redirects=False)
    assert "calendar_connect_error=invalid_state" in back.headers["location"]
    started = client.post("/api/v1/calendar/sync/oauth/microsoft/start", headers=OWNER).json()
    state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
    client.get("/api/v1/calendar/sync/oauth/callback", params={"state": state, "error": "access_denied", "error_description": "The user declined."}, follow_redirects=False)
    again = client.get("/api/v1/calendar/sync/oauth/callback", params={"state": state, "code": "example-code"}, follow_redirects=False)
    assert "calendar_connect_error=unknown_request" in again.headers["location"]
    # An email inbox state cannot be replayed here.
    mail_state = client.post("/api/v1/email/oauth/gmail/start", headers=OWNER, json={})
    if mail_state.status_code == 200:
        token = parse_qs(urlsplit(mail_state.json()["authorization_url"]).query)["state"][0]
        replay = client.get("/api/v1/calendar/sync/oauth/callback", params={"state": token, "code": "x"}, follow_redirects=False)
        assert "calendar_connect_error=invalid_state" in replay.headers["location"]


# -- Google ---------------------------------------------------------------------------------------


def test_google_connect_imports_events_and_stores_tokens_encrypted(fake: FakeCalendars) -> None:
    connection = connect()
    assert connection["email"] == "owner@example.com" and connection["status"] == "connected" and connection["fix"] == ""
    assert connection["events_synced"] == 2 and connection["last_sync_status"] == "ok"
    assert connection["last_summary"]["created"] == 2
    row = stored(connection["id"])
    assert "google-access" not in (row.access_token_enc or "") and "google-refresh" not in (row.refresh_token_enc or "")
    assert row.sync_cursor == "sync:S1"
    review = by_title("Fleet review")
    assert review["external_id"] == "g1" and review["sync_state"] == "synced" and review["connection_id"] == connection["id"]
    # The room resource is not an attendee; the address is lower-cased; the reply is mapped.
    assert [(a["email"], a["response"]) for a in review["attendees"]] == [("sajid@example.com", "accepted")]
    assert review["external_etag"] is None and review["invites_hash"] is None and review["invitations_pending"] is False  # the provider already holds these people
    assert fake.google_query[0]["singleEvents"] == "true" and "timeMin" in fake.google_query[0]


def test_google_incremental_sync_uses_the_token_and_applies_changes(fake: FakeCalendars) -> None:
    connection = connect()
    fake.google_changes = [
        google_item("g1", "Fleet review (moved)", 7, etag='"e2"', updated=stamp(0)),
        {"id": "g2", "status": "cancelled"},
        google_item("g3", "New workshop", 50),
    ]
    summary = sync(connection["id"])["summary"]
    assert fake.google_query[-1]["syncToken"] == "S1" and "timeMin" not in fake.google_query[-1]
    assert (summary["created"], summary["updated"], summary["deleted"]) == (1, 1, 1)
    assert sorted(e["title"] for e in events()) == ["Fleet review (moved)", "New workshop"]
    assert stored(connection["id"]).sync_cursor == "sync:S2"


def test_google_expired_sync_token_falls_back_to_a_full_sync(fake: FakeCalendars) -> None:
    connection = connect()
    fake.google_expire_token = True
    result = sync(connection["id"])
    assert result["connection"]["last_sync_status"] == "ok" and result["summary"]["full"] is True
    assert "syncToken" not in fake.google_query[-1]
    assert len(events()) == 2  # nothing duplicated


def test_a_local_event_is_sent_without_attendees_and_updates_and_deletes_follow(fake: FakeCalendars) -> None:
    connection = connect()
    created = new_event(attendees=[{"name": "Sara", "email": "sara@client.example"}])
    assert created["external_id"] == "g_new" and created["sync_state"] == "synced"
    assert fake.google_writes[0]["method"] == "POST" and "attendees" not in fake.google_writes[0]
    assert fake.google_writes[0]["params"]["sendUpdates"] == "none"
    patched = client.patch(f"/api/v1/calendar/events/{created['id']}", headers=MEMBER, json={"title": "Tyre cost review v2"})
    assert patched.status_code == 200 and patched.json()["sync_state"] == "synced"
    assert fake.google_writes[1]["method"] == "PATCH" and fake.google_writes[1]["id"] == "g_new" and "attendees" not in fake.google_writes[1]
    # The provider's echo of our own write is not treated as a remote change.
    fake.google_changes = [google_item("g_new", "Tyre cost review v2", 40, etag='"e9"')]
    assert sync(connection["id"])["summary"]["updated"] == 0
    assert client.delete(f"/api/v1/calendar/events/{created['id']}", headers=MEMBER).status_code == 204
    assert fake.google_writes[-1]["method"] == "DELETE" and fake.google_writes[-1]["id"] == "g_new"


def test_an_event_can_stay_in_anum_only(fake: FakeCalendars) -> None:
    connect()
    local = new_event("Private planning", sync=False)
    assert local["connection_id"] is None and local["external_id"] is None
    assert fake.google_writes == []


def test_a_provider_failure_keeps_the_event_queued_and_the_next_sync_sends_it(fake: FakeCalendars) -> None:
    connection = connect()
    fake.write_status = 500
    created = new_event()
    assert created["sync_state"] == "pending_push" and created["external_id"] is None
    failed = sync(connection["id"])
    assert failed["connection"]["status"] == "error" and "could not be sent" in failed["connection"]["last_error"]
    assert "Press Sync now" in failed["connection"]["fix"] and failed["summary"]["push_failed"] == 1
    fake.write_status = 200
    ok = sync(connection["id"])
    assert ok["connection"]["status"] == "connected" and ok["summary"]["pushed"] == 1
    assert by_title("Tyre cost review")["sync_state"] == "synced"


def test_deleting_is_refused_while_the_provider_cannot_remove_it(fake: FakeCalendars) -> None:
    connect()
    created = new_event()
    fake.write_status = 500
    refused = client.delete(f"/api/v1/calendar/events/{created['id']}", headers=MEMBER)
    assert refused.status_code == 502 and "was not deleted" in refused.json()["error"]["message"]
    assert client.get(f"/api/v1/calendar/events/{created['id']}", headers=MEMBER).status_code == 200


# -- conflicts ------------------------------------------------------------------------------------


def test_conflict_the_later_change_wins_and_the_provider_wins_a_tie(fake: FakeCalendars) -> None:
    connection = connect()
    fake.write_status = 500
    target = by_title("Fleet review")
    patched = client.patch(f"/api/v1/calendar/events/{target['id']}", headers=OWNER, json={"title": "Fleet review (ANUM edit)"})
    assert patched.json()["sync_state"] == "pending_push"
    # The provider changed it earlier than the ANUM edit: the ANUM edit wins and stays queued.
    fake.google_changes = [google_item("g1", "Fleet review (Google edit)", 5, etag='"e2"', updated=stamp(-2))]
    summary = sync(connection["id"])["summary"]
    assert summary["conflicts_local_won"] == 1
    assert by_title("Fleet review (ANUM edit)")["sync_state"] == "pending_push"
    # The provider changed it later: the provider's version overwrites the ANUM edit.
    fake.google_changes = [google_item("g1", "Fleet review (Google later)", 5, etag='"e3"', updated=stamp(2))]
    summary = sync(connection["id"])["summary"]
    assert summary["conflicts_remote_won"] == 1
    won = by_title("Fleet review (Google later)")
    assert won["sync_state"] == "synced" and won["updated_by"] == "sync"


def test_a_provider_deletion_does_not_destroy_an_unsent_local_edit(fake: FakeCalendars) -> None:
    connection = connect()
    fake.write_status = 500
    target = by_title("Supplier call")
    client.patch(f"/api/v1/calendar/events/{target['id']}", headers=OWNER, json={"title": "Supplier call (kept)"})
    fake.google_changes = [{"id": "g2", "status": "cancelled", "updated": stamp(-3)}]
    sync(connection["id"])
    kept = by_title("Supplier call (kept)")
    assert kept["external_id"] is None and kept["sync_state"] == "pending_push"
    fake.write_status = 200
    sync(connection["id"])
    assert by_title("Supplier call (kept)")["external_id"] == "g_new"


def test_changing_the_people_of_a_synced_event_needs_an_approval_again(fake: FakeCalendars) -> None:
    connection = connect()
    review = by_title("Fleet review")
    assert review["invitations_pending"] is False
    edited = client.patch(
        f"/api/v1/calendar/events/{review['id']}", headers=OWNER,
        json={"attendees": [*review["attendees"], {"name": "New Person", "email": "new@client.example"}]},
    ).json()
    assert edited["invitations_pending"] is True
    assert "attendees" not in fake.google_writes[-1] and fake.google_writes[-1]["params"]["sendUpdates"] == "none"
    assert connection["id"]


def test_events_organised_by_someone_else_follow_the_provider(fake: FakeCalendars) -> None:
    connection = connect()
    fake.google_changes = [google_item("g9", "Customer workshop", 12, organizer={"email": "boss@client.example", "self": False})]
    sync(connection["id"])
    guest = by_title("Customer workshop")
    patched = client.patch(f"/api/v1/calendar/events/{guest['id']}", headers=OWNER, json={"description": "Notes"})
    assert patched.json()["sync_state"] == "synced"
    assert not [w for w in fake.google_writes if w["method"] != "GET"]


# -- invitations ----------------------------------------------------------------------------------


def test_invitations_wait_for_an_owner_and_bind_the_exact_content(fake: FakeCalendars) -> None:
    connect()
    created = new_event("Client kickoff", attendees=[{"name": "Sara", "email": "Sara@Client.example"}, {"name": "Omar"}])
    assert created["invitations_pending"] is True
    assert client.post(f"/api/v1/calendar/events/{created['id']}/invitations", headers=VIEWER).status_code == 403
    asked = client.post(f"/api/v1/calendar/events/{created['id']}/invitations", headers=MEMBER)
    assert asked.status_code == 202, asked.text
    approval = asked.json()["approval"]
    assert asked.json()["attendees"] == ["sara@client.example"]
    assert approval["action"] == "calendar.send_invitations" and approval["risk_level"] == "high"
    assert approval["arguments"]["attendees"] == ["sara@client.example"] and approval["arguments"]["content_hash"]
    writes_before = len(fake.google_writes)
    # A member cannot approve; a wrong hash is refused; nothing is sent.
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=MEMBER, json={"payload_hash": approval["payload_hash"]}).status_code == 403
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": "f" * 64}).status_code == 409
    assert len(fake.google_writes) == writes_before
    approved = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]})
    assert approved.status_code == 200, approved.text
    sent = fake.google_writes[-1]
    assert sent["method"] == "PATCH" and sent["params"]["sendUpdates"] == "all"
    assert sent["attendees"] == [{"email": "sara@client.example", "displayName": "Sara"}]
    after = client.get(f"/api/v1/calendar/events/{created['id']}", headers=MEMBER).json()
    assert after["invitations_pending"] is False and after["sync_state"] == "synced"
    # Later edits keep the approved invitees and notify nobody new; a new attendee needs a new approval.
    edited = client.patch(f"/api/v1/calendar/events/{created['id']}", headers=OWNER, json={"attendees": [{"name": "Sara", "email": "sara@client.example"}, {"name": "Lee", "email": "lee@client.example"}]})
    assert edited.json()["invitations_pending"] is True
    assert "attendees" not in fake.google_writes[-1] and fake.google_writes[-1]["params"]["sendUpdates"] == "none"


def test_an_invitation_approved_after_the_event_changed_sends_nothing(fake: FakeCalendars) -> None:
    connect()
    created = new_event("Client kickoff", attendees=[{"name": "Sara", "email": "sara@client.example"}])
    approval = client.post(f"/api/v1/calendar/events/{created['id']}/invitations", headers=OWNER).json()["approval"]
    client.patch(f"/api/v1/calendar/events/{created['id']}", headers=OWNER, json={"attendees": [{"name": "Sara", "email": "sara@client.example"}, {"name": "Eve", "email": "eve@client.example"}]})
    before = len(fake.google_writes)
    client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]})
    assert not [w for w in fake.google_writes[before:] if w.get("params", {}).get("sendUpdates") == "all"]
    assert client.get(f"/api/v1/calendar/events/{created['id']}", headers=OWNER).json()["invitations_pending"] is True


def test_invitations_need_attendees_and_a_connected_calendar() -> None:
    plain = new_event("Solo focus")
    no_calendar = client.post(f"/api/v1/calendar/events/{plain['id']}/invitations", headers=OWNER)
    assert no_calendar.status_code == 422 and "attendee" in no_calendar.json()["error"]["message"]
    with_people = new_event("Planning", attendees=[{"name": "Sara", "email": "sara@client.example"}])
    refused = client.post(f"/api/v1/calendar/events/{with_people['id']}/invitations", headers=OWNER)
    assert refused.status_code == 409 and "Connect Google Calendar or Microsoft 365" in refused.json()["error"]["message"]
    assert client.post("/api/v1/calendar/events/event_missing/invitations", headers=OWNER).status_code == 404


# -- Microsoft ------------------------------------------------------------------------------------


def test_microsoft_connect_follows_delta_pages_and_removals(fake: FakeCalendars) -> None:
    connection = connect("microsoft")
    assert connection["email"] == "owner@contoso.example" and connection["events_synced"] == 2
    assert stored(connection["id"]).sync_cursor and "d1" in stored(connection["id"]).sync_cursor
    stand_up = by_title("Teams stand-up")
    assert stand_up["conference_url"] == "https://teams.example.com/meet/abc"
    assert stand_up["attendees"][0]["response"] == "tentative"
    fake.graph_changes = [{"id": "m2", "@removed": {"reason": "deleted"}}, graph_item("m1", "Teams stand-up (moved)", 9, change_key="ck2", modified=stamp(0))]
    summary = sync(connection["id"])["summary"]
    assert (summary["updated"], summary["deleted"]) == (1, 1)
    assert [e["title"] for e in events()] == ["Teams stand-up (moved)"]
    assert "d2" in stored(connection["id"]).sync_cursor


def test_microsoft_writes_never_carry_attendees_until_approved(fake: FakeCalendars) -> None:
    connect("microsoft")
    created = new_event("Board prep", attendees=[{"name": "Sara", "email": "sara@client.example"}])
    assert fake.graph_writes[0]["method"] == "POST" and "attendees" not in fake.graph_writes[0]
    approval = client.post(f"/api/v1/calendar/events/{created['id']}/invitations", headers=OWNER).json()["approval"]
    client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]})
    assert fake.graph_writes[-1]["attendees"][0]["emailAddress"]["address"] == "sara@client.example"
    assert client.delete(f"/api/v1/calendar/events/{created['id']}", headers=MEMBER).status_code == 204
    assert fake.graph_writes[-1]["path"].endswith("/cancel")  # the invitees are told


# -- health and disconnecting ---------------------------------------------------------------------


def test_a_revoked_grant_needs_reconnecting_and_says_how(fake: FakeCalendars) -> None:
    connection = connect()
    row = stored(connection["id"])
    row.access_expires_at = utc_now() - timedelta(minutes=1)
    fake.refresh_fails = True
    result = sync(connection["id"])["connection"]
    assert result["status"] == "reauth_required" and "expired or revoked" in result["last_error"]
    assert "Connect Google Calendar again" in result["fix"]
    # Events already synced stay readable and local edits are queued, not lost.
    assert len(events()) == 2
    edited = client.patch(f"/api/v1/calendar/events/{by_title('Fleet review')['id']}", headers=OWNER, json={"title": "Fleet review 2"})
    assert edited.status_code == 200 and edited.json()["sync_state"] == "pending_push"
    # Connecting again restores it.
    fake.refresh_fails = False
    again = connect()
    assert again["id"] == connection["id"] and again["status"] == "connected"


def test_disconnecting_revokes_the_grant_and_keeps_the_events(fake: FakeCalendars) -> None:
    connection = connect()
    done = client.delete(f"/api/v1/calendar/sync/connections/{connection['id']}", headers=OWNER)
    assert done.status_code == 200 and done.json()["revoked_at_provider"] is True and done.json()["events_kept"] == 2
    assert fake.revoked and "google-" in fake.revoked[0]
    assert client.get("/api/v1/calendar/sync", headers=OWNER).json()["connections"] == []
    kept = by_title("Fleet review")
    assert kept["connection_id"] is None and kept["external_id"] is None
    assert not [c for c in fake.calls if c.startswith("DELETE")]  # nothing is deleted at the provider
    assert client.delete(f"/api/v1/calendar/sync/connections/{connection['id']}", headers=OWNER).status_code == 404


def test_actions_are_audited_without_secrets(fake: FakeCalendars) -> None:
    connection = connect()
    sync(connection["id"])
    actions = [r.action for r in store.audit_records]
    assert {"calendar.connect.started", "calendar.connection.created", "calendar.sync.run"} <= set(actions)
    assert "example-code" not in repr(store.audit_records)


def test_the_scheduler_only_runs_with_postgresql_and_a_configured_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    class Config:
        repository_backend = "memory"

    assert calendar_sync.build_calendar_scheduler(Config()) is None
    Config.repository_backend = "postgresql"
    assert calendar_sync.build_calendar_scheduler(Config()) is not None
    monkeypatch.setattr(calendar_settings, "sync_interval_seconds", 0)
    assert calendar_sync.build_calendar_scheduler(Config()) is None


# -- provider clients -----------------------------------------------------------------------------


def test_graph_refuses_a_paging_link_to_another_host() -> None:
    import asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [], "@odata.nextLink": "https://evil.example/v1.0/me/calendarView/delta?$skiptoken=x"})

    graph = MicrosoftCalendarClient(OAuthClient("id", "secret", "https://x.example/cb"), httpx.MockTransport(handler))
    now = utc_now()
    with pytest.raises(MailProviderError) as raised:
        asyncio.run(graph.changes("token", None, since=now, until=now + timedelta(days=1)))
    assert "another host" in raised.value.message


def test_provider_event_parsing_ignores_unusable_items() -> None:
    assert google_event({"id": "../etc", "summary": "x", "start": {"dateTime": stamp(1)}, "end": {"dateTime": stamp(2)}}) is None
    assert google_event({"id": "ok1", "summary": "x"}) is None
    all_day = google_event({"id": "ok2", "summary": "Holiday", "start": {"date": "2030-01-02"}, "end": {"date": "2030-01-03"}})
    assert all_day is not None and (all_day.ends_at - all_day.starts_at) == timedelta(days=1)
    assert graph_event({"id": "ok3", "subject": "x", "start": {"dateTime": "2030-01-02T10:00:00.0000000", "timeZone": "W. Europe Standard Time"}, "end": {"dateTime": "2030-01-02T11:00:00.0000000", "timeZone": "UTC"}}) is None
    assert GoogleCalendarClient(OAuthClient("id", "secret", "https://x.example/cb")).authorization_url("s", "c").startswith("https://accounts.google.com/")


def test_the_invitation_hash_changes_with_the_people_and_the_time() -> None:
    created = new_event("Hash me", attendees=[{"name": "Sara", "email": "sara@client.example"}], sync=False)
    event = calendar_backend.events[created["id"]]
    first = invitation_hash(event)
    event.attendees[0].name = "Sara Renamed"
    assert invitation_hash(event) == first  # a display name does not change who is invited
    event.starts_at += timedelta(minutes=5)
    assert invitation_hash(event) != first
