"""HTTP routes of calendar sync (docs/calendar.md#sync). The logic lives in ``calendar_sync.py``.

Permissions: seeing the sync status needs ``calendar:read``; connecting, disconnecting and
"Sync now" need ``calendar:manage`` (owners); asking to invite attendees needs the right to
change the event (its creator or an owner), and the invitation itself waits for an owner's
approval with the payload hash.
"""

from __future__ import annotations

import asyncio

import json
import logging
from datetime import timedelta
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from .audit import AuditRecord
from .authorization import Permission
from .calendar_api import MANAGE_DENIED as EVENT_MANAGE_DENIED
from .calendar_api import CalendarEvent, EventView, _can_manage, open_calendar_store
from .calendar_providers import PROVIDER_NAMES, CalendarProvider, valid_calendar_id
from .calendar_sync import (
    MAX_EXTRA_CALENDARS,
    SEND_INVITATIONS_TOOL,
    CalendarChoice,
    CalendarConnection,
    CalendarOAuthPending,
    CalendarSyncError,
    ConnectionStatus,
    calendar_settings,
    calendar_tool_registry,
    default_connection,
    invitation_arguments,
    open_sync_store,
    provider_client,
    provider_problems,
    connection_token,
    provider_scopes,
    redirect_uri,
    remove_calendar_copies,
    retry_push,
    sync_connection,
    web_url,
)
from .dependencies import UnitsOfWork, has_permission, repository_context, require_permission, run_in_unit, tenant_context, units_of_work
from .email_providers import MailProviderError, pkce_pair
from .repository import AnumRepository
from .schemas import Approval, TenantContext, new_id, utc_now

logger = logging.getLogger("anum.calendar")

router = APIRouter(prefix="/api/v1/calendar/sync", tags=["calendar"])
invitations_router = APIRouter(prefix="/api/v1/calendar/events", tags=["calendar"])

READ_DENIED = "Seeing the calendar needs a viewer, member or owner role in this workspace."
MANAGE_DENIED = "Only workspace owners can connect, disconnect or sync calendars."


def _require(context: TenantContext, permission: Permission, detail: str) -> None:
    try:
        require_permission(context, permission)
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code, detail=detail) from None


def _http(exc: CalendarSyncError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


def _audit(
    repository: AnumRepository, context: TenantContext, action: str, target: str, outcome: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action=action, target=target, outcome=outcome, correlation_id=target, created_at=utc_now(), metadata=metadata or {},
        )
    )


# -- schemas ------------------------------------------------------------------------------


class ProviderSetup(BaseModel):
    provider: CalendarProvider
    name: str
    configured: bool
    missing: list[str]
    scopes: list[str]


class ConnectionView(BaseModel):
    id: str
    provider: CalendarProvider
    provider_name: str
    email: str
    display_name: str | None
    connected_by: str
    status: ConnectionStatus
    # What the person should do when the status is not "connected" (empty otherwise).
    fix: str
    last_sync_at: str | None
    last_sync_status: str | None
    last_error: str | None
    last_summary: dict[str, Any]
    events_synced: int
    # The account's calendars as last listed and which sync (empty until someone opens the choice).
    calendars: list[CalendarChoice]
    created_at: str


class SyncSetup(BaseModel):
    providers: list[ProviderSetup]
    redirect_uri: str
    can_manage: bool
    connections: list[ConnectionView]


class ConnectStartRequest(BaseModel):
    # "phone": the provider's sign-in returns to the app (``calendar_settings.app_return_url``)
    # instead of the web; the app then finishes the request it started.
    client: Literal["web", "phone"] = "web"


class ConnectStartResponse(BaseModel):
    authorization_url: str
    expires_at: str
    # The request's id, for ``POST /oauth/complete`` from the app that started it.
    request: str


class CalendarChoices(BaseModel):
    connection: str
    calendars: list[CalendarChoice]
    max_extra: int


class CalendarSelection(BaseModel):
    # Ids of the calendars to sync besides the primary one (which always syncs).
    selected: list[str] = Field(default_factory=list, max_length=MAX_EXTRA_CALENDARS)


class ConnectComplete(BaseModel):
    connection: str = Field(min_length=8, max_length=80)


class SyncResult(BaseModel):
    connection: ConnectionView
    summary: dict[str, Any]


class DisconnectResult(BaseModel):
    revoked_at_provider: bool
    events_kept: int
    detail: str


class InvitationResult(BaseModel):
    status: Literal["waiting_approval"] = "waiting_approval"
    summary: str
    approval: Approval
    attendees: list[str]


def _fix(connection: CalendarConnection) -> str:
    name = PROVIDER_NAMES[connection.provider]
    if connection.status == ConnectionStatus.REAUTH_REQUIRED:
        return f"{name} no longer accepts ANUM's access. Press Connect {name} again and sign in; events already synced stay."
    if connection.status == ConnectionStatus.ERROR:
        return f"The last sync failed. Press Sync now to try again; if it keeps failing, disconnect and connect {name} again."
    return ""


def _view(connection: CalendarConnection, linked: int) -> ConnectionView:
    return ConnectionView(
        id=connection.id,
        provider=connection.provider,
        provider_name=PROVIDER_NAMES[connection.provider],
        email=connection.email,
        display_name=connection.display_name,
        connected_by=connection.connected_by,
        status=connection.status,
        fix=_fix(connection),
        last_sync_at=connection.last_sync_at.isoformat() if connection.last_sync_at else None,
        last_sync_status=connection.last_sync_status,
        last_error=connection.last_error,
        last_summary=connection.last_summary,
        events_synced=linked,
        calendars=connection.calendars,
        created_at=connection.created_at.isoformat(),
    )


def _views(context: TenantContext) -> list[ConnectionView]:
    with open_sync_store(context) as store:
        connections = store.list_connections()
    with open_calendar_store(context) as events:
        return [_view(c, events.count_linked(c.id)) for c in connections]


def _state_token(pending: CalendarOAuthPending, client: str = "web") -> str:
    from .secret_box import secret_cipher

    payload = {"t": pending.tenant_id, "w": pending.workspace_id, "u": pending.user_id, "n": pending.nonce, "k": "calendar"}
    if client == "phone":
        payload["c"] = "phone"
    return secret_cipher().encrypt(json.dumps(payload, separators=(",", ":")))


# -- setup and connecting -------------------------------------------------------------------


@router.get("", response_model=SyncSetup)
def get_sync(context: TenantContext = Depends(tenant_context)) -> SyncSetup:
    """Which providers the operator set up (never a secret), what is missing and the connections."""
    _require(context, Permission.CALENDAR_READ, READ_DENIED)
    providers = []
    for provider in CalendarProvider:
        problems = provider_problems(provider)
        providers.append(
            ProviderSetup(
                provider=provider, name=PROVIDER_NAMES[provider], configured=not problems, missing=problems,
                scopes=provider_scopes(provider),
            )
        )
    return SyncSetup(
        providers=providers,
        redirect_uri=redirect_uri(),
        can_manage=has_permission(context, Permission.CALENDAR_MANAGE),
        connections=_views(context),
    )


@router.post("/oauth/{provider}/start", response_model=ConnectStartResponse)
def start_connect(
    provider: CalendarProvider,
    payload: ConnectStartRequest | None = None,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ConnectStartResponse:
    """The provider's consent URL. The browser goes there; the provider comes back to the callback."""
    _require(context, Permission.CALENDAR_MANAGE, MANAGE_DENIED)
    problems = provider_problems(provider)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{PROVIDER_NAMES[provider]} is not set up on this server. Missing: {', '.join(problems)} (docs/calendar.md#setup).",
        )
    from .secret_box import secret_cipher

    client = provider_client(provider)
    verifier, challenge = pkce_pair() if client.uses_pkce else (None, None)
    now = utc_now()
    pending = CalendarOAuthPending(
        nonce=new_id("coc"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        provider=provider,
        code_verifier_enc=secret_cipher().encrypt(verifier) if verifier else None,
        created_at=now,
        expires_at=now + timedelta(seconds=calendar_settings.oauth_state_ttl_seconds),
    )
    with open_sync_store(context) as store:
        store.add_pending(pending)
    where = payload.client if payload else "web"
    _audit(repository, context, "calendar.connect.started", pending.nonce, metadata={"provider": provider.value, "client": where})
    return ConnectStartResponse(
        authorization_url=client.authorization_url(_state_token(pending, where), challenge), expires_at=pending.expires_at.isoformat(),
        request=pending.nonce,
    )


def _web_return(**params: str) -> RedirectResponse:
    return RedirectResponse(f"{web_url()}/?{urlencode(params)}#calendar", status_code=status.HTTP_303_SEE_OTHER)


_APP_RETURN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer"><meta http-equiv="refresh" content="0;url={link}">
<title>Back to ANUM</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;margin:0;padding:32px 20px;color:#14211b;background:#f6f4ee}}
main{{max-width:28rem;margin:0 auto}}a{{display:inline-block;margin-top:16px;padding:12px 20px;border-radius:999px;background:#1f5c45;color:#fff;text-decoration:none}}</style>
</head><body><main><h1>{title}</h1><p>{text}</p><p lang="ar" dir="rtl">{text_ar}</p><a href="{link}">Open ANUM</a></main></body></html>"""


def _app_return(**params: str) -> HTMLResponse:
    """The provider's sign-in finished in the phone's browser: hand over to the app through its own
    scheme (an automatic redirect, and a button for browsers that ask first). The app finishes the
    request it started; nothing here grants anything (docs/calendar.md#connecting-on-the-phone)."""
    from html import escape

    link = escape(f"{calendar_settings.app_return_url}?{urlencode(params)}", quote=True)
    failed = "calendar_connect_error" in params
    page = _APP_RETURN_PAGE.format(
        link=link,
        title="Calendar sign-in did not finish" if failed else "Calendar sign-in finished",
        text="Go back to the ANUM app to see what happened." if failed else "Go back to the ANUM app to finish connecting.",
        text_ar="ارجع إلى تطبيق ANUM لترى ما حدث." if failed else "ارجع إلى تطبيق ANUM لإكمال الربط.",
    )
    return HTMLResponse(page, headers={"cache-control": "no-store", "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'"})


@router.get("/oauth/callback", include_in_schema=True, response_model=None)
def oauth_callback(
    state: str = Query(default="", max_length=2000),
    code: str | None = Query(default=None, max_length=4000),
    error: str | None = Query(default=None, max_length=200),
    error_description: str | None = Query(default=None, max_length=500),
) -> Response:
    """Where the provider sends the browser back. Unauthenticated by design: it only stores
    the code (encrypted) on the pending request named by the encrypted ``state`` and sends
    the browser to the web app, which finishes the connection as the signed-in person
    (``POST /oauth/complete``). A code delivered to someone else's browser is useless.
    """
    from .secret_box import SecretDecryptionError, secret_cipher

    try:
        data = json.loads(secret_cipher().decrypt_fresh(state, calendar_settings.oauth_state_ttl_seconds))
        if data.get("k") != "calendar":
            raise ValueError("not a calendar state")
        context = TenantContext(tenant_id=str(data["t"]), workspace_id=str(data["w"]), user_id=str(data["u"]), roles=[])
        nonce = str(data["n"])
        back = _app_return if data.get("c") == "phone" else _web_return
    except (SecretDecryptionError, ValueError, KeyError, TypeError):
        return _web_return(calendar_connect_error="invalid_state")
    with open_sync_store(context) as store:
        pending = store.get_pending(nonce)
        if pending is None or pending.code_enc is not None or pending.error is not None:
            return back(calendar_connect_error="unknown_request")
        if error or not code:
            pending.error = (error_description or error or "no authorization code")[:300]
        else:
            pending.code_enc = secret_cipher().encrypt(code)
        store.save_pending(pending)
    return back(calendar_connect=nonce)


@router.post("/oauth/complete", response_model=ConnectionView, status_code=status.HTTP_201_CREATED)
async def complete_connect(
    payload: ConnectComplete,
    context: TenantContext = Depends(tenant_context),
    units: UnitsOfWork = Depends(units_of_work),
) -> ConnectionView:
    _require(context, Permission.CALENDAR_MANAGE, MANAGE_DENIED)
    from .secret_box import secret_cipher

    cipher = secret_cipher()

    def claim(_repository: AnumRepository) -> Any:
        with open_sync_store(context) as store:
            pending = store.get_pending(payload.connection)
            if pending is None:
                raise HTTPException(status_code=404, detail="This connection request was not found or was already used. Start again.")
            if pending.user_id != context.user_id:
                raise HTTPException(status_code=403, detail="This connection was started by someone else; start your own.")
            if pending.code_enc is None and pending.error is None:
                raise HTTPException(status_code=409, detail="The provider has not sent you back yet. Finish the sign-in in the provider's window.")
            # Single use: consumed before the code is exchanged.
            store.delete_pending(pending.nonce)
        return pending

    # Database work runs in worker threads, each step in its own short unit of work, and no
    # transaction stays open while the provider answers (docs/realtime.md#event-loop).
    pending = await run_in_unit(units, context, claim)
    provider = pending.provider
    name = PROVIDER_NAMES[provider]
    if pending.expires_at < utc_now():
        raise HTTPException(status_code=410, detail="The connection request expired. Start again.")
    if pending.error:
        await run_in_unit(units, context, _audit, context, "calendar.connect.denied", pending.nonce, "denied", {"provider": provider.value})
        raise HTTPException(status_code=400, detail=f"{name} did not grant access: {pending.error}")
    client = provider_client(provider)
    try:
        grant = await client.exchange_code(
            cipher.decrypt(pending.code_enc or ""),
            cipher.decrypt(pending.code_verifier_enc) if pending.code_verifier_enc else None,
        )
        account = await client.account(grant.access_token)
    except MailProviderError as exc:
        await run_in_unit(units, context, _audit, context, "calendar.connect.failed", pending.nonce, "failure", {"provider": provider.value, "status": exc.status})
        raise HTTPException(status_code=502, detail=f"Connecting failed: {exc.message}") from None
    if not grant.refresh_token:
        raise HTTPException(
            status_code=502,
            detail=(
                f"{name} did not return a refresh token, so ANUM could not keep the calendar in sync. "
                "Remove ANUM's access in your account's security settings and connect again."
            ),
        )
    now = utc_now()

    def keep(repository: AnumRepository) -> CalendarConnection:
        with open_sync_store(context) as store:
            connection = store.find_connection(provider, account.email)
            created = connection is None
            if connection is None:
                connection = CalendarConnection(
                    id=new_id("cal"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, provider=provider,
                    email=account.email, connected_by=context.user_id, created_at=now, updated_at=now,
                )
                store.add_connection(connection)
            connection.display_name = account.display_name
            connection.scopes = list(grant.scopes or client.scopes)
            connection.access_token_enc = cipher.encrypt(grant.access_token)
            connection.refresh_token_enc = cipher.encrypt(grant.refresh_token)
            connection.access_expires_at = grant.expires_at
            connection.status = ConnectionStatus.CONNECTED
            connection.last_error = None
            connection.updated_at = now
            store.save_connection(connection)
        _audit(
            repository, context, "calendar.connection.created" if created else "calendar.connection.reconnected", connection.id,
            metadata={"provider": provider.value},
        )
        return connection

    connection = await run_in_unit(units, context, keep)
    try:
        connection, _ = await sync_connection(connection.id, context, force=True)
    except CalendarSyncError:
        pass
    return await asyncio.to_thread(_view_counted, context, connection)


def _view_counted(context: TenantContext, connection: CalendarConnection) -> ConnectionView:
    with open_calendar_store(context) as events:
        return _view(connection, events.count_linked(connection.id))


def _connection_or_404(context: TenantContext, connection_id: str) -> CalendarConnection:
    with open_sync_store(context) as store:
        connection = store.get_connection(connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Calendar connection not found")
    return connection


@router.post("/connections/{connection_id}/sync", response_model=SyncResult)
async def sync_now(
    connection_id: str,
    context: TenantContext = Depends(tenant_context),
    units: UnitsOfWork = Depends(units_of_work),
) -> SyncResult:
    _require(context, Permission.CALENDAR_MANAGE, MANAGE_DENIED)
    await run_in_unit(units, context, lambda _repository: _connection_or_404(context, connection_id))
    try:
        connection, summary = await sync_connection(connection_id, context, force=True)
    except CalendarSyncError as exc:
        await run_in_unit(units, context, _audit, context, "calendar.sync.run", connection_id, "failure", {"status": exc.status})
        raise _http(exc) from None

    def finish(repository: AnumRepository) -> SyncResult:
        _audit(
            repository, context, "calendar.sync.run", connection.id,
            "success" if connection.last_sync_status == "ok" else "failure",
            {"provider": connection.provider.value, **summary.model_dump()},
        )
        return SyncResult(connection=_view_counted(context, connection), summary=summary.model_dump())

    return await run_in_unit(units, context, finish)


@router.delete("/connections/{connection_id}", response_model=DisconnectResult)
async def disconnect(
    connection_id: str,
    context: TenantContext = Depends(tenant_context),
    units: UnitsOfWork = Depends(units_of_work),
) -> DisconnectResult:
    """Revoke ANUM's grant at the provider and delete the connection's tokens. The events
    stay in ANUM as ANUM-only events; nothing is deleted at the provider."""
    _require(context, Permission.CALENDAR_MANAGE, MANAGE_DENIED)
    connection = await run_in_unit(units, context, lambda _repository: _connection_or_404(context, connection_id))
    revoked = False
    if connection.access_token_enc or connection.refresh_token_enc:
        from .secret_box import SecretDecryptionError, secret_cipher

        try:
            token = secret_cipher().decrypt(connection.refresh_token_enc or connection.access_token_enc or "")
            revoked = await provider_client(connection.provider).revoke(token)
        except (MailProviderError, SecretDecryptionError, ValueError):
            revoked = False

    def forget(repository: AnumRepository) -> int:
        with open_calendar_store(context) as events:
            kept = events.unlink(connection_id)
        with open_sync_store(context) as store:
            store.delete_connection(connection_id)
        _audit(repository, context, "calendar.connection.deleted", connection_id, metadata={"provider": connection.provider.value, "revoked": revoked, "events_kept": kept})
        return kept

    kept = await run_in_unit(units, context, forget)
    name = PROVIDER_NAMES[connection.provider]
    detail = (
        f"{name} is disconnected and ANUM's access was revoked." if revoked
        else f"{name} is disconnected. ANUM could not revoke its access there; remove ANUM under your account's connected apps."
    )
    return DisconnectResult(revoked_at_provider=revoked, events_kept=kept, detail=detail + " The events stay in ANUM.")


# -- invitations ----------------------------------------------------------------------------------


# -- which calendars sync ------------------------------------------------------------------------


def _merge_choices(stored: list[CalendarChoice], listed: list[Any]) -> list[CalendarChoice]:
    """The provider's list now, keeping each calendar's earlier choice; the primary always syncs."""
    chosen = {c.id for c in stored if c.selected}
    return [
        CalendarChoice(id=c.id, name=c.name, primary=c.primary, selected=c.primary or c.id in chosen, can_edit=c.can_edit, color=c.color)
        for c in listed
    ]


async def _listed(context: TenantContext, connection: CalendarConnection) -> list[Any]:
    from .email_providers import MailProviderError as _ProviderError

    try:
        token = await connection_token(connection, context)
        return await provider_client(connection.provider).calendars(token)
    except _ProviderError as exc:
        name = PROVIDER_NAMES[connection.provider]
        if exc.status in (401, 403) or exc.reauth:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{name} did not let ANUM list this account's calendars. Disconnect and connect {name} again, "
                "accepting the calendar list permission, to choose calendars.",
            ) from None
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Listing the calendars failed: {exc.message}") from None
    except CalendarSyncError as exc:
        raise _http(exc) from None


def _save_choices(context: TenantContext, connection_id: str, calendars: list[CalendarChoice]) -> CalendarConnection:
    with open_sync_store(context) as store:
        stored = store.get_connection(connection_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="Calendar connection not found.")
        stored.calendars = calendars
        stored.updated_at = utc_now()
        store.save_connection(stored)
        return stored


@router.get("/connections/{connection_id}/calendars", response_model=CalendarChoices)
async def list_calendars(connection_id: str, context: TenantContext = Depends(tenant_context)) -> CalendarChoices:
    """The account's calendars, fresh from the provider, with which sync. Owners only: it asks the
    provider with the connection's access."""
    _require(context, Permission.CALENDAR_MANAGE, MANAGE_DENIED)
    connection = await asyncio.to_thread(_connection_or_404, context, connection_id)
    calendars = _merge_choices(connection.calendars, await _listed(context, connection))
    if calendars != connection.calendars:
        await asyncio.to_thread(_save_choices, context, connection.id, calendars)
    return CalendarChoices(connection=connection.id, calendars=calendars, max_extra=MAX_EXTRA_CALENDARS)


@router.put("/connections/{connection_id}/calendars", response_model=CalendarChoices)
async def choose_calendars(
    connection_id: str,
    payload: CalendarSelection,
    context: TenantContext = Depends(tenant_context),
    units: UnitsOfWork = Depends(units_of_work),
) -> CalendarChoices:
    """Choose which of the account's calendars sync besides the primary one. A calendar no longer
    chosen loses its copies in ANUM; a newly chosen one is read in the sync that follows."""
    _require(context, Permission.CALENDAR_MANAGE, MANAGE_DENIED)
    wanted = list(dict.fromkeys(payload.selected))
    if any(not valid_calendar_id(c) for c in wanted):
        raise HTTPException(status_code=422, detail="A calendar id is not valid. Reload the list and choose again.")
    connection = await asyncio.to_thread(_connection_or_404, context, connection_id)
    listed = await _listed(context, connection)
    known = {c.id: c for c in listed}
    unknown = [c for c in wanted if c not in known]
    if unknown:
        raise HTTPException(status_code=422, detail="A chosen calendar is no longer in this account. Reload the list and choose again.")
    extra = [c for c in wanted if not known[c].primary]
    if len(extra) > MAX_EXTRA_CALENDARS:
        raise HTTPException(status_code=422, detail=f"Choose at most {MAX_EXTRA_CALENDARS} calendars besides the primary one.")
    before = {c.id for c in connection.extra_calendars()}
    calendars = [
        CalendarChoice(id=c.id, name=c.name, primary=c.primary, selected=c.primary or c.id in extra, can_edit=c.can_edit, color=c.color)
        for c in listed
    ]
    dropped = sorted(before - set(extra))

    def keep(repository: AnumRepository) -> CalendarConnection:
        saved = _save_choices(context, connection.id, calendars)
        removed = remove_calendar_copies(context, connection.id, dropped)
        _audit(repository, context, "calendar.connection.calendars_chosen", connection.id,
               metadata={"provider": connection.provider.value, "extra": len(extra), "dropped": len(dropped), "copies_removed": removed})
        return saved

    saved = await run_in_unit(units, context, keep)
    if set(extra) - before:
        try:
            await sync_connection(saved.id, context, force=True)
        except CalendarSyncError:
            pass
    return CalendarChoices(connection=saved.id, calendars=calendars, max_extra=MAX_EXTRA_CALENDARS)


@invitations_router.post("/{event_id}/invitations", response_model=InvitationResult, status_code=status.HTTP_202_ACCEPTED)
def request_invitations(
    event_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> InvitationResult:
    """Ask to send the event's invitations to its attendees through the connected calendar.
    Nothing is sent: an approval with the exact content waits for an owner (docs/calendar.md#invitations)."""
    _require(context, Permission.CALENDAR_READ, READ_DENIED)
    with open_calendar_store(context) as events:
        event = events.get(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")
        if not _can_manage(context, event):
            raise HTTPException(status_code=403, detail=EVENT_MANAGE_DENIED)
    if event.status.value == "cancelled":
        raise HTTPException(status_code=409, detail="This event is cancelled. Restore it before inviting people.")
    if not any(a.email for a in event.attendees):
        raise HTTPException(status_code=422, detail="Add at least one attendee with an email address first.")
    with open_sync_store(context) as store:
        connection = store.get_connection(event.connection_id) if event.connection_id else default_connection(store)
    if connection is None:
        raise HTTPException(status_code=409, detail="Connect Google Calendar or Microsoft 365 under Calendar > Sync first; invitations go out through it.")
    if connection.status == ConnectionStatus.REAUTH_REQUIRED:
        raise HTTPException(status_code=409, detail=f"{PROVIDER_NAMES[connection.provider]} needs to be connected again before it can send invitations.")
    from .agent_tools import ToolCall
    from .email_api import _pause_for_approval

    arguments = invitation_arguments(event, connection)
    title = f"Invite attendees: {event.title}"
    people = ", ".join(arguments["attendees"][:20])
    _, approval = _pause_for_approval(
        repository, context, ToolCall(name=SEND_INVITATIONS_TOOL, arguments=arguments), title,
        f"Send the calendar invitation for \"{event.title}\" to {people} through {PROVIDER_NAMES[connection.provider]}.",
        registry=calendar_tool_registry(),
    )
    _audit(repository, context, "calendar.invitations.requested", event_id, metadata={"attendees": len(arguments["attendees"]), "approval": approval.id})
    return InvitationResult(
        summary="Waiting for an owner's approval. Nothing has been sent.", approval=approval, attendees=arguments["attendees"]
    )


# -- a failed send ------------------------------------------------------------------------------------


@invitations_router.post("/{event_id}/sync/retry", response_model=EventView)
async def retry_event_sync(
    event_id: str,
    context: TenantContext = Depends(tenant_context),
    units: UnitsOfWork = Depends(units_of_work),
) -> EventView:
    """Send an event to its connected calendar again after sends failed (docs/calendar.md#how-events-sync).
    The failure count starts again; the answer says whether it went through this time."""
    _require(context, Permission.CALENDAR_READ, READ_DENIED)

    def check(repository: AnumRepository) -> CalendarEvent:
        with open_calendar_store(context) as events:
            event = events.get(event_id)
            if event is None:
                raise HTTPException(status_code=404, detail="Event not found")
            if not _can_manage(context, event):
                raise HTTPException(status_code=403, detail=EVENT_MANAGE_DENIED)
            if not event.connection_id or event.sync_state != "pending_push":
                raise HTTPException(status_code=409, detail="This event is not waiting to be sent to a connected calendar.")
        _audit(repository, context, "calendar.event.sync_retried", event_id, metadata={"attempts": event.sync_attempts})
        return event

    await run_in_unit(units, context, check)
    connection = await asyncio.to_thread(_linked_connection, context, event_id)
    if connection is not None and connection.status == ConnectionStatus.REAUTH_REQUIRED:
        raise HTTPException(status_code=409, detail=f"{PROVIDER_NAMES[connection.provider]} needs to be connected again under Calendar > Sync before the event can be sent.")
    updated = await retry_push(context, event_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return await asyncio.to_thread(_event_view, context, updated)


def _linked_connection(context: TenantContext, event_id: str) -> CalendarConnection | None:
    with open_calendar_store(context) as events:
        event = events.get(event_id)
    if event is None or not event.connection_id:
        return None
    with open_sync_store(context) as store:
        return store.get_connection(event.connection_id)


def _event_view(context: TenantContext, event: CalendarEvent) -> EventView:
    from .calendar_api import _view

    if event.series_id is not None:
        with open_calendar_store(context) as events:
            series = events.get(event.series_id)
        if series is not None:
            return _view(context, series, occurrence_start=event.recurrence_start, override=event)
    return _view(context, event)
