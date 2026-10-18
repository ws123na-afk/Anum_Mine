"""Phone push notifications (docs/notifications.md), memory backend.

FCM itself is faked: ``FakeSender`` for the dispatcher, ``httpx.MockTransport`` for the
HTTP v1 client. The service-account key is generated per test run, never committed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from anum_api import developer, main, push
from anum_api.dependencies import event_runtime, memory_repository
from anum_api.developer import DevRequestStatus, developer_settings, developer_store
from anum_api.developer_watch import build_developer_watcher, discover_in_progress, refresh_in_progress
from anum_api.events import CanonicalEventName, create_event
from anum_api.onboarding import NotificationPreferences, _notifications
from anum_api.push import (
    PushDispatcher,
    PushIntent,
    PushKind,
    intent_for,
    push_device_store,
    push_dispatcher,
    resolve_recipients,
)
from anum_api.push_fcm import (
    FcmSender,
    PushMessage,
    SendOutcome,
    parse_service_account,
    service_account_problem,
)
from anum_api.schemas import (
    Approval,
    ApprovalStatus,
    RiskLevel,
    Task,
    TaskStatus,
    TenantContext,
    WorkspaceApprovalPolicy,
    WorkspaceMembership,
    new_id,
    utc_now,
)
from anum_api.secret_box import secret_cipher
from anum_api.settings import Settings, settings
from anum_api.store import store

TENANT, WORKSPACE = "tenant_push", "workspace_push"
# Plainly fake, low-entropy device tokens (gitleaks: CLAUDE.md).
PHONE_A = "fcm-token-example-owner-a-phone"
PHONE_B = "fcm-token-example-owner-b-phone"
PHONE_M = "fcm-token-example-member-phone"

client = TestClient(main.app)


def headers(user: str = "owner_a", role: str = "owner", workspace: str = WORKSPACE) -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


def ctx(user: str = "owner_a", role: str = "owner") -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, roles=[role])


def service_account_json(project_id: str = "anum-example") -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return json.dumps(
        {
            "type": "service_account",
            "project_id": project_id,
            "client_email": f"push@{project_id}.iam.gserviceaccount.com",
            "private_key": pem,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


SERVICE_ACCOUNT = service_account_json()


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[PushMessage] = []
        self.outcomes: dict[str, SendOutcome] = {}

    async def send(self, message: PushMessage) -> SendOutcome:
        self.messages.append(message)
        return self.outcomes.get(message.token, SendOutcome.SENT)

    async def close(self) -> None:
        pass

    def tokens(self) -> list[str]:
        return sorted(message.token for message in self.messages)


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    def reset() -> None:
        push_device_store.clear()
        _notifications.clear()
        developer_store.clear()
        store.memberships.clear()
        store.approval_policies.clear()
        for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
            collection.clear()
        with push_dispatcher._lock:
            push_dispatcher._queue.clear()

    reset()
    yield
    reset()


@pytest.fixture
def sender(monkeypatch: pytest.MonkeyPatch) -> FakeSender:
    fake = FakeSender()
    monkeypatch.setattr(settings, "fcm_service_account_json", SecretStr(SERVICE_ACCOUNT))
    monkeypatch.setattr(push_dispatcher, "_sender_factory", lambda: fake)
    monkeypatch.setattr(push_dispatcher, "_sender", None)
    return fake


def member(user: str, role: str, *, active: bool = True) -> None:
    now = utc_now()
    memory_repository.save_membership(
        WorkspaceMembership(
            tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, role=role, active=active, created_at=now, updated_at=now
        )
    )


def register(user: str, role: str, token: str, platform: str = "android") -> dict[str, Any]:
    response = client.post(
        "/api/v1/me/devices", headers=headers(user, role), json={"platform": platform, "token": token, "label": "Phone"}
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def deliver() -> push.DeliveryReport:
    return asyncio.run(push_dispatcher.run_pending())


def pending_approval(*, risk: RiskLevel = RiskLevel.HIGH, requested_by: str = "member_m", created_by: str = "member_m") -> Approval:
    now = utc_now()
    task = memory_repository.create_task(
        Task(
            id=new_id("task"),
            title="Secret project title",
            prompt="Send the board deck to the investor list",
            status=TaskStatus.WAITING_APPROVAL,
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            created_at=now,
            updated_at=now,
            created_by=created_by,
        )
    )
    return memory_repository.save_approval(
        Approval(
            id=new_id("approval"),
            task_id=task.id,
            action="email.send",
            risk_level=risk,
            status=ApprovalStatus.PENDING,
            reason="High-risk tool",
            created_at=now,
            requested_by=requested_by,
            arguments={"to": "investors@example.com"},
        )
    )


def announce(event_type: CanonicalEventName, subject: str, payload: dict[str, Any]) -> None:
    event_runtime.after_commit([create_event(event_type, ctx(), subject, payload).event])


# --------------------------------------------------------------------------- settings and status


def test_service_account_setting_is_validated_without_echoing_the_key() -> None:
    assert service_account_problem(SERVICE_ACCOUNT) is None
    account = parse_service_account(SERVICE_ACCOUNT)
    assert account.project_id == "anum-example" and "PRIVATE KEY" not in repr(account)
    for bad, expected in (
        ("not json", "JSON key file"),
        ('{"type": "authorized_user"}', "Google service-account key"),
        ('{"type": "service_account", "project_id": "p"}', "missing client_email, private_key"),
        (
            '{"type": "service_account", "project_id": "p", "client_email": "e", "private_key": "nope"}',
            "PEM private key",
        ),
    ):
        assert expected in (service_account_problem(bad) or "")
        with pytest.raises(ValidationError) as caught:
            Settings(fcm_service_account_json=bad)
        assert "ANUM_FCM_SERVICE_ACCOUNT_JSON" in str(caught.value)
        assert bad not in str(caught.value)
    assert Settings(fcm_service_account_json="").fcm_service_account_json is None
    assert Settings(fcm_service_account_json=SERVICE_ACCOUNT).fcm_service_account_json is not None


def test_status_says_when_push_is_off_and_registration_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "fcm_service_account_json", None)
    status = client.get("/api/v1/push/status", headers=headers("member_m", "member")).json()
    assert status["available"] is False and "ANUM_FCM_SERVICE_ACCOUNT_JSON" in status["message"]
    refused = client.post("/api/v1/me/devices", headers=headers(), json={"platform": "android", "token": PHONE_A})
    assert refused.status_code == 409 and "off" in refused.json()["error"]["message"]
    # Off means nothing is even queued.
    announce(CanonicalEventName.APPROVAL_REQUESTED, "approval_x", {"task_id": "task_x"})
    assert push_dispatcher.pending == 0


def test_status_names_the_project_when_push_is_on(sender: FakeSender) -> None:
    status = client.get("/api/v1/push/status", headers=headers("viewer_v", "viewer")).json()
    assert status == {
        "available": True,
        "provider": "fcm",
        "project_id": "anum-example",
        "message": "Phone push is on. Each phone must allow notifications and be registered.",
    }


# --------------------------------------------------------------------------- devices


def test_register_list_and_remove_only_the_callers_own_devices(sender: FakeSender) -> None:
    created = client.post(
        "/api/v1/me/devices", headers=headers(), json={"platform": "ios", "token": PHONE_A, "label": "iPhone"}
    )
    assert created.status_code == 201
    device = created.json()
    assert device["platform"] == "ios" and device["label"] == "iPhone" and device["token_hint"] == PHONE_A[-4:]
    assert PHONE_A not in created.text and "token_ciphertext" not in device

    again = client.post("/api/v1/me/devices", headers=headers(), json={"platform": "ios", "token": PHONE_A})
    assert again.status_code == 200 and again.json()["id"] == device["id"]

    (stored,) = push_device_store.list(ctx())
    assert stored.token_ciphertext != PHONE_A and secret_cipher().decrypt(stored.token_ciphertext) == PHONE_A

    assert [item["id"] for item in client.get("/api/v1/me/devices", headers=headers()).json()] == [device["id"]]
    assert client.get("/api/v1/me/devices", headers=headers("owner_b")).json() == []
    assert client.get("/api/v1/me/devices", headers=headers(workspace="workspace_other")).json() == []
    assert client.delete(f"/api/v1/me/devices/{device['id']}", headers=headers("owner_b")).status_code == 404
    assert client.delete(f"/api/v1/me/devices/{device['id']}", headers=headers()).status_code == 204
    assert client.get("/api/v1/me/devices", headers=headers()).json() == []
    assert client.delete(f"/api/v1/me/devices/{device['id']}", headers=headers()).status_code == 404

    actions = [(record.action, dict(record.metadata)) for record in store.audit_records]
    assert actions == [
        ("push.device.registered", {"platform": "ios"}),
        ("push.device.removed", {"platform": "ios"}),
    ]
    assert PHONE_A[-4:] not in json.dumps([record.target for record in store.audit_records])


def test_registration_is_validated(sender: FakeSender) -> None:
    for body in (
        {"platform": "windows", "token": PHONE_A},
        {"platform": "android", "token": "short"},
        {"platform": "android", "token": "has spaces in the token value"},
        {"platform": "android", "token": PHONE_A, "user_id": "someone_else"},
    ):
        response = client.post("/api/v1/me/devices", headers=headers(), json=body)
        assert response.status_code == 422, body
        assert PHONE_A not in response.text


def test_the_least_recently_registered_phone_makes_room(sender: FakeSender, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "push_max_devices_per_user", 2)
    first = register("owner_a", "owner", PHONE_A)
    register("owner_a", "owner", PHONE_B)
    register("owner_a", "owner", PHONE_M)
    remaining = client.get("/api/v1/me/devices", headers=headers()).json()
    assert len(remaining) == 2 and first["id"] not in [item["id"] for item in remaining]


# --------------------------------------------------------------------------- delivery


def test_events_map_to_push_kinds_with_ids_only() -> None:
    def event(kind: CanonicalEventName, subject: str, payload: dict[str, Any]):  # type: ignore[no-untyped-def]
        return create_event(kind, ctx(), subject, payload).event

    approval = intent_for(event(CanonicalEventName.APPROVAL_REQUESTED, "approval_1", {"task_id": "task_1", "tool": "email.send"}))
    assert approval is not None and approval.kind == PushKind.APPROVAL_REQUIRED
    assert approval.data() == {
        "anum_kind": "approval_required",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "task_id": "task_1",
        "approval_id": "approval_1",
    }
    done = intent_for(event(CanonicalEventName.AGENT_RUN_COMPLETED, "run_1", {"task_id": "task_1"}))
    assert done is not None and done.kind == PushKind.TASK_COMPLETED and done.run_id == "run_1"
    failed = intent_for(event(CanonicalEventName.AGENT_RUN_FAILED, "run_1", {"task_id": "task_1"}))
    assert failed is not None and failed.kind == PushKind.RUN_FAILED
    ready = intent_for(event(CanonicalEventName.DEVELOPER_REQUEST_READY_FOR_REVIEW, "dev_1", {"status": "waiting_approval"}))
    assert ready is not None and ready.data()["developer_request_id"] == "dev_1"
    assert intent_for(event(CanonicalEventName.TASK_CREATED, "task_1", {})) is None
    assert intent_for(event(CanonicalEventName.APPROVAL_APPROVED, "approval_1", {"task_id": "task_1"})) is None


def test_two_person_risk_levels_match_the_approval_route() -> None:
    assert push.TWO_PERSON_RISK_LEVELS == main.TWO_PERSON_RISK_LEVELS


def test_approval_push_reaches_the_owners_who_may_decide_it(sender: FakeSender) -> None:
    member("owner_a", "owner")
    member("owner_b", "owner")
    member("member_m", "member")
    member("owner_gone", "owner", active=False)
    register("owner_a", "owner", PHONE_A)
    register("owner_b", "owner", PHONE_B)
    register("member_m", "member", PHONE_M)
    approval = pending_approval()

    announce(CanonicalEventName.APPROVAL_REQUESTED, approval.id, {"task_id": approval.task_id, "tool": "email.send"})
    assert push_dispatcher.pending == 1
    report = deliver()
    assert (report.recipients, report.sent) == (2, 2)
    assert sender.tokens() == sorted([PHONE_A, PHONE_B])
    message = sender.messages[0]
    assert (message.title, message.body) == ("Approval needed", "An action is waiting for your decision.")
    assert message.data["approval_id"] == approval.id and message.collapse_id == approval.id
    # Nothing about the task, the tool call or its arguments leaves the server.
    rendered = json.dumps(message.payload())
    for secret in ("Secret project title", "board deck", "investors@example.com", "email.send"):
        assert secret not in rendered
    assert push_device_store.list(ctx("owner_a"))[0].last_sent_at is not None


def test_approval_push_respects_preferences_and_the_two_person_rule(sender: FakeSender) -> None:
    member("owner_a", "owner")
    member("owner_b", "owner")
    register("owner_a", "owner", PHONE_A)
    register("owner_b", "owner", PHONE_B)
    _notifications.save(ctx("owner_b"), NotificationPreferences(approval_required=False))
    approval = pending_approval(requested_by="owner_a", created_by="owner_a")

    intent = PushIntent(PushKind.APPROVAL_REQUIRED, TENANT, WORKSPACE, "event_1", approval_id=approval.id, task_id=approval.task_id)
    assert resolve_recipients(intent) == ["owner_a", "owner_b"]
    memory_repository.save_approval_policy(WorkspaceApprovalPolicy(two_person_rule=True), ctx())
    # The creator cannot approve their own high-risk action under the two-person rule.
    assert resolve_recipients(intent) == ["owner_b"]
    announce(CanonicalEventName.APPROVAL_REQUESTED, approval.id, {"task_id": approval.task_id})
    assert deliver().sent == 0  # owner_b turned approval pushes off

    _notifications.save(ctx("owner_b"), NotificationPreferences(push_enabled=False))
    announce(CanonicalEventName.APPROVAL_REQUESTED, approval.id, {"task_id": approval.task_id})
    assert deliver().sent == 0  # and phone push off altogether
    _notifications.save(ctx("owner_b"), NotificationPreferences())
    announce(CanonicalEventName.APPROVAL_REQUESTED, approval.id, {"task_id": approval.task_id})
    assert deliver().sent == 1 and sender.tokens() == [PHONE_B]

    approval.status = ApprovalStatus.APPROVED
    memory_repository.save_approval(approval)
    assert resolve_recipients(intent) == []  # decided: nobody is reminded any more


def test_run_results_go_to_the_task_creator_only_while_they_may_read_it(sender: FakeSender) -> None:
    member("owner_a", "owner")
    member("member_m", "member")
    register("owner_a", "owner", PHONE_A)
    register("member_m", "member", PHONE_M)
    approval = pending_approval(created_by="member_m")

    announce(CanonicalEventName.AGENT_RUN_COMPLETED, "run_1", {"task_id": approval.task_id})
    assert deliver().sent == 1 and sender.tokens() == [PHONE_M]
    assert (sender.messages[0].title, sender.messages[0].data["run_id"]) == ("Task finished", "run_1")

    sender.messages.clear()
    _notifications.save(ctx("member_m", "member"), NotificationPreferences(run_failed=False))
    announce(CanonicalEventName.AGENT_RUN_FAILED, "run_1", {"task_id": approval.task_id})
    assert deliver().sent == 0

    _notifications.save(ctx("member_m", "member"), NotificationPreferences())
    member("member_m", "member", active=False)
    announce(CanonicalEventName.AGENT_RUN_FAILED, "run_1", {"task_id": approval.task_id})
    assert deliver().recipients == 0 and sender.messages == []


def test_unregistered_tokens_are_removed_and_one_phone_buzzes_once(sender: FakeSender) -> None:
    member("owner_a", "owner")
    member("owner_b", "owner")
    register("owner_a", "owner", PHONE_A)
    register("owner_b", "owner", PHONE_A)  # the same phone, signed in as both
    register("owner_b", "owner", PHONE_B)
    sender.outcomes[PHONE_B] = SendOutcome.UNREGISTERED
    approval = pending_approval()

    announce(CanonicalEventName.APPROVAL_REQUESTED, approval.id, {"task_id": approval.task_id})
    report = deliver()
    assert (report.sent, report.removed) == (1, 1)
    assert sender.tokens() == sorted([PHONE_A, PHONE_B])
    assert [device.token_hint for device in push_device_store.list(ctx("owner_b"))] == [PHONE_A[-4:]]


def test_fan_out_is_bounded(sender: FakeSender, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "push_max_recipients", 2)
    for index in range(4):
        member(f"owner_{index}", "owner")
        register(f"owner_{index}", "owner", f"fcm-token-example-owner-{index}")
    approval = pending_approval()
    announce(CanonicalEventName.APPROVAL_REQUESTED, approval.id, {"task_id": approval.task_id})
    assert deliver().sent == 2


def test_the_queue_is_bounded_and_one_bad_event_does_not_stop_the_rest(
    sender: FakeSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "push_max_pending", 10)
    dispatcher = PushDispatcher(lambda: sender)
    events = [create_event(CanonicalEventName.AGENT_RUN_FAILED, ctx(), f"run_{i}", {"task_id": "missing"}).event for i in range(12)]
    dispatcher.submit(events)
    assert dispatcher.pending == 10 and dispatcher.dropped == 2

    calls: list[str] = []

    def flaky(intent: PushIntent, *, limit: int | None = None) -> list[str]:
        calls.append(intent.run_id or "")
        if len(calls) == 1:
            raise RuntimeError("database unavailable")
        return []

    monkeypatch.setattr(push, "resolve_recipients", flaky)
    asyncio.run(dispatcher.run_pending())
    assert len(calls) == 10 and dispatcher.pending == 0


def test_the_dispatcher_runs_in_the_background_from_committed_events(sender: FakeSender) -> None:
    member("owner_a", "owner")
    register("owner_a", "owner", PHONE_A)
    approval = pending_approval()

    async def scenario() -> None:
        push_dispatcher.start()
        try:
            await asyncio.to_thread(
                announce, CanonicalEventName.APPROVAL_REQUESTED, approval.id, {"task_id": approval.task_id}
            )
            for _ in range(200):
                if sender.messages:
                    break
                await asyncio.sleep(0.01)
        finally:
            await push_dispatcher.stop()

    asyncio.run(scenario())
    assert sender.tokens() == [PHONE_A]


# --------------------------------------------------------------------------- FCM HTTP v1


class FakeFcm:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.token_requests = 0
        self.sends: list[dict[str, Any]] = []
        self.auth: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            self.token_requests += 1
            assert b"grant_type=urn" in request.content and b"assertion=" in request.content
            return httpx.Response(200, json={"access_token": f"access-{self.token_requests}", "expires_in": 3600})
        assert request.url.path == "/v1/projects/anum-example/messages:send"
        self.auth.append(request.headers["authorization"])
        self.sends.append(json.loads(request.content))
        return self.responses.pop(0)


def fcm_sender(fake: FakeFcm, sleeps: list[float]) -> FcmSender:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return FcmSender(
        parse_service_account(SERVICE_ACCOUNT),
        transport=httpx.MockTransport(fake),
        max_attempts=3,
        sleep=sleep,
        jitter=lambda: 1.0,
    )


MESSAGE = PushMessage(token=PHONE_A, title="Approval needed", body="An action is waiting.", data={"approval_id": "a1"}, collapse_id="a1")


def _send(fake: FakeFcm, sleeps: list[float]) -> SendOutcome:
    async def run() -> SendOutcome:
        sender = fcm_sender(fake, sleeps)
        try:
            return await sender.send(MESSAGE)
        finally:
            await sender.close()

    return asyncio.run(run())


def test_fcm_sends_one_v1_message_with_an_oauth_token() -> None:
    fake, sleeps = FakeFcm([httpx.Response(200, json={"name": "projects/anum-example/messages/1"})]), []
    assert _send(fake, sleeps) == SendOutcome.SENT
    (sent,) = fake.sends
    assert sent["message"]["token"] == PHONE_A
    assert sent["message"]["notification"] == {"title": "Approval needed", "body": "An action is waiting."}
    assert sent["message"]["data"] == {"approval_id": "a1"}
    assert sent["message"]["android"]["notification"]["tag"] == "a1"
    assert sent["message"]["apns"]["headers"]["apns-collapse-id"] == "a1"
    assert fake.auth == ["Bearer access-1"] and sleeps == []


def test_fcm_retries_with_backoff_and_honours_retry_after() -> None:
    fake = FakeFcm(
        [
            httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}}),
            httpx.Response(429, headers={"Retry-After": "7"}, json={"error": {"status": "RESOURCE_EXHAUSTED"}}),
            httpx.Response(200, json={}),
        ]
    )
    sleeps: list[float] = []
    assert _send(fake, sleeps) == SendOutcome.SENT
    assert sleeps == [1.0, 7.0] and len(fake.sends) == 3 and fake.token_requests == 1


def test_fcm_gives_up_after_the_last_attempt() -> None:
    fake, sleeps = FakeFcm([httpx.Response(500, json={}) for _ in range(3)]), []
    assert _send(fake, sleeps) == SendOutcome.FAILED
    assert sleeps == [1.0, 2.0]


def test_fcm_refreshes_an_expired_access_token() -> None:
    fake, sleeps = FakeFcm([httpx.Response(401, json={"error": {"status": "UNAUTHENTICATED"}}), httpx.Response(200, json={})]), []
    assert _send(fake, sleeps) == SendOutcome.SENT
    assert fake.auth == ["Bearer access-1", "Bearer access-2"]


@pytest.mark.parametrize("code", ["UNREGISTERED", "INVALID_ARGUMENT", "SENDER_ID_MISMATCH"])
def test_fcm_token_errors_mean_remove_the_device(code: str) -> None:
    body = {"error": {"status": "NOT_FOUND", "details": [{"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": code}]}}
    fake, sleeps = FakeFcm([httpx.Response(404 if code == "UNREGISTERED" else 400, json=body)]), []
    assert _send(fake, sleeps) == SendOutcome.UNREGISTERED and sleeps == []


def test_a_wrong_project_does_not_remove_devices() -> None:
    fake, sleeps = FakeFcm([httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}})]), []
    assert _send(fake, sleeps) == SendOutcome.FAILED


# --------------------------------------------------------------------------- developer mode


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from test_developer import PAT, REPO, FakeGitHub

    fake = FakeGitHub()
    monkeypatch.setattr(developer, "github_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(developer_settings, "github_repo", REPO)
    monkeypatch.setattr(developer_settings, "github_token", SecretStr(PAT))
    monkeypatch.setattr(developer_settings, "tenant_id", TENANT)
    monkeypatch.setattr(developer_settings, "workspace_id", "")
    monkeypatch.setattr(developer_settings, "status_cache_seconds", 0.0)
    from anum_api.developer_projects import project_store

    project_store.clear()
    # The repository as a project that uses the API's token (docs/developer-mode.md#projects).
    owner, repo = REPO.split("/")
    added = client.post(
        "/api/v1/developer/projects",
        headers=headers(),
        json={"name": "ANUM", "github_owner": owner, "github_repo": repo, "token_source": "environment"},
    )
    assert added.status_code == 201, added.text
    yield fake
    project_store.clear()


def test_the_watcher_announces_a_ready_pull_request_to_its_owner(sender: FakeSender, github: Any) -> None:
    member("owner_a", "owner")
    member("owner_b", "owner")
    register("owner_a", "owner", PHONE_A)
    register("owner_b", "owner", PHONE_B)
    created = client.post("/api/v1/developer/requests", headers=headers(), json={"text": "Add a dark mode toggle to settings"})
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert discover_in_progress(TENANT) == [(request_id, WORKSPACE)]
    assert discover_in_progress("tenant_other") == []

    github.start_run(request_id, status="completed", conclusion="success")
    github.open_pull(request_id)
    github.set_ci("a" * 40, "success")
    assert asyncio.run(refresh_in_progress()) == 1
    assert developer_store.get(request_id, ctx()).status == DevRequestStatus.WAITING_APPROVAL
    (event,) = [item for item in store.events if item.type == "developer_request.ready_for_review"]
    assert event.payload == {"status": "waiting_approval", "previous_status": "queued", "pr_number": 7}

    assert deliver().sent == 1
    (message,) = sender.messages
    assert message.token == PHONE_A and message.title == "Change ready for review"
    assert message.data["developer_request_id"] == request_id
    assert "dark mode" not in json.dumps(message.payload())
    # Nothing changed since: the next pass announces nothing again.
    assert asyncio.run(refresh_in_progress()) == 0 and push_dispatcher.pending == 0


def test_the_watcher_runs_only_with_push_and_developer_mode(sender: FakeSender, github: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    assert build_developer_watcher() is not None
    monkeypatch.setattr(developer_settings, "watch_seconds", 0.0)
    assert build_developer_watcher() is None
    monkeypatch.setattr(developer_settings, "watch_seconds", 60.0)
    monkeypatch.setattr(settings, "fcm_service_account_json", None)
    assert build_developer_watcher() is None


def test_a_refresh_on_view_announces_ci_failures_too(sender: FakeSender, github: Any) -> None:
    created = client.post("/api/v1/developer/requests", headers=headers(), json={"text": "Add a dark mode toggle to settings"})
    request_id = created.json()["id"]
    github.start_run(request_id, status="completed", conclusion="success")
    github.open_pull(request_id)
    github.set_ci("a" * 40, "failure")
    view = client.get(f"/api/v1/developer/requests/{request_id}", headers=headers()).json()
    assert view["status"] == "ci_failed"
    assert [item.type for item in store.events if item.type.startswith("developer_request.")] == ["developer_request.failed"]
    assert push_dispatcher.pending == 1


def test_queued_requests_older_than_the_grace_period_are_still_found(github: Any) -> None:
    created = client.post("/api/v1/developer/requests", headers=headers(), json={"text": "Add a dark mode toggle to settings"})
    request = developer_store.get(created.json()["id"], ctx())
    request.created_at = utc_now() - timedelta(hours=2)
    developer_store.save(request)
    assert discover_in_progress(TENANT, WORKSPACE) == [(request.id, WORKSPACE)]


def test_tyre_alerts_reach_every_member_who_wants_them(sender: FakeSender) -> None:
    member("owner_a", "owner")
    member("member_m", "member")
    register("owner_a", "owner", PHONE_A)
    register("member_m", "member", PHONE_M)
    announce(CanonicalEventName.TYRE_ALERTS_RAISED, "tyre_alerts", {"critical": 1, "warning": 2})
    assert deliver().sent == 2 and sender.tokens() == sorted([PHONE_A, PHONE_M])
    message = sender.messages[0]
    assert (message.title, message.data["anum_kind"]) == ("Tyre alerts", "tyre_alerts")
    assert message.collapse_id == f"tyre_alerts:{WORKSPACE}" and "critical" not in message.body

    sender.messages.clear()
    _notifications.save(ctx("member_m", "member"), NotificationPreferences(tyre_alerts=False))
    announce(CanonicalEventName.TYRE_ALERTS_RAISED, "tyre_alerts", {"critical": 1, "warning": 0})
    assert deliver().sent == 1 and sender.tokens() == [PHONE_A]


def test_the_daily_trading_report_pushes_its_own_text_in_the_phones_language(sender: FakeSender) -> None:
    # docs/finance-trading-intel.md#daily-report: not "Team work", ids only, Arabic on an Arabic phone.
    member("owner_a", "owner")
    member("viewer_v", "viewer")
    register("owner_a", "owner", PHONE_A)
    arabic = client.post("/api/v1/me/devices", headers=headers("owner_a", "owner"),
                         json={"platform": "android", "token": PHONE_B, "label": "Phone", "language": "ar"})
    assert arabic.status_code == 201 and arabic.json()["language"] == "ar"
    bad = client.post("/api/v1/me/devices", headers=headers("owner_a", "owner"),
                      json={"platform": "android", "token": PHONE_M, "language": "fr"})
    assert bad.status_code == 422
    announce(CanonicalEventName.NOTIFICATION_CREATED, "ntf_report",
             {"recipient": "owner_a", "kind": "finance_daily_report", "work_item_id": None})
    report = deliver()
    assert (report.recipients, report.sent) == (1, 2)
    by_token = {m.token: m for m in sender.messages}
    assert (by_token[PHONE_A].title, by_token[PHONE_A].body) == push.TEXT[push.PushKind.FINANCE_REPORT]
    assert (by_token[PHONE_B].title, by_token[PHONE_B].body) == push.TEXT_AR[push.PushKind.FINANCE_REPORT]
    assert by_token[PHONE_A].data["anum_kind"] == "finance_report" and "Team work" not in by_token[PHONE_A].title
    # Only someone who may read finance gets it: a viewer named as recipient gets nothing.
    sender.messages.clear()
    register("viewer_v", "viewer", PHONE_M)
    announce(CanonicalEventName.NOTIFICATION_CREATED, "ntf_report_2",
             {"recipient": "viewer_v", "kind": "finance_daily_report", "work_item_id": None})
    assert deliver().sent == 0 and sender.messages == []
