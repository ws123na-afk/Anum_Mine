"""Async helpers that call a provider, a model or a tool keep no store and no transaction open
on the event loop (docs/realtime.md#event-loop; pre-sale review P7, docs/security.md).

``test_routes_off_event_loop.py`` and ``test_security_inventory.py`` check route bodies. They do
not follow an ``async`` helper a route awaits, and a route that takes ``repository_context`` keeps
that transaction (and its pooled connection) open across every await. This file covers both:

1. **Static, transitive**: each helper in ``HELPERS`` (the email provider helpers, calendar sync,
   delivery, the governed tool execution behind WhatsApp sends, the WhatsApp, email and mail AI
   tools) runs no store opener on the event loop, neither in its own body nor in a plain function
   it calls there (followed a few levels deep). A store step must run in ``asyncio.to_thread``
   (or ``dependencies.run_in_unit``) in its own short unit of work.
2. **Static, routes**: no new ``async`` route holds the request's unit of work across an await
   that is not a worker thread. The routes that still do are frozen in
   ``request_unit_across_await_baseline.txt``, which may only shrink.
3. **At run time** on the in-memory backend: the main paths through those helpers (an email
   connect, sync, body, model triage and drafts and a mediated archive; a calendar connect, push,
   sync and delete; a WhatsApp template sent at once through ``finish_execution_in_units``)
   open no store on the event loop.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from loop_guard import store_opens_on_loop
from pydantic import SecretStr

from anum_api import agent_tools, calendar_sync, delivery, email_api, email_inbox, main, runtime, whatsapp
from anum_api.mailbox import ai as mail_ai
from anum_api.mailbox import ai_api as mail_ai_api
from anum_api.mailbox import automation_tools as mail_automation_tools
from anum_api.mailbox import service as mail_service
from anum_api.mailbox import sync as mail_sync

BASELINE = Path(__file__).with_name("request_unit_across_await_baseline.txt")
_STORE_OPENERS = ("open_", "_unit_of_work", "units(", "SessionLocal", "session(", "_store(")
_WORKER = ("to_thread", "run_in_unit", "run_in_executor")

HELPERS: list[Callable[..., Any]] = [
    # Email provider helpers and the email tools (docs/email.md).
    email_inbox.access_token, email_inbox.sync_account, email_inbox.fetch_body, email_inbox.fetch_attachment,
    email_inbox.save_provider_draft, email_inbox.analyze_message, email_inbox.triage_with_model, email_inbox.model_reply,
    email_inbox.create_draft, email_inbox.create_new_draft, email_inbox.search_email, email_inbox.read_email,
    email_inbox.mark_email_read, email_inbox.archive_email, email_inbox.unarchive_email, email_inbox.draft_reply,
    email_inbox.send_email, email_api._mediated, email_api._message_action,
    # Calendar sync (docs/calendar.md#sync).
    calendar_sync.connection_token, calendar_sync.sync_connection, calendar_sync.attach_and_push,
    calendar_sync.remove_at_provider, calendar_sync.send_invitations,
    # Delivery (invoice and report emails) and the governed tool execution behind WhatsApp sends.
    delivery.request_delivery, runtime.AgentRuntime.finish_execution, runtime.AgentRuntime.recover_interrupted_execution,
    runtime.AgentRuntime._execute_tool, runtime.finish_execution_in_units, agent_tools.ToolRegistry.execute,
    # WhatsApp tools and model triage (docs/whatsapp.md).
    whatsapp.send_tool, whatsapp.template_tool, whatsapp.draft_tool, whatsapp.list_tool, whatsapp.read_tool,
    whatsapp.triage_conversations,
    # The mail AI tool registry and what it reaches (docs/email.md#ai).
    mail_ai._ask_model, mail_ai.target_of, mail_ai.summarize_mail, mail_ai.classify_mail, mail_ai.classify_mail_page,
    mail_ai.draft_mail_reply, mail_ai.archive_mail_bulk, mail_ai.export_mail, mail_ai.request_mail_action,
    mail_ai_api._mediated, mail_ai_api.start_forward, mail_ai_api.ask_owner_action,
    mail_service.on_server, mail_service.send_mail, mail_service.deliver, mail_service.delete_mail,
    mail_automation_tools.change_mail_settings, mail_automation_tools.activate_mail_forward_rule,
    mail_sync.run_rules, mail_sync.handle_mailbox,
]


# -- 1. helpers, followed transitively -----------------------------------------------------------


def _tree(fn: Callable[..., Any]) -> ast.AST | None:
    try:
        return ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    except (OSError, TypeError, SyntaxError):
        return None


def _loop_nodes(body: list[ast.stmt]) -> Iterator[ast.AST]:
    """Nodes that run where the function runs: nested plain functions and lambdas run where
    they are called (a worker thread when handed to ``asyncio.to_thread``)."""
    stack: list[ast.AST] = [node for node in body if not isinstance(node, ast.FunctionDef)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for child in ast.iter_child_nodes(node) if not isinstance(child, (ast.FunctionDef, ast.Lambda)))


def _opens(nodes: list[ast.AST]) -> bool:
    return any(
        isinstance(node, ast.With) and any(marker in ast.unparse(item.context_expr) for item in node.items for marker in _STORE_OPENERS)
        for node in nodes
    )


def _resolve(func: ast.AST, names: dict[str, Any], owner: type | None) -> Any:
    if isinstance(func, ast.Name):
        return names.get(func.id)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id == "self" and owner is not None:
            return getattr(owner, func.attr, None)
        base = names.get(func.value.id)
        if inspect.ismodule(base):
            return getattr(base, func.attr, None)
    return None


def _sync_opens(fn: Any, seen: set[int], depth: int = 0) -> bool:
    """A plain function that opens a store itself or through the plain functions it calls."""
    fn = inspect.unwrap(fn)
    if not inspect.isfunction(fn) or inspect.iscoroutinefunction(fn) or id(fn) in seen or depth > 4:
        return False
    if not (fn.__module__ or "").startswith("anum_api"):
        return False
    seen.add(id(fn))
    tree = _tree(fn)
    if tree is None:
        return False
    nodes = list(ast.walk(tree))
    if _opens(nodes):
        return True
    names = getattr(fn, "__globals__", {})
    return any(isinstance(node, ast.Call) and _sync_opens(_resolve(node.func, names, None), seen, depth + 1) for node in nodes)


def _on_loop(fn: Callable[..., Any]) -> list[str]:
    """Store openers an async function reaches on the event loop: a ``with`` in its own body or
    a nested ``async`` function, or a call to a plain function that opens one. A function handed
    to ``asyncio.to_thread`` (or ``run_in_unit``) is an argument, not a call, so it does not count."""
    fn = inspect.unwrap(fn)
    tree = _tree(fn)
    assert isinstance(tree, ast.AsyncFunctionDef), f"{fn.__qualname__} is not an async function"
    nodes = list(_loop_nodes(tree.body))
    found = ["with " + ast.unparse(node.items[0].context_expr) for node in nodes if _opens([node])]
    names = getattr(fn, "__globals__", {})
    owner = None
    qualname = fn.__qualname__.split(".")
    if len(qualname) == 2:
        owner = getattr(inspect.getmodule(fn), qualname[0], None)
    nested = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and _opens(list(_loop_nodes(node.body)))}
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in nested:
            found.append(node.func.id + "()")
        elif _sync_opens(_resolve(node.func, names, owner), set()):
            found.append(ast.unparse(node.func) + "()")
    return found


def test_async_helpers_open_no_store_on_the_event_loop() -> None:
    offenders = {f"{fn.__module__}.{fn.__qualname__}": _on_loop(fn) for fn in HELPERS}
    offenders = {name: found for name, found in offenders.items() if found}
    assert not offenders, (
        "These async helpers open a store (or call a plain function that does) on the event loop; run each "
        f"store step in asyncio.to_thread in its own short unit of work (docs/realtime.md#event-loop): {offenders}"
    )


def test_the_helper_check_sees_a_store_opened_through_a_plain_function() -> None:
    """The detector itself: a direct ``with``, a plain helper called on the loop and a nested
    function called on the loop count; the same handed to a worker thread does not."""
    scope: dict[str, Any] = {}
    exec(  # nosec B102 - a fixed snippet for the detector's own test
        textwrap.dedent(
            '''
            import asyncio
            from contextlib import nullcontext as open_thing_store

            def plain():
                with open_thing_store():
                    return 1

            async def direct():
                with open_thing_store():
                    await asyncio.sleep(0)

            async def via_plain():
                plain()

            async def in_thread():
                await asyncio.to_thread(plain)

            async def nested_on_loop():
                def step():
                    with open_thing_store():
                        pass
                step()

            async def nested_in_thread():
                def step():
                    with open_thing_store():
                        pass
                await asyncio.to_thread(step)
            '''
        ),
        scope,
    )
    source = {
        "direct": "async def direct():\n    with open_thing_store():\n        await asyncio.sleep(0)\n",
        "via_plain": "async def via_plain():\n    plain()\n",
        "in_thread": "async def in_thread():\n    await asyncio.to_thread(plain)\n",
        "nested_on_loop": "async def nested_on_loop():\n    def step():\n        with open_thing_store():\n            pass\n    step()\n",
        "nested_in_thread": (
            "async def nested_in_thread():\n    def step():\n        with open_thing_store():\n            pass\n"
            "    await asyncio.to_thread(step)\n"
        ),
    }
    plain_source = "def plain():\n    with open_thing_store():\n        return 1\n"
    original = inspect.getsource

    def fake_source(obj: Any) -> str:
        name = getattr(obj, "__name__", "")
        if name == "plain":
            return plain_source
        return source[name] if name in source else original(obj)

    scope["plain"].__module__ = scope["direct"].__module__ = "anum_api.detector_case"
    for name in source:
        scope[name].__module__ = "anum_api.detector_case"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(inspect, "getsource", fake_source)
        found = {name: bool(_on_loop(scope[name])) for name in source}
    assert found == {"direct": True, "via_plain": True, "in_thread": False, "nested_on_loop": True, "nested_in_thread": False}


# -- 2. routes that hold the request's unit of work across an await -------------------------------

_REQUEST_UNITS = {"repository_context", "_repository_unit", "memory_repository_context", "identity_repository_context",
                  "provisioning_repository_context"}


def _dependency_names(dependant: Any, names: set[str]) -> set[str]:
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            names.add(getattr(dependency.call, "__qualname__", ""))
        _dependency_names(dependency, names)
    return names


def _holds_unit_across_an_await(route: Any) -> bool:
    fn = inspect.unwrap(route.endpoint)
    if not inspect.iscoroutinefunction(fn) or not (_dependency_names(route.dependant, set()) & _REQUEST_UNITS):
        return False
    tree = _tree(fn)
    if tree is None:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Await):
            called = node.value.func if isinstance(node.value, ast.Call) else None
            if called is None or ast.unparse(called).split(".")[-1] not in _WORKER:
                return True
    return False


def test_no_new_async_route_holds_the_request_unit_across_an_await() -> None:
    from test_security_inventory import _key, _route_contexts

    found = {_key(route) for route in _route_contexts() if _holds_unit_across_an_await(route)}
    baseline = {line.strip() for line in BASELINE.read_text().splitlines() if line.strip() and not line.startswith("#")}
    added = sorted(found - baseline)
    fixed = sorted(baseline - found)
    assert not added, (
        "New async routes keep the request's unit of work (repository_context) open across a provider, model "
        "or tool call. Take `units: UnitsOfWork = Depends(units_of_work)` and run each store step with "
        f"run_in_unit (docs/realtime.md#event-loop): {added}"
    )
    assert not fixed, f"These routes no longer hold it; remove them from {BASELINE.name}: {fixed}"


def test_the_routes_this_change_fixed_stay_fixed() -> None:
    """The email, calendar, mail AI, WhatsApp send and draft, invoice and report delivery routes."""
    baseline = BASELINE.read_text()
    for prefix in ("/api/v1/email/", "/api/v1/calendar/", "/api/v1/mail/ai/", "/api/v1/whatsapp/conversations/",
                   "/api/v1/tyre/reports/", "/invoices/{invoice_id}/email"):
        assert prefix not in baseline, prefix


# -- 3. at run time -------------------------------------------------------------------------------


@pytest.fixture
def on_loop(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    with store_opens_on_loop(monkeypatch) as calls:
        yield calls


@pytest.fixture
def email_case(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import test_email as case

    mail = case.FakeMail()
    monkeypatch.setattr(email_inbox, "email_transport", httpx.MockTransport(mail))
    settings = email_inbox.email_settings
    for name, value in {
        "google_client_id": "example-google-client", "google_client_secret": SecretStr("example"),
        "public_api_url": "https://api.shop.example", "web_url": "https://app.shop.example",
    }.items():
        monkeypatch.setattr(settings, name, value)
    email_inbox.set_model_gateway(case.FakeGateway())
    email_inbox.email_backend.clear()
    main.integration_registry.memory_configurations.clear()
    yield case
    email_inbox.email_backend.clear()
    email_inbox.set_model_gateway(None)


def test_email_provider_helpers_run_their_store_steps_off_the_loop(email_case: Any, on_loop: list[str]) -> None:
    client, owner, member = email_case.client, email_case.OWNER, email_case.MEMBER
    account = email_case.connect()  # OAuth complete, then the first sync (sync_account, access_token)
    assert client.post(f"/api/v1/email/accounts/{account['id']}/sync", headers=owner).status_code == 200
    first = next(m for m in email_case.messages()["items"] if m["subject"] == "Question about tyres")
    detail = client.get(f"/api/v1/email/messages/{first['id']}", headers=owner)
    assert detail.status_code == 200 and detail.json()["body"], detail.text  # fetch_body
    assert client.post(f"/api/v1/email/messages/{first['id']}/analyze", headers=member).status_code == 200  # the model
    drafted = client.post(f"/api/v1/email/messages/{first['id']}/drafts", headers=member, json={})
    assert drafted.status_code == 201 and drafted.json()["draft"]["source"] == "model", drafted.text  # save_provider_draft
    edited = client.patch(f"/api/v1/email/drafts/{drafted.json()['draft']['id']}", headers=member, json={"body": "Hello Sara."})
    assert edited.status_code == 200, edited.text
    archived = client.post(f"/api/v1/email/messages/{first['id']}/archive", headers=owner)  # a mediated tool
    assert archived.status_code == 200 and archived.json()["status"] == "done", archived.text
    assert on_loop == [], f"opened on the event loop: {on_loop}"


@pytest.fixture
def calendar_case(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import test_calendar_sync as case

    fake = case.FakeCalendars()
    monkeypatch.setattr(calendar_sync, "calendar_transport", httpx.MockTransport(fake))
    settings = calendar_sync.calendar_settings
    for name, value in {
        "google_client_id": "example-google-client", "google_client_secret": SecretStr("example"),
        "public_api_url": "https://api.shop.example", "web_url": "https://app.shop.example",
    }.items():
        monkeypatch.setattr(settings, name, value)
    case.calendar_backend.clear()
    case.sync_backend.clear()
    yield case
    case.calendar_backend.clear()
    case.sync_backend.clear()


def test_calendar_sync_and_push_run_their_store_steps_off_the_loop(calendar_case: Any, on_loop: list[str]) -> None:
    connection = calendar_case.connect()  # OAuth complete, then sync_connection
    event = calendar_case.new_event("Supplier visit", sync=True)  # attach_and_push
    assert event["sync_state"] == "synced", event
    calendar_case.sync(connection["id"])
    deleted = calendar_case.client.delete(f"/api/v1/calendar/events/{event['id']}", headers=calendar_case.OWNER)  # remove_at_provider
    assert deleted.status_code == 204, deleted.text
    assert on_loop == [], f"opened on the event loop: {on_loop}"


@pytest.fixture
def whatsapp_case(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import test_whatsapp as case
    from anum_api.autonomy import autonomy_memory_store
    from anum_api.store import store

    meta = case.FakeMeta()
    monkeypatch.setattr(whatsapp, "graph_transport", httpx.MockTransport(meta))
    monkeypatch.setattr(whatsapp.whatsapp_settings, "graph_api_version", "v23.0")
    monkeypatch.setattr(whatsapp.whatsapp_settings, "graph_api_url", "https://graph.facebook.com")
    whatsapp.reset_limits()
    case.whatsapp_store.clear()
    autonomy_memory_store.clear()
    store.approval_policies.clear()
    case.meta_sent = meta.sent
    yield case
    case.whatsapp_store.clear()
    autonomy_memory_store.clear()
    whatsapp.reset_limits()


def test_a_whatsapp_send_runs_the_tool_with_no_unit_of_work_on_the_loop(whatsapp_case: Any, on_loop: list[str]) -> None:
    case = whatsapp_case
    case.connect()
    case.deliver(case.inbound(age=timedelta(hours=40)))
    conversation = case.only_conversation()
    case.client.put(f"{case.BASE}/account", headers=case.OWNER,
                    json={"phone_number_id": case.PHONE_ID, "business_account_id": case.WABA_ID, "auto_send_templates": ["order_update"]})
    template = {"name": "order_update", "language": "en_US", "parameters": ["Ahmed", "A-1001"]}
    sent = case.client.post(f"{case.BASE}/conversations/{conversation['id']}/send", headers=case.MEMBER, json={"template": template})
    assert sent.status_code == 200 and sent.json()["outcome"] == "sent", sent.text  # finish_execution_in_units
    assert len(case.meta_sent) == 1
    drafted = case.client.post(f"{case.BASE}/conversations/{conversation['id']}/draft", headers=case.MEMBER, json={})
    assert drafted.status_code == 200, drafted.text
    assert on_loop == [], f"opened on the event loop: {on_loop}"
