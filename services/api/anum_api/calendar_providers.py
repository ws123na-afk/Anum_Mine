"""Calendar provider clients for calendar sync (docs/calendar.md#sync).

Two providers, each through its official REST API and OAuth 2.0 (the same OAuth plumbing
as the inbox, ``email_providers``):

* **Google Calendar**: Calendar API v3 on the account's primary calendar. Incremental sync
  uses ``events.list`` with the stored ``nextSyncToken``; a 410 answer means the token
  expired and a full sync starts again.
* **Microsoft 365 / Outlook.com**: Microsoft Graph v1.0. Incremental sync uses
  ``/me/calendarView/delta`` over a fixed window; the stored cursor is the
  ``@odata.deltaLink`` (only its token is kept; requests always go to the fixed Graph path).

Repeating events (docs/calendar.md#how-events-sync): Google lists a series as its master (an
RRULE in ``recurrence``) plus the instances changed or cancelled on their own; ANUM keeps the
master as one series with its exceptions when the rule is inside ANUM's subset, and otherwise
falls back to the series' single instances (``events/{id}/instances``). Graph's
``calendarView/delta`` reports occurrences only, so Microsoft series stay flattened: each
occurrence is one ANUM event. ANUM writes its own series with each provider's own fields (an
RRULE for Google, a ``recurrence`` pattern and range for Graph), all-day events as dates, and an
occurrence's change or removal on the matching instance. Rules every client keeps (as the inbox clients do): only the
fixed provider hosts are contacted, redirects are not followed, every call has a timeout,
tokens only go in the ``Authorization`` header, errors carry the status and the provider's
short text, and everything a provider returns is untrusted data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlsplit

import httpx

from .calendar_recurrence import Frequency, Recurrence, all_day_bounds, all_day_days, valid_zone, zone_of
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
# Google series whose rule ANUM cannot hold are read as their instances; at most this many a pass.
MAX_FLATTENED_SERIES = 50
_DATE = re.compile(r"^\d{8}$")
_STAMP = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$")


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
    # Whole days (the provider gave dates): the first and the last day, inclusive.
    all_day: bool = False
    timezone: str | None = None
    # A Google series master: its rule (None when ANUM cannot hold it) and removed occurrences.
    recurrence: Recurrence | None = None
    exdates: list[datetime] = field(default_factory=list)
    recurrence_lines: list[str] = field(default_factory=list)
    # A master whose rule ANUM cannot hold: the ids of its instances read instead (flattened).
    flattened_instances: list[str] | None = None
    # One instance of a series: the master's id and the start the instance originally had.
    recurring_event_id: str | None = None
    original_start: datetime | None = None
    original_date: date | None = None
    # Graph's ``type``: ``occurrence`` (made by the rule), ``exception`` (changed on its own), ...
    instance_kind: str | None = None

    @property
    def is_master(self) -> bool:
        return bool(self.recurrence_lines)


@dataclass(frozen=True)
class RemoteInstance:
    """One occurrence of a provider series: its id and the start it originally had."""

    external_id: str
    original_start: datetime | None
    original_date: date | None = None
    cancelled: bool = False


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
    # The event carries an online meeting (Teams): its join details live in the body, so an update
    # must not replace the body.
    online_meeting: bool = False
    # Whole days in ``timezone`` (the first and last day, inclusive).
    all_day: bool = False
    start_date: date | None = None
    end_date: date | None = None
    # A series: its rule, written in the provider's own fields, expanded in ``timezone``.
    timezone: str | None = None
    recurrence: Recurrence | None = None
    # One occurrence of a series (written on the provider's instance).
    instance: bool = False


@dataclass(frozen=True)
class PushResult:
    external_id: str
    etag: str | None
    updated_at: datetime


class PushRefused(MailProviderError):
    """ANUM cannot write this change to the provider at all (the provider has no way to express it,
    or the occurrence is gone there): retrying would fail the same way, so the event is marked
    failed at once with this sentence."""

    def __init__(self, message: str) -> None:
        super().__init__(422, message)


def valid_event_id(value: object) -> bool:
    return isinstance(value, str) and _EVENT_ID.fullmatch(value) is not None


def _path_id(value: str | None) -> str:
    if not valid_event_id(value):
        raise MailProviderError(409, "The stored provider event id is not valid; sync the calendar again.")
    return quote(str(value), safe="")


def window_stale(window_end: datetime | None, until: datetime) -> bool:
    """Whether a stored sync window no longer reaches far enough ahead and needs a full refresh.

    A sync token or delta link only reports changes inside the window it was started with, so
    the window is renewed (a full sync) once its end is more than a margin behind the moving
    horizon ``until``: events that later come into range are then picked up."""
    if window_end is None:
        return True
    ahead = until - datetime.now(timezone.utc)
    margin = min(timedelta(days=30), max(ahead, timedelta(0)) / 2)
    return window_end < until - margin


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
    # True when the provider tells the attendees about every change the organiser makes (Graph has
    # no switch like Google's sendUpdates), so a change to an event with people waits for approval.
    notifies_on_update: bool = False

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

    async def instances(self, token: str, master_id: str, *, since: datetime, until: datetime) -> list[RemoteInstance]:
        """The occurrences of one series inside the window, with the start each originally had."""
        raise NotImplementedError

    async def _token(self, url: str, form: dict[str, str], previous_refresh: str | None = None) -> TokenGrant:
        _, body = await self.http.request("POST", url, form=form)
        return _grant(body, previous_refresh, self.label)


# -- Google Calendar ---------------------------------------------------------------------------


_GOOGLE_RESPONSES = {"needsAction": "invited", "accepted": "accepted", "tentative": "tentative", "declined": "declined"}


def _google_moment(value: Any) -> tuple[datetime | None, date | None, str | None]:
    """A Google start or end: (instant, date for an all-day value, the IANA zone named)."""
    if not isinstance(value, dict):
        return None, None, None
    zone = value.get("timeZone") if isinstance(value.get("timeZone"), str) and valid_zone(value["timeZone"]) else None
    if value.get("dateTime"):
        return _parse_iso(value.get("dateTime")), None, zone
    if isinstance(value.get("date"), str):
        try:
            day = date.fromisoformat(value["date"])
        except ValueError:
            return None, None, zone
        return datetime.combine(day, time(0), timezone.utc), day, zone
    return None, None, zone


def _stamp(value: str, zone_name: str | None) -> tuple[datetime | None, date | None]:
    """An RFC 5545 date or date-time (``20261110``, ``20261110T090000``, ``20261110T050000Z``):
    the instant (local times in ``zone_name``) or, for a date, the day."""
    value = value.strip()
    if _DATE.match(value):
        try:
            return None, date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        except ValueError:
            return None, None
    match = _STAMP.match(value)
    if not match:
        return None, None
    try:
        local = datetime(*(int(match[i]) for i in range(1, 7)))
    except ValueError:
        return None, None
    if match[7]:
        return local.replace(tzinfo=timezone.utc), None
    return local.replace(tzinfo=zone_of(zone_name)).astimezone(timezone.utc), None


def parse_google_recurrence(
    lines: list[str], starts_at: datetime, zone_name: str | None, all_day: bool
) -> tuple[Recurrence | None, list[datetime]]:
    """A Google master's ``recurrence`` lines as ANUM's rule and the original starts it excludes.

    The rule is None when ANUM cannot hold it (RDATE or EXRULE, more than one RRULE, a part
    outside ANUM's subset such as BYMONTH or BYHOUR, a week start other than Monday on a weekly
    rule with an interval): the caller then reads the series' instances instead."""
    rule: Recurrence | None = None
    excluded: list[datetime] = []
    zone = zone_of(zone_name)
    for raw in lines:
        if not isinstance(raw, str):
            return None, []
        head, _, body = raw.partition(":")
        name, *params = head.split(";")
        name = name.strip().upper()
        if name == "RRULE":
            if rule is not None:
                return None, []
            parts: list[str] = []
            until_local: date | None = None
            week_start = "MO"
            for part in body.split(";"):
                key, _, value = part.partition("=")
                key = key.strip().upper()
                if key == "WKST":
                    week_start = value.strip().upper()
                    continue
                if key == "UNTIL":
                    moment, day = _stamp(value, zone_name)
                    if day is not None:
                        until_local = day
                    elif moment is not None:
                        # The last day an occurrence may start on: the UNTIL instant's local day,
                        # or the day before when the series' time of day is later than it.
                        local = moment.astimezone(zone)
                        wall = starts_at.astimezone(zone).time()
                        until_local = local.date() if all_day or local.time() >= wall else local.date() - timedelta(days=1)
                    else:
                        return None, []
                    continue
                parts.append(part)
            try:
                rule = Recurrence.parse(";".join(parts))
                if until_local is not None:
                    rule = rule.model_copy(update={"until": until_local})
                    rule = Recurrence.model_validate(rule.model_dump())
            except ValueError:
                return None, []
            if week_start != "MO" and rule.freq == Frequency.WEEKLY and rule.interval > 1 and len(rule.by_weekday) > 1:
                return None, []
        elif name == "EXDATE":
            zone_param = next((p.split("=", 1)[1] for p in params if p.upper().startswith("TZID=")), None)
            for value in body.split(","):
                moment, day = _stamp(value, zone_param or zone_name)
                if day is not None:
                    moment = all_day_bounds(day, day, zone_name)[0] if all_day else None
                if moment is None:
                    return None, []
                excluded.append(moment)
        else:
            return None, []
    return rule, excluded


def google_event(item: dict[str, Any]) -> RemoteEvent | None:
    """One Google event, or None when it has no usable id or times."""
    external_id = item.get("id")
    if not valid_event_id(external_id):
        return None
    cancelled = item.get("status") == "cancelled"
    starts_at, start_day, zone = _google_moment(item.get("start"))
    ends_at, end_day, end_zone = _google_moment(item.get("end"))
    zone = zone or end_zone
    all_day = start_day is not None
    updated = _parse_iso(item.get("updated")) or datetime.now(timezone.utc)
    original_start, original_day, _ = _google_moment(item.get("originalStartTime"))
    recurring = item.get("recurringEventId")
    if starts_at is None or ends_at is None:
        if not cancelled:
            return None
        # A cancelled occurrence may come back as an id and status only.
        starts_at = ends_at = updated
        all_day = False
    if all_day and start_day is not None and end_day is not None:
        # Google's end date is exclusive; the instants are that day's midnights in the event's zone
        # (UTC when Google names none).
        starts_at, ends_at = all_day_bounds(start_day, max(start_day, end_day - timedelta(days=1)), zone or "UTC")
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
    lines = [line for line in item.get("recurrence") or [] if isinstance(line, str)][:20]
    rule, excluded = parse_google_recurrence(lines, starts_at, zone or "UTC", all_day) if lines and not cancelled else (None, [])
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
        all_day=all_day,
        timezone=zone or ("UTC" if all_day or lines else None),
        recurrence=rule,
        exdates=excluded,
        recurrence_lines=lines,
        recurring_event_id=str(recurring) if valid_event_id(recurring) else None,
        original_start=original_start if original_day is None else None,
        original_date=original_day,
    )


def _local_wall(moment: datetime, zone_name: str | None) -> str:
    return moment.astimezone(zone_of(zone_name)).strftime("%Y-%m-%dT%H:%M:%S")


def google_rrule(rule: Recurrence, starts_at: datetime, zone_name: str | None, all_day: bool) -> str:
    """ANUM's rule as Google's RRULE line. UNTIL is a date for an all-day series and the end of
    that local day in UTC for a timed one (RFC 5545 wants UTC with a zoned start)."""
    text = rule.model_copy(update={"until": None}).rrule() if rule.until else rule.rrule()
    if rule.until is not None:
        if all_day:
            text += f";UNTIL={rule.until:%Y%m%d}"
        else:
            last = datetime.combine(rule.until, time(23, 59, 59), zone_of(zone_name)).astimezone(timezone.utc)
            text += f";UNTIL={last:%Y%m%dT%H%M%SZ}"
    return f"RRULE:{text}"


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
        # Series come as their master and their changed instances (``singleEvents=false``). A cursor
        # from before series were read (no ``|series`` mark) starts a full sync once.
        delta = CalendarDelta(full=True)
        params: dict[str, str] = {"singleEvents": "false", "showDeleted": "true", "maxResults": "250"}
        kind, _, rest = (cursor or "").partition(":")
        value, _, tail = rest.partition("|")
        end_text, _, mode = tail.partition("|")
        window_end = _parse_iso(end_text)
        current = mode == "series"
        if kind == "sync" and current and _GOOGLE_TOKEN.fullmatch(value) and not window_stale(window_end, until):
            # The token reports changes inside the window it was started with (timeMax below).
            params["syncToken"] = value
            delta.full = False
        elif kind == "page" and current and _GOOGLE_TOKEN.fullmatch(value) and window_end is not None:
            # A full sync that stopped at the page limit resumes where it stopped, in its own window.
            until = window_end
            params.update(timeMin=_iso(since), timeMax=_iso(until), pageToken=value)
        else:
            params.update(timeMin=_iso(since), timeMax=_iso(until))
        window = _iso(until)
        for _ in range(MAX_PAGES):
            status, body = await self.http.request("GET", self.EVENTS, token=token, params=params, allow=frozenset({410}))
            if status == 410:
                # The sync token expired: start again with a full sync.
                return await self.changes(token, None, since=since, until=until)
            body = body if isinstance(body, dict) else {}
            for item in body.get("items") or []:
                if isinstance(item, dict):
                    found = google_event(item)
                    if found is not None:
                        delta.events.append(found)
            page = body.get("nextPageToken")
            if isinstance(page, str) and _GOOGLE_TOKEN.fullmatch(page):
                params = {k: v for k, v in params.items() if k != "pageToken"}
                params["pageToken"] = page
                continue
            sync = body.get("nextSyncToken")
            if isinstance(sync, str) and _GOOGLE_TOKEN.fullmatch(sync):
                # Keep the window's end: a token from an older, shorter window is renewed in time.
                kept = window if delta.full or not end_text else end_text
                delta.cursor = f"sync:{sync}|{kept}|series"
            await self._flatten(token, delta, since=since, until=until)
            return delta
        delta.more = True
        delta.cursor = f"page:{params['pageToken']}|{window}|series"
        await self._flatten(token, delta, since=since, until=until)
        return delta

    async def _flatten(self, token: str, delta: CalendarDelta, *, since: datetime, until: datetime) -> None:
        """Masters whose rule ANUM cannot hold: read their instances as single events instead."""
        done = 0
        for master in [e for e in delta.events if e.is_master and e.recurrence is None and not e.cancelled]:
            if done >= MAX_FLATTENED_SERIES:
                break
            done += 1
            found = await self.instance_events(token, master.external_id, since=since, until=until)
            master.flattened_instances = [e.external_id for e in found]
            delta.events.extend(found)

    async def instance_events(self, token: str, master_id: str, *, since: datetime, until: datetime) -> list[RemoteEvent]:
        """The instances of one series inside the window (``events/{id}/instances``)."""
        params: dict[str, str] = {"timeMin": _iso(since), "timeMax": _iso(until), "maxResults": "250", "showDeleted": "true"}
        out: list[RemoteEvent] = []
        for _ in range(MAX_PAGES):
            _, body = await self.http.request("GET", f"{self.EVENTS}/{_path_id(master_id)}/instances", token=token, params=params)
            body = body if isinstance(body, dict) else {}
            for item in body.get("items") or []:
                if isinstance(item, dict):
                    found = google_event(item)
                    if found is not None:
                        out.append(found)
            page = body.get("nextPageToken")
            if not (isinstance(page, str) and _GOOGLE_TOKEN.fullmatch(page)):
                break
            params["pageToken"] = page
        return out

    async def instances(self, token: str, master_id: str, *, since: datetime, until: datetime) -> list[RemoteInstance]:
        return [
            RemoteInstance(e.external_id, e.original_start, e.original_date, e.cancelled)
            for e in await self.instance_events(token, master_id, since=since, until=until)
        ]

    @staticmethod
    def _body(event: OutgoingEvent) -> dict[str, Any]:
        body: dict[str, Any] = {
            "summary": event.title,
            "description": event.description or "",
            "location": event.location or "",
            "start": {"dateTime": _iso(event.starts_at), "timeZone": "UTC"},
            "end": {"dateTime": _iso(event.ends_at), "timeZone": "UTC"},
        }
        if event.all_day and event.start_date is not None and event.end_date is not None:
            # Dates; Google's end date is the day after the last one. The other key is cleared so an
            # update can turn a timed event into an all-day one.
            body["start"] = {"date": event.start_date.isoformat(), "dateTime": None, "timeZone": event.timezone or "UTC"}
            body["end"] = {"date": (event.end_date + timedelta(days=1)).isoformat(), "dateTime": None, "timeZone": event.timezone or "UTC"}
        elif event.recurrence is not None or event.instance:
            # A series repeats at its wall-clock time in its own zone.
            zone = event.timezone or "UTC"
            body["start"] = {"dateTime": _local_wall(event.starts_at, zone), "timeZone": zone, "date": None}
            body["end"] = {"dateTime": _local_wall(event.ends_at, zone), "timeZone": zone, "date": None}
        if event.recurrence is not None:
            body["recurrence"] = [google_rrule(event.recurrence, event.starts_at, event.timezone, event.all_day)]
        if event.instance:
            # Restores an occurrence that was cancelled on its own.
            body["status"] = "confirmed"
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
    all_day = bool(item.get("isAllDay"))
    if all_day:
        # Graph gives an all-day event as midnights in the asked zone (UTC): whole UTC days.
        first, last = all_day_days(starts_at, ends_at, "UTC")
        starts_at, ends_at = all_day_bounds(first, last, "UTC")
    master = item.get("seriesMasterId")
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
        all_day=all_day,
        timezone="UTC" if all_day else None,
        recurring_event_id=str(master) if valid_event_id(master) else None,
        original_start=_parse_iso(item.get("originalStart")),
        instance_kind=str(item.get("type")) if isinstance(item.get("type"), str) else None,
    )


_GRAPH_DAYS = {"MO": "monday", "TU": "tuesday", "WE": "wednesday", "TH": "thursday", "FR": "friday", "SA": "saturday", "SU": "sunday"}
_GRAPH_INDEX = {1: "first", 2: "second", 3: "third", 4: "fourth", -1: "last"}


def graph_recurrence(rule: Recurrence, starts_at: datetime, zone_name: str | None) -> dict[str, Any]:
    """ANUM's rule as Graph's ``patternedRecurrence`` (pattern and range), in the series' zone."""
    zone = zone_name or "UTC"
    first = starts_at.astimezone(zone_of(zone)).date()
    weekday = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")[first.weekday()]
    pattern: dict[str, Any] = {"interval": rule.interval, "firstDayOfWeek": "monday"}
    if rule.freq == Frequency.DAILY:
        pattern["type"] = "daily"
    elif rule.freq == Frequency.WEEKLY:
        pattern.update(type="weekly", daysOfWeek=[_GRAPH_DAYS[d] for d in (rule.by_weekday or [weekday])])
    elif rule.freq == Frequency.MONTHLY and rule.by_setpos is not None:
        pattern.update(type="relativeMonthly", daysOfWeek=[_GRAPH_DAYS[rule.by_weekday[0]]], index=_GRAPH_INDEX[rule.by_setpos])
    elif rule.freq == Frequency.MONTHLY:
        day = rule.by_monthday if rule.by_monthday is not None else first.day
        if day < 1:
            raise PushRefused(
                "Microsoft calendars cannot repeat on the last day of the month. Choose a day of the month or the last weekday instead."
            )
        pattern.update(type="absoluteMonthly", dayOfMonth=day)
    else:
        pattern.update(type="absoluteYearly", dayOfMonth=first.day, month=first.month)
    span: dict[str, Any] = {"startDate": first.isoformat(), "recurrenceTimeZone": zone}
    if rule.until is not None:
        span.update(type="endDate", endDate=rule.until.isoformat())
    elif rule.count is not None:
        span.update(type="numbered", numberOfOccurrences=rule.count)
    else:
        span["type"] = "noEnd"
    return {"pattern": pattern, "range": span}


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
    notifies_on_update = True
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
        if resumed is not None and not window_stale(window_end, until):
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
    def _body(event: OutgoingEvent, *, update: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {
            "subject": event.title,
            "location": {"displayName": event.location or ""},
            "start": {"dateTime": event.starts_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
            "end": {"dateTime": event.ends_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
        }
        if event.all_day and event.start_date is not None and event.end_date is not None:
            zone = event.timezone or "UTC"
            body["isAllDay"] = True
            body["start"] = {"dateTime": f"{event.start_date.isoformat()}T00:00:00", "timeZone": zone}
            body["end"] = {"dateTime": f"{(event.end_date + timedelta(days=1)).isoformat()}T00:00:00", "timeZone": zone}
        elif event.recurrence is not None or event.instance:
            zone = event.timezone or "UTC"
            body["isAllDay"] = False
            body["start"] = {"dateTime": _local_wall(event.starts_at, zone), "timeZone": zone}
            body["end"] = {"dateTime": _local_wall(event.ends_at, zone), "timeZone": zone}
        if event.recurrence is not None:
            body["recurrence"] = graph_recurrence(event.recurrence, event.starts_at, event.timezone)
        # Replacing the body of a Teams meeting would drop its join details: leave it alone then.
        if not (update and event.online_meeting):
            body["body"] = {"contentType": "text", "content": event.description or ""}
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
            "PATCH", f"{self.API}/events/{_path_id(external_id)}", token=token, json_body=self._body(event, update=True), headers=self.HEADERS
        )
        return self._pushed(body)

    async def instances(self, token: str, master_id: str, *, since: datetime, until: datetime) -> list[RemoteInstance]:
        path = f"{self.API}/events/{_path_id(master_id)}/instances"
        params: dict[str, str] | None = {"startDateTime": _iso(since), "endDateTime": _iso(until), "$select": "id,originalStart,isCancelled"}
        url = path
        out: list[RemoteInstance] = []
        for _ in range(MAX_PAGES):
            _, body = await self.http.request("GET", url, token=token, params=params, headers=self.HEADERS)
            body = body if isinstance(body, dict) else {}
            for item in body.get("value") or []:
                if isinstance(item, dict) and valid_event_id(item.get("id")):
                    out.append(RemoteInstance(str(item["id"]), _parse_iso(item.get("originalStart")), None, bool(item.get("isCancelled"))))
            next_link = body.get("@odata.nextLink")
            if not next_link:
                break
            parts = urlsplit(str(next_link))
            if parts.scheme != "https" or parts.netloc != "graph.microsoft.com" or unquote(parts.path) != unquote(urlsplit(path).path):
                raise MailProviderError(502, "Microsoft Graph returned a paging link to another place.")
            url, params = f"{path}?{parts.query}", None
        return out

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
    "PushRefused",
    "PushResult",
    "RemoteInstance",
    "RemoteAttendee",
    "RemoteEvent",
    "google_event",
    "google_rrule",
    "graph_event",
    "graph_recurrence",
    "parse_google_recurrence",
]
