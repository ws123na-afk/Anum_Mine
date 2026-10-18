"""Audit coverage of state-changing routes (docs/governance-and-scale.md#audit-log).

Each module's test file walks every POST, PUT, PATCH and DELETE route of its router, as
listed by the OpenAPI schema, through an :class:`AuditProbe`: a successful call must add
at least one audit record in the caller's workspace, and no record may carry a secret
or the content the call sent. ``probe.assert_covered()`` then fails when a route of the
module was not exercised, so a new route without an audit record cannot slip in.

Routes that change no shared state worth a security record are listed in ``EXEMPT`` with
the reason; ``tests/test_audit_coverage.py`` checks that list against the schema.
"""

from __future__ import annotations

import json
import re
from dataclasses import fields
from collections.abc import Iterable
from typing import Any

import httpx
from fastapi.testclient import TestClient

from anum_api.main import app
from anum_api.store import store

MUTATING = ("post", "put", "patch", "delete")
MODULE_PREFIXES = {
    "agents": "/api/v1/agents",
    "automation": "/api/v1/automation",
    "calls": "/api/v1/calls",
    "brevo": "/api/v1/brevo",
    "business": "/api/v1/business",
    "business_pipeline": "/api/v1/business/pipeline",
    "business_outreach": "/api/v1/business/outreach",
    "autonomy": "/api/v1/autonomy",
    "developer": "/api/v1/developer",
    "email": "/api/v1/email",
    "learning": "/api/v1/learning",
    "mail": "/api/v1/mail",
    "mail_ai": "/api/v1/mail/ai",
    "whatsapp": "/api/v1/whatsapp",
}
_CALL_WEBHOOK = (
    "The voice provider's signed delivery during a call, not a person's action; call starts, ends, "
    "stored recordings and opt-outs it causes are audited as call.* records by the system actor."
)
EXEMPT: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/calls/webhooks/twilio/{route_id}/voice"): _CALL_WEBHOOK,
    ("POST", "/api/v1/calls/webhooks/twilio/{route_id}/dial/{call_id}"): _CALL_WEBHOOK,
    ("POST", "/api/v1/calls/webhooks/twilio/{route_id}/status"): _CALL_WEBHOOK,
    ("POST", "/api/v1/calls/webhooks/twilio/{route_id}/status/{call_id}"): _CALL_WEBHOOK,
    ("POST", "/api/v1/calls/webhooks/twilio/{route_id}/recording/{call_id}"): _CALL_WEBHOOK,
    ("POST", "/api/v1/calls/webhooks/twilio/{route_id}/ai/{call_id}"): _CALL_WEBHOOK,
    ("POST", "/api/v1/calls/webhooks/twilio/{route_id}/turn/{call_id}"): _CALL_WEBHOOK,
    ("POST", "/api/v1/automation/schedules/preview"): "Computes the next fire times of a cron expression; stores nothing.",
    ("POST", "/api/v1/developer/resolve"): "Matches a spoken request to a project name; stores nothing.",
    ("POST", "/api/v1/automation/hooks/{tenant_id}/{workspace_id}/{trigger_id}"): (
        "An outside system's HMAC-signed delivery, not a person's action; the run it starts is in the run "
        "history with the trigger and delivery id, and every change to the trigger itself is audited."
    ),
    ("POST", "/api/v1/whatsapp/webhook"): (
        "Meta's signed delivery of customer messages, not a person's action; deliveries are counted in "
        "metrics and every reply a person or ANUM sends is audited on /send."
    ),
    ("POST", "/api/v1/whatsapp/conversations/{conversation_id}/read"): (
        "Per-view read marker inside ANUM; it would flood the security log and changes nothing outside it."
    ),
}


def audit_text(records: Iterable[Any]) -> str:
    """Every field of the records (metadata included) as one JSON string, for content checks."""

    def plain(value: Any) -> Any:
        if hasattr(value, "items"):
            return {str(key): plain(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [plain(item) for item in value]
        return value

    return json.dumps(
        [{field.name: plain(getattr(record, field.name)) for field in fields(record)} for record in records],
        default=str,
        ensure_ascii=False,
    )


def mutating_routes(prefix: str) -> set[tuple[str, str]]:
    """``(METHOD, path template)`` of every state-changing route under ``prefix``.

    A route under a longer module prefix (``/api/v1/mail/ai`` inside ``/api/v1/mail``) belongs
    to that module's walk only."""
    paths = app.openapi()["paths"]
    inner = [other for other in MODULE_PREFIXES.values() if other.startswith(prefix + "/")]

    def under(path: str, base: str) -> bool:
        return path.startswith(base + "/") or path == base

    return {
        (method.upper(), path)
        for path, operations in paths.items()
        if under(path, prefix) and not any(under(path, other) for other in inner)
        for method in operations
        if method in MUTATING
    }


def _template_pattern(template: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(template)) + "$")


class AuditProbe:
    """Calls routes and checks each successful call left an audit record."""

    def __init__(self, client: TestClient, module: str, *, secrets: Iterable[str] = ()) -> None:
        self.client = client
        self.module = module
        self.prefix = MODULE_PREFIXES[module]
        self.routes = mutating_routes(self.prefix)
        self.patterns = {route: _template_pattern(route[1]) for route in self.routes}
        self.secrets = [value for value in secrets if value]
        self.covered: dict[tuple[str, str], list[str]] = {}

    def _route(self, method: str, path: str) -> tuple[str, str]:
        found = [route for route, pattern in self.patterns.items() if route[0] == method and pattern.match(path)]
        # The most specific template wins (a literal segment beats a parameter).
        found.sort(key=lambda route: route[1].count("{"))
        assert found, f"{method} {path} is not a state-changing route of {self.prefix}"
        return found[0]

    def call(self, method: str, path: str, headers: dict[str, str], *, sent: Iterable[str] = (), **kwargs: Any) -> httpx.Response:
        """Make the request; it must succeed and add an audit record in the caller's workspace.

        ``sent`` lists content the request carried (texts, bodies) that must not be copied
        into the audit record.
        """
        method = method.upper()
        route = self._route(method, path)
        before = len(store.audit_records)
        response = self.client.request(method, path, headers=headers, **kwargs)
        assert response.status_code < 300, f"{method} {path}: {response.status_code} {response.text}"
        added = [
            record
            for record in store.audit_records[before:]
            if (record.tenant_id, record.workspace_id) == (headers["x-tenant-id"], headers["x-workspace-id"])
        ]
        assert added, f"{method} {route[1]} changed state without an audit record"
        serialized = audit_text(added)
        for value in [*self.secrets, *[item for item in sent if item]]:
            assert value not in serialized, f"{method} {route[1]} copied a secret or the request's content into the audit log"
        self.covered.setdefault(route, []).extend(record.action for record in added)
        return response

    def assert_covered(self) -> None:
        missing = sorted(route for route in self.routes if route not in self.covered and route not in EXEMPT)
        assert not missing, f"{self.module}: state-changing routes not exercised by the audit walk: {missing}"
