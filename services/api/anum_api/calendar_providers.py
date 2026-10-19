"""Calendar provider clients for calendar sync (docs/calendar.md#sync).

Two providers, each through its official REST API and OAuth 2.0 (the same OAuth plumbing
as the inbox, ``email_providers``):

* **Google Calendar**: Calendar API v3 on the account's primary calendar. Incremental sync
  uses ``events.list`` with the stored ``nextSyncToken``; a 410 answer means the token
  expired and a full sync starts again.
* **Microsoft 365 / Outlook.com**: Microsoft Graph v1.0. Incremental sync uses
  ``/me/calendarView/delta`` over a fixed window; the stored cursor is the
  ``@odata.deltaLink`` (only its token is kept; requests always go to the fixed Graph path).

Recurring events arrive as their single occurrences (``singleEvents`` and ``calendarView``),
each stored as one ANUM event. Rules every client keeps (as the inbox clients do): only the
fixed provider hosts are contacted, redirects are not followed, every call has a timeout,
tokens only go in the ``Authorization`` header, errors carry the status and the provider's
short text, and everything a provider returns is untrusted data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlsplit

import httpx

from .email_providers import (
    MailProviderError,
    OAuthClient,
    TokenGrant,
    _grant,
    _Http,
)

# Microsoft tenant (operator setting): an alias, a GUID or a domain; never a path or a host.
_MICROSOFT_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}(?:\.[A-Za-z0-9][A-Za-z0-9-]{0,62}){0,5}")
# Google and Graph event ids: base32hex (Google) or base64url-like (Graph). No dots or slashes
# survive ``quote(..., safe="")``, so an id can never add a path segment.
_EVENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9+/=_-]{0,1023}")
_GRAPH_DELTA_PATH = re.compile(r"/v1\.0/me/calendarView/delta")
_GRAPH_PAGING_KEYS = ("$deltatoken", "$skiptoken")
_GRAPH_TOKEN = re.compile(r"[A-Za-z0-9._~=%-]{1,4096}")
_GOOGLE_TOKEN = re.compile(r"[A-Za-z0-9._~=+/-]{1,4096}")
_EMAIL = re.compile(r"^[^@\s<>\"']{1,64}@[^@\s<>\"']{1,253}\.[A-Za-z]{2,}$")
MAX_PAGES = 25


class CalendarProvider(StrEnum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"


PROVIDER_NAMES = {
    CalendarProvider.GOOGLE: "Google Calendar",
    CalendarProvider.MICROSOFT: "Microsoft 365 Calendar",
}

GOOGLE_SCOPES = ("openid", "email", "https://www.googleapis.com/auth/calendar.events")
MICROSOFT_SCOPES = ("offline_access", "User.Read", "Calendars.ReadWrite")


@dataclass(frozen=True)
class CalendarAccount:
    email: str
    display_name: str | None = None


@dataclass
class RemoteAttendee:
    email: str
    name: str
    response: str  # invited, accepted, tentative, declined
    organizer: bool = False


@dataclass
class RemoteEvent:
    """One occurrence as the provider has it, already normalised to UTC."""

    external_id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    updated_at: datetime
    etag: str | None = None
    description: str | None = None
    location: str | None = None
    conference_url: str | None = None
    cancelled: bool = False
    is_organizer: bool = True
    organizer_email: str | None = None
    attendees: list[RemoteAttendee] = field(default_factory=list)


@dataclass
class CalendarDelta:
    events: list[RemoteEvent] = field(default_factory=list)
    # External ids removed at the provider (Graph ``@removed``).
    removed: list[str] = field(default_factory=list)
    cursor: str | None = None
    full: bool = False
    # True when the page limit was reached; the cursor resumes from the next page.
    more: bool = False


@dataclass(frozen=True)
class OutgoingEvent:
    """What ANUM writes at the provider. ``attendees`` are only sent when invitations were approved."""

    title: str
    starts_at: datetime
    ends_at: datetime
    description: str | None = None
    location: str | None = None
    attendees: tuple[tuple[str, str], ...] | None = None  # (email, name); None leaves them unchanged


@dataclass(frozen=True)
class PushResult:
    external_id: str
    etag: str | None
    updated_at: datetime


def valid_event_id(value: object) -> bool:
    return isinstance(value, str) and _EVENT_ID.fullmatch(value) is not None


def _path_id(value: str | None) -> str:
    if not valid_event_id(value):
        raise MailProviderError(409, "The stored provider event id is not valid; sync the calendar again.")
    return quote(str(value), safe="")


def _clean(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:limit]


def _https(value: Any) -> str | None:
    if isinstance(value, str) and re.match(r"^https://[^\s/$.?#][^\s]{0,490}$", value.strip()):
        return value.strip()
    return None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    # Graph writes seven fractional digits; Python reads at most six.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _email(value: Any) -> str | None:
    if isinstance(value, str) and _EMAIL.match(value.strip()):
        return value.strip().lower()
    return None


class CalendarProviderClient:
    provider: CalendarProvider
    label: str
    scopes: tuple[str, ...]
    uses_pkce: bool = True

    def __init__(self, oauth: OAuthClient, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.oauth = oauth
        self.http = _Http(transport, self.label)

    def authorization_url(self, state: str, code_challenge: str | None) -> str:
        raise NotImplementedError

    async def exchange_code(self, code: str, code_verifier: str | None) -> TokenGrant:
        raise NotImplementedError

    async def refresh(self, refresh_token: str) -> TokenGrant:
        raise NotImplementedError

    async def revoke(self, token: str) -> bool:
        return False

    async def account(self, token: str) -> CalendarAccount:
        raise NotImplementedError

    async def changes(self, token: str, cursor: str | None, *, since: datetime, until: datetime) -> CalendarDelta:
        raise NotImplementedError

    async def create(self, token: str, event: OutgoingEvent, *, notify: bool) -> PushResult:
        raise NotImplementedError

    async def update(self, token: str, external_id: str, event: OutgoingEvent, *, notify: bool) -> PushResult:
        raise NotImplementedError

    async def delete(self, token: str, external_id: str, *, notify: bool) -> None:
        raise NotImplementedError

    async def _token(self, url: str, form: dict[str, str], previous_refresh: str | None = None) -> TokenGrant:
        _, body = await self.http.request("POST", url, form=form)
        return _grant(body, previous_refresh, self.label)


# -- Google Calendar ---------------------------------------------------------------------------


_GOOGLE_RESPONSES = {"needsAction": "invited", "accepted": "accepted", "tentative": "tentative", "declined": "declined"}


def google_event(item: dict[str, Any]) -> RemoteEvent | None:
    """One Google event, or None when it has no usable id or times."""
    external_id = item.get("id")
    if not valid_event_id(external_id):
        return None
    cancelled = item.get("status") == "cancelled"
    start = item.get("start") or {}
    end = item.get("end") or {}
    starts_at = _parse_iso(start.get("dateTime")) or _parse_iso(f"{start.get('date')}T00:00:00Z" if start.get("date") else None)
    ends_at = _parse_iso(end.get("dateTime")) or _parse_iso(f"{end.get('date')}T00:00:00Z" if end.get("date") else None)
    updated = _parse_iso(item.get("updated")) or datetime.now(timezone.utc)
    if starts_at is None or ends_at is None:
        if not cancelled:
            return None
        # A cancelled occurrence may come back as an id and status only.
        starts_at = ends_at = updated
    organizer = item.get("organizer") or {}
    attendees: list[RemoteAttendee] = []
    for person in item.get("attendees") or []:
        if not isinstance(person, dict) or person.get("resource"):
            continue
        address = _email(person.get("email"))
        if address is None:
            continue
        attendees.append(
            RemoteAttendee(
                email=address,
                name=_clean(person.get("displayName"), 120) or address.split("@")[0],
                response=_GOOGLE_RESPONSES.get(str(person.get("responseStatus")), "invited"),
                organizer=bool(person.get("organizer")),
            )
        )
    conference = _https(item.get("hangoutLink"))
    if conference is None:
        for point in (item.get("conferenceData") or {}).get("entryPoints") or []:
            if isinstance(point, dict) and point.get("entryPointType") == "video":
                conference = _https(point.get("uri"))
                break
    return RemoteEvent(
        external_id=str(external_id),
        title=_clean(item.get("summary"), 160) or "(no title)",
        starts_at=starts_at,
        ends_at=ends_at,
        updated_at=updated,
        etag=_clean(item.get("etag"), 200) or None,
        description=_text(item.get("description"), 2000),
        location=_text(item.get("location"), 200),
        conference_url=conference,
        cancelled=cancelled,
        is_organizer=bool(organizer.get("self", True)),
        organizer_email=_email(organizer.get("email")),
        attendees=attendees,
    )


class GoogleCalendarClient(CalendarProviderClient):
    provider = CalendarProvider.GOOGLE
    label = "Google Calendar"
    scopes = GOOGLE_SCOPES
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    GRANT_URL = "https://oauth2.googleapis.com/token"
    REVOKE_URL = "https://oauth2.googleapis.com/revoke"
    USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
    EVENTS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

    def authorization_url(self, state: str, code_challenge: str | None) -> str:
        params = {
            "client_id": self.oauth.client_id,
            "redirect_uri": self.oauth.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        if code_challenge:
            params.update(code_challenge=code_challenge, code_challenge_method="S256")
        return f"{self.AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str | None) -> TokenGrant:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.oauth.client_id,
            "client_secret": self.oauth.client_secret,
            "redirect_uri": self.oauth.redirect_uri,
        }
        if code_verifier:
            form["code_verifier"] = code_verifier
        return await self._token(self.GRANT_URL, form)

    async def refresh(self, refresh_token: str) -> TokenGrant:
        return await self._token(
            self.GRANT_URL,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.oauth.client_id,
                "client_secret": self.oauth.client_secret,
            },
            refresh_token,
        )

    async def revoke(self, token: str) -> bool:
        await self.http.request("POST", self.REVOKE_URL, form={"token": token}, allow=frozenset({400}))
        return True

    async def account(self, token: str) -> CalendarAccount:
        _, body = await self.http.request("GET", self.USERINFO_URL, token=token)
        body = body if isinstance(body, dict) else {}
        address = _email(body.get("email"))
        if address is None:
            raise MailProviderError(502, "Google did not return the account's email address.")
        return CalendarAccount(email=address, display_name=_clean(body.get("name"), 200) or None)

    async def changes(self, token: str, cursor: str | None, *, since: datetime, until: datetime) -> CalendarDelta:
        delta = CalendarDelta(full=True)
        params: dict[str, str] = {"singleEvents": "true", "showDeleted": "true", "maxResults": "250"}
        if cursor and cursor.startswith("sync:") and _GOOGLE_TOKEN.fullmatch(cursor[5:]):
            params["syncToken"] = cursor[5:]
            delta.full = False
        elif cursor and cursor.startswith("page:") and _GOOGLE_TOKEN.fullmatch(cursor[5:]):
            # A full sync that stopped at the page limit resumes where it stopped.
            params.update(timeMin=_iso(since), pageToken=cursor[5:])
        else:
            params["timeMin"] = _iso(since)
        for _ in range(MAX_PAGES):
            status, body = await self.http.request("GET", self.EVENTS, token=token, params=params, allow=frozenset({410}))
            if status == 410:
                # The sync token expired: start again with a full sync.
                return await self.changes(token, None, since=since, until=until)
            body = body if isinstance(body, dict) else {}
            for item in body.get("items") or []:
                if isinstance(item, dict):
                    found = google_event(item)
                    if found is not None and (found.cancelled or found.starts_at < until):
                        delta.events.append(found)
            page = body.get("nextPageToken")
            if isinstance(page, str) and _GOOGLE_TOKEN.fullmatch(page):
                params = {k: v for k, v in params.items() if k != "pageToken"}
                params["pageToken"] = page
                continue
            sync = body.get("nextSyncToken")
            delta.cursor = f"sync:{sync}" if isinstance(sync, str) and _GOOGLE_TOKEN.fullmatch(sync) else None
            return delta
        delta.more = True
        delta.cursor = f"page:{params['pageToken']}"
        return delta

    @staticmethod
    def _body(event: OutgoingEvent) -> dict[str, Any]:
        body: dict[str, Any] = {
            "summary": event.title,
            "description": event.description or "",
            "location": event.location or "",
            "start": {"dateTime": _iso(event.starts_at), "timeZone": "UTC"},
            "end": {"dateTime": _iso(event.ends_at), "timeZone": "UTC"},
        }
        if event.attendees is not None:
            body["attendees"] = [{"email": email, "displayName": name} for email, name in event.attendees]
        return body

    @staticmethod
    def _pushed(body: Any) -> PushResult:
        found = google_event(body) if isinstance(body, dict) else None
        if found is None:
            raise MailProviderError(502, "Google Calendar did not return the saved event.")
        return PushResult(found.external_id, found.etag, found.updated_at)

    async def create(self, token: str, event: OutgoingEvent, *, notify: bool) -> PushResult:
        _, body = await self.http.request(
            "POST", self.EVENTS, token=token, params={"sendUpdates": "all" if notify else "none"}, json_body=self._body(event)
        )
        return self._pushed(body)

    async def update(self, token: str, external_id: str, event: OutgoingEvent, *, notify: bool) -> PushResult:
        _, body = await self.http.request(
            "PATCH", f"{self.EVENTS}/{_path_id(external_id)}", token=token,
            params={"sendUpdates": "all" if notify else "none"}, json_body=self._body(event),
        )
        return self._pushed(body)

    async def delete(self, token: str, external_id: str, *, notify: bool) -> None:
        await self.http.request(
            "DELETE", f"{self.EVENTS}/{_path_id(external_id)}", token=token,
            params={"sendUpdates": "all" if notify else "none"}, allow=frozenset({404, 410}),
        )


# -- Microsoft Graph ----------------------------------------------------------------------------


_GRAPH_RESPONSES = {
    "none": "invited",
    "notResponded": "invited",
    "accepted": "accepted",
    "organizer": "accepted",
    "tentativelyAccepted": "tentative",
    "declined": "declined",
}


def _graph_time(value: Any) -> datetime | None:
    if not isinstance(value, dict):
        return None
    parsed = _parse_iso(value.get("dateTime"))
    # Requests ask for UTC (Prefer: outlook.timezone="UTC"); anything else is refused.
    zone = str(value.get("timeZone") or "UTC")
    return parsed if zone.upper() in {"UTC", "ETC/UTC", "COORDINATED UNIVERSAL TIME"} else None


def graph_event(item: dict[str, Any]) -> RemoteEvent | None:
    external_id = item.get("id")
    if not valid_event_id(external_id):
        return None
    starts_at = _graph_time(item.get("start"))
    ends_at = _graph_time(item.get("end"))
    if starts_at is None or ends_at is None:
        return None
    organizer = ((item.get("organizer") or {}).get("emailAddress") or {})
    attendees: list[RemoteAttendee] = []
    for person in item.get("attendees") or []:
        if not isinstance(person, dict) or person.get("type") == "resource":
            continue
        address_obj = person.get("emailAddress") or {}
        address = _email(address_obj.get("address"))
        if address is None:
            continue
        attendees.append(
            RemoteAttendee(
                email=address,
                name=_clean(address_obj.get("name"), 120) or address.split("@")[0],
                response=_GRAPH_RESPONSES.get(str((person.get("status") or {}).get("response")), "invited"),
            )
        )
    body = item.get("body") or {}
    preview = item.get("bodyPreview")
    description = _text(preview, 2000) if body.get("contentType") != "text" else _text(body.get("content"), 2000)
    return RemoteEvent(
        external_id=str(external_id),
        title=_clean(item.get("subject"), 160) or "(no title)",
        starts_at=starts_at,
        ends_at=ends_at,
        updated_at=_parse_iso(item.get("lastModifiedDateTime")) or datetime.now(timezone.utc),
        etag=_clean(item.get("changeKey"), 200) or None,
        description=description,
        location=_text((item.get("location") or {}).get("displayName"), 200),
        conference_url=_https((item.get("onlineMeeting") or {}).get("joinUrl")),
        cancelled=bool(item.get("isCancelled")),
        is_organizer=bool(item.get("isOrganizer", True)),
        organizer_email=_email(organizer.get("address")),
        attendees=attendees,
    )


def _graph_delta_query(link: str) -> str | None:
    """The paging query of a Graph calendarView delta link, or None when it is anything else."""
    parts = urlsplit(link)
    if parts.scheme != "https" or parts.netloc != "graph.microsoft.com" or not _GRAPH_DELTA_PATH.fullmatch(unquote(parts.path)):
        return None
    for pair in parts.query.split("&"):
        key, _, value = pair.partition("=")
        if key in _GRAPH_PAGING_KEYS and _GRAPH_TOKEN.fullmatch(value):
            return f"{key}={value}"
    return None


class MicrosoftCalendarClient(CalendarProviderClient):
    provider = CalendarProvider.MICROSOFT
    label = "Microsoft Graph"
    scopes = MICROSOFT_SCOPES
    API = "https://graph.microsoft.com/v1.0/me"
    HEADERS = {"prefer": 'outlook.timezone="UTC", odata.maxpagesize=50'}

    def __init__(self, oauth: OAuthClient, transport: httpx.AsyncBaseTransport | None = None, *, tenant: str = "common") -> None:
        super().__init__(oauth, transport)
        if not _MICROSOFT_TENANT.fullmatch(tenant):
            raise ValueError("The Microsoft tenant must be common, organizations, consumers, a tenant id or a domain")
        base = f"https://login.microsoftonline.com/{quote(tenant, safe='')}/oauth2/v2.0"
        self.auth_url = f"{base}/authorize"
        self.token_url = f"{base}/token"

    def authorization_url(self, state: str, code_challenge: str | None) -> str:
        params = {
            "client_id": self.oauth.client_id,
            "redirect_uri": self.oauth.redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "scope": " ".join(self.scopes),
            "prompt": "select_account",
            "state": state,
        }
        if code_challenge:
            params.update(code_challenge=code_challenge, code_challenge_method="S256")
        return f"{self.auth_url}?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str | None) -> TokenGrant:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.oauth.client_id,
            "client_secret": self.oauth.client_secret,
            "redirect_uri": self.oauth.redirect_uri,
            "scope": " ".join(self.scopes),
        }
        if code_verifier:
            form["code_verifier"] = code_verifier
        return await self._token(self.token_url, form)

    async def refresh(self, refresh_token: str) -> TokenGrant:
        # Microsoft rotates refresh tokens: the new one replaces the stored one.
        return await self._token(
            self.token_url,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.oauth.client_id,
                "client_secret": self.oauth.client_secret,
                "scope": " ".join(self.scopes),
            },
            refresh_token,
        )

    async def account(self, token: str) -> CalendarAccount:
        _, body = await self.http.request("GET", self.API, token=token, params={"$select": "mail,userPrincipalName,displayName"})
        body = body if isinstance(body, dict) else {}
        address = _email(body.get("mail")) or _email(body.get("userPrincipalName"))
        if address is None:
            raise MailProviderError(502, "Microsoft Graph did not return the account's email address.")
        return CalendarAccount(email=address, display_name=_clean(body.get("displayName"), 200) or None)

    async def changes(self, token: str, cursor: str | None, *, since: datetime, until: datetime) -> CalendarDelta:
        delta_url = f"{self.API}/calendarView/delta"
        state: dict[str, Any] = {}
        if cursor:
            try:
                state = json.loads(cursor)
            except ValueError:
                state = {}
        window_end = _parse_iso(state.get("until")) if isinstance(state, dict) else None
        resumed = _graph_delta_query(str(state.get("link") or "")) if isinstance(state, dict) else None
        # The delta window is fixed when it starts; start a new window before it runs out.
        if resumed is not None and window_end is not None and window_end > datetime.now(timezone.utc) + timedelta(days=30):
            url: str | None = f"{delta_url}?{resumed}"
            params: dict[str, str] | None = None
            result = CalendarDelta(full=False)
        else:
            window_end = until
            url = delta_url
            params = {"startDateTime": _iso(since), "endDateTime": _iso(until)}
            result = CalendarDelta(full=True)
        for _ in range(MAX_PAGES * 4):
            if url is None:
                break
            status, body = await self.http.request("GET", url, token=token, params=params, headers=self.HEADERS, allow=frozenset({410}))
            if status == 410:
                return await self.changes(token, None, since=since, until=until)
            params = None
            body = body if isinstance(body, dict) else {}
            for item in body.get("value") or []:
                if not isinstance(item, dict) or not valid_event_id(item.get("id")):
                    continue
                if "@removed" in item:
                    result.removed.append(str(item["id"]))
                    continue
                found = graph_event(item)
                if found is not None:
                    result.events.append(found)
            delta_link = body.get("@odata.deltaLink")
            if delta_link:
                if _graph_delta_query(str(delta_link)) is None:
                    raise MailProviderError(502, "Microsoft Graph returned a delta link to another host.")
                result.cursor = json.dumps({"link": str(delta_link), "until": _iso(window_end)}, separators=(",", ":"))
                return result
            next_link = body.get("@odata.nextLink")
            if not next_link:
                url = None
                continue
            paging = _graph_delta_query(str(next_link))
            if paging is None:
                raise MailProviderError(502, "Microsoft Graph returned a paging link to another host.")
            url = f"{delta_url}?{paging}"
        if url is not None:
            result.more = True
            result.cursor = json.dumps({"link": f"{delta_url}?{url.split('?', 1)[1]}", "until": _iso(window_end)}, separators=(",", ":"))
        return result

    @staticmethod
    def _body(event: OutgoingEvent) -> dict[str, Any]:
        body: dict[str, Any] = {
            "subject": event.title,
            "body": {"contentType": "text", "content": event.description or ""},
            "location": {"displayName": event.location or ""},
            "start": {"dateTime": event.starts_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
            "end": {"dateTime": event.ends_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
        }
        if event.attendees is not None:
            body["attendees"] = [
                {"emailAddress": {"address": email, "name": name}, "type": "required"} for email, name in event.attendees
            ]
        return body

    @staticmethod
    def _pushed(body: Any) -> PushResult:
        found = graph_event(body) if isinstance(body, dict) else None
        if found is None:
            raise MailProviderError(502, "Microsoft Graph did not return the saved event.")
        return PushResult(found.external_id, found.etag, found.updated_at)

    async def create(self, token: str, event: OutgoingEvent, *, notify: bool) -> PushResult:
        # Graph sends invitations to the attendees of an event the account organises; without
        # approved invitations ANUM sends no attendees, so nobody is notified.
        _, body = await self.http.request("POST", f"{self.API}/events", token=token, json_body=self._body(event), headers=self.HEADERS)
        return self._pushed(body)

    async def update(self, token: str, external_id: str, event: OutgoingEvent, *, notify: bool) -> PushResult:
        _, body = await self.http.request(
            "PATCH", f"{self.API}/events/{_path_id(external_id)}", token=token, json_body=self._body(event), headers=self.HEADERS
        )
        return self._pushed(body)

    async def delete(self, token: str, external_id: str, *, notify: bool) -> None:
        if notify:
            # Cancelling (rather than deleting) tells the attendees.
            await self.http.request(
                "POST", f"{self.API}/events/{_path_id(external_id)}/cancel", token=token,
                json_body={"comment": ""}, allow=frozenset({404, 410}),
            )
            return
        await self.http.request("DELETE", f"{self.API}/events/{_path_id(external_id)}", token=token, allow=frozenset({404, 410}))


__all__ = [
    "CalendarAccount",
    "CalendarDelta",
    "CalendarProvider",
    "CalendarProviderClient",
    "GoogleCalendarClient",
    "MicrosoftCalendarClient",
    "OutgoingEvent",
    "PROVIDER_NAMES",
    "PushResult",
    "RemoteAttendee",
    "RemoteEvent",
    "google_event",
    "graph_event",
]
