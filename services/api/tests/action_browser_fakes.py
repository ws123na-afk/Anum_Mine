"""A fake browser service in action mode and a scripted planner, for the action browser's API tests.

The fake speaks the session routes of ``anum_api.action_browser.service_routes`` over an
``httpx.MockTransport`` and models a small site (sign in, a form, a payment page, a statement
download, two price pages). It records every request body, so tests can prove a secret only
ever travelled in the one ``act`` request that typed it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from anum_api.action_browser.kernel import fingerprint
from anum_api.action_browser.planner import PlannedStep

PASSWORD = "plain-fake-pass"
CARD = "4242424242424242"
STATEMENT = b"%PDF-1.4\n% fake statement for tests\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


def field(ref: str, name: str, label: str, *, type: str = "text", autocomplete: str = "", form: int | None = 0) -> dict[str, Any]:
    return {"ref": ref, "tag": "input", "type": type, "name": name, "label": label, "autocomplete": autocomplete, "form": form}


def button(ref: str, text: str, *, form: int | None = 0) -> dict[str, Any]:
    return {"ref": ref, "tag": "button", "type": "submit" if form is not None else "button", "text": text, "form": form}


class FakeSite:
    """Pages by path; each page is a title, text, elements and the action of its form."""

    def __init__(self, host: str = "shop.example") -> None:
        self.host = host
        self.price = "AED 1,240"
        self.captcha_on: str | None = None
        self.pages: dict[str, dict[str, Any]] = {}
        self.rebuild()

    def rebuild(self) -> None:
        self.pages = {
            "/login": {"title": "Sign in", "text": "Sign in to your account", "action": "/login", "elements": [
                field("e1", "username", "Email"), field("e2", "password", "Password", type="password"), button("e3", "Sign in")]},
            "/passenger": {"title": "Passenger details", "text": "Who is travelling?", "action": "/passenger", "elements": [
                field("e1", "passenger_name", "Passenger name"), button("e2", "Continue")]},
            "/pay": {"title": "Payment", "text": f"Fare\nTotal {self.price}", "action": "/pay/charge", "elements": [
                field("e1", "cardnumber", "Card number", autocomplete="cc-number"), button("e2", f"Pay {self.price}")]},
            "/done": {"title": "Booked", "text": "Booking reference ABC123", "action": None, "elements": []},
            "/visa": {"title": "Visa form, step 1", "text": "Applicant", "action": "/visa/2", "elements": [
                field("e1", "full_name", "Full name"), button("e2", "Next")]},
            "/visa/2": {"title": "Visa form, step 2", "text": "Review your application", "action": "/visa/submit", "elements": [
                field("e1", "passport", "Passport number"), button("e2", "Submit application")]},
            "/visa/sent": {"title": "Sent", "text": "Application number V-778", "action": None, "elements": []},
            "/statements": {"title": "Statements", "text": "October statement", "action": None, "elements": [
                {"ref": "e1", "tag": "a", "type": "", "text": "Download October statement", "href": f"https://{self.host}/files/oct.pdf", "download": True},
                {"ref": "e2", "tag": "a", "type": "", "text": "Download tool", "href": f"https://{self.host}/files/tool.exe", "download": True}]},
            "/prices/a": {"title": "Supplier A", "text": "Cement 50kg AED 21", "action": None, "elements": []},
            "/prices/b": {"title": "Supplier B", "text": "Cement 50kg AED 19", "action": None, "elements": []},
        }


class FakeActionService:
    """Session routes of the browser service, over a mock transport."""

    def __init__(self, site: FakeSite | None = None) -> None:
        self.site = site or FakeSite()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.created = 0
        self.busy = False
        self.no_action_mode = False
        self.change_before_act = False
        self.closed: list[str] = []
        self.inputs: list[dict[str, Any]] = []

    # -- page model ------------------------------------------------------------

    def _page(self, session: dict[str, Any]) -> dict[str, Any]:
        path = session["path"]
        spec = self.site.pages.get(path, {"title": "Not found", "text": "", "elements": [], "action": None})
        text = spec["text"]
        frames: list[str] = []
        if self.site.captcha_on == path:
            frames = ["www.recaptcha.net"]
        fields = [{"name": e["name"], "type": e["type"], "label": e["label"], "autocomplete": e.get("autocomplete", ""), "visible": True}
                  for e in spec["elements"] if e["tag"] == "input"]
        return {"url": f"https://{self.site.host}{path}", "host": self.site.host, "path": path, "title": spec["title"], "text": text,
                "fields": fields, "frames": frames, "challenge": False, "frame_urls": []}

    def _element(self, session: dict[str, Any], ref: str) -> dict[str, Any] | None:
        spec = self.site.pages.get(session["path"], {"elements": []})
        return next((e for e in spec["elements"] if e["ref"] == ref), None)

    def _describe(self, session: dict[str, Any], element: dict[str, Any]) -> dict[str, Any]:
        spec = self.site.pages[session["path"]]
        target = {k: element.get(k, "") for k in ("tag", "type", "name", "label", "text", "href")}
        target["download"] = bool(element.get("download"))
        target["autocomplete"] = element.get("autocomplete", "")
        target["submits"] = element["tag"] == "button" and element.get("form") is not None
        if element.get("form") is not None and spec.get("action"):
            target["form"] = {"action": f"https://{self.site.host}{spec['action']}", "method": "post", "text": spec["text"],
                              "key": f"0:{session['path']}",
                              "fields": [{"name": e["name"], "type": e["type"], "label": e["label"], "autocomplete": e.get("autocomplete", "")}
                                         for e in spec["elements"] if e["tag"] == "input"]}
        return target

    def _snapshot(self, session: dict[str, Any]) -> dict[str, Any]:
        page = self._page(session)
        elements = []
        for element in self.site.pages.get(session["path"], {"elements": []})["elements"]:
            item = dict(element)
            if element["tag"] == "input":
                value = session["values"].get((session["path"], element["ref"]), "")
                masked = element["type"] == "password" or element.get("autocomplete", "").startswith("cc-")
                item["value"] = session["handles"].get((session["path"], element["ref"])) or ("(hidden)" if value and masked else value)
            elements.append(item)
        held = session.pop("held", None)
        return {**page, "elements": elements, "held": held, **({"downloads": session.pop("new_downloads")} if session.get("new_downloads") else {})}

    def _inspect(self, session: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        target: dict[str, Any] = {}
        if action.get("type") in {"click", "type", "select"}:
            element = self._element(session, str(action.get("ref")))
            if element is None:
                raise LookupError
            target = self._describe(session, element)
        described = {"action": action, "page": self._page(session), "target": target}
        described["fingerprint"] = fingerprint(described)
        return described

    def _do(self, session: dict[str, Any], action: dict[str, Any], secret: str | None) -> None:
        kind = action.get("type")
        path = session["path"]
        if kind == "navigate":
            url = str(action["url"])
            host = url.split("/")[2]
            if not any(host == s or host.endswith("." + s) for s in session["sites"]):
                session["held"] = url
                return
            session["path"] = "/" + url.split("/", 3)[3] if url.count("/") >= 3 and url.split("/", 3)[3] else "/login"
            return
        ref = str(action.get("ref"))
        if kind == "type":
            value = secret if secret is not None else str(action.get("value"))
            session["values"][(path, ref)] = value
            if secret is not None:
                session["handles"][(path, ref)] = str(action.get("value"))
            return
        element = self._element(session, ref) or {}
        if path == "/login" and ref == "e3":
            ok = session["values"].get(("/login", "e2")) == PASSWORD and session["values"].get(("/login", "e1")) == "owner@example.com"
            session["path"] = "/passenger" if ok else "/login"
        elif path == "/passenger" and ref == "e2":
            session["path"] = "/pay"
        elif path == "/pay" and ref == "e2":
            session["paid_with"] = session["values"].get(("/pay", "e1"))
            session["path"] = "/done"
        elif path == "/visa" and ref == "e2":
            session["path"] = "/visa/2"
        elif path == "/visa/2" and ref == "e2":
            session["submitted"] = True
            session["path"] = "/visa/sent"
        elif element.get("download"):
            name = str(element["href"]).rsplit("/", 1)[1]
            if name.endswith(".pdf"):
                item = {"id": f"d{len(session['downloads']) + 1}", "name": name, "size": len(STATEMENT)}
                session["downloads"].append(item)
                session["new_downloads"] = [item]

    # -- transport --------------------------------------------------------------

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        self.requests.append((request.method, path, body))
        if request.headers.get("authorization") != "Bearer action-browser-token-for-tests-only-example":
            return httpx.Response(401, json={"detail": "Not authorised."})
        if self.no_action_mode and path.startswith("/v1/sessions"):
            return httpx.Response(404, json={"detail": "Not Found"})
        if path == "/healthz":
            return httpx.Response(200, json={"status": "ok", "action_mode": not self.no_action_mode})
        if path == "/v1/sessions" and request.method == "POST":
            if self.busy:
                return httpx.Response(429, json={"outcome": "busy", "message": "The browser is busy with another task; try again when it ends."})
            self.created += 1
            sid = f"browser{self.created}"
            self.sessions[sid] = {"path": "/", "sites": body["sites"], "values": {}, "handles": {}, "downloads": []}
            return httpx.Response(200, json={"session_id": sid, "sites": body["sites"]})
        parts = path.split("/")
        sid = parts[3] if len(parts) > 3 else ""
        session = self.sessions.get(sid)
        if request.method == "DELETE":
            self.closed.append(sid)
            self.sessions.pop(sid, None)
            return httpx.Response(200, json={"closed": session is not None})
        if session is None:
            return httpx.Response(404, json={"outcome": "session_gone", "message": "The browser session has ended."})
        tail = parts[4] if len(parts) > 4 else ""
        if tail == "snapshot":
            return httpx.Response(200, json=self._snapshot(session))
        if tail == "inspect":
            try:
                return httpx.Response(200, json=self._inspect(session, body["action"]))
            except LookupError:
                return httpx.Response(409, json={"outcome": "no_target", "message": "That element is not on the page any more."})
        if tail == "act":
            action = body["action"]
            if self.change_before_act:
                self.site.price = "AED 1,990"
                self.site.rebuild()
                self.change_before_act = False
            if body.get("expect"):
                try:
                    fresh = self._inspect(session, action)
                except LookupError:
                    return httpx.Response(409, json={"outcome": "no_target", "message": "That element is not on the page any more."})
                if fresh["fingerprint"] != body["expect"].get("fingerprint") or fresh["page"]["url"] != body["expect"].get("url"):
                    return httpx.Response(200, json={"outcome": "page_changed", "inspect": fresh})
            self._do(session, action, body.get("secret"))
            snapshot = self._snapshot(session)
            if snapshot.get("held"):
                return httpx.Response(200, json={"outcome": "held_navigation", "held": snapshot["held"], "snapshot": snapshot})
            return httpx.Response(200, json={"outcome": "done", "snapshot": snapshot})
        if tail == "screenshot":
            return httpx.Response(200, content=b"\xff\xd8\xff\xe0fake-jpeg", headers={"content-type": "image/jpeg"})
        if tail == "input":
            self.inputs.append(body)
            return httpx.Response(200, json={"outcome": "done"})
        if tail == "domains":
            session["sites"] = [*session["sites"], *body["sites"]]
            return httpx.Response(200, json={"sites": session["sites"]})
        if tail == "downloads":
            item = next((d for d in session["downloads"] if d["id"] == parts[5]), None)
            if item is None:
                return httpx.Response(404, json={})
            return httpx.Response(200, content=STATEMENT, headers={"content-type": "application/octet-stream", "x-download-name": item["name"]})
        return httpx.Response(404, json={})

    def bodies(self) -> list[str]:
        return [json.dumps(body) for _, _, body in self.requests]


class ScriptedPlanner:
    """Answers the steps it was given in order, and records everything it was shown."""

    def __init__(self, steps: list[PlannedStep | dict[str, Any]]) -> None:
        self.steps = [s if isinstance(s, PlannedStep) else PlannedStep.model_validate(s) for s in steps]
        self.seen: list[str] = []

    async def next_step(self, *, goal: str, scope: dict[str, Any], history: list[str], snapshot: dict[str, Any]) -> PlannedStep:
        self.seen.append(json.dumps({"goal": goal, "scope": scope, "history": history, "snapshot": snapshot}, ensure_ascii=False))
        if not self.steps:
            return PlannedStep(action="fail", answer="Script ran out of steps.")
        return self.steps.pop(0)
