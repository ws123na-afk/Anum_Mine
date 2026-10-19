"""HTTP routes of calendar sync (docs/calendar.md#sync). The logic lives in ``calendar_sync.py``.

Permissions: seeing the sync status needs ``calendar:read``; connecting, disconnecting and
"Sync now" need ``calendar:manage`` (owners); asking to invite attendees needs the right to
change the event (its creator or an owner), and the invitation itself waits for an owner's
approval with the payload hash.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .audit import AuditRecord
from .authorization import Permission
from .calendar_api import MANAGE_DENIED as EVENT_MANAGE_DENIED
from .calendar_api import _can_manage, open_calendar_store
from .calendar_providers import PROVIDER_NAMES, CalendarProvider
from .calendar_sync import (
    SEND_INVITATIONS_TOOL,
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
    provider_scopes,
    redirect_uri,
    sync_connection,
    web_url,
)
from .dependencies import has_permission, repository_context, require_permission, tenant_context
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
    created_at: str


class SyncSetup(BaseModel):
    providers: list[ProviderSetup]
    redirect_uri: str
    can_manage: bool
    connections: list[ConnectionView]


class ConnectStartResponse(BaseModel):
    authorization_url: str
    expires_at: str


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
        created_at=connection.created_at.isoformat(),
    )


def _views(context: TenantContext) -> list[ConnectionView]:
    with open_sync_store(context) as store:
        connections = store.list_connections()
    with open_calendar_store(context) as events:
        return [_view(c, events.count_linked(c.id)) for c in connections]


def _state_token(pending: CalendarOAuthPending) -> str:
    from .secret_box import secret_cipher

    payload = {"t": pending.tenant_id, "w": pending.workspace_id, "u": pending.user_id, "n": pending.nonce, "k": "calendar"}
    return secret_cipher().encrypt(json.dumps(payload, separators=(",", ":")))


# -- setup and connecting -------------------------------------------------------------------


@router.get("", response_model=SyncSetup)
async def get_sync(context: TenantContext = Depends(tenant_context)) -> SyncSetup:
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
async def start_connect(
    provider: CalendarProvider,
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
    _audit(repository, context, "calendar.connect.started", pending.nonce, metadata={"provider": provider.value})
    return ConnectStartResponse(
        authorization_url=client.authorization_url(_state_token(pending), challenge), expires_at=pending.expires_at.isoformat()
    )


def _web_return(**params: str) -> RedirectResponse:
    return RedirectResponse(f"{web_url()}/?{urlencode(params)}#calendar", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/oauth/callback", include_in_schema=True)
async def oauth_callback(
    state: str = Query(default="", max_length=2000),
    code: str | None = Query(default=None, max_length=4000),
    error: str | None = Query(default=None, max_length=200),
    error_description: str | None = Query(default=None, max_length=500),
) -> RedirectResponse:
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
    except (SecretDecryptionError, ValueError, KeyError, TypeError):
        return _web_return(calendar_connect_error="invalid_state")
    with open_sync_store(context) as store:
        pending = store.get_pending(nonce)
        if pending is None or pending.code_enc is not None or pending.error is not None:
            return _web_return(calendar_connect_error="unknown_request")
        if error or not code:
            pending.error = (error_description or error or "no authorization code")[:300]
        else:
            pending.code_enc = secret_cipher().encrypt(code)
        store.save_pending(pending)
    return _web_return(calendar_connect=nonce)


@router.post("/oauth/complete", response_model=ConnectionView, status_code=status.HTTP_201_CREATED)
async def complete_connect(
    payload: ConnectComplete,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ConnectionView:
    _require(context, Permission.CALENDAR_MANAGE, MANAGE_DENIED)
    from .secret_box import secret_cipher

    cipher = secret_cipher()
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
    provider = pending.provider
    name = PROVIDER_NAMES[provider]
    if pending.expires_at < utc_now():
        raise HTTPException(status_code=410, detail="The connection request expired. Start again.")
    if pending.error:
        _audit(repository, context, "calendar.connect.denied", pending.nonce, "denied", {"provider": provider.value})
        raise HTTPException(status_code=400, detail=f"{name} did not grant access: {pending.error}")
    client = provider_client(provider)
    try:
        grant = await client.exchange_code(
            cipher.decrypt(pending.code_enc or ""),
            cipher.decrypt(pending.code_verifier_enc) if pending.code_verifier_enc else None,
        )
        account = await client.account(grant.access_token)
    except MailProviderError as exc:
        _audit(repository, context, "calendar.connect.failed", pending.nonce, "failure", {"provider": provider.value, "status": exc.status})
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
    try:
        connection, _ = await sync_connection(connection.id, context, force=True)
    except CalendarSyncError:
        pass
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
    repository: AnumRepository = Depends(repository_context),
) -> SyncResult:
    _require(context, Permission.CALENDAR_MANAGE, MANAGE_DENIED)
    _connection_or_404(context, connection_id)
    try:
        connection, summary = await sync_connection(connection_id, context, force=True)
    except CalendarSyncError as exc:
        _audit(repository, context, "calendar.sync.run", connection_id, "failure", {"status": exc.status})
        raise _http(exc) from None
    _audit(
        repository, context, "calendar.sync.run", connection.id,
        "success" if connection.last_sync_status == "ok" else "failure",
        {"provider": connection.provider.value, **summary.model_dump()},
    )
    with open_calendar_store(context) as events:
        return SyncResult(connection=_view(connection, events.count_linked(connection.id)), summary=summary.model_dump())


@router.delete("/connections/{connection_id}", response_model=DisconnectResult)
async def disconnect(
    connection_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> DisconnectResult:
    """Revoke ANUM's grant at the provider and delete the connection's tokens. The events
    stay in ANUM as ANUM-only events; nothing is deleted at the provider."""
    _require(context, Permission.CALENDAR_MANAGE, MANAGE_DENIED)
    connection = _connection_or_404(context, connection_id)
    revoked = False
    if connection.access_token_enc or connection.refresh_token_enc:
        from .secret_box import SecretDecryptionError, secret_cipher

        try:
            token = secret_cipher().decrypt(connection.refresh_token_enc or connection.access_token_enc or "")
            revoked = await provider_client(connection.provider).revoke(token)
        except (MailProviderError, SecretDecryptionError, ValueError):
            revoked = False
    with open_calendar_store(context) as events:
        kept = events.unlink(connection_id)
    with open_sync_store(context) as store:
        store.delete_connection(connection_id)
    _audit(repository, context, "calendar.connection.deleted", connection_id, metadata={"provider": connection.provider.value, "revoked": revoked, "events_kept": kept})
    name = PROVIDER_NAMES[connection.provider]
    detail = (
        f"{name} is disconnected and ANUM's access was revoked." if revoked
        else f"{name} is disconnected. ANUM could not revoke its access there; remove ANUM under your account's connected apps."
    )
    return DisconnectResult(revoked_at_provider=revoked, events_kept=kept, detail=detail + " The events stay in ANUM.")


# -- invitations ----------------------------------------------------------------------------------


@invitations_router.post("/{event_id}/invitations", response_model=InvitationResult, status_code=status.HTTP_202_ACCEPTED)
async def request_invitations(
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
