"""The action browser in a real Chromium against a LOCAL test site (marker ``browser``; docs/action-browser.md).

Never a real site: a small shop, a two-step form, a statement download and two price pages are
served over TLS on 127.0.0.1 by this test. Chromium reaches them only through the session's
CONNECT proxy, with fake DNS answers mapped to the local server, so every connection took the
real guarded path. The tool, the browser service routes and the kernel are the real ones; only
the planner is scripted (it finds elements by their labels in the real snapshot).

Proves: sign in with a saved login, fill a form, stop at pay with an approval, approve, finish;
the reject path; the page-changed path; a multi-step form submitted after approval; a statement
downloaded into Files; two pages compared into a table; a private address refused by the proxy.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import ssl
import tempfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from anum_api.action_browser import ACT_TOOL
from anum_api.action_browser.kernel import ActionSession, SessionLimits
from anum_api.action_browser.planner import PlannedStep
from anum_api.action_browser.service_routes import ActionSessions, action_routes
from anum_api.action_browser.store import memory_action_store
from anum_api.action_browser.tool import ActionBrowserTool, ActionServiceClient
from anum_api.action_browser.vault import CredentialWrite, create_credential
from anum_api.agent_tools import ToolCall, ToolResult, running_in
from anum_api.browser_research.kernel import chromium_available
from anum_api.schemas import TenantContext

pytestmark = pytest.mark.browser

EXECUTABLE = os.environ.get("ANUM_TEST_CHROMIUM_PATH") or None
CONTEXT = TenantContext(tenant_id="tenant_real", workspace_id="workspace_real", user_id="owner_r", roles=["owner"])
PASSWORD = "plain-fake-pass"
CARD = "4242424242424242"
TOKEN = "action-browser-token-for-tests-only-example"
PUBLIC_IP = "93.184.215.14"
DEMO = Path(os.environ.get("ANUM_ACTION_BROWSER_DEMO_DIR", "")) if os.environ.get("ANUM_ACTION_BROWSER_DEMO_DIR") else None
PDF = b"%PDF-1.4\n% test statement\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


def page(title: str, body: str) -> bytes:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font:16px system-ui;margin:40px;max-width:640px}} label{{display:block;margin:12px 0 4px}} input{{font:inherit;padding:6px;width:300px}}
button{{font:inherit;margin-top:16px;padding:8px 16px}}</style></head><body><h1>{title}</h1>{body}</body></html>""".encode()


class Shop:
    def __init__(self) -> None:
        self.price = "AED 1,240"
        self.charged: list[str] = []
        self.visa: list[dict[str, str]] = []
        self.posts: list[str] = []


def handler_for(shop: Shop) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def _send(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            for key, value in {"content-type": "text/html; charset=utf-8", "content-length": str(len(body)), **(headers or {})}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _signed_in(self) -> bool:
            return "sid=ok" in (self.headers.get("cookie") or "")

        def _form(self) -> dict[str, str]:
            length = int(self.headers.get("content-length") or 0)
            return {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/login":
                return self._send(200, page("Sign in", """<form method="post" action="/login"><label for="email">Email</label>
<input id="email" name="email" type="email"><label for="pw">Password</label><input id="pw" name="password" type="password">
<button type="submit">Sign in</button></form>"""))
            if path in {"/passenger", "/pay", "/statements"} and not self._signed_in():
                return self._send(303, headers={"location": "/login"})
            if path == "/passenger":
                return self._send(200, page("Passenger details", """<form method="post" action="/passenger"><label for="n">Passenger name</label>
<input id="n" name="passenger" type="text"><button type="submit">Continue</button></form>"""))
            if path == "/pay":
                return self._send(200, page("Payment", f"""<p>Fare to Riyadh</p><p>Total {shop.price}</p><form method="post" action="/pay/charge">
<label for="cc">Card number</label><input id="cc" name="cardnumber" autocomplete="cc-number" inputmode="numeric">
<button type="submit">Pay {shop.price}</button></form>"""))
            if path == "/done":
                return self._send(200, page("Booked", "<p>Booking reference ABC123</p>"))
            if path == "/visa":
                return self._send(200, page("Visa form, step 1", """<form method="get" action="/visa/2"><label for="fn">Full name</label>
<input id="fn" name="full_name"><button type="submit">Next</button></form>"""))
            if path == "/visa/2":
                return self._send(200, page("Visa form, step 2", """<form method="post" action="/visa/submit"><label for="pp">Passport number</label>
<input id="pp" name="passport"><button type="submit">Submit application</button></form>"""))
            if path == "/visa/sent":
                return self._send(200, page("Application sent", "<p>Application number V-778</p>"))
            if path == "/statements":
                return self._send(200, page("Statements", '<p>October statement</p><a href="/files/statement-oct.pdf" download>Download October statement</a>'))
            if path == "/files/statement-oct.pdf":
                return self._send(200, PDF, {"content-type": "application/pdf", "content-disposition": "attachment; filename=statement-oct.pdf"})
            if path == "/prices/a":
                return self._send(200, page("Supplier A", "<table><tr><th>Item</th><th>Price</th></tr><tr><td>Cement 50kg</td><td>AED 21</td></tr></table>"))
            if path == "/prices/b":
                return self._send(200, page("Supplier B", "<table><tr><th>Item</th><th>Price</th></tr><tr><td>Cement 50kg</td><td>AED 19</td></tr></table>"))
            if path == "/leak":
                # A hostile page tries to reach a private address and the metadata service.
                return self._send(200, page("Leak", """<p>Nothing here</p><script>fetch('https://internal.test/x').catch(()=>{});
fetch('https://169.254.169.254/latest').catch(()=>{});</script>"""))
            return self._send(404, page("Not found", ""))

        def do_POST(self) -> None:  # noqa: N802
            form = self._form()
            if self.path == "/login":
                if form.get("email") == "owner@example.com" and form.get("password") == PASSWORD:
                    return self._send(303, headers={"location": "/passenger", "set-cookie": "sid=ok; Path=/; Secure; HttpOnly"})
                return self._send(303, headers={"location": "/login"})
            if self.path == "/passenger":
                return self._send(303, headers={"location": "/pay"})
            if self.path == "/pay/charge":
                shop.charged.append(form.get("cardnumber", ""))
                return self._send(303, headers={"location": "/done"})
            if self.path == "/visa/submit":
                shop.visa.append(form)
                return self._send(303, headers={"location": "/visa/sent"})
            return self._send(404)

    return Handler


def _certificate(directory: str) -> tuple[str, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "shop.example")])
    now = dt.datetime.now(dt.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("shop.example"), x509.DNSName("partner.example")]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = os.path.join(directory, "cert.pem"), os.path.join(directory, "key.pem")
    Path(cert_path).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    Path(key_path).write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


@pytest.fixture
def site() -> Iterator[tuple[Shop, int]]:
    shop = Shop()
    with tempfile.TemporaryDirectory() as directory:
        cert, key = _certificate(directory)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(shop))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield shop, server.server_address[1]
        finally:
            server.shutdown()


class LabelPlanner:
    """Scripted steps that name elements by label or text; refs come from the real snapshot."""

    def __init__(self, steps: list[dict[str, Any]], before_done: Any = None) -> None:
        self.steps = list(steps)
        self.seen: list[str] = []
        self.before_done = before_done

    async def next_step(self, *, goal: str, scope: dict[str, Any], history: list[str], snapshot: dict[str, Any]) -> PlannedStep:
        self.seen.append(json.dumps(snapshot, ensure_ascii=False))
        if not self.steps:
            return PlannedStep(action="fail", answer="Script ran out of steps.")
        step = dict(self.steps.pop(0))
        if step.get("action") == "done" and self.before_done is not None:
            await self.before_done()
        label = step.pop("find", None)
        if label is not None:
            for element in snapshot.get("elements") or []:
                if label.lower() in " ".join(str(element.get(k) or "") for k in ("label", "text", "name")).lower():
                    step["ref"] = element["ref"]
                    break
            else:
                raise AssertionError(f"no element {label!r} on {snapshot.get('url')}: {snapshot.get('elements')}")
        return PlannedStep.model_validate(step)


class Harness:
    def __init__(self, port: int) -> None:
        async def resolve(host: str, _port: int) -> list[str]:
            if host in {"shop.example", "partner.example"}:
                return [PUBLIC_IP]
            if host == "internal.test":
                return ["10.0.0.5"]
            raise OSError("no such host")

        async def connect(address: str, _port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            assert address == PUBLIC_IP, address  # nothing else is ever dialled
            return await asyncio.open_connection("127.0.0.1", port)

        self.created: list[ActionSession] = []

        def factory(session_id: str, sites: list[str]) -> ActionSession:
            session = ActionSession(session_id, sites, limits=SessionLimits(), resolve=resolve, connect=connect, ignore_https_errors=True)
            self.created.append(session)
            return session

        self.sessions = ActionSessions(slots=1, executable_path=EXECUTABLE, factory=factory)
        from starlette.applications import Starlette

        app = Starlette(routes=action_routes(lambda request: request.headers.get("authorization") == f"Bearer {TOKEN}", self.sessions))
        self.client = ActionServiceClient("http://browser", TOKEN, timeout_seconds=90, client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app)))

    def tool(self, planner: Any) -> ActionBrowserTool:
        return ActionBrowserTool(problem=None, client=self.client, planner_factory=lambda _c: planner)

    async def shot(self, name: str) -> None:
        if DEMO is None or not self.created or self.created[-1].closed:
            return
        DEMO.mkdir(parents=True, exist_ok=True)
        (DEMO / f"{name}.jpg").write_bytes(await self.created[-1].screenshot())


async def call(tool: ActionBrowserTool, arguments: dict[str, Any]) -> ToolResult:
    with running_in("task_real", "run_real"):
        return await tool(ToolCall(name=ACT_TOOL, arguments=arguments), CONTEXT)


SCOPE = {"goal": "Book one seat to Riyadh", "start_url": "https://shop.example/login", "allowed_domains": ["shop.example"],
         "logins": {"shop": "shop.example"}, "cards": ["main"]}
SIGN_IN = [
    {"action": "type", "find": "Email", "value": "{{login:shop.username}}"},
    {"action": "type", "find": "Password", "value": "{{login:shop.password}}"},
    {"action": "click", "find": "Sign in"},
]
BOOK = [*SIGN_IN, {"action": "type", "find": "Passenger name", "value": "Owner Name"}, {"action": "click", "find": "Continue"},
        {"action": "type", "find": "Card number", "value": "{{card:main.number}}"}]


@pytest.fixture(autouse=True)
def _vault() -> Iterator[None]:
    memory_action_store.clear()
    create_credential(CONTEXT, CredentialWrite(kind="login", handle="shop", site="shop.example", username="owner@example.com", password=PASSWORD))
    create_credential(CONTEXT, CredentialWrite(kind="card", handle="main", name="Owner Name", number=CARD, expiry="08/29", cvc="123"))
    yield
    memory_action_store.clear()


def test_chromium_is_installed_for_this_job() -> None:
    ok, detail = chromium_available(EXECUTABLE)
    assert ok, detail


def test_sign_in_fill_stop_at_pay_approve_and_complete(site: tuple[Shop, int]) -> None:
    shop, port = site

    async def go() -> None:
        h = Harness(port)
        planner = LabelPlanner([*BOOK, {"action": "click", "find": "Pay"}, {"action": "done", "answer": "Booked, reference ABC123."}],
                               before_done=lambda: h.shot("7-done-booking-reference"))
        tool = h.tool(planner)
        card_gate = await call(tool, SCOPE)
        assert card_gate.status == "paused", card_gate.summary
        await h.shot("1-start-filled-passenger-then-card-approval")
        args = card_gate.continuation.arguments
        assert args["kind"] == "card_entry" and args["amount_text"] == "AED 1,240"
        pay_gate = await call(tool, args)
        assert pay_gate.status == "paused", pay_gate.summary
        await h.shot("2-waiting-for-approval-pay")
        pay = pay_gate.continuation.arguments
        assert pay["kind"] in {"binding_button", "payment_form"} and pay["amount"] == 1240.0 and pay["url"] == "https://shop.example/pay"
        assert shop.charged == []
        # The card field shows the handle, never the number, to the model and in the snapshot.
        assert CARD not in "".join(planner.seen) and PASSWORD not in "".join(planner.seen)
        done = await call(tool, pay)
        assert done.status == "succeeded", done.summary
        assert shop.charged == [CARD] and done.output["answer"] == "Booked, reference ABC123."
        assert h.created[-1].closed and len(h.sessions) == 0
        record = memory_action_store.get_session(CONTEXT, pay["session_id"])
        assert record.status == "completed" and record.current_url == "https://shop.example/done"
        log = json.dumps([s.model_dump(mode="json") for s in memory_action_store.steps(CONTEXT, record.id)])
        assert CARD not in log and PASSWORD not in log and "Typed the saved login shop (password)" in log

    asyncio.run(go())


def test_reject_leaves_nothing_paid_and_page_changed_asks_again(site: tuple[Shop, int]) -> None:
    shop, port = site

    async def go() -> None:
        h = Harness(port)
        # After a page change the planner proposes Pay again, which is asked again with the new price.
        tool = h.tool(LabelPlanner([*BOOK, {"action": "click", "find": "Pay"}, {"action": "click", "find": "Pay"}]))
        args = (await call(tool, SCOPE)).continuation.arguments
        pay = (await call(tool, args)).continuation.arguments
        # The price changes while the owner looks: approving the old picture does nothing and asks again.
        shop.price = "AED 1,990"
        await h.created[-1].page.reload()
        again = await call(tool, pay)
        assert again.status == "paused" and again.continuation.arguments["amount_text"] == "AED 1,990"
        assert shop.charged == []
        await h.shot("3-page-changed-asks-again")
        # Reject: the runtime never calls the tool again; the API closes the browser.
        await h.client.close(memory_action_store.get_session(CONTEXT, pay["session_id"]).browser_id or "")
        assert shop.charged == [] and len(h.sessions) == 0

    asyncio.run(go())


def test_a_multi_step_form_is_submitted_only_after_approval(site: tuple[Shop, int]) -> None:
    shop, port = site

    async def go() -> None:
        h = Harness(port)
        tool = h.tool(LabelPlanner([
            {"action": "type", "find": "Full name", "value": "Owner Name"}, {"action": "click", "find": "Next"},
            {"action": "type", "find": "Passport", "value": "P1234567"}, {"action": "click", "find": "Submit application"},
            {"action": "extract", "data": {"fields": {"Application number": "V-778"}}}, {"action": "done", "answer": "Submitted."},
        ]))
        gate = await call(tool, {**SCOPE, "goal": "Fill the visa form", "start_url": "https://shop.example/visa", "logins": {}, "cards": []})
        assert gate.status == "paused" and "Submit application" in gate.continuation.arguments["target"]
        assert shop.visa == []
        done = await call(tool, gate.continuation.arguments)
        assert done.status == "succeeded" and shop.visa == [{"passport": "P1234567"}]
        assert done.output["fields"] == {"Application number": "V-778"}

    asyncio.run(go())


def test_sign_in_and_download_a_statement_into_files(site: tuple[Shop, int]) -> None:
    from anum_api.files import file_store, open_file_metadata_store

    _, port = site

    async def go() -> None:
        h = Harness(port)
        tool = h.tool(LabelPlanner([*SIGN_IN, {"action": "navigate", "url": "https://shop.example/statements"},
                                    {"action": "click", "find": "Download October"}, {"action": "done", "answer": "Saved the statement."}]))
        done = await call(tool, {**SCOPE, "goal": "Download my October statement", "cards": []})
        assert done.status == "succeeded", done.summary
        (saved,) = done.output["files"]
        assert saved["name"] == "statement-oct.pdf"
        with open_file_metadata_store(CONTEXT) as files:
            record = files.get(CONTEXT, saved["id"])
        assert record.content_type == "application/pdf" and file_store.storage.get(record.storage_key) == PDF

    asyncio.run(go())


def test_compare_two_pages_into_a_table(site: tuple[Shop, int]) -> None:
    _, port = site

    async def go() -> None:
        h = Harness(port)
        tool = h.tool(LabelPlanner([
            {"action": "extract", "data": {"columns": ["Supplier", "Cement 50kg"], "rows": [["A", "AED 21"]]}},
            {"action": "navigate", "url": "https://shop.example/prices/b"},
            {"action": "extract", "data": {"rows": [["B", "AED 19"]]}},
            {"action": "done", "answer": "Supplier B is cheaper.", "save_as": "csv"},
        ]))
        done = await call(tool, {**SCOPE, "goal": "Compare cement prices", "start_url": "https://shop.example/prices/a", "logins": {}, "cards": []})
        assert done.status == "succeeded" and done.output["rows"] == [["A", "AED 21"], ["B", "AED 19"]]
        assert done.output["files"][0]["name"].endswith(".csv")

    asyncio.run(go())


def test_the_proxy_refuses_private_addresses_and_off_list_navigation_is_held(site: tuple[Shop, int]) -> None:
    _, port = site

    async def go() -> None:
        h = Harness(port)
        tool = h.tool(LabelPlanner([{"action": "wait"}, {"action": "navigate", "url": "https://partner.example/login"}]))
        gate = await call(tool, {**SCOPE, "start_url": "https://shop.example/leak", "logins": {}, "cards": []})
        assert gate.status == "paused" and gate.continuation.arguments["resume"] == "off_site"
        stats = h.created[-1].proxy.stats
        assert stats.reasons["refused_address"] >= 1
        await h.client.close(memory_action_store.get_session(CONTEXT, gate.continuation.arguments["session_id"]).browser_id or "")

    asyncio.run(go())
