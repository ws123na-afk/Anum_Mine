"""Mail provider clients for the unified inbox (docs/email.md).

Three providers, each through its official REST API and OAuth 2.0:

* **Gmail** (Google Workspace and consumer Gmail): Gmail API v1. Incremental sync uses
  ``users.history.list`` from the stored ``historyId``.
* **Outlook** (Microsoft 365 and Outlook.com): Microsoft Graph v1.0. Incremental sync uses
  the inbox ``messages/delta`` query; the stored cursor is the ``@odata.deltaLink``.
* **Zoho Mail**: Zoho Mail REST API. Zoho has no delta query, so the stored cursor is the
  newest ``receivedTime`` seen and every pass re-reads the newest page (read state changes).

Rules every client keeps:

* Only the fixed provider hosts are contacted (operator settings choose Zoho's data centre
  and Microsoft's tenant, never a tenant request), redirects are not followed and every
  call has a timeout.
* Tokens are only ever placed in the ``Authorization`` header. Errors carry the HTTP status
  and the provider's short error text, never request headers or bodies.
* Responses are read with a byte cap; message bodies and attachments have their own caps
  (``ANUM_EMAIL_BODY_MAX_BYTES``, ``ANUM_EMAIL_ATTACHMENT_MAX_BYTES``).
* Everything a provider returns is untrusted data (threat model G3).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage as MimeMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from enum import StrEnum
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlencode

import httpx

TIMEOUT_SECONDS = 20.0
JSON_MAX_BYTES = 8 * 1024 * 1024
MAX_ADDRESSES = 50
MAX_SUBJECT_CHARS = 500
MAX_SNIPPET_CHARS = 400


class EmailProvider(StrEnum):
    GMAIL = "gmail"
    OUTLOOK = "outlook"
    ZOHO = "zoho"


PROVIDER_NAMES = {
    EmailProvider.GMAIL: "Gmail / Google Workspace",
    EmailProvider.OUTLOOK: "Outlook / Microsoft 365",
    EmailProvider.ZOHO: "Zoho Mail",
}

GMAIL_SCOPES = ("https://www.googleapis.com/auth/gmail.modify",)
OUTLOOK_SCOPES = ("offline_access", "User.Read", "Mail.ReadWrite", "Mail.Send")
ZOHO_SCOPES = ("ZohoMail.accounts.READ", "ZohoMail.folders.READ", "ZohoMail.messages.ALL")
ZOHO_DATA_CENTRES = ("com", "eu", "in", "com.au", "jp", "ca", "sa", "com.cn")


class MailProviderError(Exception):
    """A provider call failed. ``message`` is safe to show (status and the provider's short text).

    ``reauth`` is true when the stored grant no longer works (revoked, expired refresh
    token, missing scope): the owner must connect the account again.
    """

    def __init__(self, status: int, message: str, *, reauth: bool = False) -> None:
        self.status = status
        self.message = message
        self.reauth = reauth
        super().__init__(message)


class HistoryExpiredError(MailProviderError):
    """The incremental cursor is too old; a full resync is needed."""

    def __init__(self) -> None:
        super().__init__(404, "The provider no longer has changes that old; resyncing.")


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str
    redirect_uri: str


@dataclass(frozen=True)
class TokenGrant:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MailboxProfile:
    address: str
    display_name: str | None = None
    provider_account_id: str | None = None


@dataclass
class MessageMeta:
    provider_message_id: str
    thread_id: str
    subject: str
    from_address: str
    from_name: str | None
    to: list[str]
    cc: list[str]
    snippet: str
    labels: list[str]
    received_at: datetime
    is_read: bool
    is_archived: bool
    has_attachments: bool
    internet_message_id: str | None = None
    references: str | None = None
    folder_id: str | None = None
    # Hints for rules triage: a List-Unsubscribe header, the provider's promotions/spam
    # classification.
    bulk: bool = False


@dataclass
class SyncResult:
    upserts: list[MessageMeta] = field(default_factory=list)
    # Provider message ids that left the inbox (archived, moved, deleted).
    removed: list[str] = field(default_factory=list)
    # Provider message ids deleted at the provider.
    deleted: list[str] = field(default_factory=list)
    # Read-state or archive changes for messages already stored: id -> (is_read, is_archived).
    changes: dict[str, tuple[bool | None, bool | None]] = field(default_factory=dict)
    cursor: str | None = None
    full: bool = False


@dataclass(frozen=True)
class AttachmentInfo:
    id: str
    filename: str
    content_type: str
    size: int


@dataclass
class MessageBody:
    text: str
    truncated: bool
    attachments: list[AttachmentInfo]


@dataclass(frozen=True)
class OutgoingMessage:
    """What is sent or saved as a draft. ``reply_to`` is the provider id of the message answered."""

    from_address: str
    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    body: str
    reply_to: str | None = None
    thread_id: str | None = None
    in_reply_to_header: str | None = None
    references_header: str | None = None
    folder_id: str | None = None


# -- helpers --------------------------------------------------------------------------------


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge) per RFC 7636."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _clean(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _addresses(values: Any) -> list[str]:
    out: list[str] = []
    for _, address in getaddresses([values] if isinstance(values, str) else [str(v) for v in values or []]):
        address = address.strip().lower()
        if address and "@" in address and len(address) <= 320 and address not in out:
            out.append(address)
        if len(out) >= MAX_ADDRESSES:
            break
    return out


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _from_millis(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class _TextExtractor(HTMLParser):
    """HTML to plain text: drops script/style/head, keeps line breaks. Nothing is rendered."""

    SKIP = frozenset({"script", "style", "head", "title", "noscript", "template"})
    BREAKS = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup: fall back to stripping tags
        return re.sub(r"<[^>]+>", " ", html)
    return "".join(parser.parts)


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_body(text: str, max_bytes: int) -> tuple[str, bool]:
    """Plain text without control characters or long blank runs, cut at ``max_bytes`` of UTF-8."""
    cleaned = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    encoded = cleaned.encode("utf-8")
    if len(encoded) <= max_bytes:
        return cleaned, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip(), True


def safe_filename(name: str) -> str:
    base = re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]+", "_", name or "").strip(" .")
    return (base or "attachment")[:180]


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def build_mime(message: OutgoingMessage) -> bytes:
    mime = MimeMessage()
    mime["From"] = message.from_address
    mime["To"] = ", ".join(message.to)
    if message.cc:
        mime["Cc"] = ", ".join(message.cc)
    mime["Subject"] = message.subject
    if message.in_reply_to_header:
        mime["In-Reply-To"] = message.in_reply_to_header
        mime["References"] = " ".join(filter(None, [message.references_header, message.in_reply_to_header]))
    mime.set_content(message.body)
    return mime.as_bytes()


# -- transport --------------------------------------------------------------------------------


class _Http:
    def __init__(self, transport: httpx.AsyncBaseTransport | None, label: str) -> None:
        self._transport = transport
        self._label = label

    async def request(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        auth_scheme: str = "Bearer",
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        form: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        allow: frozenset[int] = frozenset(),
        max_bytes: int = JSON_MAX_BYTES,
        raw: bool = False,
    ) -> tuple[int, Any]:
        sent = {"accept": "application/json", "accept-encoding": "identity", "user-agent": "anum-email"}
        if token:
            sent["authorization"] = f"{auth_scheme} {token}"
        sent.update(headers or {})
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                async with httpx.AsyncClient(
                    timeout=TIMEOUT_SECONDS, transport=self._transport, follow_redirects=False
                ) as client:
                    async with client.stream(
                        method, url, params=params, json=json_body, data=form, headers=sent
                    ) as response:
                        buffer = bytearray()
                        async for chunk in response.aiter_bytes():
                            buffer.extend(chunk)
                            if len(buffer) > max_bytes:
                                raise MailProviderError(
                                    413, f"{self._label} answered with more than {max_bytes} bytes."
                                )
                        status = response.status_code
                        reason = response.reason_phrase
                        content_type = response.headers.get("content-type", "")
        except MailProviderError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise MailProviderError(0, f"{self._label} did not answer in time.") from None
        except httpx.HTTPError as exc:
            raise MailProviderError(0, f"{self._label} could not be reached ({type(exc).__name__}).") from None
        body: Any = bytes(buffer)
        if not raw and buffer:
            try:
                body = json.loads(buffer.decode("utf-8"))
            except ValueError:
                body = None if "json" in content_type else bytes(buffer)
        elif not raw:
            body = None
        if status >= 400 and status not in allow:
            raise _error(self._label, status, body, reason)
        return status, body


def _error(label: str, status: int, body: Any, reason: str) -> MailProviderError:
    detail = ""
    error_code = ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or "")
            error_code = str(error.get("code") or error.get("status") or "")
        elif isinstance(error, str):
            error_code = error
            detail = str(body.get("error_description") or error)
        data = body.get("data")
        if not detail and isinstance(data, dict):
            detail = str(data.get("moreInfo") or data.get("errorCode") or "")
        status_obj = body.get("status")
        if not detail and isinstance(status_obj, dict):
            detail = str(status_obj.get("description") or "")
    text = (detail or reason or "error")[:200]
    reauth = status == 401 or error_code in {"invalid_grant", "unauthorized_client", "InvalidAuthenticationToken"}
    return MailProviderError(status, f"{label} answered {status}: {text}", reauth=reauth)


def _grant(body: Any, previous_refresh: str | None, label: str) -> TokenGrant:
    if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
        error = body.get("error") if isinstance(body, dict) else None
        raise MailProviderError(400, f"{label} did not return an access token ({error or 'no token'}).", reauth=error == "invalid_grant")
    try:
        lifetime = int(body.get("expires_in") or 3600)
    except (TypeError, ValueError):
        lifetime = 3600
    scope = body.get("scope")
    return TokenGrant(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token") or previous_refresh,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=max(60, lifetime)),
        scopes=tuple(str(scope).replace(",", " ").split()) if scope else (),
    )


# -- provider base ----------------------------------------------------------------------------


class MailProviderClient:
    provider: EmailProvider
    label: str
    scopes: tuple[str, ...]
    uses_pkce: bool = True
    api_host: str

    def __init__(self, oauth: OAuthClient, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.oauth = oauth
        self.http = _Http(transport, self.label)

    # OAuth
    def authorization_url(self, state: str, code_challenge: str | None) -> str:
        raise NotImplementedError

    async def exchange_code(self, code: str, code_verifier: str | None) -> TokenGrant:
        raise NotImplementedError

    async def refresh(self, refresh_token: str) -> TokenGrant:
        raise NotImplementedError

    async def revoke(self, token: str) -> bool:
        """Revoke the grant at the provider; False when the provider has no such endpoint."""
        return False

    # Mailbox
    async def profile(self, token: str) -> MailboxProfile:
        raise NotImplementedError

    async def sync(
        self, token: str, cursor: str | None, *, account_ref: str | None, since: datetime, limit: int
    ) -> SyncResult:
        raise NotImplementedError

    async def body(self, token: str, message: MessageMeta, *, account_ref: str | None, max_bytes: int) -> MessageBody:
        raise NotImplementedError

    async def attachment(
        self, token: str, message: MessageMeta, attachment_id: str, *, account_ref: str | None, max_bytes: int
    ) -> bytes:
        raise NotImplementedError

    async def mark_read(self, token: str, message: MessageMeta, read: bool, *, account_ref: str | None) -> None:
        raise NotImplementedError

    async def archive(self, token: str, message: MessageMeta, *, account_ref: str | None) -> None:
        raise NotImplementedError

    async def save_draft(self, token: str, outgoing: OutgoingMessage, *, account_ref: str | None) -> str | None:
        raise NotImplementedError

    async def delete_draft(self, token: str, draft_id: str, *, account_ref: str | None) -> None:
        raise NotImplementedError

    async def send(self, token: str, outgoing: OutgoingMessage, *, account_ref: str | None) -> str | None:
        raise NotImplementedError

    # shared OAuth plumbing
    async def _token(self, url: str, form: dict[str, str], previous_refresh: str | None = None) -> TokenGrant:
        _, body = await self.http.request("POST", url, form=form)
        return _grant(body, previous_refresh, self.label)


# -- Gmail ------------------------------------------------------------------------------------


class GmailClient(MailProviderClient):
    provider = EmailProvider.GMAIL
    label = "Gmail"
    scopes = GMAIL_SCOPES
    api_host = "gmail.googleapis.com"
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    GRANT_URL = "https://oauth2.googleapis.com/token"
    REVOKE_URL = "https://oauth2.googleapis.com/revoke"
    API = "https://gmail.googleapis.com/gmail/v1/users/me"
    METADATA_HEADERS = ("From", "To", "Cc", "Subject", "Date", "Message-ID", "References", "List-Unsubscribe")

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

    async def profile(self, token: str) -> MailboxProfile:
        _, body = await self.http.request("GET", f"{self.API}/profile", token=token)
        address = str((body or {}).get("emailAddress") or "").lower()
        if not address:
            raise MailProviderError(502, "Gmail did not return the mailbox address.")
        return MailboxProfile(address=address)

    async def _meta(self, token: str, message_id: str) -> MessageMeta | None:
        params: list[tuple[str, str]] = [("format", "metadata")]
        params.extend(("metadataHeaders", name) for name in self.METADATA_HEADERS)
        status, body = await self.http.request(
            "GET", f"{self.API}/messages/{quote(message_id, safe='')}", token=token,
            params=httpx.QueryParams(params), allow=frozenset({404}),
        )
        if status == 404 or not isinstance(body, dict):
            return None
        return gmail_meta(body)

    async def sync(
        self, token: str, cursor: str | None, *, account_ref: str | None, since: datetime, limit: int
    ) -> SyncResult:
        if cursor:
            try:
                return await self._incremental(token, cursor, limit)
            except HistoryExpiredError:
                pass
        _, profile = await self.http.request("GET", f"{self.API}/profile", token=token)
        history_id = str((profile or {}).get("historyId") or "")
        days = max(1, (datetime.now(timezone.utc) - since).days)
        result = SyncResult(cursor=history_id or cursor, full=True)
        page_token: str | None = None
        ids: list[str] = []
        while len(ids) < limit:
            params = {"q": f"in:inbox newer_than:{days}d", "maxResults": str(min(100, limit - len(ids)))}
            if page_token:
                params["pageToken"] = page_token
            _, body = await self.http.request("GET", f"{self.API}/messages", token=token, params=params)
            ids.extend(str(item["id"]) for item in (body or {}).get("messages", []) if isinstance(item, dict) and item.get("id"))
            page_token = (body or {}).get("nextPageToken")
            if not page_token:
                break
        for message_id in ids[:limit]:
            meta = await self._meta(token, message_id)
            if meta is not None:
                result.upserts.append(meta)
        return result

    async def _incremental(self, token: str, cursor: str, limit: int) -> SyncResult:
        result = SyncResult(cursor=cursor)
        added: list[str] = []
        page_token: str | None = None
        while True:
            params: list[tuple[str, str]] = [("startHistoryId", cursor), ("maxResults", "500")]
            params.extend(("historyTypes", kind) for kind in ("messageAdded", "messageDeleted", "labelAdded", "labelRemoved"))
            if page_token:
                params.append(("pageToken", page_token))
            status, body = await self.http.request(
                "GET", f"{self.API}/history", token=token, params=httpx.QueryParams(params), allow=frozenset({404})
            )
            if status == 404:
                raise HistoryExpiredError()
            body = body or {}
            for entry in body.get("history", []) or []:
                for item in entry.get("messagesAdded", []) or []:
                    message = item.get("message") or {}
                    if "INBOX" in (message.get("labelIds") or []) and message.get("id") not in added:
                        added.append(str(message["id"]))
                for item in entry.get("messagesDeleted", []) or []:
                    message_id = str((item.get("message") or {}).get("id") or "")
                    if message_id:
                        result.deleted.append(message_id)
                for key, adding in (("labelsAdded", True), ("labelsRemoved", False)):
                    for item in entry.get(key, []) or []:
                        message_id = str((item.get("message") or {}).get("id") or "")
                        labels = set(item.get("labelIds") or [])
                        read, archived = result.changes.get(message_id, (None, None))
                        if "UNREAD" in labels:
                            read = not adding
                        if "INBOX" in labels:
                            archived = not adding
                        if message_id and (read is not None or archived is not None):
                            result.changes[message_id] = (read, archived)
            if body.get("historyId"):
                result.cursor = str(body["historyId"])
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        for message_id in added[:limit]:
            meta = await self._meta(token, message_id)
            if meta is not None:
                result.upserts.append(meta)
        return result

    async def _full(self, token: str, message: MessageMeta) -> dict[str, Any]:
        _, body = await self.http.request(
            "GET", f"{self.API}/messages/{quote(message.provider_message_id, safe='')}", token=token, params={"format": "full"}
        )
        return body if isinstance(body, dict) else {}

    async def body(self, token: str, message: MessageMeta, *, account_ref: str | None, max_bytes: int) -> MessageBody:
        payload = (await self._full(token, message)).get("payload") or {}
        plain: list[str] = []
        html: list[str] = []
        attachments: list[AttachmentInfo] = []

        def walk(part: dict[str, Any]) -> None:
            mime = str(part.get("mimeType") or "").lower()
            body = part.get("body") or {}
            filename = str(part.get("filename") or "")
            if filename and body.get("attachmentId"):
                attachments.append(
                    AttachmentInfo(
                        id=str(body["attachmentId"]),
                        filename=safe_filename(filename),
                        content_type=mime or "application/octet-stream",
                        size=int(body.get("size") or 0),
                    )
                )
            elif body.get("data") and mime in {"text/plain", "text/html"}:
                text = _b64url_decode(str(body["data"])).decode("utf-8", errors="replace")
                (plain if mime == "text/plain" else html).append(text)
            for child in part.get("parts") or []:
                if isinstance(child, dict):
                    walk(child)

        walk(payload)
        raw = "\n".join(plain) if plain else html_to_text("\n".join(html))
        text, truncated = normalize_body(raw, max_bytes)
        return MessageBody(text=text, truncated=truncated, attachments=attachments)

    async def attachment(
        self, token: str, message: MessageMeta, attachment_id: str, *, account_ref: str | None, max_bytes: int
    ) -> bytes:
        _, body = await self.http.request(
            "GET",
            f"{self.API}/messages/{quote(message.provider_message_id, safe='')}/attachments/{quote(attachment_id, safe='')}",
            token=token,
            # base64 grows the payload by a third.
            max_bytes=max_bytes * 4 // 3 + 4096,
        )
        data = _b64url_decode(str((body or {}).get("data") or ""))
        if len(data) > max_bytes:
            raise MailProviderError(413, f"The attachment is larger than {max_bytes} bytes.")
        return data

    async def _modify(self, token: str, message: MessageMeta, add: list[str], remove: list[str]) -> None:
        await self.http.request(
            "POST",
            f"{self.API}/messages/{quote(message.provider_message_id, safe='')}/modify",
            token=token,
            json_body={"addLabelIds": add, "removeLabelIds": remove},
        )

    async def mark_read(self, token: str, message: MessageMeta, read: bool, *, account_ref: str | None) -> None:
        await self._modify(token, message, [] if read else ["UNREAD"], ["UNREAD"] if read else [])

    async def archive(self, token: str, message: MessageMeta, *, account_ref: str | None) -> None:
        await self._modify(token, message, [], ["INBOX"])

    def _raw(self, outgoing: OutgoingMessage) -> dict[str, Any]:
        message: dict[str, Any] = {"raw": base64.urlsafe_b64encode(build_mime(outgoing)).decode("ascii")}
        if outgoing.thread_id:
            message["threadId"] = outgoing.thread_id
        return message

    async def save_draft(self, token: str, outgoing: OutgoingMessage, *, account_ref: str | None) -> str | None:
        _, body = await self.http.request("POST", f"{self.API}/drafts", token=token, json_body={"message": self._raw(outgoing)})
        return str((body or {}).get("id") or "") or None

    async def delete_draft(self, token: str, draft_id: str, *, account_ref: str | None) -> None:
        await self.http.request("DELETE", f"{self.API}/drafts/{quote(draft_id, safe='')}", token=token, allow=frozenset({404}))

    async def send(self, token: str, outgoing: OutgoingMessage, *, account_ref: str | None) -> str | None:
        _, body = await self.http.request("POST", f"{self.API}/messages/send", token=token, json_body=self._raw(outgoing))
        return str((body or {}).get("id") or "") or None


def gmail_meta(body: dict[str, Any]) -> MessageMeta:
    headers = {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        for item in ((body.get("payload") or {}).get("headers") or [])
        if isinstance(item, dict)
    }
    labels = [str(label) for label in body.get("labelIds") or []]
    name, address = parseaddr(headers.get("from", ""))
    received = _from_millis(body.get("internalDate"))
    if received is None:
        try:
            received = parsedate_to_datetime(headers.get("date", ""))
        except (TypeError, ValueError):
            received = datetime.now(timezone.utc)
    payload = body.get("payload") or {}
    has_attachments = any(
        isinstance(part, dict) and part.get("filename") for part in payload.get("parts") or []
    ) or str(payload.get("mimeType", "")).lower() == "multipart/mixed"
    return MessageMeta(
        provider_message_id=str(body.get("id")),
        thread_id=str(body.get("threadId") or body.get("id")),
        subject=_clean(headers.get("subject"), MAX_SUBJECT_CHARS),
        from_address=address.lower(),
        from_name=_clean(name, 200) or None,
        to=_addresses(headers.get("to", "")),
        cc=_addresses(headers.get("cc", "")),
        snippet=_clean(html_to_text(str(body.get("snippet") or "")), MAX_SNIPPET_CHARS),
        labels=labels[:40],
        received_at=received if received.tzinfo else received.replace(tzinfo=timezone.utc),
        is_read="UNREAD" not in labels,
        is_archived="INBOX" not in labels,
        has_attachments=has_attachments,
        internet_message_id=headers.get("message-id") or None,
        references=headers.get("references") or None,
        bulk=bool(headers.get("list-unsubscribe")) or bool({"CATEGORY_PROMOTIONS", "SPAM"} & set(labels)),
    )


# -- Microsoft Graph --------------------------------------------------------------------------


class OutlookClient(MailProviderClient):
    provider = EmailProvider.OUTLOOK
    label = "Microsoft Graph"
    scopes = OUTLOOK_SCOPES
    api_host = "graph.microsoft.com"
    API = "https://graph.microsoft.com/v1.0/me"
    SELECT = (
        "id,conversationId,subject,from,toRecipients,ccRecipients,bodyPreview,receivedDateTime,"
        "isRead,hasAttachments,internetMessageId,categories,inferenceClassification,parentFolderId"
    )

    def __init__(self, oauth: OAuthClient, transport: httpx.AsyncBaseTransport | None = None, *, tenant: str = "common") -> None:
        super().__init__(oauth, transport)
        self.tenant = tenant
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

    async def profile(self, token: str) -> MailboxProfile:
        _, body = await self.http.request(
            "GET", self.API, token=token, params={"$select": "id,mail,userPrincipalName,displayName"}
        )
        body = body or {}
        address = str(body.get("mail") or body.get("userPrincipalName") or "").lower()
        if not address:
            raise MailProviderError(502, "Microsoft Graph did not return the mailbox address.")
        return MailboxProfile(address=address, display_name=_clean(body.get("displayName"), 200) or None)

    async def sync(
        self, token: str, cursor: str | None, *, account_ref: str | None, since: datetime, limit: int
    ) -> SyncResult:
        headers = {"prefer": "odata.maxpagesize=50"}
        result = SyncResult(full=not cursor)
        if cursor and cursor.startswith(f"{self.API}/"):
            url: str | None = cursor
            params: dict[str, str] | None = None
        else:
            url = f"{self.API}/mailFolders('inbox')/messages/delta"
            params = {
                "$select": self.SELECT,
                "$filter": f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            }
            result.full = True
        pages = 0
        while url and pages < 50:
            pages += 1
            status, body = await self.http.request("GET", url, token=token, params=params, headers=headers, allow=frozenset({410}))
            if status == 410:
                # The delta token expired: start again from a full sync.
                return await self.sync(token, None, account_ref=account_ref, since=since, limit=limit)
            params = None
            body = body or {}
            for item in body.get("value", []) or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                if "@removed" in item:
                    result.removed.append(str(item["id"]))
                    continue
                if len(result.upserts) < limit:
                    result.upserts.append(graph_meta(item))
            next_link = body.get("@odata.nextLink")
            delta_link = body.get("@odata.deltaLink")
            if delta_link:
                result.cursor = str(delta_link)
                break
            url = str(next_link) if next_link and str(next_link).startswith(f"{self.API}/") else None
            if next_link and url is None:
                raise MailProviderError(502, "Microsoft Graph returned a paging link to another host.")
        return result

    async def body(self, token: str, message: MessageMeta, *, account_ref: str | None, max_bytes: int) -> MessageBody:
        message_url = f"{self.API}/messages/{quote(message.provider_message_id, safe='')}"
        _, body = await self.http.request(
            "GET", message_url, token=token, params={"$select": "body"},
            headers={"prefer": 'outlook.body-content-type="text"'},
        )
        content = ((body or {}).get("body") or {})
        raw = str(content.get("content") or "")
        if str(content.get("contentType", "")).lower() == "html":
            raw = html_to_text(raw)
        text, truncated = normalize_body(raw, max_bytes)
        attachments: list[AttachmentInfo] = []
        if message.has_attachments:
            _, listed = await self.http.request(
                "GET", f"{message_url}/attachments", token=token,
                params={"$select": "id,name,contentType,size,isInline"},
            )
            for item in (listed or {}).get("value", []) or []:
                if isinstance(item, dict) and item.get("id") and not item.get("isInline"):
                    attachments.append(
                        AttachmentInfo(
                            id=str(item["id"]),
                            filename=safe_filename(str(item.get("name") or "")),
                            content_type=str(item.get("contentType") or "application/octet-stream"),
                            size=int(item.get("size") or 0),
                        )
                    )
        return MessageBody(text=text, truncated=truncated, attachments=attachments)

    async def attachment(
        self, token: str, message: MessageMeta, attachment_id: str, *, account_ref: str | None, max_bytes: int
    ) -> bytes:
        _, data = await self.http.request(
            "GET",
            f"{self.API}/messages/{quote(message.provider_message_id, safe='')}/attachments/{quote(attachment_id, safe='')}/$value",
            token=token,
            raw=True,
            max_bytes=max_bytes,
        )
        return bytes(data or b"")

    async def mark_read(self, token: str, message: MessageMeta, read: bool, *, account_ref: str | None) -> None:
        await self.http.request(
            "PATCH", f"{self.API}/messages/{quote(message.provider_message_id, safe='')}", token=token, json_body={"isRead": read}
        )

    async def archive(self, token: str, message: MessageMeta, *, account_ref: str | None) -> None:
        await self.http.request(
            "POST",
            f"{self.API}/messages/{quote(message.provider_message_id, safe='')}/move",
            token=token,
            json_body={"destinationId": "archive"},
        )

    @staticmethod
    def _message(outgoing: OutgoingMessage, *, include_subject: bool) -> dict[str, Any]:
        message: dict[str, Any] = {
            "toRecipients": [{"emailAddress": {"address": address}} for address in outgoing.to],
            "ccRecipients": [{"emailAddress": {"address": address}} for address in outgoing.cc],
            "body": {"contentType": "Text", "content": outgoing.body},
        }
        if include_subject:
            message["subject"] = outgoing.subject
        return message

    async def save_draft(self, token: str, outgoing: OutgoingMessage, *, account_ref: str | None) -> str | None:
        if outgoing.reply_to:
            _, body = await self.http.request(
                "POST",
                f"{self.API}/messages/{quote(outgoing.reply_to, safe='')}/createReply",
                token=token,
                json_body={"message": self._message(outgoing, include_subject=False)},
            )
        else:
            _, body = await self.http.request(
                "POST", f"{self.API}/messages", token=token, json_body=self._message(outgoing, include_subject=True)
            )
        return str((body or {}).get("id") or "") or None

    async def delete_draft(self, token: str, draft_id: str, *, account_ref: str | None) -> None:
        await self.http.request(
            "DELETE", f"{self.API}/messages/{quote(draft_id, safe='')}", token=token, allow=frozenset({404})
        )

    async def send(self, token: str, outgoing: OutgoingMessage, *, account_ref: str | None) -> str | None:
        if outgoing.reply_to:
            await self.http.request(
                "POST",
                f"{self.API}/messages/{quote(outgoing.reply_to, safe='')}/reply",
                token=token,
                json_body={"message": self._message(outgoing, include_subject=False)},
            )
        else:
            await self.http.request(
                "POST",
                f"{self.API}/sendMail",
                token=token,
                json_body={"message": self._message(outgoing, include_subject=True), "saveToSentItems": True},
            )
        return None


def graph_meta(item: dict[str, Any]) -> MessageMeta:
    sender = ((item.get("from") or {}).get("emailAddress") or {})
    received = _parse_iso(item.get("receivedDateTime")) or datetime.now(timezone.utc)
    categories = [str(value) for value in item.get("categories") or []][:20]
    return MessageMeta(
        provider_message_id=str(item["id"]),
        thread_id=str(item.get("conversationId") or item["id"]),
        subject=_clean(item.get("subject"), MAX_SUBJECT_CHARS),
        from_address=str(sender.get("address") or "").lower(),
        from_name=_clean(sender.get("name"), 200) or None,
        to=_addresses([((r or {}).get("emailAddress") or {}).get("address", "") for r in item.get("toRecipients") or []]),
        cc=_addresses([((r or {}).get("emailAddress") or {}).get("address", "") for r in item.get("ccRecipients") or []]),
        snippet=_clean(item.get("bodyPreview"), MAX_SNIPPET_CHARS),
        labels=categories,
        received_at=received,
        is_read=bool(item.get("isRead")),
        is_archived=False,
        has_attachments=bool(item.get("hasAttachments")),
        internet_message_id=item.get("internetMessageId") or None,
        folder_id=item.get("parentFolderId") or None,
        bulk=str(item.get("inferenceClassification") or "").lower() == "other",
    )


# -- Zoho Mail ----------------------------------------------------------------------------------


class ZohoClient(MailProviderClient):
    provider = EmailProvider.ZOHO
    label = "Zoho Mail"
    scopes = ZOHO_SCOPES
    # Zoho's documentation does not describe PKCE for server-based clients; the state check,
    # the client secret and the server-side code exchange protect the flow.
    uses_pkce = False

    def __init__(self, oauth: OAuthClient, transport: httpx.AsyncBaseTransport | None = None, *, data_centre: str = "com") -> None:
        super().__init__(oauth, transport)
        if data_centre not in ZOHO_DATA_CENTRES:
            raise ValueError("unknown Zoho data centre")
        self.accounts_base = f"https://accounts.zoho.{data_centre}/oauth/v2"
        self.api_host = f"mail.zoho.{data_centre}"
        self.api = f"https://mail.zoho.{data_centre}/api/accounts"

    def authorization_url(self, state: str, code_challenge: str | None) -> str:
        params = {
            "client_id": self.oauth.client_id,
            "redirect_uri": self.oauth.redirect_uri,
            "response_type": "code",
            "scope": ",".join(self.scopes),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{self.accounts_base}/auth?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str | None) -> TokenGrant:
        return await self._token(
            f"{self.accounts_base}/token",
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.oauth.client_id,
                "client_secret": self.oauth.client_secret,
                "redirect_uri": self.oauth.redirect_uri,
            },
        )

    async def refresh(self, refresh_token: str) -> TokenGrant:
        return await self._token(
            f"{self.accounts_base}/token",
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.oauth.client_id,
                "client_secret": self.oauth.client_secret,
            },
            refresh_token,
        )

    async def revoke(self, token: str) -> bool:
        await self.http.request("POST", f"{self.accounts_base}/token/revoke", params={"token": token}, allow=frozenset({400}))
        return True

    async def _get(self, token: str, url: str, params: Mapping[str, Any] | None = None) -> Any:
        _, body = await self.http.request("GET", url, token=token, auth_scheme="Zoho-oauthtoken", params=params)
        return (body or {}).get("data") if isinstance(body, dict) else None

    async def profile(self, token: str) -> MailboxProfile:
        data = await self._get(token, self.api)
        first = data[0] if isinstance(data, list) and data else {}
        address = str(first.get("primaryEmailAddress") or first.get("mailboxAddress") or "").lower()
        account_id = str(first.get("accountId") or "")
        if not address or not account_id:
            raise MailProviderError(502, "Zoho Mail did not return the mailbox account.")
        return MailboxProfile(address=address, display_name=_clean(first.get("displayName"), 200) or None, provider_account_id=account_id)

    async def _inbox_folder(self, token: str, account_ref: str) -> str:
        data = await self._get(token, f"{self.api}/{quote(account_ref, safe='')}/folders")
        for folder in data or []:
            if isinstance(folder, dict) and str(folder.get("folderType", "")).lower() == "inbox":
                return str(folder.get("folderId"))
        raise MailProviderError(502, "Zoho Mail did not return an inbox folder.")

    async def sync(
        self, token: str, cursor: str | None, *, account_ref: str | None, since: datetime, limit: int
    ) -> SyncResult:
        if not account_ref:
            raise MailProviderError(400, "The Zoho Mail account id is missing; connect the account again.", reauth=True)
        state: dict[str, Any] = {}
        if cursor:
            try:
                state = json.loads(cursor)
            except ValueError:
                state = {}
        folder = str(state.get("inbox") or "") or await self._inbox_folder(token, account_ref)
        newest_seen = int(state.get("newest") or 0)
        floor = max(newest_seen, int(since.timestamp() * 1000)) if not newest_seen else newest_seen
        result = SyncResult(full=not cursor)
        newest = newest_seen
        start = 1
        page = 50
        while len(result.upserts) < limit:
            data = await self._get(
                token,
                f"{self.api}/{quote(account_ref, safe='')}/messages/view",
                {"folderId": folder, "start": start, "limit": page, "sortorder": "false", "includeto": "true"},
            )
            items = [item for item in data or [] if isinstance(item, dict)]
            older = False
            for item in items:
                meta = zoho_meta(item, folder)
                stamp = int(meta.received_at.timestamp() * 1000)
                newest = max(newest, stamp)
                if start == 1 or stamp > floor:
                    # The newest page is always re-read so read-state changes show up.
                    result.upserts.append(meta)
                if stamp <= floor:
                    older = True
            if older or len(items) < page:
                break
            start += page
        result.cursor = json.dumps({"inbox": folder, "newest": newest})
        return result

    def _message_url(self, account_ref: str | None, message: MessageMeta) -> str:
        if not account_ref or not message.folder_id:
            raise MailProviderError(400, "The Zoho Mail message has no folder; sync the account again.")
        return (
            f"{self.api}/{quote(account_ref, safe='')}/folders/{quote(message.folder_id, safe='')}"
            f"/messages/{quote(message.provider_message_id, safe='')}"
        )

    async def body(self, token: str, message: MessageMeta, *, account_ref: str | None, max_bytes: int) -> MessageBody:
        url = self._message_url(account_ref, message)
        data = await self._get(token, f"{url}/content")
        text, truncated = normalize_body(html_to_text(str((data or {}).get("content") or "")), max_bytes)
        attachments: list[AttachmentInfo] = []
        if message.has_attachments:
            info = await self._get(token, f"{url}/attachmentinfo")
            for item in (info or {}).get("attachments", []) or []:
                if isinstance(item, dict) and item.get("attachmentId"):
                    attachments.append(
                        AttachmentInfo(
                            id=str(item["attachmentId"]),
                            filename=safe_filename(str(item.get("attachmentName") or "")),
                            content_type="application/octet-stream",
                            size=int(item.get("attachmentSize") or 0),
                        )
                    )
        return MessageBody(text=text, truncated=truncated, attachments=attachments)

    async def attachment(
        self, token: str, message: MessageMeta, attachment_id: str, *, account_ref: str | None, max_bytes: int
    ) -> bytes:
        _, data = await self.http.request(
            "GET",
            f"{self._message_url(account_ref, message)}/attachments/{quote(attachment_id, safe='')}",
            token=token,
            auth_scheme="Zoho-oauthtoken",
            raw=True,
            max_bytes=max_bytes,
        )
        return bytes(data or b"")

    async def _update(self, token: str, account_ref: str | None, mode: str, message: MessageMeta) -> None:
        if not account_ref:
            raise MailProviderError(400, "The Zoho Mail account id is missing; connect the account again.", reauth=True)
        await self.http.request(
            "PUT",
            f"{self.api}/{quote(account_ref, safe='')}/updatemessage",
            token=token,
            auth_scheme="Zoho-oauthtoken",
            json_body={"mode": mode, "messageId": [message.provider_message_id]},
        )

    async def mark_read(self, token: str, message: MessageMeta, read: bool, *, account_ref: str | None) -> None:
        await self._update(token, account_ref, "markAsRead" if read else "markAsUnread", message)

    async def archive(self, token: str, message: MessageMeta, *, account_ref: str | None) -> None:
        await self._update(token, account_ref, "archiveMails", message)

    def _payload(self, outgoing: OutgoingMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "fromAddress": outgoing.from_address,
            "toAddress": ",".join(outgoing.to),
            "subject": outgoing.subject,
            "content": outgoing.body,
            "mailFormat": "plaintext",
        }
        if outgoing.cc:
            payload["ccAddress"] = ",".join(outgoing.cc)
        return payload

    async def save_draft(self, token: str, outgoing: OutgoingMessage, *, account_ref: str | None) -> str | None:
        if not account_ref:
            return None
        payload = {**self._payload(outgoing), "mode": "draft"}
        _, body = await self.http.request(
            "POST", f"{self.api}/{quote(account_ref, safe='')}/messages", token=token,
            auth_scheme="Zoho-oauthtoken", json_body=payload,
        )
        data = (body or {}).get("data") if isinstance(body, dict) else None
        return str((data or {}).get("messageId") or "") or None

    async def delete_draft(self, token: str, draft_id: str, *, account_ref: str | None) -> None:
        # Zoho deletes a draft through the drafts folder, which needs its id; the draft is
        # left for the owner to remove (docs/email.md, Zoho limits).
        return None

    async def send(self, token: str, outgoing: OutgoingMessage, *, account_ref: str | None) -> str | None:
        if not account_ref:
            raise MailProviderError(400, "The Zoho Mail account id is missing; connect the account again.", reauth=True)
        payload = self._payload(outgoing)
        url = f"{self.api}/{quote(account_ref, safe='')}/messages"
        if outgoing.reply_to:
            url = f"{url}/{quote(outgoing.reply_to, safe='')}"
            payload["action"] = "reply"
        _, body = await self.http.request("POST", url, token=token, auth_scheme="Zoho-oauthtoken", json_body=payload)
        data = (body or {}).get("data") if isinstance(body, dict) else None
        return str((data or {}).get("messageId") or "") or None


def zoho_meta(item: dict[str, Any], folder_id: str) -> MessageMeta:
    name, address = parseaddr(str(item.get("sender") or item.get("fromAddress") or ""))
    address = (str(item.get("fromAddress") or "") or address).lower()
    if "<" in address:
        address = parseaddr(address)[1].lower()
    received = _from_millis(item.get("receivedTime")) or datetime.now(timezone.utc)
    flag = str(item.get("status") or item.get("status2") or "")
    return MessageMeta(
        provider_message_id=str(item.get("messageId")),
        thread_id=str(item.get("threadId") or item.get("messageId")),
        subject=_clean(item.get("subject"), MAX_SUBJECT_CHARS),
        from_address=address,
        from_name=_clean(name or item.get("sender"), 200) or None,
        to=_addresses(str(item.get("toAddress") or "").replace("&lt;", "<").replace("&gt;", ">")),
        cc=_addresses(str(item.get("ccAddress") or "").replace("&lt;", "<").replace("&gt;", ">")),
        snippet=_clean(html_to_text(str(item.get("summary") or "")), MAX_SNIPPET_CHARS),
        labels=[],
        received_at=received,
        is_read=flag == "1",
        is_archived=False,
        has_attachments=str(item.get("hasAttachment") or "0") == "1",
        folder_id=folder_id,
    )


__all__ = [
    "AttachmentInfo",
    "EmailProvider",
    "GmailClient",
    "MailProviderClient",
    "MailProviderError",
    "MailboxProfile",
    "MessageBody",
    "MessageMeta",
    "OAuthClient",
    "OutgoingMessage",
    "OutlookClient",
    "PROVIDER_NAMES",
    "SyncResult",
    "TokenGrant",
    "ZohoClient",
    "build_mime",
    "html_to_text",
    "normalize_body",
    "pkce_pair",
]
