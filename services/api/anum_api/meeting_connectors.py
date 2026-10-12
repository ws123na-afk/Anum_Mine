"""Zoom and Microsoft Teams transcript connectors (docs/meetings.md#connectors).

Each user connects their own Zoom or Microsoft 365 account with OAuth 2.0
(authorization code). ANUM then reads, never writes:

* Zoom: cloud recordings of the last 30 days (``GET /v2/users/me/recordings``) and the
  ``TRANSCRIPT`` file of one meeting (``GET /v2/meetings/{uuid}/recordings`` then its
  ``download_url``). Scopes: ``cloud_recording:read:list_user_recordings``,
  ``cloud_recording:read:list_recording_files`` and ``user:read:user`` (granular), or
  the classic ``recording:read`` and ``user:read``.
* Teams: transcripts of meetings the user organised (Microsoft Graph
  ``getAllTranscripts``, then ``/transcripts/{id}/content?$format=text/vtt``). Delegated
  scopes: ``OnlineMeetings.Read``, ``OnlineMeetingTranscript.Read.All``, ``User.Read`` and
  ``offline_access``.

The OAuth ``state`` is encrypted with ``ANUM_SECRETS_KEY`` and names the tenant,
workspace and user that started the flow. The provider redirects the browser to the web
app (``ANUM_MEETINGS_OAUTH_REDIRECT_URI``), which posts ``code`` and ``state`` to the API
*signed in*; the API refuses a state that names anyone but the caller, so a link
crafted by someone else cannot attach the victim's account to the attacker's workspace.

Tokens are encrypted at rest (Fernet, ``ANUM_SECRETS_KEY``) in the user-private,
forced-RLS ``meeting_connector_accounts`` table and are never returned to a client. A
refresh re-encrypts them with the current key. Disconnecting deletes them.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field

from .schemas import TenantContext, utc_now
from .scoped_store import open_scoped_store
from .secret_box import SecretDecryptionError, secret_cipher
from .settings import Settings

# Tests replace this with an ``httpx.MockTransport``.
http_transport: httpx.AsyncBaseTransport | None = None

STATE_TTL_SECONDS = 600
HTTP_TIMEOUT_SECONDS = 30.0
MAX_LISTED = 50
TEAMS_TITLE_LOOKUPS = 25

ZOOM_AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
ZOOM_TOKEN_URL = "https://zoom.us/oauth/token"  # nosec B105 - an endpoint URL, not a secret
ZOOM_API = "https://api.zoom.us/v2"
MICROSOFT_LOGIN = "https://login.microsoftonline.com"
GRAPH_API = "https://graph.microsoft.com/v1.0"
# Ids that reach a request path: Zoom meeting UUIDs are base64 (``+``, ``/``, ``=``), Graph
# meeting and transcript ids are base64url-like, Graph user ids are GUIDs. Anything else
# is refused before a URL is built, and every id is percent-encoded into it.
_EXTERNAL_ID = re.compile(r"[A-Za-z0-9+/=_-]{1,256}")
_GRAPH_USER_ID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
# Hosts a Zoom transcript download (and its one redirect) may come from.
_ZOOM_DOWNLOAD_DOMAINS = ("zoom.us", "zoomgov.com")
TEAMS_SCOPES = ("offline_access", "User.Read", "OnlineMeetings.Read", "OnlineMeetingTranscript.Read.All")


class ConnectorProvider(StrEnum):
    ZOOM = "zoom"
    TEAMS = "teams"


class ConnectorError(RuntimeError):
    """A connector call failed; ``detail`` is safe to show. ``reconnect`` asks the user to connect again."""

    def __init__(self, detail: str, *, reconnect: bool = False, status_code: int = 502) -> None:
        super().__init__(detail)
        self.detail = detail
        self.reconnect = reconnect
        self.status_code = status_code


class TokenSet(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime
    scopes: list[str] = Field(default_factory=list)


class StoredConnection(BaseModel):
    """One user's connected account for one provider (ciphertext only)."""

    tenant_id: str
    workspace_id: str
    user_id: str
    provider: ConnectorProvider
    account_label: str
    external_user_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    token_ciphertext: str = Field(repr=False)
    expires_at: datetime
    connected_at: datetime
    updated_at: datetime


class ExternalMeeting(BaseModel):
    external_id: str
    title: str
    started_at: datetime | None = None
    duration_minutes: int | None = None
    imported_meeting_id: str | None = None


@dataclass(frozen=True)
class FetchedTranscript:
    title: str
    started_at: datetime | None
    duration_seconds: int | None
    vtt: str


# --------------------------------------------------------------------------- store


class ConnectionStore(Protocol):
    def get(self, context: TenantContext, provider: ConnectorProvider) -> StoredConnection | None: ...
    def list_connections(self, context: TenantContext) -> list[StoredConnection]: ...
    def save(self, connection: StoredConnection) -> StoredConnection: ...
    def delete(self, context: TenantContext, provider: ConnectorProvider) -> bool: ...


class InMemoryConnectionStore:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str, str, str], StoredConnection] = {}
        self._lock = RLock()

    @staticmethod
    def _key(context: TenantContext, provider: str) -> tuple[str, str, str, str]:
        return (context.tenant_id, context.workspace_id, context.user_id, str(provider))

    def get(self, context: TenantContext, provider: ConnectorProvider) -> StoredConnection | None:
        with self._lock:
            return self.items.get(self._key(context, provider))

    def list_connections(self, context: TenantContext) -> list[StoredConnection]:
        with self._lock:
            return [item for key, item in self.items.items() if key[:3] == self._key(context, "")[:3]]

    def save(self, connection: StoredConnection) -> StoredConnection:
        with self._lock:
            self.items[(connection.tenant_id, connection.workspace_id, connection.user_id, str(connection.provider))] = connection
        return connection

    def delete(self, context: TenantContext, provider: ConnectorProvider) -> bool:
        with self._lock:
            return self.items.pop(self._key(context, provider), None) is not None

    def clear(self) -> None:
        with self._lock:
            self.items.clear()


connection_store = InMemoryConnectionStore()


@contextmanager
def open_connection_store(context: TenantContext) -> Iterator[ConnectionStore]:
    """The caller's own connections (``anum.user_id`` is set for RLS)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.meetings_repository import SqlAlchemyConnectionStore

        return SqlAlchemyConnectionStore(session)

    with open_scoped_store(context, connection_store, sql_store, user_scoped=True) as store:
        yield store


# --------------------------------------------------------------------------- configuration


def setup_problem(provider: ConnectorProvider, config: Settings) -> str | None:
    """What the server administrator must set before users can connect; None when ready."""
    if provider == ConnectorProvider.ZOOM:
        missing = [
            name
            for name, value in (
                ("ANUM_ZOOM_CLIENT_ID", config.zoom_client_id.strip()),
                ("ANUM_ZOOM_CLIENT_SECRET", config.zoom_client_secret.get_secret_value().strip() if config.zoom_client_secret else ""),
                ("ANUM_MEETINGS_OAUTH_REDIRECT_URI", config.meetings_oauth_redirect_uri.strip()),
            )
            if not value
        ]
        app = "a Zoom OAuth app (Zoom App Marketplace, General app, user-managed)"
    else:
        missing = [
            name
            for name, value in (
                ("ANUM_MICROSOFT_CLIENT_ID", config.microsoft_client_id.strip()),
                (
                    "ANUM_MICROSOFT_CLIENT_SECRET",
                    config.microsoft_client_secret.get_secret_value().strip() if config.microsoft_client_secret else "",
                ),
                ("ANUM_MEETINGS_OAUTH_REDIRECT_URI", config.meetings_oauth_redirect_uri.strip()),
            )
            if not value
        ]
        app = "a Microsoft Entra app registration with delegated Graph permissions"
    if not missing:
        return None
    return f"Not set up on this server: register {app} and set {', '.join(missing)}."


def _client_credentials(provider: ConnectorProvider, config: Settings) -> tuple[str, str]:
    if provider == ConnectorProvider.ZOOM:
        secret = config.zoom_client_secret.get_secret_value().strip() if config.zoom_client_secret else ""
        return config.zoom_client_id.strip(), secret
    secret = config.microsoft_client_secret.get_secret_value().strip() if config.microsoft_client_secret else ""
    return config.microsoft_client_id.strip(), secret


def _microsoft_url(config: Settings, path: str) -> str:
    return f"{MICROSOFT_LOGIN}/{quote(config.microsoft_tenant, safe='')}/oauth2/v2.0/{path}"


# --------------------------------------------------------------------------- OAuth state


def encode_state(context: TenantContext, provider: ConnectorProvider, *, now: datetime | None = None) -> str:
    issued = now or utc_now()
    payload = {
        "p": str(provider),
        "t": context.tenant_id,
        "w": context.workspace_id,
        "u": context.user_id,
        "n": secrets.token_urlsafe(12),
        "exp": int((issued + timedelta(seconds=STATE_TTL_SECONDS)).timestamp()),
    }
    return secret_cipher().encrypt(json.dumps(payload, separators=(",", ":")))


def decode_state(state: str, context: TenantContext, *, now: datetime | None = None) -> ConnectorProvider:
    """The provider named by a state the caller started, or ConnectorError (400)."""
    try:
        payload = json.loads(secret_cipher().decrypt(state.strip()))
    except (SecretDecryptionError, ValueError, TypeError) as exc:
        raise ConnectorError("This sign-in link is not valid. Start connecting again.", status_code=400) from exc
    if int(payload.get("exp", 0)) < int((now or utc_now()).timestamp()):
        raise ConnectorError("This sign-in took longer than 10 minutes. Start connecting again.", status_code=400)
    if (payload.get("t"), payload.get("w"), payload.get("u")) != (context.tenant_id, context.workspace_id, context.user_id):
        raise ConnectorError(
            "This sign-in was started by another user or in another workspace. Start connecting again here.",
            status_code=403,
        )
    try:
        return ConnectorProvider(payload.get("p"))
    except ValueError as exc:
        raise ConnectorError("This sign-in link is not valid. Start connecting again.", status_code=400) from exc


def authorize_url(provider: ConnectorProvider, context: TenantContext, config: Settings) -> str:
    client_id, _ = _client_credentials(provider, config)
    state = encode_state(context, provider)
    redirect = config.meetings_oauth_redirect_uri.strip()
    if provider == ConnectorProvider.ZOOM:
        query = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": state}
        return f"{ZOOM_AUTHORIZE_URL}?{urlencode(query)}"
    query = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect,
        "response_mode": "query",
        "scope": " ".join(TEAMS_SCOPES),
        "state": state,
        "prompt": "select_account",
    }
    return f"{_microsoft_url(config, 'authorize')}?{urlencode(query)}"


# --------------------------------------------------------------------------- HTTP


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, transport=http_transport, follow_redirects=False)


def _provider_name(provider: ConnectorProvider) -> str:
    return "Zoom" if provider == ConnectorProvider.ZOOM else "Microsoft"


def _check(response: httpx.Response, provider: ConnectorProvider) -> None:
    name = _provider_name(provider)
    if response.status_code < 400:
        return
    if response.status_code == 401:
        raise ConnectorError(f"{name} signed this connection out. Connect again.", reconnect=True, status_code=409)
    if response.status_code == 403:
        raise ConnectorError(
            f"{name} refused: the connected account lacks a permission ANUM needs "
            "(cloud recording or meeting transcript access). Ask your admin, then connect again.",
            reconnect=True,
            status_code=409,
        )
    if response.status_code == 404:
        raise ConnectorError(f"{name} has no transcript for this meeting (it may have been deleted).", status_code=404)
    if response.status_code == 429:
        raise ConnectorError(f"{name} is rate limiting requests. Try again in a minute.", status_code=429)
    raise ConnectorError(f"{name} answered HTTP {response.status_code}. Try again later.")


async def _request(provider: ConnectorProvider, method: str, url: str, **kwargs: Any) -> httpx.Response:
    try:
        async with _client() as client:
            response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"{_provider_name(provider)} could not be reached. Try again later.") from exc
    _check(response, provider)
    return response


def _json(response: httpx.Response, provider: ConnectorProvider) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise ConnectorError(f"{_provider_name(provider)} answered with something that is not JSON.") from exc
    if not isinstance(body, dict):
        raise ConnectorError(f"{_provider_name(provider)} answered unexpectedly.")
    return body


def _token_set(body: dict[str, Any], provider: ConnectorProvider, *, previous_refresh: str | None = None) -> TokenSet:
    access = body.get("access_token")
    if not isinstance(access, str) or not access:
        raise ConnectorError(f"{_provider_name(provider)} did not return an access token. Connect again.", reconnect=True)
    expires_in = body.get("expires_in")
    seconds = int(expires_in) if isinstance(expires_in, (int, float, str)) and str(expires_in).isdigit() else 3600
    scope = body.get("scope")
    scopes = [item for item in str(scope).replace(",", " ").split() if item] if scope else []
    refresh = body.get("refresh_token") if isinstance(body.get("refresh_token"), str) else previous_refresh
    return TokenSet(access_token=access, refresh_token=refresh, expires_at=utc_now() + timedelta(seconds=seconds), scopes=scopes)


async def _token_request(provider: ConnectorProvider, config: Settings, data: dict[str, str]) -> dict[str, Any]:
    client_id, client_secret = _client_credentials(provider, config)
    if provider == ConnectorProvider.ZOOM:
        url, auth = ZOOM_TOKEN_URL, (client_id, client_secret)
    else:
        url, auth = _microsoft_url(config, "token"), None
        data = {**data, "client_id": client_id, "client_secret": client_secret, "scope": " ".join(TEAMS_SCOPES)}
    try:
        async with _client() as client:
            response = await client.post(url, data=data, auth=auth) if auth else await client.post(url, data=data)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"{_provider_name(provider)} sign-in could not be reached. Try again.") from exc
    if response.status_code in {400, 401}:
        raise ConnectorError(
            f"{_provider_name(provider)} did not accept the sign-in (it may have expired). Connect again.",
            reconnect=True,
            status_code=409,
        )
    _check(response, provider)
    return _json(response, provider)


async def exchange_code(provider: ConnectorProvider, code: str, config: Settings) -> tuple[TokenSet, str, str | None]:
    """Tokens plus the account's label and provider user id."""
    body = await _token_request(
        provider,
        config,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": config.meetings_oauth_redirect_uri.strip()},
    )
    tokens = _token_set(body, provider)
    headers = {"Authorization": f"Bearer {tokens.access_token}"}
    if provider == ConnectorProvider.ZOOM:
        me = _json(await _request(provider, "GET", f"{ZOOM_API}/users/me", headers=headers), provider)
        label = str(me.get("email") or me.get("display_name") or "Zoom account")
        return tokens, label[:200], str(me["id"]) if me.get("id") else None
    me = _json(
        await _request(provider, "GET", f"{GRAPH_API}/me", params={"$select": "id,displayName,userPrincipalName,mail"}, headers=headers),
        provider,
    )
    label = str(me.get("mail") or me.get("userPrincipalName") or me.get("displayName") or "Microsoft account")
    return tokens, label[:200], str(me["id"]) if me.get("id") else None


def seal(tokens: TokenSet) -> str:
    return secret_cipher().encrypt(json.dumps({"a": tokens.access_token, "r": tokens.refresh_token}))


def unseal(connection: StoredConnection) -> TokenSet:
    try:
        raw = json.loads(secret_cipher().decrypt(connection.token_ciphertext))
    except (SecretDecryptionError, ValueError) as exc:
        raise ConnectorError(
            "The saved connection can no longer be read (the server's secrets key changed). Connect again.",
            reconnect=True,
            status_code=409,
        ) from exc
    return TokenSet(access_token=raw["a"], refresh_token=raw.get("r"), expires_at=connection.expires_at, scopes=connection.scopes)


async def fresh_access_token(context: TenantContext, provider: ConnectorProvider, config: Settings) -> tuple[str, StoredConnection]:
    """A usable access token for the caller's connection, refreshed (and saved) when due."""
    with open_connection_store(context) as store:
        connection = store.get(context, provider)
    if connection is None:
        raise ConnectorError(f"Connect your {_provider_name(provider)} account first.", reconnect=True, status_code=409)
    tokens = unseal(connection)
    if tokens.expires_at - timedelta(seconds=60) > utc_now():
        return tokens.access_token, connection
    if not tokens.refresh_token:
        raise ConnectorError(f"Your {_provider_name(provider)} sign-in expired. Connect again.", reconnect=True, status_code=409)
    try:
        body = await _token_request(provider, config, {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token})
    except ConnectorError as exc:
        if exc.reconnect:
            with open_connection_store(context) as store:
                store.delete(context, provider)
        raise
    renewed = _token_set(body, provider, previous_refresh=tokens.refresh_token)
    updated = connection.model_copy(
        update={
            "token_ciphertext": seal(renewed),
            "expires_at": renewed.expires_at,
            "scopes": renewed.scopes or connection.scopes,
            "updated_at": utc_now(),
        }
    )
    with open_connection_store(context) as store:
        store.save(updated)
    return renewed.access_token, updated


# --------------------------------------------------------------------------- listing and fetching


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _checked_id(value: str, pattern: re.Pattern[str], what: str) -> str:
    if not pattern.fullmatch(value):
        raise ConnectorError(f"This {what} reference is not valid.", status_code=422)
    return value


def _graph_user(connection: StoredConnection) -> str:
    user_id = connection.external_user_id
    if not user_id or not _GRAPH_USER_ID.fullmatch(user_id):
        raise ConnectorError("The Microsoft connection is missing its user id. Connect again.", reconnect=True, status_code=409)
    return user_id


def _zoom_download_url(value: object) -> str | None:
    """A Zoom download URL rebuilt from its parts when its host is Zoom's; else None."""
    if not isinstance(value, str):
        return None
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        return None
    if not any(host == domain or host.endswith("." + domain) for domain in _ZOOM_DOWNLOAD_DOMAINS):
        return None
    return urlunsplit(("https", host, parts.path, parts.query, ""))


def _graph_next_params(next_link: object) -> dict[str, str] | None:
    """The OData paging parameters of a Graph ``@odata.nextLink`` (the URL itself is not
    followed: the next page is requested from the same constant path)."""
    if not isinstance(next_link, str):
        return None
    parts = urlsplit(next_link)
    if parts.scheme != "https" or parts.hostname != "graph.microsoft.com":
        return None
    params = {key: value for key, value in parse_qsl(parts.query) if key.startswith("$")}
    return params or None


def _zoom_uuid(uuid: str) -> str:
    # Zoom: a UUID that starts with "/" or contains "//" must be double URL-encoded.
    encoded = quote(uuid, safe="")
    return quote(encoded, safe="") if uuid.startswith("/") or "//" in uuid else encoded


async def list_meetings(
    provider: ConnectorProvider, access_token: str, connection: StoredConnection, *, days: int
) -> list[ExternalMeeting]:
    """Meetings of the last ``days`` days that have a transcript, newest first."""
    headers = {"Authorization": f"Bearer {access_token}"}
    today = utc_now()
    start = today - timedelta(days=days)
    found: list[ExternalMeeting] = []
    if provider == ConnectorProvider.ZOOM:
        params = {"from": start.date().isoformat(), "to": today.date().isoformat(), "page_size": "100"}
        body = _json(await _request(provider, "GET", f"{ZOOM_API}/users/me/recordings", params=params, headers=headers), provider)
        for meeting in body.get("meetings") or []:
            files = meeting.get("recording_files") or []
            if not any(str(item.get("file_type", "")).upper() == "TRANSCRIPT" for item in files):
                continue
            uuid = str(meeting.get("uuid") or "")
            if not uuid:
                continue
            found.append(
                ExternalMeeting(
                    external_id=uuid,
                    title=str(meeting.get("topic") or "Zoom meeting")[:200],
                    started_at=_parse_time(meeting.get("start_time")),
                    duration_minutes=int(meeting["duration"]) if isinstance(meeting.get("duration"), int) else None,
                )
            )
    else:
        user_id = _graph_user(connection)
        function = (
            f"getAllTranscripts(meetingOrganizerUserId='{user_id}',"
            f"startDateTime={start.strftime('%Y-%m-%dT%H:%M:%SZ')},endDateTime={today.strftime('%Y-%m-%dT%H:%M:%SZ')})"
        )
        url = f"{GRAPH_API}/users/{quote(user_id, safe='')}/onlineMeetings/{function}"
        items: list[dict[str, Any]] = []
        page_params: dict[str, str] | None = {}
        pages = 0
        while page_params is not None and pages < 3 and len(items) < MAX_LISTED:
            body = _json(await _request(provider, "GET", url, params=page_params, headers=headers), provider)
            items.extend(item for item in body.get("value") or [] if isinstance(item, dict))
            page_params = _graph_next_params(body.get("@odata.nextLink"))
            pages += 1
        titles: dict[str, tuple[str, datetime | None, datetime | None]] = {}
        for item in sorted(items, key=lambda value: str(value.get("createdDateTime") or ""), reverse=True)[:MAX_LISTED]:
            meeting_id, transcript_id = str(item.get("meetingId") or ""), str(item.get("id") or "")
            if not _EXTERNAL_ID.fullmatch(meeting_id) or not _EXTERNAL_ID.fullmatch(transcript_id):
                continue
            if meeting_id not in titles and len(titles) < TEAMS_TITLE_LOOKUPS:
                try:
                    meeting = _json(
                        await _request(
                            provider,
                            "GET",
                            f"{GRAPH_API}/users/{quote(user_id, safe='')}/onlineMeetings/{quote(meeting_id, safe='')}",
                            params={"$select": "subject,startDateTime,endDateTime"},
                            headers=headers,
                        ),
                        provider,
                    )
                    titles[meeting_id] = (
                        str(meeting.get("subject") or ""),
                        _parse_time(meeting.get("startDateTime")),
                        _parse_time(meeting.get("endDateTime")),
                    )
                except ConnectorError as exc:
                    if exc.reconnect:
                        raise
                    titles[meeting_id] = ("", None, None)
            subject, started, ended = titles.get(meeting_id, ("", None, None))
            started = started or _parse_time(item.get("createdDateTime"))
            ended = ended or _parse_time(item.get("endDateTime"))
            duration = int((ended - started).total_seconds() // 60) if started and ended and ended > started else None
            found.append(
                ExternalMeeting(
                    external_id=f"{meeting_id}::{transcript_id}",
                    title=(subject or f"Teams meeting {started.date().isoformat() if started else ''}".strip())[:200],
                    started_at=started,
                    duration_minutes=duration,
                )
            )
    found.sort(key=lambda item: item.started_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return found[:MAX_LISTED]


async def _download_text(provider: ConnectorProvider, url: str, headers: dict[str, str], params: dict[str, str], max_bytes: int) -> str:
    try:
        async with _client() as client:
            async with client.stream("GET", url, headers=headers, params=params) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location", "")
                    location = _zoom_download_url(location) or ""
                    if not location:
                        raise ConnectorError(f"{_provider_name(provider)} redirected the download unexpectedly.")
                    # Zoom serves the file from its CDN: follow one HTTPS redirect without the token.
                    async with client.stream("GET", location) as redirected:
                        return await _read_capped(redirected, provider, max_bytes)
                return await _read_capped(response, provider, max_bytes)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"{_provider_name(provider)} could not be reached. Try again later.") from exc


async def _read_capped(response: httpx.Response, provider: ConnectorProvider, max_bytes: int) -> str:
    _check(response, provider)
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise ConnectorError("The transcript is larger than this server's transcript limit.", status_code=413)
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


async def fetch_transcript(
    provider: ConnectorProvider,
    access_token: str,
    connection: StoredConnection,
    external_id: str,
    *,
    max_bytes: int,
) -> FetchedTranscript:
    headers = {"Authorization": f"Bearer {access_token}"}
    if provider == ConnectorProvider.ZOOM:
        meeting = _json(
            await _request(
                provider,
                "GET",
                f"{ZOOM_API}/meetings/{_zoom_uuid(_checked_id(external_id, _EXTERNAL_ID, 'Zoom meeting'))}/recordings",
                headers=headers,
            ),
            provider,
        )
        transcript = next(
            (item for item in meeting.get("recording_files") or [] if str(item.get("file_type", "")).upper() == "TRANSCRIPT"),
            None,
        )
        url = _zoom_download_url(transcript.get("download_url")) if transcript else None
        if url is None:
            raise ConnectorError("Zoom has no transcript for this meeting. Turn on audio transcripts in Zoom's recording settings.", status_code=404)
        vtt = await _download_text(provider, url, headers, {}, max_bytes)
        duration = meeting.get("duration")
        return FetchedTranscript(
            title=str(meeting.get("topic") or "Zoom meeting")[:200],
            started_at=_parse_time(meeting.get("start_time")),
            duration_seconds=int(duration) * 60 if isinstance(duration, int) else None,
            vtt=vtt,
        )
    if "::" not in external_id:
        raise ConnectorError("This Teams transcript reference is not valid.", status_code=422)
    user_id = _graph_user(connection)
    meeting_id, transcript_id = (_checked_id(part, _EXTERNAL_ID, "Teams transcript") for part in external_id.split("::", 1))
    base = f"{GRAPH_API}/users/{quote(user_id, safe='')}/onlineMeetings/{quote(meeting_id, safe='')}"
    title, started, ended = "", None, None
    try:
        meeting = _json(
            await _request(provider, "GET", base, params={"$select": "subject,startDateTime,endDateTime"}, headers=headers), provider
        )
        title = str(meeting.get("subject") or "")
        started, ended = _parse_time(meeting.get("startDateTime")), _parse_time(meeting.get("endDateTime"))
    except ConnectorError as exc:
        if exc.reconnect:
            raise
    vtt = await _download_text(
        provider, f"{base}/transcripts/{quote(transcript_id, safe='')}/content", headers, {"$format": "text/vtt"}, max_bytes
    )
    duration = int((ended - started).total_seconds()) if started and ended and ended > started else None
    return FetchedTranscript(
        title=(title or f"Teams meeting {started.date().isoformat() if started else ''}".strip())[:200],
        started_at=started,
        duration_seconds=duration,
        vtt=vtt,
    )
