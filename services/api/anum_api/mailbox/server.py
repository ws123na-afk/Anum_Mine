"""The mail server over IMAP (implicit TLS) and SMTP submission (docs/email.md#email-center).

Standard library ``imaplib`` and ``smtplib`` with the system's certificate store and host
name checks. Every call opens its own connection, logs in, does one job and logs out; the
API runs it in a worker thread (``asyncio.to_thread``) with a socket timeout, so a slow or
unreachable server never blocks the event loop. Errors come back as :class:`MailServerError`
with a short reason a person can act on; the password never appears in one.
"""

from __future__ import annotations

import base64
import imaplib
import re
import smtplib
import socket
import ssl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol

from .config import MailServerSettings
from .mime import Person, parse_headers

FolderRole = Literal["inbox", "sent", "drafts", "junk", "trash", "archive"]
FlagName = Literal["seen", "flagged"]
FLAGS: dict[str, str] = {"seen": "\\Seen", "flagged": "\\Flagged", "answered": "\\Answered"}
SPECIAL_USE: dict[str, FolderRole] = {"\\sent": "sent", "\\drafts": "drafts", "\\junk": "junk", "\\trash": "trash", "\\archive": "archive"}
# Common folder names when the server does not mark special use (RFC 6154).
NAMES: dict[str, FolderRole] = {
    "sent": "sent", "sent items": "sent", "sent messages": "sent", "drafts": "drafts", "junk": "junk", "spam": "junk",
    "junk e-mail": "junk", "trash": "trash", "deleted items": "trash", "deleted messages": "trash", "archive": "archive",
}
MAX_FOLDERS = 200
MAX_SEARCH = 20_000


class MailServerError(Exception):
    """Something the person can understand. ``kind`` is for the client's wording."""

    def __init__(self, kind: Literal["auth", "offline", "tls", "protocol", "not_found", "too_large", "rejected", "uncertain"], message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class SendUncertain(MailServerError):
    """The connection broke while the server was taking the message: it may have been sent."""

    def __init__(self, message: str) -> None:
        super().__init__("uncertain", message)


@dataclass(frozen=True)
class Folder:
    id: str
    name: str
    role: FolderRole | None
    total: int
    unread: int
    selectable: bool = True
    # The folder's UIDVALIDITY (RFC 3501): when it changes, every UID seen before means nothing.
    uidvalidity: int | None = None


@dataclass(frozen=True)
class Summary:
    uid: int
    subject: str
    sender: Person | None
    to: list[Person]
    cc: list[Person]
    date: datetime
    message_id: str | None
    seen: bool
    flagged: bool
    answered: bool
    size: int
    maybe_attachments: bool


@dataclass(frozen=True)
class Page:
    items: list[Summary]
    total: int
    next_before: int | None


@dataclass(frozen=True)
class SendReport:
    refused: dict[str, str] = field(default_factory=dict)


class MailServer(Protocol):
    """What the Email Center needs from a mail server (tests use a fake)."""

    def check_login(self) -> None: ...
    def folders(self) -> list[Folder]: ...
    def messages(self, folder: str, *, before: int | None, limit: int, query: str | None, unread: bool, flagged: bool) -> Page: ...
    def messages_after(self, folder: str, *, after: int, limit: int) -> Page: ...
    def fetch(self, folder: str, uid: int, *, mark_seen: bool, max_bytes: int) -> bytes: ...
    def set_flag(self, folder: str, uids: list[int], flag: str, value: bool) -> None: ...
    def move(self, folder: str, uids: list[int], target: str) -> None: ...
    def delete(self, folder: str, uids: list[int]) -> None: ...
    def append(self, folder: str, raw: bytes, *, seen: bool) -> None: ...
    def find_message_id(self, folder: str, message_id: str) -> int | None: ...
    def send(self, sender: str, recipients: list[str], raw: bytes) -> SendReport: ...


# -- modified UTF-7 mailbox names (RFC 3501 section 5.1.3) ---------------------------------


def decode_mutf7(name: str) -> str:
    def part(match: re.Match[str]) -> str:
        inner = match.group(1)
        if inner == "":
            return "&"
        padded = inner.replace(",", "/") + "=" * (-len(inner) % 4)
        try:
            return base64.b64decode(padded).decode("utf-16-be")
        except (ValueError, UnicodeDecodeError):
            return match.group(0)

    return re.sub(r"&([A-Za-z0-9+,]*)-", part, name)


def encode_mutf7(name: str) -> str:
    out: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer:
            encoded = base64.b64encode(buffer.encode("utf-16-be")).decode("ascii").rstrip("=").replace("/", ",")
            out.append(f"&{encoded}-")
            buffer = ""

    for char in name:
        if 0x20 <= ord(char) <= 0x7E:
            flush()
            out.append("&-" if char == "&" else char)
        else:
            buffer += char
    flush()
    return "".join(out)


def quote_mailbox(name: str) -> str:
    encoded = encode_mutf7(name)
    return '"' + encoded.replace("\\", "\\\\").replace('"', '\\"') + '"'


_LIST_LINE = re.compile(rb'^\((?P<flags>[^)]*)\) (?P<delim>"(?:[^"\\]|\\.)*"|NIL) (?P<name>.+)$')


def parse_list_line(line: bytes | tuple[bytes, bytes]) -> tuple[list[str], str] | None:
    """Flags and decoded name of one LIST response line (quoted, atom or literal name)."""
    if isinstance(line, tuple):
        head, literal = line
        match = _LIST_LINE.match(head.rstrip())
        if not match:
            return None
        raw_name = literal
    else:
        match = _LIST_LINE.match(line.rstrip())
        if not match:
            return None
        raw_name = match.group("name")
        if raw_name.startswith(b'"') and raw_name.endswith(b'"'):
            raw_name = re.sub(rb"\\(.)", rb"\1", raw_name[1:-1])
    flags = [flag.decode("ascii", "replace").lower() for flag in match.group("flags").split()]
    return flags, decode_mutf7(raw_name.decode("utf-8", "replace"))


def folder_role(name: str, flags: list[str]) -> FolderRole | None:
    if name.upper() == "INBOX":
        return "inbox"
    for flag in flags:
        if flag in SPECIAL_USE:
            return SPECIAL_USE[flag]
    return NAMES.get(name.lower())


_FETCH_META = re.compile(rb"UID (\d+)")
_FETCH_FLAGS = re.compile(rb"FLAGS \(([^)]*)\)")
_FETCH_SIZE = re.compile(rb"RFC822\.SIZE (\d+)")
_FETCH_DATE = re.compile(rb'INTERNALDATE "([^"]+)"')


def parse_fetch(data: list[object]) -> list[Summary]:
    """Summaries from a ``UID FETCH (UID FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS ...])``."""
    found: list[Summary] = []
    for item in data:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta, headers = item[0], item[1]
        if not isinstance(meta, bytes) or not isinstance(headers, bytes):
            continue
        uid = _FETCH_META.search(meta)
        if not uid:
            continue
        flags_match = _FETCH_FLAGS.search(meta)
        flags = flags_match.group(1).decode("ascii", "replace").lower().split() if flags_match else []
        size = _FETCH_SIZE.search(meta)
        internal = _FETCH_DATE.search(meta)
        fields = parse_headers(headers)
        when = fields["date"]
        if not isinstance(when, datetime) and internal:
            try:
                when = datetime.strptime(internal.group(1).decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
            except ValueError:
                when = None
        found.append(
            Summary(
                uid=int(uid.group(1)),
                subject=str(fields["subject"]),
                sender=fields["sender"] if isinstance(fields["sender"], Person) else None,
                to=list(fields["to"]),  # type: ignore[call-overload]
                cc=list(fields["cc"]),  # type: ignore[call-overload]
                date=when if isinstance(when, datetime) else datetime.fromtimestamp(0, timezone.utc),
                message_id=fields["message_id"] if isinstance(fields["message_id"], str) else None,
                seen="\\seen" in flags,
                flagged="\\flagged" in flags,
                answered="\\answered" in flags,
                size=int(size.group(1)) if size else 0,
                maybe_attachments=bool(fields["maybe_attachments"]),
            )
        )
    return found


def uid_set(uids: list[int]) -> str:
    return ",".join(str(int(uid)) for uid in sorted(set(uids)) if int(uid) > 0)


# -- the real server ------------------------------------------------------------------------


class ImapSmtpServer:
    def __init__(self, cfg: MailServerSettings, username: str, password: str) -> None:
        self.cfg = cfg
        self.username = username
        self.password = password

    def _context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    @contextmanager
    def _imap(self) -> Iterator[imaplib.IMAP4_SSL]:
        try:
            connection = imaplib.IMAP4_SSL(self.cfg.imap_host, self.cfg.imap_port, ssl_context=self._context(), timeout=self.cfg.timeout_seconds)
        except ssl.SSLError:
            raise MailServerError("tls", "The mail server's certificate could not be verified.") from None
        except (OSError, imaplib.IMAP4.error):
            raise MailServerError("offline", "The mail server cannot be reached right now.") from None
        try:
            try:
                connection.login(self.username, self.password)
            except imaplib.IMAP4.error:
                raise MailServerError("auth", "The mail server refused this address and password.") from None
            yield connection
        except (socket.timeout, TimeoutError):
            raise MailServerError("offline", "The mail server did not answer in time.") from None
        except ssl.SSLError:
            raise MailServerError("tls", "The secure connection to the mail server broke.") from None
        except imaplib.IMAP4.abort:
            raise MailServerError("offline", "The mail server closed the connection.") from None
        except imaplib.IMAP4.error:
            raise MailServerError("protocol", "The mail server refused the request.") from None
        except OSError:
            raise MailServerError("offline", "The connection to the mail server was lost.") from None
        finally:
            try:
                connection.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    @staticmethod
    def _ok(result: tuple[str, list[object]], what: str) -> list[object]:
        status, data = result
        if status != "OK":
            raise MailServerError("protocol", f"The mail server could not {what}.")
        return data

    def _select(self, connection: imaplib.IMAP4_SSL, folder: str, *, readonly: bool) -> None:
        status, _ = connection.select(quote_mailbox(folder), readonly=readonly)
        if status != "OK":
            raise MailServerError("not_found", "That folder does not exist on the mail server.")

    def check_login(self) -> None:
        with self._imap():
            return

    def folders(self) -> list[Folder]:
        with self._imap() as connection:
            lines = self._ok(connection.list(), "list folders")
            found: list[Folder] = []
            for line in lines[: MAX_FOLDERS * 2]:
                parsed = parse_list_line(line)  # type: ignore[arg-type]
                if parsed is None:
                    continue
                flags, name = parsed
                selectable = "\\noselect" not in flags and "\\nonexistent" not in flags
                total = unread = 0
                validity: int | None = None
                if selectable:
                    status, data = connection.status(quote_mailbox(name), "(MESSAGES UNSEEN UIDVALIDITY)")
                    if status == "OK" and data and isinstance(data[0], bytes):
                        counts = dict(re.findall(rb"(MESSAGES|UNSEEN|UIDVALIDITY) (\d+)", data[0]))
                        total, unread = int(counts.get(b"MESSAGES", 0)), int(counts.get(b"UNSEEN", 0))
                        validity = int(counts[b"UIDVALIDITY"]) if b"UIDVALIDITY" in counts else None
                found.append(Folder(
                    id=name, name=name, role=folder_role(name, flags), total=total, unread=unread, selectable=selectable, uidvalidity=validity,
                ))
                if len(found) >= MAX_FOLDERS:
                    break
            return found

    def messages(self, folder: str, *, before: int | None, limit: int, query: str | None, unread: bool, flagged: bool) -> Page:
        with self._imap() as connection:
            self._select(connection, folder, readonly=True)
            criteria: list[str] = []
            if before is not None:
                if before <= 1:
                    return Page(items=[], total=0, next_before=None)
                criteria += ["UID", f"1:{before - 1}"]
            if unread:
                criteria.append("UNSEEN")
            if flagged:
                criteria.append("FLAGGED")
            if query:
                connection.literal = query.encode("utf-8")  # type: ignore[attr-defined]
                data = self._ok(connection.uid("SEARCH", "CHARSET", "UTF-8", *criteria, "TEXT"), "search")
            else:
                data = self._ok(connection.uid("SEARCH", *(criteria or ["ALL"])), "list messages")
            uids = sorted({int(value) for value in (data[0] or b"").split()[:MAX_SEARCH]} if data and isinstance(data[0], bytes) else set(), reverse=True)
            page = uids[:limit]
            if not page:
                return Page(items=[], total=len(uids), next_before=None)
            fetched = self._ok(
                connection.uid(
                    "FETCH", uid_set(page),
                    "(UID FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID CONTENT-TYPE)])",
                ),
                "read the message list",
            )
            items = sorted(parse_fetch(fetched), key=lambda item: item.uid, reverse=True)
            return Page(items=items, total=len(uids), next_before=page[-1] if len(uids) > limit else None)

    def messages_after(self, folder: str, *, after: int, limit: int) -> Page:
        """The oldest ``limit`` messages with a UID above ``after``, oldest first.

        ``total`` counts every message above ``after`` (up to MAX_SEARCH), so a caller knows whether
        more wait. The background forwarding reads new mail forward from its cursor with this.
        """
        floor = max(0, int(after))
        with self._imap() as connection:
            self._select(connection, folder, readonly=True)
            data = self._ok(connection.uid("SEARCH", "UID", f"{floor + 1}:*"), "list new messages")
            found = {int(value) for value in (data[0] or b"").split()[:MAX_SEARCH]} if data and isinstance(data[0], bytes) else set()
            # "n:*" always matches the newest message, even when its UID is below n (RFC 3501 6.4.8).
            uids = sorted(uid for uid in found if uid > floor)
            page = uids[: max(0, limit)]
            if not page:
                return Page(items=[], total=len(uids), next_before=None)
            fetched = self._ok(
                connection.uid(
                    "FETCH", uid_set(page),
                    "(UID FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID CONTENT-TYPE)])",
                ),
                "read the message list",
            )
            items = sorted(parse_fetch(fetched), key=lambda item: item.uid)
            return Page(items=items, total=len(uids), next_before=None)

    def fetch(self, folder: str, uid: int, *, mark_seen: bool, max_bytes: int) -> bytes:
        with self._imap() as connection:
            self._select(connection, folder, readonly=not mark_seen)
            sized = self._ok(connection.uid("FETCH", str(int(uid)), "(RFC822.SIZE)"), "open the message")
            meta = next((item for item in sized if isinstance(item, bytes) and _FETCH_SIZE.search(item)), None)
            if meta is None:
                raise MailServerError("not_found", "That message is no longer in this folder.")
            size = _FETCH_SIZE.search(meta)
            if size and int(size.group(1)) > max_bytes:
                raise MailServerError("too_large", "This message is too large to open in ANUM. Open it in webmail.")
            data = self._ok(connection.uid("FETCH", str(int(uid)), "(BODY.PEEK[])"), "open the message")
            raw = next((item[1] for item in data if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes)), None)
            if raw is None:
                raise MailServerError("not_found", "That message is no longer in this folder.")
            if mark_seen:
                connection.uid("STORE", str(int(uid)), "+FLAGS.SILENT", "(\\Seen)")
            return raw

    def set_flag(self, folder: str, uids: list[int], flag: str, value: bool) -> None:
        with self._imap() as connection:
            self._select(connection, folder, readonly=False)
            self._ok(connection.uid("STORE", uid_set(uids), "+FLAGS.SILENT" if value else "-FLAGS.SILENT", f"({FLAGS[flag]})"), "change the message")

    def move(self, folder: str, uids: list[int], target: str) -> None:
        with self._imap() as connection:
            self._select(connection, folder, readonly=False)
            members = uid_set(uids)
            if "MOVE" in connection.capabilities:
                self._ok(connection.uid("MOVE", members, quote_mailbox(target)), "move the message")
                return
            self._ok(connection.uid("COPY", members, quote_mailbox(target)), "move the message")
            self._ok(connection.uid("STORE", members, "+FLAGS.SILENT", "(\\Deleted)"), "move the message")
            connection.expunge()

    def delete(self, folder: str, uids: list[int]) -> None:
        with self._imap() as connection:
            self._select(connection, folder, readonly=False)
            members = uid_set(uids)
            self._ok(connection.uid("STORE", members, "+FLAGS.SILENT", "(\\Deleted)"), "delete the message")
            if "UIDPLUS" in connection.capabilities:
                self._ok(connection.uid("EXPUNGE", members), "delete the message")
            else:
                connection.expunge()

    def append(self, folder: str, raw: bytes, *, seen: bool) -> None:
        with self._imap() as connection:
            self._ok(
                connection.append(quote_mailbox(folder), "(\\Seen)" if seen else "()", imaplib.Time2Internaldate(time.time()), raw),
                "save the message",
            )

    def find_message_id(self, folder: str, message_id: str) -> int | None:
        with self._imap() as connection:
            self._select(connection, folder, readonly=True)
            data = self._ok(connection.uid("SEARCH", "HEADER", "Message-ID", f'"{message_id}"'), "search")
            values = (data[0] or b"").split() if data and isinstance(data[0], bytes) else []
            return int(values[-1]) if values else None

    def send(self, sender: str, recipients: list[str], raw: bytes) -> SendReport:
        """Submit one message. Before the message data starts, every failure is definite
        (nothing was sent); a break after that raises :class:`SendUncertain`."""
        context = self._context()
        timeout = self.cfg.timeout_seconds
        try:
            if self.cfg.smtp_security == "tls":
                client: smtplib.SMTP = smtplib.SMTP_SSL(self.cfg.smtp_host, self.cfg.smtp_port, timeout=timeout, context=context)
            else:
                client = smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=timeout)
        except ssl.SSLError:
            raise MailServerError("tls", "The mail server's certificate could not be verified.") from None
        except OSError:
            raise MailServerError("offline", "The mail server cannot be reached right now.") from None
        try:
            try:
                client.ehlo()
                if self.cfg.smtp_security == "starttls":
                    if not client.has_extn("starttls"):
                        raise MailServerError("tls", "The mail server does not offer STARTTLS, so ANUM did not send.")
                    client.starttls(context=context)
                    client.ehlo()
                client.login(self.username, self.password)
            except smtplib.SMTPAuthenticationError:
                raise MailServerError("auth", "The mail server refused this address and password.") from None
            except ssl.SSLError:
                raise MailServerError("tls", "The secure connection to the mail server failed.") from None
            except (smtplib.SMTPException, OSError):
                raise MailServerError("offline", "The mail server cannot take mail right now.") from None
            try:
                refused = client.sendmail(sender, recipients, raw)
            except smtplib.SMTPRecipientsRefused:
                raise MailServerError("rejected", "The mail server refused every recipient.") from None
            except smtplib.SMTPSenderRefused:
                raise MailServerError("rejected", "The mail server refused this sender address.") from None
            except smtplib.SMTPDataError as exc:
                raise MailServerError("rejected", f"The mail server refused the message ({exc.smtp_code}).") from None
            except (smtplib.SMTPException, OSError):
                raise SendUncertain("The connection broke while the message was being sent. Check Sent before trying again.") from None
            return SendReport(refused={address: f"{code}" for address, (code, _) in (refused or {}).items()})
        finally:
            try:
                client.quit()
            except (smtplib.SMTPException, OSError):
                pass

