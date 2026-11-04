"""Every API route and every database role grant, checked against the security model.

The pre-sale review (docs/security.md#pre-sale-review-november-2026) found no gap, so this
test keeps it that way as routes are added:

* every route depends on one of the authentication dependencies, or is listed in
  ``PUBLIC_ROUTES`` with the credential that replaces a session (a signature, a signed link,
  a hashed token) or why it is public;
* every authenticated route reaches a permission check (``require_permission`` and its
  relatives), or is listed in ``SELF_SCOPED_ROUTES`` with the reason it needs none: the
  route only reads or changes the caller's own rows;
* no new ``async def`` route opens a store or a unit of work on the event loop
  (docs/realtime.md#event-loop); the routes that still do are frozen in
  ``event_loop_store_baseline.txt`` and the list may only shrink;
* every narrow NOLOGIN role that widens row visibility is granted to the application login
  ``WITH INHERIT FALSE`` in ``infra/helm/bootstrap-database.sql``.

Stale entries in either list fail too, so the lists stay true. The database half (every
table has forced row-level security and a policy) is ``test_postgres_security_inventory.py``.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
import textwrap
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from anum_api.main import app

REPO = Path(__file__).resolve().parents[3]
BASELINE = Path(__file__).with_name("event_loop_store_baseline.txt")

AUTH_DEPENDENCIES = frozenset(
    {"tenant_context", "provisioning_tenant_context", "authenticated_identity", "directory_identity"}
)
PERMISSION_CHECKS = frozenset(
    {
        "require_permission",
        "has_permission",
        "require_owner_or_org_admin",
        "is_organization_admin",
        "require_organization",
        "_require_workspace_role",
    }
)

# Routes without a session. Each needs a reason; the credential is checked in the handler.
PUBLIC_ROUTES: dict[str, str] = {
    "GET /health": "Liveness probe; returns no tenant data.",
    "GET /api/v1/auth/sign-in-options": "Which sign-in buttons the login page shows; operator config only.",
    "POST /api/v1/auth/local/session": "Development sign-in; refused unless ANUM_ENVIRONMENT is local/test and auth mode is headers.",
    "DELETE /api/v1/auth/local/session": "Revokes the presented local session only; local/test only.",
    "POST /api/v1/auth/local/otp/request": "Development sign-in; local/test only.",
    "POST /api/v1/auth/local/otp/verify": "Development sign-in; local/test only.",
    "POST /api/v1/auth/local/password/forgot": "Development sign-in; local/test only.",
    "POST /api/v1/auth/local/password/reset": "Development sign-in; local/test only.",
    "POST /api/v1/automation/hooks/{tenant_id}/{workspace_id}/{trigger_id}": "HMAC signature with the trigger's secret, replay window, refusal limit per caller.",
    "POST /api/v1/commerce/webhooks/{tenant_id}/{workspace_id}/{connector_id}": "HMAC signature with the connection's secret, five-minute window, refusal limit per caller.",
    "POST /api/v1/billing/webhooks/stripe/{issuer_tenant_id}": "Stripe signature with the issuer's webhook secret.",
    "POST /api/v1/billing/webhooks/moyasar/{issuer_tenant_id}": "Moyasar secret token compared in constant time, then the payment is read back from Moyasar's API with the issuer's key; database work in a thread.",
    "GET /api/v1/public/legal": "The configured issuer's company details and legal facts for the public legal pages, through the narrow platform reader; no tenant id.",
    "GET /api/v1/whatsapp/webhook": "Meta's subscription check against the hashed verify token.",
    "POST /api/v1/whatsapp/webhook": "X-Hub-Signature-256 with the app secret, bad-signature limit per caller.",
    "POST /api/v1/attendance/device/events": "iPhone Shortcut: a scoped device token through attendance/devices.py DeviceAuthority, else the ordinary sign-in (tenant_context called in device_principal); attendance:use checked; no coordinates accepted.",
    "POST /api/v1/calls/webhooks/twilio/{route_id}/voice": "Twilio signature with the account's auth token; unguessable route id.",
    "POST /api/v1/calls/webhooks/twilio/{route_id}/dial/{call_id}": "Twilio signature; unguessable route id.",
    "POST /api/v1/calls/webhooks/twilio/{route_id}/status": "Twilio signature; unguessable route id.",
    "POST /api/v1/calls/webhooks/twilio/{route_id}/status/{call_id}": "Twilio signature; unguessable route id.",
    "POST /api/v1/calls/webhooks/twilio/{route_id}/recording/{call_id}": "Twilio signature; unguessable route id.",
    "POST /api/v1/calls/webhooks/twilio/{route_id}/ai/{call_id}": "Twilio signature; unguessable route id.",
    "POST /api/v1/calls/webhooks/twilio/{route_id}/turn/{call_id}": "Twilio signature; unguessable route id.",
    "GET /api/v1/email/oauth/callback": "OAuth redirect; the single-use, expiring, hash-stored state names the caller.",
    "GET /api/v1/calendar/sync/oauth/callback": "OAuth redirect; the single-use, expiring, hash-stored state names the caller.",
    "GET /api/v1/files/links/{token}": "Signed, short-lived HMAC link to one file (docs/files.md#downloads-and-previews).",
    "GET /api/v1/documents/links/{token}": "Signed, short-lived HMAC link to one vault document; the claimed person must still see it (docs/documents.md#downloads-and-links).",
    "POST /api/v1/documents/device/upload": "iPhone Shortcut upload: a hashed device token with the documents scope; the owner's membership and role are re-checked (docs/documents.md#scanning-from-an-iphone).",
    "GET /api/v1/marketing/media/{link}": "Encrypted, expiring link to one image or video for social platforms.",
    "GET /api/v1/organization/lifecycle/exports/download/{token}": "Signed, expiring link to one organisation export.",
    "GET /api/v1/public/logos/{public_id}/{name}": "Public brand logo by its random public id; nothing else is served.",
    "GET /api/v1/public/billing/plans": "The public price list.",
    "GET /api/v1/signup/status": "Self-serve sign-up: a valid Keycloak token of a pending account (signup_identity).",
    "POST /api/v1/signup/organization": "Self-serve sign-up: a valid Keycloak token of a pending account (signup_identity).",
    "POST /api/v1/finance/wallet/ingest": "iPhone Shortcut device token, the same credential and checks as the SMS ingest (Apple Wallet events).",
    "POST /api/v1/integrations/iphone/ping": "iPhone Shortcut device token (Authorization: Bearer), the shared credential of docs/iphone-integrations.md: scope, permission, switch and per-scope rate limit checked; https only; records only the test.",
    "POST /api/v1/finance/sms/ingest": "iPhone Shortcut device token (Authorization: Bearer): hashed 256-bit secret checked in constant time in its owner's RLS scope, revocable, rate limited per device, https only.",
    "GET /api/v1/portal/me": "Customer portal: the hashed 256-bit link token (portal_session), one client account.",
    "GET /api/v1/portal/offers": "Customer portal link token.",
    "GET /api/v1/portal/offers/{proposal_id}": "Customer portal link token.",
    "POST /api/v1/portal/offers/{proposal_id}/decision": "Customer portal link token.",
    "GET /api/v1/portal/change-orders": "Customer portal link token.",
    "GET /api/v1/portal/change-orders/{change_order_id}": "Customer portal link token.",
    "POST /api/v1/portal/change-orders/{change_order_id}/decision": "Customer portal link token.",
    "GET /api/v1/portal/invoices": "Customer portal link token.",
    "GET /api/v1/portal/invoices/{invoice_id}": "Customer portal link token.",
    "POST /api/v1/portal/invoices/{invoice_id}/payment-notices": "Customer portal link token.",
    "GET /api/v1/portal/invoices/{invoice_id}/print": "Customer portal link token.",
    "GET /api/v1/portal/invoices/{invoice_id}/pdf": "Customer portal link token.",
    "GET /api/v1/portal/tickets": "Customer portal link token.",
    "GET /api/v1/portal/tickets/{ticket_id}": "Customer portal link token.",
    "POST /api/v1/portal/tickets": "Customer portal link token.",
}

# Authenticated routes with no workspace permission check. Each acts only on the caller's
# own rows (filtered by user id in the handler and, where the table has one, by an
# owner-only row policy) or decides access by its own rule.
SELF_SCOPED_ROUTES: dict[str, str] = {
    "GET /api/v1/onboarding": "The caller's own onboarding state.",
    "POST /api/v1/auth/local/workspace/switch": "Development sign-in (local/test only); needs an active membership in the target workspace.",
    "POST /api/v1/auth/local/tenant/switch": "Development sign-in (local/test only); needs an active membership in the target organisation.",
    "GET /api/v1/notification-preferences": "The caller's own preferences.",
    "PUT /api/v1/notification-preferences": "The caller's own preferences.",
    "POST /api/v1/workspace-invitations/accept": "Accepting needs the invitation's single-use token and, when bound, the verified email.",
    "GET /api/v1/me/workspace-memberships": "The caller's own memberships, through the INHERIT FALSE membership reader.",
    "GET /api/v1/me/organizations": "The organisations the caller's token lists.",
    "GET /api/v1/executive/overview": "Only workspaces with an approved, unexpired grant to the caller, each re-checked in its own scope.",
    "GET /api/v1/push/status": "Whether phone push is configured; no tenant data.",
    "GET /api/v1/me/devices": "The caller's own registered devices.",
    "POST /api/v1/me/devices": "Registers a device for the caller only.",
    "DELETE /api/v1/me/devices/{device_id}": "The caller's own device.",
    "GET /api/v1/favorites": "The caller's own favorites.",
    "DELETE /api/v1/favorites": "The caller's own favorites.",
    "DELETE /api/v1/favorites/{favorite_id}": "The caller's own favorite.",
    "GET /api/v1/notification-channels": "Which channels can reach the caller.",
    "POST /api/v1/notification-channels/email-test": "Sends only to the caller's own address; five, then one a minute.",
    "GET /api/v1/notifications": "The caller's own notifications.",
    "POST /api/v1/notifications/{notification_id}/read": "The caller's own notification (recipient-only update).",
    "POST /api/v1/notifications/read-all": "The caller's own notifications.",
}

_STORE_OPENERS = ("open_", "_unit_of_work", "units(", "SessionLocal", "session(", "_store(")


def _route_contexts() -> Iterator[Any]:
    for route in app.router.routes:
        if type(route).__name__ == "_IncludedRouter":
            contexts = route.effective_route_contexts
            yield from (contexts() if callable(contexts) else contexts)
        elif hasattr(route, "dependant"):
            yield route


def _dependency_names(dependant: Any, names: set[str]) -> set[str]:
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            names.add(getattr(dependency.call, "__qualname__", repr(dependency.call)))
        _dependency_names(dependency, names)
    return names


def _key(route: Any) -> str:
    return f"{','.join(sorted(route.methods or []))} {route.path}"


def _source_tree(fn: Callable[..., Any]) -> ast.AST | None:
    try:
        return ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        return None


def _local_imports(tree: ast.AST, module: str) -> dict[str, Any]:
    """Names a function imports inside its body (``from .organisations import _require_operator``)."""
    found: dict[str, Any] = {}
    package = module.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and node.module is not None:
            base = package
            for _ in range(node.level - 1):
                base = base.rsplit(".", 1)[0]
            try:
                imported = importlib.import_module(f"{base}.{node.module}")
            except ImportError:
                continue
            for alias in node.names:
                if hasattr(imported, alias.name):
                    found[alias.asname or alias.name] = getattr(imported, alias.name)
    return found


def _reaches_permission_check(fn: Callable[..., Any], seen: set[int], depth: int = 0) -> bool:
    fn = inspect.unwrap(fn)
    if id(fn) in seen or depth > 6 or not (getattr(fn, "__module__", "") or "").startswith("anum_api"):
        return False
    seen.add(id(fn))
    tree = _source_tree(fn)
    if tree is None:
        return False
    names = {**getattr(fn, "__globals__", {}), **_local_imports(tree, fn.__module__)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in PERMISSION_CHECKS or (isinstance(node.value, ast.Name) and node.value.id == "policy"):
                return True
            if isinstance(node.value, ast.Name) and node.value.id in names:
                target = getattr(names[node.value.id], node.attr, None)
                if inspect.isfunction(target) and _reaches_permission_check(target, seen, depth + 1):
                    return True
        elif isinstance(node, ast.Name):
            if node.id in PERMISSION_CHECKS:
                return True
            target = names.get(node.id)
            if inspect.isfunction(target) and _reaches_permission_check(target, seen, depth + 1):
                return True
    return False


def _loop_nodes(body: list[ast.stmt]) -> Iterator[ast.AST]:
    """Nodes that run on the event loop: the async body, minus nested plain functions and
    lambdas (they run where they are called; see ``_opens_store_on_loop``). Nested ``async``
    functions run on the loop, so their bodies count."""
    stack: list[ast.AST] = [node for node in body if not isinstance(node, ast.FunctionDef)]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.Lambda)):
                stack.append(child)


def _opens_store(nodes: Iterator[ast.AST]) -> bool:
    return any(
        isinstance(node, ast.With)
        and any(marker in ast.unparse(item.context_expr) for item in node.items for marker in _STORE_OPENERS)
        for node in nodes
    )


def _opens_store_on_loop(fn: Callable[..., Any]) -> bool:
    """A ``with`` store opener that runs on the event loop: in the async body itself, or in a
    nested plain function the async body calls directly. A nested function handed to
    ``asyncio.to_thread`` (or any other callee) runs in a worker thread and does not count."""
    fn = inspect.unwrap(fn)
    if not inspect.iscoroutinefunction(fn):
        return False
    tree = _source_tree(fn)
    if tree is None:
        return False
    route = tree.body[0]
    if not isinstance(route, ast.AsyncFunctionDef):
        return False
    on_loop = list(_loop_nodes(route.body))
    if _opens_store(iter(on_loop)):
        return True
    nested = {
        node.name: node
        for node in ast.walk(route)
        if isinstance(node, ast.FunctionDef) and _opens_store(_loop_nodes(node.body))
    }
    called = {
        node.func.id for node in on_loop if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return bool(called & set(nested))


@pytest.fixture(scope="module")
def routes() -> list[Any]:
    found = list(_route_contexts())
    assert len(found) > 900, "the route walk found too few routes; FastAPI's router layout changed"
    return found


def test_every_route_is_authenticated_or_listed_as_public(routes: list[Any]) -> None:
    public = {
        _key(route): route
        for route in routes
        if not (_dependency_names(route.dependant, set()) & AUTH_DEPENDENCIES)
    }
    unlisted = sorted(set(public) - set(PUBLIC_ROUTES))
    stale = sorted(set(PUBLIC_ROUTES) - set(public))
    assert not unlisted, (
        "Routes without an authentication dependency. Add tenant_context (or another "
        f"authentication dependency), or list the route in PUBLIC_ROUTES with its credential: {unlisted}"
    )
    assert not stale, f"PUBLIC_ROUTES entries that are no longer public routes: {stale}"
    assert all(reason.strip() for reason in PUBLIC_ROUTES.values())


def test_every_authenticated_route_checks_a_permission_or_is_self_scoped(routes: list[Any]) -> None:
    unchecked: set[str] = set()
    for route in routes:
        if not (_dependency_names(route.dependant, set()) & AUTH_DEPENDENCIES):
            continue
        seen: set[int] = set()
        if _reaches_permission_check(route.endpoint, seen):
            continue
        if any(dep.call and _reaches_permission_check(dep.call, seen) for dep in route.dependant.dependencies):
            continue
        unchecked.add(_key(route))
    unlisted = sorted(unchecked - set(SELF_SCOPED_ROUTES))
    stale = sorted(set(SELF_SCOPED_ROUTES) - unchecked)
    assert not unlisted, (
        "Authenticated routes that never call require_permission (or a relative). Check the "
        f"caller's role, or list the route in SELF_SCOPED_ROUTES with the reason: {unlisted}"
    )
    assert not stale, f"SELF_SCOPED_ROUTES entries that now check a permission (remove them): {stale}"


def test_no_new_async_route_opens_a_store_on_the_event_loop(routes: list[Any]) -> None:
    """Pre-existing routes are frozen in the baseline (open item in docs/security.md); new
    ones must run their database work with asyncio.to_thread or be plain ``def`` routes."""
    found = {_key(route) for route in routes if _opens_store_on_loop(route.endpoint)}
    baseline = {
        line.strip() for line in BASELINE.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")
    }
    added = sorted(found - baseline)
    fixed = sorted(baseline - found)
    assert not added, (
        "New async routes open a store or unit of work on the event loop, which stalls every "
        f"user while it waits (docs/realtime.md#event-loop): {added}"
    )
    assert not fixed, f"These routes no longer open a store on the loop; remove them from {BASELINE.name}: {fixed}"


def test_narrow_roles_are_granted_without_inherit() -> None:
    sql = (REPO / "infra/helm/bootstrap-database.sql").read_text(encoding="utf-8")
    grants = re.findall(r"^grant (anum_\w+) to (anum_\w+)([^;]*);", sql, flags=re.MULTILINE | re.IGNORECASE)
    to_app = {role: options.lower() for role, login, options in grants if login == "anum_app"}
    narrow = {
        "anum_maintenance",
        "anum_membership_reader",
        "anum_platform_reader",
        "anum_tenant_directory",
        "anum_tenant_purger",
        "anum_executive_reader",
    }
    assert narrow <= set(to_app), sorted(narrow - set(to_app))
    for role, options in to_app.items():
        assert "inherit false" in options, f"{role} is granted to anum_app with INHERIT"


def _detector_cases() -> dict[str, Callable[..., Any]]:
    import asyncio

    def open_thing() -> Any: ...

    async def opens_in_body() -> None:
        with open_thing():
            pass

    async def hands_it_to_a_thread() -> None:
        def load() -> None:
            with open_thing():
                pass

        await asyncio.to_thread(load)

    async def calls_it_on_the_loop() -> None:
        def load() -> None:
            with open_thing():
                pass

        load()

    async def nested_async_counts() -> None:
        async def inner() -> None:
            with open_thing():
                pass

        await inner()

    def plain_route() -> None:
        with open_thing():
            pass

    return {fn.__name__: fn for fn in (opens_in_body, hands_it_to_a_thread, calls_it_on_the_loop, nested_async_counts, plain_route)}


def test_the_event_loop_detector_follows_where_a_store_opener_runs() -> None:
    """A nested plain function handed to ``asyncio.to_thread`` runs in a worker thread; one
    the async body calls directly, or a nested ``async`` function, runs on the loop."""
    found = {name: _opens_store_on_loop(fn) for name, fn in _detector_cases().items()}
    assert found == {
        "opens_in_body": True,
        "hands_it_to_a_thread": False,
        "calls_it_on_the_loop": True,
        "nested_async_counts": True,
        "plain_route": False,
    }
