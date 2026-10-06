"""Authenticated task journey against real Keycloak, PostgreSQL and NATS JetStream.

This is the Stage 2 exit check (docs/production-plan.md): a user signs in through
Keycloak, creates a task, sees live status over SSE, approves the risky sample action,
and the run is persisted. It drives a running API the way the web client would:

1. Authorization code + PKCE (S256) sign-in as the realm's dev user for client
   `anum-web`, with the Keycloak login form posted directly (no browser).
2. Onboarding bootstrap (tenant, workspace, owner membership) when it is not done yet.
3. An SSE subscription to /api/v1/events/stream opened before the task exists.
4. Create a task whose prompt triggers the high-risk `external.action` tool, run it,
   see it pause for approval, approve it, and see it complete.
5. Check the task's events arrived on the SSE stream and on the NATS JetStream stream,
   and that the task, run, steps, approval and events are rows in PostgreSQL that the
   non-superuser application role can only see inside the tenant's RLS scope.

Run it with services/api/scripts/run_journey.sh, which starts the services and the API.
Every failure exits non-zero with a `JOURNEY FAILED:` line saying what was expected.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import html
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg

DEFAULT_REDIRECT_URI = "http://localhost:5173/journey/callback"
RISKY_PROMPT = "Send the quarterly status summary to the finance distribution list."
EXPECTED_STREAM_EVENTS = (
    "task.created",
    "approval.requested",
    "approval.approved",
    "agent_run.completed",
)


class JourneyError(RuntimeError):
    """A journey expectation did not hold."""


def step(message: str) -> None:
    print(f"[journey] {message}", flush=True)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise JourneyError(message)


def expect_status(response: httpx.Response, expected: int, action: str) -> Any:
    if response.status_code != expected:
        raise JourneyError(
            f"{action}: expected HTTP {expected}, got {response.status_code}: {response.text[:500]}"
        )
    return response.json() if response.content else None


# --------------------------------------------------------------------------- sign-in


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


def _login_form_action(page: str) -> str | None:
    for form in re.finditer(r"<form\b[^>]*>", page, flags=re.IGNORECASE):
        tag = form.group(0)
        if "kc-form-login" not in tag:
            continue
        action = re.search(r'action="([^"]+)"', tag)
        if action:
            return html.unescape(action.group(1))
    return None


def _login_error(page: str) -> str:
    match = re.search(
        r'<span[^>]*id="input-error[^"]*"[^>]*>(.*?)</span>', page, flags=re.DOTALL | re.IGNORECASE
    ) or re.search(r'class="[^"]*alert-error[^"]*".*?<span[^>]*>(.*?)</span>', page, flags=re.DOTALL)
    if match:
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", match.group(1)))).strip()
    title = re.search(r"<title>(.*?)</title>", page, flags=re.DOTALL | re.IGNORECASE)
    return f"page title {title.group(1).strip()!r}" if title else "no error message on the page"


def _jwt_claims(token: str) -> dict[str, Any]:
    """Decode the payload for reporting only; the API is what verifies the signature."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def sign_in(issuer: str, client_id: str, redirect_uri: str, username: str, password: str) -> str:
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)
    with httpx.Client(timeout=30, follow_redirects=False) as browser:
        authorize = browser.get(
            f"{issuer}/protocol/openid-connect/auth",
            params={
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": "openid",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        expect(
            authorize.status_code == 200,
            f"Open the Keycloak login page: expected HTTP 200, got {authorize.status_code}: {authorize.text[:300]}",
        )
        action = _login_form_action(authorize.text)
        expect(action is not None, "Keycloak login page has no kc-form-login form")

        login = browser.post(
            action,
            data={"username": username, "password": password, "credentialId": ""},
        )
        if login.status_code != 302:
            raise JourneyError(
                f"Keycloak did not redirect after the login form post (HTTP {login.status_code}): "
                f"{_login_error(login.text)}"
            )
        location = login.headers["location"]
        expect(
            location.startswith(redirect_uri),
            f"Keycloak redirected to {location.split('?')[0]!r}, expected {redirect_uri!r}",
        )
        query = parse_qs(urlparse(location).query)
        expect(query.get("state") == [state], "Authorization response state does not match the request")
        if "error" in query:
            raise JourneyError(f"Authorization failed: {query['error']} {query.get('error_description')}")
        code = query.get("code", [None])[0]
        expect(code is not None, "Authorization response carries no code")

        token = browser.post(
            f"{issuer}/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        body = expect_status(token, 200, "Exchange the authorization code (PKCE) for tokens")
        expect(body.get("token_type", "").lower() == "bearer", f"Unexpected token type {body.get('token_type')!r}")
        return body["access_token"]


def check_pkce_is_required(issuer: str, client_id: str, redirect_uri: str) -> None:
    """The public web client must refuse an authorization request without a PKCE challenge."""
    with httpx.Client(timeout=30, follow_redirects=False) as browser:
        response = browser.get(
            f"{issuer}/protocol/openid-connect/auth",
            params={
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": "openid",
                "state": "no-pkce",
            },
        )
    location = response.headers.get("location", "")
    refused = response.status_code in (302, 303) and "error=" in location
    refused = refused or response.status_code >= 400
    expect(refused, f"Keycloak accepted an authorization request without PKCE (HTTP {response.status_code})")


# --------------------------------------------------------------------------- SSE


@dataclass
class StreamedEvent:
    id: str
    type: str
    data: dict[str, Any]
    received_at: float


@dataclass
class EventStream:
    events: list[StreamedEvent] = field(default_factory=list)
    connected: asyncio.Event = field(default_factory=asyncio.Event)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    error: BaseException | None = None

    async def run(self, client: httpx.AsyncClient) -> None:
        try:
            async with client.stream(
                "GET",
                "/api/v1/events/stream",
                headers={"accept": "text/event-stream"},
                timeout=httpx.Timeout(10.0, read=None),
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise JourneyError(
                        f"Open the SSE stream: expected HTTP 200, got {response.status_code}: "
                        f"{response.text[:300]}"
                    )
                content_type = response.headers.get("content-type", "")
                if not content_type.startswith("text/event-stream"):
                    raise JourneyError(f"SSE stream has content-type {content_type!r}")
                self.connected.set()
                fields: dict[str, str] = {}
                async for line in response.aiter_lines():
                    if line == "":
                        if "data" in fields:
                            await self._add(fields)
                        fields = {}
                    elif line.startswith(":"):
                        continue
                    else:
                        name, _, value = line.partition(":")
                        value = value[1:] if value.startswith(" ") else value
                        fields[name] = fields[name] + "\n" + value if name in fields else value
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # surfaced by wait_for_types
            self.error = exc
            self.connected.set()
            async with self.changed:
                self.changed.notify_all()

    async def _add(self, fields: dict[str, str]) -> None:
        data = json.loads(fields["data"])
        event = StreamedEvent(
            id=fields.get("id", data.get("id", "")),
            type=fields.get("event", data.get("type", "")),
            data=data,
            received_at=time.monotonic(),
        )
        async with self.changed:
            self.events.append(event)
            self.changed.notify_all()

    def for_task(self, task_id: str) -> list[StreamedEvent]:
        return [
            event
            for event in self.events
            if event.data.get("subject") == task_id
            or (event.data.get("payload") or {}).get("task_id") == task_id
        ]

    async def wait_for_types(self, task_id: str, types: tuple[str, ...], timeout: float) -> list[StreamedEvent]:
        def satisfied() -> bool:
            seen = {event.type for event in self.for_task(task_id)}
            return self.error is not None or all(kind in seen for kind in types)

        try:
            async with self.changed:
                await asyncio.wait_for(self.changed.wait_for(satisfied), timeout)
        except TimeoutError:
            seen = [event.type for event in self.for_task(task_id)]
            raise JourneyError(
                f"SSE stream did not deliver {list(types)} for {task_id} within {timeout:.0f}s; "
                f"received {seen}"
            ) from None
        if self.error is not None:
            raise JourneyError(f"SSE stream failed: {self.error}") from self.error
        return self.for_task(task_id)


# --------------------------------------------------------------------------- NATS


async def nats_events_for_task(nats_url: str, stream: str, tenant_id: str, workspace_id: str, task_id: str) -> list[dict[str, Any]]:
    import nats
    from nats.js.api import DeliverPolicy

    from anum_api.event_bus import SUBJECT_ROOT, scope_tokens

    tenant_token, workspace_token = scope_tokens(tenant_id, workspace_id)
    subject = f"{SUBJECT_ROOT}.{tenant_token}.{workspace_token}.>"
    nc = await nats.connect(nats_url, connect_timeout=5, max_reconnect_attempts=0)
    try:
        js = nc.jetstream()
        info = await js.stream_info(stream)
        received: list[dict[str, Any]] = []
        subscription = await js.subscribe(
            subject, stream=stream, ordered_consumer=True, deliver_policy=DeliverPolicy.ALL
        )
        try:
            while True:
                try:
                    message = await subscription.next_msg(timeout=2)
                except nats.errors.TimeoutError:
                    break
                event = json.loads(message.data)
                if event.get("subject") == task_id or (event.get("payload") or {}).get("task_id") == task_id:
                    received.append({"nats_subject": message.subject, **event})
        finally:
            await subscription.unsubscribe()
        step(f"NATS stream {stream}: {info.state.messages} messages in total")
        return received
    finally:
        await nc.close()


# --------------------------------------------------------------------------- PostgreSQL


def verify_persistence(database_url: str, tenant_id: str, workspace_id: str, task_id: str, run_id: str, approval_id: str) -> dict[str, Any]:
    url = database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as connection:
        role = connection.execute(
            "select current_user, rolsuper, rolbypassrls from pg_roles where rolname = current_user"
        ).fetchone()
        expect(
            role is not None and not role[1] and not role[2],
            f"Database role {role[0] if role else '?'} is a superuser or bypasses RLS; "
            "the journey must run the API as the non-superuser application role",
        )

        with connection.transaction():
            unscoped = connection.execute("select count(*) from tasks where id = %s", (task_id,)).fetchone()[0]
        expect(unscoped == 0, f"RLS leak: the task is visible without a tenant context ({unscoped} rows)")

        with connection.transaction():
            connection.execute("select set_config('anum.tenant_id', %s, true)", (tenant_id,))
            connection.execute("select set_config('anum.workspace_id', %s, true)", (workspace_id,))
            task = connection.execute(
                "select status, tenant_id, workspace_id from tasks where id = %s", (task_id,)
            ).fetchone()
            run = connection.execute(
                "select status, result, checkpoint->>'phase' from agent_runs where id = %s and task_id = %s",
                (run_id, task_id),
            ).fetchone()
            steps = [
                row[0]
                for row in connection.execute(
                    "select type from agent_run_steps where run_id = %s order by created_at, id", (run_id,)
                )
            ]
            approval = connection.execute(
                "select status, action, risk_level, decided_at is not null from approvals where id = %s and task_id = %s",
                (approval_id, task_id),
            ).fetchone()
            events = [
                row[0]
                for row in connection.execute(
                    "select type from domain_events where subject = %s or payload->>'task_id' = %s "
                    "order by created_at, id",
                    (task_id, task_id),
                )
            ]

    expect(task is not None, f"Task {task_id} is not in PostgreSQL")
    expect(task[0] == "completed", f"Persisted task status is {task[0]!r}, expected 'completed'")
    expect(task[1:] == (tenant_id, workspace_id), f"Persisted task scope is {task[1:]}, expected {(tenant_id, workspace_id)}")
    expect(run is not None, f"Agent run {run_id} is not in PostgreSQL")
    expect(run[0] == "completed" and run[2] == "completed", f"Persisted run status/phase is {run[0]!r}/{run[2]!r}")
    expect(bool(run[1]), "Persisted run has no result")
    for kind in ("model_call", "tool_proposal", "approval_wait", "tool_result", "final"):
        expect(kind in steps, f"Persisted run steps {steps} lack {kind!r}")
    expect(approval is not None, f"Approval {approval_id} is not in PostgreSQL")
    expect(
        approval == ("approved", "external.action", "high", True),
        f"Persisted approval is {approval}, expected approved high-risk external.action with a decision time",
    )
    for kind in EXPECTED_STREAM_EVENTS:
        expect(kind in events, f"Persisted events {events} lack {kind!r}")
    return {"db_role": role[0], "task": task[0], "run": run[0], "steps": steps, "approval": approval[0], "events": events}


# --------------------------------------------------------------------------- journey


async def journey(args: argparse.Namespace) -> None:
    issuer = args.issuer.rstrip("/")
    step(f"API {args.api_url}, issuer {issuer}, client {args.client_id}")

    async with httpx.AsyncClient(base_url=args.api_url, timeout=30) as anonymous:
        health = expect_status(await anonymous.get("/health"), 200, "API health")
        step(f"API health: {health}")
        unauthenticated = await anonymous.get("/api/v1/tasks")
        expect(unauthenticated.status_code == 401, f"GET /api/v1/tasks without a token returned {unauthenticated.status_code}, expected 401")
        forged = await anonymous.get(
            "/api/v1/tasks",
            headers={"x-tenant-id": "tenant_local", "x-workspace-id": "workspace_foundation", "x-user-id": "dev", "x-user-roles": "owner"},
        )
        expect(forged.status_code == 401, f"Header-asserted identity returned {forged.status_code} in oidc mode, expected 401")
        step("API rejects requests without a bearer token and ignores header-asserted identity (401)")

    check_pkce_is_required(issuer, args.client_id, args.redirect_uri)
    step(f"Keycloak refuses an {args.client_id} authorization request without PKCE")
    access_token = await asyncio.to_thread(
        sign_in, issuer, args.client_id, args.redirect_uri, args.username, args.password
    )
    claims = _jwt_claims(access_token)
    tenant_id, workspace_id = claims.get("tenant_id"), claims.get("workspace_id")
    audience = claims.get("aud") if isinstance(claims.get("aud"), list) else [claims.get("aud")]
    expect(claims.get("azp") == args.client_id, f"Token azp is {claims.get('azp')!r}, expected {args.client_id!r}")
    expect(tenant_id and workspace_id, f"Token lacks tenant_id/workspace_id claims: {sorted(claims)}")
    step(
        f"Signed in as {claims.get('preferred_username')!r} via authorization code + PKCE: "
        f"sub={claims.get('sub')} tenant={tenant_id} workspace={workspace_id} aud={audience} "
        f"roles={(claims.get('realm_access') or {}).get('roles')}"
    )

    headers = {"authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(base_url=args.api_url, headers=headers, timeout=30) as api:
        tampered = access_token[:-4] + ("AAAA" if not access_token.endswith("AAAA") else "BBBB")
        bad = await api.get("/api/v1/tasks", headers={"authorization": f"Bearer {tampered}"})
        expect(bad.status_code == 401, f"A token with a broken signature returned {bad.status_code}, expected 401")

        onboarding = expect_status(await api.get("/api/v1/onboarding"), 200, "Read onboarding status")
        if not onboarding["complete"]:
            onboarding = expect_status(
                await api.put(
                    "/api/v1/onboarding",
                    json={"organization_name": "Journey Org", "workspace_name": "Journey Workspace"},
                ),
                200,
                "Complete onboarding",
            )
            step("Onboarding bootstrapped the tenant, workspace and owner membership")
        else:
            step("Onboarding already complete (reusing the persisted membership)")
        membership = onboarding["membership"]
        expect(onboarding["complete"], f"Onboarding is not complete: {onboarding}")
        expect(membership["role"] == "owner" and membership["user_id"] == claims["sub"], f"Unexpected membership {membership}")

        stream = EventStream()
        reader = asyncio.create_task(stream.run(api))
        try:
            await asyncio.wait_for(stream.connected.wait(), 15)
            if stream.error is not None:
                raise JourneyError(f"SSE stream failed: {stream.error}")
            await asyncio.sleep(1.0)  # let the server register the realtime listener
            step("Subscribed to /api/v1/events/stream (SSE) before creating the task")

            task = expect_status(
                await api.post("/api/v1/tasks", json={"title": "Journey: risky action", "prompt": RISKY_PROMPT}),
                201,
                "Create the task",
            )
            task_id = task["id"]
            expect(task["status"] == "created", f"New task status is {task['status']!r}")
            expect((task["tenant_id"], task["workspace_id"]) == (tenant_id, workspace_id), f"Task scope {task}")
            created_at = time.monotonic()
            await stream.wait_for_types(task_id, ("task.created",), args.event_timeout)
            step(f"Created {task_id}; SSE delivered task.created in {time.monotonic() - created_at:.2f}s")

            ran = expect_status(await api.post(f"/api/v1/tasks/{task_id}/run"), 200, "Run the task")
            approval = ran.get("approval")
            expect(approval is not None, f"Running the risky task did not request an approval: {ran}")
            expect(ran["task"]["status"] == "waiting_approval", f"Task status after run is {ran['task']['status']!r}")
            expect(approval["status"] == "pending" and approval["risk_level"] == "high", f"Approval {approval}")
            expect(approval["action"] == "external.action", f"Approval action is {approval['action']!r}")
            run_id = ran["run"]["id"]
            ran_at = time.monotonic()
            await stream.wait_for_types(task_id, ("approval.requested",), args.event_timeout)
            step(
                f"Run {run_id} paused for approval {approval['id']} ({approval['action']}, "
                f"{approval['risk_level']} risk); SSE delivered approval.requested in {time.monotonic() - ran_at:.2f}s"
            )
            status_now = expect_status(await api.get(f"/api/v1/tasks/{task_id}"), 200, "Read the task")
            expect(status_now["status"] == "waiting_approval", f"Task status is {status_now['status']!r}")
            pending = expect_status(await api.get("/api/v1/approvals"), 200, "List approvals")
            expect(any(item["id"] == approval["id"] and item["status"] == "pending" for item in pending), "Approval is not listed as pending")

            decided = expect_status(
                await api.post(f"/api/v1/approvals/{approval['id']}/approve"), 200, "Approve the risky action"
            )
            expect(decided["approval"]["status"] == "approved", f"Approval after approve is {decided['approval']}")
            expect(decided["task"]["status"] == "completed", f"Task after approval is {decided['task']['status']!r}")
            expect(decided["run"] and decided["run"]["status"] == "completed", f"Run after approval is {decided['run']}")
            approved_at = time.monotonic()
            delivered = await stream.wait_for_types(task_id, EXPECTED_STREAM_EVENTS, args.event_timeout)
            step(f"Approved; SSE delivered approval.approved and agent_run.completed in {time.monotonic() - approved_at:.2f}s")
            again = await api.post(f"/api/v1/approvals/{approval['id']}/approve")
            expect(again.status_code == 409, f"Approving twice returned {again.status_code}, expected 409")
        finally:
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):
                pass

        stream_types = [event.type for event in delivered]
        expect(
            [kind for kind in stream_types if kind in EXPECTED_STREAM_EVENTS] == list(EXPECTED_STREAM_EVENTS),
            f"SSE events arrived as {stream_types}, expected order {list(EXPECTED_STREAM_EVENTS)}",
        )
        ids = [event.id for event in delivered]
        expect(len(ids) == len(set(ids)), f"SSE delivered duplicate event ids: {ids}")
        step(f"SSE events for the task, in order: {stream_types}")

        final_run = expect_status(await api.get(f"/api/v1/tasks/{task_id}/latest-run"), 200, "Read the latest run")
        expect(final_run["id"] == run_id and final_run["status"] == "completed", f"Latest run {final_run['id']} is {final_run['status']}")
        api_events = expect_status(await api.get("/api/v1/events"), 200, "List events")
        api_event_ids = {event["id"] for event in api_events}
        expect(set(ids) <= api_event_ids, "Some streamed events are missing from GET /api/v1/events")

    nats_events = await nats_events_for_task(args.nats_url, args.nats_stream, tenant_id, workspace_id, task_id)
    nats_types = [event["type"] for event in nats_events]
    for kind in EXPECTED_STREAM_EVENTS:
        expect(kind in nats_types, f"NATS JetStream stream {args.nats_stream} lacks {kind} for {task_id}; has {nats_types}")
    expect({event["id"] for event in nats_events} >= set(ids), "Streamed events are missing from NATS JetStream")
    step(f"NATS JetStream carries the task's events: {[event['nats_subject'] for event in nats_events]}")

    persisted = await asyncio.to_thread(
        verify_persistence, args.database_url, tenant_id, workspace_id, task_id, run_id, approval["id"]
    )
    step(
        f"PostgreSQL (as {persisted['db_role']}, RLS enforced): task={persisted['task']} run={persisted['run']} "
        f"approval={persisted['approval']} steps={persisted['steps']} events={persisted['events']}"
    )
    step("PASSED: sign-in, task, live status, approval and persisted run all verified")


def parse_args(argv: list[str]) -> argparse.Namespace:
    env = os.environ.get
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api-url", default=env("JOURNEY_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--issuer", default=env("JOURNEY_ISSUER", "http://localhost:8080/realms/anum"))
    parser.add_argument("--client-id", default=env("JOURNEY_CLIENT_ID", "anum-web"))
    parser.add_argument("--redirect-uri", default=env("JOURNEY_REDIRECT_URI", DEFAULT_REDIRECT_URI))
    parser.add_argument("--username", default=env("JOURNEY_USERNAME", "dev"))
    # The realm's DEV-ONLY user (infra/keycloak/anum-realm.json); never a real credential.
    parser.add_argument("--password", default=env("JOURNEY_PASSWORD", "anum-dev-only-password"))
    parser.add_argument("--nats-url", default=env("JOURNEY_NATS_URL", "nats://localhost:4222"))
    parser.add_argument("--nats-stream", default=env("JOURNEY_NATS_STREAM", "ANUM_EVENTS"))
    parser.add_argument(
        "--database-url",
        default=env("JOURNEY_DATABASE_URL", "postgresql://anum_app:anum_app@localhost:5432/anum"),
        help="Connect as the application role, not a superuser, so RLS is enforced.",
    )
    parser.add_argument("--event-timeout", type=float, default=float(env("JOURNEY_EVENT_TIMEOUT", "20")))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        asyncio.run(journey(args))
    except JourneyError as exc:
        print(f"JOURNEY FAILED: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
