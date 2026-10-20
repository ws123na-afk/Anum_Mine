"""Calendar sync: Google Calendar and Microsoft 365 connections (docs/calendar.md#sync).

What lives here (the HTTP routes are in ``calendar_sync_api.py``, the provider clients in
``calendar_providers.py``, the PostgreSQL store in ``db/calendar_sync_repository.py``):

* **Settings** (``ANUM_CALENDAR_*``): the owner's OAuth clients. A client id or secret left
  empty falls back to the inbox's (``ANUM_EMAIL_*``) so one Google or Microsoft app can serve
  both; the app then needs the calendar scopes too. Nothing is required: a provider without a
  client is shown as "not set up" with the names of the missing settings.
* **Connections**: a primary calendar connected to one workspace, with its refresh and access
  tokens encrypted by ``ANUM_SECRETS_KEY`` (``secret_box``).
* **Sync**: one pass pulls the provider's changes (Google sync token, Graph delta link),
  applies them to ANUM events, then pushes the ANUM events waiting to be sent. Run by the
  background scheduler (PostgreSQL only) as the application role inside each connection's
  tenant and workspace context, and by a person with "Sync now".
* **Conflict rule**: see ``apply_remote``.
* **Invitations**: ANUM never gives the provider an attendee list on its own; people are
  invited only by ``calendar.send_invitations`` after an owner approves the exact content
  (payload hash). Everything a provider returns is untrusted data (threat model G3).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from .calendar_api import (
    MAX_ATTENDEES,
    Attendee,
    AttendeeResponse,
    CalendarEvent,
    EventKind,
    EventStatus,
    open_calendar_store,
)
from .calendar_providers import (
    _MICROSOFT_TENANT,
    PROVIDER_NAMES,
    CalendarProvider,
    CalendarProviderClient,
    GoogleCalendarClient,
    MicrosoftCalendarClient,
    OutgoingEvent,
    RemoteEvent,
)
from .email_providers import InvalidReferenceError, MailProviderError, OAuthClient
from .schemas import RiskLevel, TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store

logger = logging.getLogger("anum.calendar")

SEND_INVITATIONS_TOOL = "calendar.send_invitations"
SYNC_LEASE = timedelta(minutes=3)
MAX_PUSH_PER_PASS = 100
MAX_EVENT_DURATION = timedelta(days=14)
# Events organised by someone else carry this source reference: they follow the provider.
GUEST_REF = "attendee"


class CalendarSettings(BaseSettings):
    """``ANUM_CALENDAR_*``. Client secrets are the owner's, set on the server only."""

    google_client_id: str = ""
    google_client_secret: SecretStr | None = None
    microsoft_client_id: str = ""
    microsoft_client_secret: SecretStr | None = None
    microsoft_tenant: str = ""
    # Empty: the inbox's ANUM_EMAIL_PUBLIC_API_URL and ANUM_EMAIL_WEB_URL.
    public_api_url: str = ""
    web_url: str = ""
    # Background sync of connected calendars (PostgreSQL only); 0 turns it off.
    sync_interval_seconds: float = Field(default=300, ge=0, le=86400)
    days_back: int = Field(default=30, ge=1, le=365)
    days_ahead: int = Field(default=365, ge=7, le=730)
    oauth_state_ttl_seconds: int = Field(default=900, ge=60, le=3600)

    model_config = SettingsConfigDict(env_prefix="ANUM_CALENDAR_", env_file=".env", extra="ignore", hide_input_in_errors=True)


calendar_settings = CalendarSettings()
# Tests replace this with an httpx.MockTransport; production uses the network.
calendar_transport: httpx.AsyncBaseTransport | None = None


def _email():  # type: ignore[no-untyped-def]
    from .email_inbox import email_settings

    return email_settings


def api_url(cfg: CalendarSettings | None = None) -> str:
    cfg = cfg or calendar_settings
    return (cfg.public_api_url or _email().public_api_url).rstrip("/")


def web_url(cfg: CalendarSettings | None = None) -> str:
    cfg = cfg or calendar_settings
    return (cfg.web_url or _email().web_url).rstrip("/")


def redirect_uri(cfg: CalendarSettings | None = None) -> str:
    return f"{api_url(cfg)}/api/v1/calendar/sync/oauth/callback"


def _secret(value: SecretStr | None) -> str:
    return value.get_secret_value().strip() if value is not None else ""


def _client_id(provider: CalendarProvider, cfg: CalendarSettings) -> str:
    if provider == CalendarProvider.GOOGLE:
        return (cfg.google_client_id or _email().google_client_id).strip()
    return (cfg.microsoft_client_id or _email().microsoft_client_id).strip()


def _client_secret(provider: CalendarProvider, cfg: CalendarSettings) -> str:
    if provider == CalendarProvider.GOOGLE:
        return _secret(cfg.google_client_secret) or _secret(_email().google_client_secret)
    return _secret(cfg.microsoft_client_secret) or _secret(_email().microsoft_client_secret)


def _tenant(cfg: CalendarSettings) -> str:
    return cfg.microsoft_tenant or _email().microsoft_tenant


def provider_problems(provider: CalendarProvider, cfg: CalendarSettings | None = None) -> list[str]:
    """Names of missing or invalid settings for ``provider`` (empty when it is set up)."""
    cfg = cfg or calendar_settings
    name = "GOOGLE" if provider == CalendarProvider.GOOGLE else "MICROSOFT"
    problems = []
    if not _client_id(provider, cfg):
        problems.append(f"ANUM_CALENDAR_{name}_CLIENT_ID")
    if not _client_secret(provider, cfg):
        problems.append(f"ANUM_CALENDAR_{name}_CLIENT_SECRET")
    if provider == CalendarProvider.MICROSOFT and _tenant(cfg) and not _MICROSOFT_TENANT.fullmatch(_tenant(cfg)):
        problems.append("ANUM_CALENDAR_MICROSOFT_TENANT")
    if not re.match(r"^https?://[^\s/]+", api_url(cfg)):
        problems.append("ANUM_CALENDAR_PUBLIC_API_URL")
    if not re.match(r"^https?://[^\s/]+", web_url(cfg)):
        problems.append("ANUM_CALENDAR_WEB_URL")
    return problems


def provider_client(provider: CalendarProvider, cfg: CalendarSettings | None = None) -> CalendarProviderClient:
    cfg = cfg or calendar_settings
    oauth = OAuthClient(_client_id(provider, cfg), _client_secret(provider, cfg), redirect_uri(cfg))
    if provider == CalendarProvider.GOOGLE:
        return GoogleCalendarClient(oauth, calendar_transport)
    return MicrosoftCalendarClient(oauth, calendar_transport, tenant=_tenant(cfg) or "common")


def provider_scopes(provider: CalendarProvider) -> list[str]:
    return list((GoogleCalendarClient if provider == CalendarProvider.GOOGLE else MicrosoftCalendarClient).scopes)


# -- models -------------------------------------------------------------------------------


class ConnectionStatus(StrEnum):
    CONNECTED = "connected"
    ERROR = "error"
    REAUTH_REQUIRED = "reauth_required"


class CalendarConnection(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    provider: CalendarProvider
    email: str
    display_name: str | None = None
    connected_by: str
    status: ConnectionStatus = ConnectionStatus.CONNECTED
    scopes: list[str] = Field(default_factory=list)
    access_token_enc: str | None = None
    refresh_token_enc: str | None = None
    access_expires_at: datetime | None = None
    sync_cursor: str | None = None
    last_sync_at: datetime | None = None
    last_sync_status: str | None = None
    last_error: str | None = None
    last_summary: dict[str, Any] = Field(default_factory=dict)
    sync_lease_until: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CalendarOAuthPending(BaseModel):
    nonce: str
    tenant_id: str
    workspace_id: str
    user_id: str
    provider: CalendarProvider
    code_verifier_enc: str | None = None
    code_enc: str | None = None
    error: str | None = None
    created_at: datetime
    expires_at: datetime


class SyncStoreProtocol(Protocol):
    def list_connections(self) -> list[CalendarConnection]: ...
    def get_connection(self, connection_id: str) -> CalendarConnection | None: ...
    def find_connection(self, provider: CalendarProvider, email: str) -> CalendarConnection | None: ...
    def add_connection(self, connection: CalendarConnection) -> CalendarConnection: ...
    def save_connection(self, connection: CalendarConnection) -> CalendarConnection: ...
    def delete_connection(self, connection_id: str) -> None: ...
    def claim_sync(self, connection_id: str, now: datetime, until: datetime) -> bool: ...
    def add_pending(self, pending: CalendarOAuthPending) -> CalendarOAuthPending: ...
    def get_pending(self, nonce: str) -> CalendarOAuthPending | None: ...
    def save_pending(self, pending: CalendarOAuthPending) -> CalendarOAuthPending: ...
    def delete_pending(self, nonce: str) -> None: ...


class InMemorySyncBackend:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self.connections: dict[str, CalendarConnection] = {}
        self.pending: dict[str, CalendarOAuthPending] = {}
        self.lock = RLock()

    def clear(self) -> None:
        with self.lock:
            self.connections.clear()
            self.pending.clear()


class MemorySyncScope:
    def __init__(self, backend: InMemorySyncBackend, context: TenantContext) -> None:
        self.b = backend
        self.scope = (context.tenant_id, context.workspace_id)

    def _mine(self, item: Any) -> bool:
        return (item.tenant_id, item.workspace_id) == self.scope

    def list_connections(self) -> list[CalendarConnection]:
        with self.b.lock:
            items = [c.model_copy(deep=True) for c in self.b.connections.values() if self._mine(c)]
        return sorted(items, key=lambda c: (c.created_at, c.id))

    def get_connection(self, connection_id: str) -> CalendarConnection | None:
        with self.b.lock:
            found = self.b.connections.get(connection_id)
            return found.model_copy(deep=True) if found is not None and self._mine(found) else None

    def find_connection(self, provider: CalendarProvider, email: str) -> CalendarConnection | None:
        return next((c for c in self.list_connections() if c.provider == provider and c.email == email), None)

    def add_connection(self, connection: CalendarConnection) -> CalendarConnection:
        with self.b.lock:
            self.b.connections[connection.id] = connection.model_copy(deep=True)
        return connection

    def save_connection(self, connection: CalendarConnection) -> CalendarConnection:
        with self.b.lock:
            if connection.id not in self.b.connections or not self._mine(self.b.connections[connection.id]):
                raise KeyError(connection.id)
            self.b.connections[connection.id] = connection.model_copy(deep=True)
        return connection

    def delete_connection(self, connection_id: str) -> None:
        with self.b.lock:
            found = self.b.connections.get(connection_id)
            if found is not None and self._mine(found):
                del self.b.connections[connection_id]

    def claim_sync(self, connection_id: str, now: datetime, until: datetime) -> bool:
        with self.b.lock:
            found = self.b.connections.get(connection_id)
            if found is None or not self._mine(found):
                return False
            if found.sync_lease_until is not None and found.sync_lease_until > now:
                return False
            found.sync_lease_until = until
            return True

    def add_pending(self, pending: CalendarOAuthPending) -> CalendarOAuthPending:
        with self.b.lock:
            self.b.pending[pending.nonce] = pending.model_copy(deep=True)
        return pending

    def get_pending(self, nonce: str) -> CalendarOAuthPending | None:
        with self.b.lock:
            found = self.b.pending.get(nonce)
            return found.model_copy(deep=True) if found is not None and self._mine(found) else None

    def save_pending(self, pending: CalendarOAuthPending) -> CalendarOAuthPending:
        with self.b.lock:
            self.b.pending[pending.nonce] = pending.model_copy(deep=True)
        return pending

    def delete_pending(self, nonce: str) -> None:
        with self.b.lock:
            found = self.b.pending.get(nonce)
            if found is not None and self._mine(found):
                del self.b.pending[nonce]


sync_backend = InMemorySyncBackend()


@contextmanager
def open_sync_store(context: TenantContext) -> Iterator[SyncStoreProtocol]:
    """One unit of work in the caller's tenant and workspace scope (RLS with PostgreSQL)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.calendar_sync_repository import SqlAlchemyCalendarSyncStore

        return SqlAlchemyCalendarSyncStore(session, context)

    with open_scoped_store(context, MemorySyncScope(sync_backend, context), sql_store) as store:
        yield store


class CalendarSyncError(Exception):
    """A refusal the API turns into an HTTP error (``status``) with a message to show."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(message)


def _failure(exc: MailProviderError) -> CalendarSyncError:
    if isinstance(exc, InvalidReferenceError):
        return CalendarSyncError(exc.status, exc.message)
    if exc.reauth:
        return CalendarSyncError(409, f"{exc.message} Connect the calendar again under Calendar > Sync.")
    return CalendarSyncError(502, exc.message)


# -- tokens ---------------------------------------------------------------------------------


def _cipher():  # type: ignore[no-untyped-def]
    from .secret_box import secret_cipher

    return secret_cipher()


def _mark_reauth(connection: CalendarConnection, context: TenantContext, reason: str) -> None:
    with open_sync_store(context) as store:
        stored = store.get_connection(connection.id)
        if stored is None:
            return
        stored.status = ConnectionStatus.REAUTH_REQUIRED
        stored.last_error = reason[:500]
        stored.last_sync_status = "error"
        stored.updated_at = utc_now()
        store.save_connection(stored)
    connection.status = ConnectionStatus.REAUTH_REQUIRED
    connection.last_error = reason[:500]


async def connection_token(connection: CalendarConnection, context: TenantContext) -> str:
    """A current access token, refreshed (and stored) when it is about to expire."""
    from .secret_box import SecretDecryptionError

    now = utc_now()
    cipher = _cipher()
    if connection.access_token_enc and connection.access_expires_at and connection.access_expires_at > now + timedelta(seconds=60):
        try:
            return cipher.decrypt(connection.access_token_enc)
        except SecretDecryptionError:
            pass
    if not connection.refresh_token_enc:
        _mark_reauth(connection, context, "No refresh token is stored; connect the calendar again.")
        raise CalendarSyncError(409, "This calendar needs to be connected again (no refresh token).")
    try:
        refresh = cipher.decrypt(connection.refresh_token_enc)
    except SecretDecryptionError:
        _mark_reauth(connection, context, "The stored token could not be decrypted (was ANUM_SECRETS_KEY changed?).")
        raise CalendarSyncError(409, "The stored calendar token could not be decrypted; connect the calendar again.") from None
    try:
        grant = await provider_client(connection.provider).refresh(refresh)
    except MailProviderError as exc:
        if exc.reauth:
            _mark_reauth(connection, context, exc.message)
        raise _failure(exc) from None
    connection.access_token_enc = cipher.encrypt(grant.access_token)
    if grant.refresh_token and grant.refresh_token != refresh:
        connection.refresh_token_enc = cipher.encrypt(grant.refresh_token)
    connection.access_expires_at = grant.expires_at
    connection.updated_at = utc_now()
    with open_sync_store(context) as store:
        stored = store.get_connection(connection.id)
        if stored is not None:
            stored.access_token_enc = connection.access_token_enc
            stored.refresh_token_enc = connection.refresh_token_enc
            stored.access_expires_at = connection.access_expires_at
            stored.updated_at = connection.updated_at
            store.save_connection(stored)
    return grant.access_token


# -- events <-> provider ----------------------------------------------------------------------


def invitation_hash(event: CalendarEvent) -> str:
    """What an owner approves: the event as the invitees would see it and who is invited."""
    payload = {
        "title": event.title,
        "starts_at": event.starts_at.astimezone(timezone.utc).isoformat(),
        "ends_at": event.ends_at.astimezone(timezone.utc).isoformat(),
        "location": event.location or "",
        "description": event.description or "",
        "attendees": sorted({(a.email or "").lower() for a in event.attendees if a.email}),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def default_connection(store: SyncStoreProtocol) -> CalendarConnection | None:
    """The calendar new ANUM events are sent to: the oldest connection that works."""
    return next((c for c in store.list_connections() if c.status == ConnectionStatus.CONNECTED), None)


def _attendees_from(remote: RemoteEvent, previous: list[Attendee]) -> list[Attendee]:
    known = {a.email: a.id for a in previous if a.email}
    out: list[Attendee] = []
    seen: set[str] = set()
    for person in remote.attendees:
        if person.email in seen:
            continue
        seen.add(person.email)
        out.append(
            Attendee(
                id=known.get(person.email) or new_id("attendee"),
                name=person.name or person.email,
                email=person.email,
                role="Organizer" if person.organizer else None,
                response=AttendeeResponse(person.response),
            )
        )
    return out[:MAX_ATTENDEES]


def _remote_times_ok(remote: RemoteEvent) -> bool:
    return remote.ends_at > remote.starts_at and remote.ends_at - remote.starts_at <= MAX_EVENT_DURATION


def _copy_remote(event: CalendarEvent, remote: RemoteEvent, now: datetime) -> CalendarEvent:
    event.title = remote.title
    event.description = remote.description
    event.location = remote.location
    event.starts_at, event.ends_at = remote.starts_at, remote.ends_at
    if remote.conference_url:
        event.conference_url = remote.conference_url
    event.attendees = _attendees_from(remote, event.attendees)
    event.status = EventStatus.CANCELLED if remote.cancelled else EventStatus.SCHEDULED
    event.source_ref = None if remote.is_organizer else GUEST_REF
    event.external_etag = remote.etag
    event.synced_at = now
    event.sync_state = "synced"
    event.updated_by = "sync"
    event.updated_at = now
    # The provider already holds this attendee list (it came from there), so ANUM gives it nothing
    # new: the event counts as invited as it is. Any local change to the people, time, title or
    # place changes the hash and needs an owner's approval again.
    event.invites_hash = invitation_hash(event) if any(a.email for a in event.attendees) else None
    return event


def _remote_changed(local: CalendarEvent, remote: RemoteEvent) -> bool:
    if remote.cancelled and local.status != EventStatus.CANCELLED:
        return True  # a deletion or cancellation marker carries no etag worth comparing
    if remote.etag and local.external_etag:
        return remote.etag != local.external_etag
    return local.synced_at is None or remote.updated_at > local.synced_at


class SyncSummary(BaseModel):
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    pushed: int = 0
    held: int = 0
    push_failed: int = 0
    conflicts_remote_won: int = 0
    conflicts_local_won: int = 0
    full: bool = False


def apply_remote(store: Any, connection: CalendarConnection, remote: RemoteEvent, summary: SyncSummary, now: datetime) -> None:
    """Apply one provider event to ANUM.

    Conflict rule (docs/calendar.md#conflicts): an event changed only at the provider is
    overwritten from the provider; changed only in ANUM it waits to be sent. Changed on both
    sides since they last agreed, the later change wins (compared by the time each side
    saved it) and the provider wins a tie. The losing side is overwritten and counted in the
    sync summary. A provider deletion removes the ANUM event unless it has unsent changes,
    which then win and are sent as a new event.
    """
    local = store.by_external(connection.id, remote.external_id)
    if local is None:
        if remote.cancelled:
            return
        if not _remote_times_ok(remote):
            summary.skipped += 1
            return
        event = CalendarEvent(
            id=new_id("event"),
            tenant_id=connection.tenant_id,
            workspace_id=connection.workspace_id,
            title=remote.title,
            kind=EventKind.MEETING,
            starts_at=remote.starts_at,
            ends_at=remote.ends_at,
            attendees=[],
            source=connection.provider.value,
            created_by=connection.connected_by,
            created_at=now,
            updated_at=now,
            connection_id=connection.id,
            external_id=remote.external_id,
        )
        store.add(_copy_remote(event, remote, now))
        summary.created += 1
        return
    if not _remote_changed(local, remote):
        return
    local_changed = local.sync_state == "pending_push"
    if local_changed:
        remote_newer = remote.updated_at >= local.updated_at
        if not remote_newer:
            # The ANUM edit is newer: it stays queued and will overwrite the provider's copy.
            summary.conflicts_local_won += 1
            if remote.cancelled:
                local.external_id = local.external_etag = None
                store.save(local)
            else:
                local.external_etag = remote.etag
                store.save(local)
            return
        summary.conflicts_remote_won += 1
    if remote.cancelled and not _is_cancelled_not_deleted(remote):
        store.delete(local.id)
        summary.deleted += 1
        return
    if not _remote_times_ok(remote):
        summary.skipped += 1
        return
    store.save(_copy_remote(local, remote, now))
    summary.updated += 1


def _is_cancelled_not_deleted(remote: RemoteEvent) -> bool:
    """Graph reports a cancelled meeting as an event with ``isCancelled``; Google reports a
    deleted or cancelled event with status ``cancelled`` and usually no details. A cancelled
    event that still carries times and a real title is kept (as cancelled)."""
    return remote.title != "(no title)" and remote.ends_at > remote.starts_at


def outgoing_for(event: CalendarEvent) -> tuple[OutgoingEvent, bool]:
    """The event as ANUM writes it, and whether invitations go with it (only the approved ones)."""
    approved = event.invites_hash is not None and event.invites_hash == invitation_hash(event)
    attendees = tuple((a.email, a.name) for a in event.attendees if a.email) if approved else None
    return (
        OutgoingEvent(
            title=event.title,
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            description=event.description,
            location=event.location,
            attendees=attendees,
            online_meeting=bool(event.conference_url),
        ),
        approved,
    )


async def push_event(client: CalendarProviderClient, token: str, event: CalendarEvent) -> CalendarEvent:
    """Send one ANUM event to the provider and return it with its link fields updated (not saved)."""
    outgoing, notify = outgoing_for(event)
    if client.notifies_on_update and event.external_id and event.status != EventStatus.CANCELLED and not notify and any(a.email for a in event.attendees):
        # The provider would tell the attendees about this change on its own: hold it until an owner
        # approves the event as it now is (docs/calendar.md#invitations). It stays queued.
        return event
    if event.status == EventStatus.CANCELLED:
        if event.external_id:
            await client.delete(token, event.external_id, notify=notify)
        event.connection_id = event.external_id = event.external_etag = event.synced_at = None
        event.sync_state = "local"
        return event
    if event.external_id:
        result = await client.update(token, event.external_id, outgoing, notify=notify)
    else:
        result = await client.create(token, outgoing, notify=notify)
    event.external_id = result.external_id
    event.external_etag = result.etag
    event.synced_at = utc_now()
    event.sync_state = "synced"
    return event


# -- sync -------------------------------------------------------------------------------------


async def sync_connection(connection_id: str, context: TenantContext, *, force: bool = False) -> tuple[CalendarConnection, SyncSummary]:
    """One sync pass: pull the provider's changes, then send ANUM's. A second concurrent pass
    for the connection is skipped. Health is recorded on the connection."""
    now = utc_now()
    summary = SyncSummary()
    with open_sync_store(context) as store:
        connection = store.get_connection(connection_id)
        if connection is None:
            raise CalendarSyncError(404, "Calendar connection not found.")
        if not force and connection.last_sync_at and (now - connection.last_sync_at).total_seconds() < 15:
            return connection, summary
        if not store.claim_sync(connection_id, now, now + SYNC_LEASE):
            return connection, summary
    try:
        if provider_problems(connection.provider):
            raise CalendarSyncError(409, f"{PROVIDER_NAMES[connection.provider]} is not set up on this server.")
        token = await connection_token(connection, context)
        client = provider_client(connection.provider)
        cursor = connection.sync_cursor
        since = now - timedelta(days=calendar_settings.days_back)
        until = now + timedelta(days=calendar_settings.days_ahead)
        for _ in range(4):
            delta = await client.changes(token, cursor, since=since, until=until)
            summary.full = summary.full or delta.full
            with open_calendar_store(context) as events:
                stamp = utc_now()
                for remote in delta.events:
                    apply_remote(events, connection, remote, summary, stamp)
                for external_id in delta.removed:
                    local = events.by_external(connection.id, external_id)
                    if local is not None and local.sync_state != "pending_push":
                        events.delete(local.id)
                        summary.deleted += 1
            cursor = delta.cursor or cursor
            if not delta.more:
                break
        failures: list[str] = []
        with open_calendar_store(context) as events:
            waiting = events.pending_push(connection.id, MAX_PUSH_PER_PASS)
        for event in waiting:
            try:
                pushed = await push_event(client, token, event)
            except MailProviderError as exc:
                summary.push_failed += 1
                failures.append(exc.message)
                if exc.reauth:
                    raise
                continue
            if pushed.sync_state == "pending_push":
                summary.held += 1  # waiting for the owner's approval of the changed event
                continue
            with open_calendar_store(context) as events:
                current = events.get(event.id)
                # Edited again while it was being sent: keep the newer edit queued.
                if current is not None and current.updated_at == event.updated_at:
                    events.save(pushed)
                    summary.pushed += 1
                elif current is not None:
                    current.external_id, current.external_etag = pushed.external_id, pushed.external_etag
                    events.save(current)
        error = failures[0] if failures else None
    except (CalendarSyncError, MailProviderError) as exc:
        message = exc.message
        reauth = isinstance(exc, MailProviderError) and exc.reauth
        with open_sync_store(context) as store:
            stored = store.get_connection(connection_id)
            if stored is not None:
                stored.sync_lease_until = None
                stored.last_sync_status = "error"
                stored.last_error = message[:500]
                if reauth:
                    stored.status = ConnectionStatus.REAUTH_REQUIRED
                elif stored.status != ConnectionStatus.REAUTH_REQUIRED:
                    stored.status = ConnectionStatus.ERROR
                stored.updated_at = utc_now()
                store.save_connection(stored)
                connection = stored
        logger.warning(
            "calendar_sync_failed provider=%s",
            connection.provider.value,
            extra={"anum_calendar": {"event": "sync_failed", "connection_id": connection_id.replace("\r", "").replace("\n", ""), "reauth": reauth}},
        )
        return connection, summary
    except BaseException:
        with open_sync_store(context) as store:
            stored = store.get_connection(connection_id)
            if stored is not None:
                stored.sync_lease_until = None
                store.save_connection(stored)
        raise
    finished = utc_now()
    with open_sync_store(context) as store:
        stored = store.get_connection(connection_id)
        if stored is None:  # disconnected meanwhile
            raise CalendarSyncError(404, "Calendar connection not found.")
        stored.sync_cursor = cursor or stored.sync_cursor
        stored.last_sync_at = finished
        stored.last_sync_status = "error" if error else "ok"
        stored.last_error = f"Some changes could not be sent: {error}" if error else None
        stored.last_summary = summary.model_dump()
        stored.status = ConnectionStatus.ERROR if error else ConnectionStatus.CONNECTED
        stored.sync_lease_until = None
        stored.updated_at = finished
        store.save_connection(stored)
    return stored, summary


async def attach_and_push(context: TenantContext, event_id: str, *, create: bool) -> CalendarEvent | None:
    """After a local create or change: queue the event for the provider and try to send it now.

    A failure leaves the event queued (``pending_push``) for the next sync pass; it never
    fails the person's own save."""
    with open_sync_store(context) as store:
        connection = default_connection(store) if create else None
    with open_calendar_store(context) as events:
        event = events.get(event_id)
        if event is None:
            return None
        if create:
            if connection is None:
                return event
            event.connection_id = connection.id
        elif event.connection_id is None or event.source_ref == GUEST_REF:
            return event
        event.sync_state = "pending_push"
        events.save(event)
    with open_sync_store(context) as store:
        linked = store.get_connection(event.connection_id or "")
    if linked is None or linked.status == ConnectionStatus.REAUTH_REQUIRED:
        return event
    try:
        token = await connection_token(linked, context)
        pushed = await push_event(provider_client(linked.provider), token, event.model_copy(deep=True))
    except (CalendarSyncError, MailProviderError) as exc:
        logger.info("calendar_push_deferred", extra={"anum_calendar": {"event": "push_deferred", "status": getattr(exc, "status", 0)}})
        return event
    with open_calendar_store(context) as events:
        current = events.get(event_id)
        if current is None:
            return None
        if current.updated_at == event.updated_at:
            events.save(pushed)
            return pushed
        current.external_id, current.external_etag = pushed.external_id, pushed.external_etag
        events.save(current)
        return current


async def remove_at_provider(context: TenantContext, event: CalendarEvent) -> None:
    """Before a local delete of a linked event: remove it at the provider too. Raises
    ``CalendarSyncError`` when the provider cannot be reached, so nothing is lost silently."""
    if not event.connection_id or not event.external_id or event.source_ref == GUEST_REF:
        return
    with open_sync_store(context) as store:
        connection = store.get_connection(event.connection_id)
    if connection is None:
        return
    notify = event.invites_hash is not None and event.invites_hash == invitation_hash(event)
    try:
        token = await connection_token(connection, context)
        await provider_client(connection.provider).delete(token, event.external_id, notify=notify)
    except MailProviderError as exc:
        raise _failure(exc) from None


# -- invitations (approval flow) -----------------------------------------------------------------


def invitation_arguments(event: CalendarEvent, connection: CalendarConnection) -> dict[str, Any]:
    """What the approver sees and the payload hash binds."""
    return {
        "event_id": event.id,
        "connection_id": connection.id,
        "calendar": f"{PROVIDER_NAMES[connection.provider]} ({connection.email})",
        "title": event.title,
        "starts_at": event.starts_at.astimezone(timezone.utc).isoformat(),
        "ends_at": event.ends_at.astimezone(timezone.utc).isoformat(),
        "location": event.location or "",
        "attendees": sorted({a.email.lower() for a in event.attendees if a.email}),
        "content_hash": invitation_hash(event),
    }


def invitations_definition() -> ToolDefinition:
    return ToolDefinition(
        name=SEND_INVITATIONS_TOOL,
        description=(
            "Send a calendar invitation for one exact event to its attendees through the connected Google or "
            "Microsoft calendar. Runs only after an owner approves the exact content."
        ),
        risk_level=RiskLevel.HIGH,
        # Members ask; only an owner decides the approval (approval:decide).
        required_roles=frozenset({"owner", "member"}),
        timeout_seconds=60,
        idempotent=True,
        category="general",
        action_kind="send",
        always_ask=True,
    )


def _failed(summary: str) -> ToolResult:
    return ToolResult(status="failed", summary=summary)


async def send_invitations(call: ToolCall, context: TenantContext) -> ToolResult:
    args = call.arguments
    event_id = str(args.get("event_id", ""))
    approved_hash = str(args.get("content_hash", ""))
    with open_calendar_store(context) as events:
        event = events.get(event_id)
    if event is None:
        return _failed("The event was deleted; no invitation was sent.")
    if invitation_hash(event) != approved_hash:
        return _failed("The event or its attendees changed after it was sent for approval; no invitation was sent. Ask for approval again.")
    if event.status == EventStatus.CANCELLED:
        return _failed("The event is cancelled; no invitation was sent.")
    with open_sync_store(context) as store:
        connection = store.get_connection(str(args.get("connection_id", "")))
    if connection is None or connection.status == ConnectionStatus.REAUTH_REQUIRED:
        return _failed("The calendar is not connected any more; no invitation was sent. Connect it again and ask for approval again.")
    if event.connection_id not in (None, connection.id):
        return _failed("The event is synced with another calendar; no invitation was sent.")
    event.connection_id = connection.id
    event.invites_hash = approved_hash
    event.sync_state = "pending_push"
    with open_calendar_store(context) as events:
        events.save(event)
    try:
        token = await connection_token(connection, context)
        pushed = await push_event(provider_client(connection.provider), token, event.model_copy(deep=True))
    except (CalendarSyncError, MailProviderError) as exc:
        return ToolResult(
            status="failed",
            summary=f"Approved, but {PROVIDER_NAMES[connection.provider]} refused it: {exc.message} The next sync tries again.",
        )
    with open_calendar_store(context) as events:
        current = events.get(event_id)
        if current is not None:
            current.external_id, current.external_etag = pushed.external_id, pushed.external_etag
            current.synced_at, current.sync_state = pushed.synced_at, "synced"
            events.save(current)
    count = len(args.get("attendees") or [])
    return ToolResult(
        status="succeeded",
        summary=f"Invitation for \"{event.title}\" sent to {count} {'person' if count == 1 else 'people'} through {PROVIDER_NAMES[connection.provider]}.",
        output={"event_id": event_id, "attendees": count},
        audit_metadata={"event_id": event_id, "attendees": count, "provider": connection.provider.value},
    )


def register_calendar_tools(registry: ToolRegistry) -> ToolRegistry:
    """Add the calendar tools to a runtime's registry (the API's and the Temporal worker's)."""
    if SEND_INVITATIONS_TOOL not in registry.names:
        registry.register(invitations_definition(), send_invitations)
    return registry


def calendar_tool_registry() -> ToolRegistry:
    return register_calendar_tools(ToolRegistry())


# -- background sync ------------------------------------------------------------------------------


class CalendarSyncScheduler:
    """Syncs connected calendars every ``ANUM_CALENDAR_SYNC_INTERVAL_SECONDS`` (PostgreSQL only).

    Discovery runs as ``anum_maintenance`` (migration 0090: ids and scope of connections that
    do not need reconnecting); each sync runs as the application role inside the connection's
    tenant and workspace RLS context, like every other cross-tenant job.
    """

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="anum-calendar-sync")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=30)
            except TimeoutError:
                self._task.cancel()

    async def run_once(self) -> int:
        from sqlalchemy import text

        from .maintenance import default_session_factory, discover

        rows = await asyncio.to_thread(
            discover,
            default_session_factory,
            text(
                "select id, tenant_id, workspace_id, connected_by from calendar_connections "
                "where status <> 'reauth_required' and (last_sync_at is null or last_sync_at < now() - make_interval(secs => :age)) "
                "order by last_sync_at nulls first limit 50"
            ),
            {"age": self.interval},
        )
        done = 0
        for row in rows:
            if self._stopping.is_set():
                break
            context = TenantContext(tenant_id=row.tenant_id, workspace_id=row.workspace_id, user_id=row.connected_by, roles=["owner"])
            try:
                await sync_connection(row.id, context, force=True)
                done += 1
            except Exception:  # noqa: BLE001 - one connection's failure must not stop the others
                logger.exception("calendar_background_sync_failed", extra={"anum_calendar": {"event": "background_failed", "connection_id": row.id}})
        return done

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 - keep the loop alive; the next pass retries
                logger.exception("calendar_background_sync_pass_failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.interval)
            except TimeoutError:
                pass


def build_calendar_scheduler(config: Any, cfg: CalendarSettings | None = None) -> CalendarSyncScheduler | None:
    cfg = cfg or calendar_settings
    if config.repository_backend != "postgresql" or cfg.sync_interval_seconds <= 0:
        return None
    if all(provider_problems(provider, cfg) for provider in CalendarProvider):
        return None
    return CalendarSyncScheduler(cfg.sync_interval_seconds)

