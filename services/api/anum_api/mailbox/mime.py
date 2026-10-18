"""Reading and writing RFC 5322 messages for the Email Center (standard library only).

Mail is untrusted input: headers are parsed with ``email.policy.default`` and every value
is cut to a fixed length; HTML goes through :mod:`html_safe`. Outgoing messages are built
with ``EmailMessage``, which refuses a header value that contains a line break, so text a
person or a model typed can never add a header.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage, Message
from email.parser import BytesHeaderParser, BytesParser
from email.utils import format_datetime, getaddresses, make_msgid, parsedate_to_datetime

from .html_safe import SafeHtml, html_to_text, sanitize_html

MAX_SUBJECT = 500
MAX_NAME = 200
MAX_ADDRESSES = 100
MAX_REFERENCES = 4000
MESSAGE_ID = re.compile(r"^<[^<>\s@]{1,200}@[^<>\s@]{1,200}>$")
ADDRESS = re.compile(r"^[^@\s<>,;\"()]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")


@dataclass(frozen=True)
class Person:
    name: str
    address: str


@dataclass(frozen=True)
class Attachment:
    index: int
    filename: str
    content_type: str
    size: int


@dataclass
class ParsedMessage:
    subject: str
    sender: Person | None
    to: list[Person]
    cc: list[Person]
    reply_to: list[Person]
    date: datetime | None
    message_id: str | None
    in_reply_to: str | None
    references: str | None
    text: str
    text_truncated: bool
    html: SafeHtml | None
    attachments: list[Attachment] = field(default_factory=list)


def _clip(value: object, limit: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", str(value or "")).strip()
    return text[:limit]


def people(value: object) -> list[Person]:
    """Addresses of a header value, at most :data:`MAX_ADDRESSES`."""
    if value is None:
        return []
    found: list[Person] = []
    try:
        addresses = list(getattr(value, "addresses"))
        pairs = [(item.display_name, item.addr_spec) for item in addresses]
    except (AttributeError, TypeError, ValueError):
        pairs = getaddresses([str(value)])
    for name, address in pairs[:MAX_ADDRESSES]:
        address = _clip(address, 320)
        if address:
            found.append(Person(name=_clip(name, MAX_NAME), address=address))
    return found


def _message_id(value: object) -> str | None:
    text = _clip(value, 400)
    return text if MESSAGE_ID.match(text) else None


def _date(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = getattr(value, "datetime", None) or parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _header(message: Message, name: str) -> object:
    try:
        return message.get(name)
    except (ValueError, TypeError, IndexError):  # a malformed header the parser could not model
        return None


def parse_headers(raw: bytes) -> dict[str, object]:
    """The list fields of one message from its header block only."""
    message = BytesHeaderParser(policy=policy.default).parsebytes(raw)
    content_type = _clip(_header(message, "content-type"), 200).lower()
    senders = people(_header(message, "from"))
    return {
        "subject": _clip(_header(message, "subject"), MAX_SUBJECT),
        "sender": senders[0] if senders else None,
        "to": people(_header(message, "to")),
        "cc": people(_header(message, "cc")),
        "date": _date(_header(message, "date")),
        "message_id": _message_id(_header(message, "message-id")),
        "maybe_attachments": content_type.startswith("multipart/mixed"),
    }


def _is_attachment(part: Message) -> bool:
    if part.is_multipart():
        return False
    disposition = part.get_content_disposition()
    if disposition == "attachment":
        return True
    if part.get_filename():
        return True
    return part.get_content_maintype() not in {"text", "multipart"} and disposition != "inline"


def _parts(message: Message) -> list[Message]:
    return [part for part in message.walk() if _is_attachment(part)]


def _filename(part: Message, index: int) -> str:
    name = _clip(part.get_filename() or "", 200).replace("/", "_").replace("\\", "_")
    return name or f"attachment-{index + 1}"


def parse_message(raw: bytes, *, text_max_chars: int = 200_000) -> ParsedMessage:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(message, EmailMessage)  # nosec B101 - policy.default always builds EmailMessage
    html_part = message.get_body(preferencelist=("html",))
    text_part = message.get_body(preferencelist=("plain",))
    html: SafeHtml | None = None
    text = ""
    if html_part is not None and html_part.get_content_type() == "text/html":
        try:
            source = html_part.get_content()
        except (LookupError, UnicodeError, ValueError):
            source = html_part.get_payload(decode=True).decode("utf-8", "replace") if html_part.get_payload(decode=True) else ""
        html = sanitize_html(str(source))
        text = html_to_text(str(source))
    if text_part is not None and text_part.get_content_type() == "text/plain":
        try:
            text = str(text_part.get_content())
        except (LookupError, UnicodeError, ValueError):
            payload = text_part.get_payload(decode=True)
            text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""
    truncated = len(text) > text_max_chars
    attachments = []
    for index, part in enumerate(_parts(message)):
        payload = part.get_payload(decode=True)
        attachments.append(
            Attachment(index=index, filename=_filename(part, index), content_type=_clip(part.get_content_type(), 120), size=len(payload) if isinstance(payload, bytes) else 0)
        )
    senders = people(_header(message, "from"))
    return ParsedMessage(
        subject=_clip(_header(message, "subject"), MAX_SUBJECT),
        sender=senders[0] if senders else None,
        to=people(_header(message, "to")),
        cc=people(_header(message, "cc")),
        reply_to=people(_header(message, "reply-to")),
        date=_date(_header(message, "date")),
        message_id=_message_id(_header(message, "message-id")),
        in_reply_to=_message_id(_header(message, "in-reply-to")),
        references=_clip(_header(message, "references"), MAX_REFERENCES) or None,
        text=text[:text_max_chars],
        text_truncated=truncated,
        html=html,
        attachments=attachments,
    )


def attachment_bytes(raw: bytes, index: int) -> tuple[bytes, Attachment] | None:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    parts = _parts(message)
    if index < 0 or index >= len(parts):
        return None
    part = parts[index]
    payload = part.get_payload(decode=True)
    data = payload if isinstance(payload, bytes) else b""
    return data, Attachment(index=index, filename=_filename(part, index), content_type=_clip(part.get_content_type(), 120), size=len(data))


def valid_address(value: str) -> bool:
    return bool(ADDRESS.match(value.strip()))


def new_message_id(sender_address: str) -> str:
    """A unique Message-ID on the sender's own domain."""
    domain = sender_address.rsplit("@", 1)[-1].lower()
    return make_msgid(domain=domain)


@dataclass(frozen=True)
class OutgoingFile:
    """An attachment as sent: checked and scanned when it was attached (docs/email.md#attachments)."""

    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class Outgoing:
    sender_name: str
    sender_address: str
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body: str
    message_id: str
    in_reply_to: str | None = None
    references: str | None = None
    date: datetime | None = None
    attachments: tuple[OutgoingFile, ...] = ()


def _address(text: str, name: str = "") -> Address:
    local, _, domain = text.strip().rpartition("@")
    return Address(display_name=name, username=local, domain=domain)


def build_message(outgoing: Outgoing) -> bytes:
    """The message as sent (Bcc recipients are envelope only, never a header)."""
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = _address(outgoing.sender_address, outgoing.sender_name)
    message["To"] = [_address(value) for value in outgoing.to]
    if outgoing.cc:
        message["Cc"] = [_address(value) for value in outgoing.cc]
    message["Subject"] = outgoing.subject
    message["Date"] = format_datetime(outgoing.date or datetime.now(timezone.utc))
    message["Message-ID"] = outgoing.message_id
    if outgoing.in_reply_to:
        message["In-Reply-To"] = outgoing.in_reply_to
    if outgoing.references:
        message["References"] = outgoing.references
    message["MIME-Version"] = "1.0"
    message.set_content(outgoing.body or "", subtype="plain", charset="utf-8")
    for item in outgoing.attachments:
        maintype, _, subtype = item.content_type.partition("/")
        if not maintype or not subtype:
            maintype, subtype = "application", "octet-stream"
        # The file name is a parameter value: the policy encodes it (RFC 2231) and refuses line breaks.
        message.add_attachment(item.data, maintype=maintype, subtype=subtype, filename=_clip(item.filename, 255))
    return message.as_bytes()


def reply_subject(subject: str) -> str:
    return subject if re.match(r"^\s*re:", subject, re.I) else f"Re: {subject}".strip()


def forward_subject(subject: str) -> str:
    return subject if re.match(r"^\s*(fw|fwd):", subject, re.I) else f"Fwd: {subject}".strip()


def quoted(parsed: ParsedMessage, *, forward: bool) -> str:
    """The original's text under a reply or forward (plain text, capped)."""
    sender = parsed.sender
    who = (f"{sender.name} <{sender.address}>" if sender and sender.name else sender.address) if sender else "unknown sender"
    when = parsed.date.strftime("%d %b %Y %H:%M UTC") if parsed.date else "an unknown date"
    original = parsed.text[:20_000]
    if forward:
        head = [
            "",
            "",
            "---------- Forwarded message ---------",
            f"From: {who}",
            f"Date: {when}",
            f"Subject: {parsed.subject}",
            f"To: {', '.join(p.address for p in parsed.to)}",
            "",
        ]
        return "\n".join(head) + original
    lines = "\n".join(f"> {line}" for line in original.splitlines())
    return f"\n\nOn {when}, {who} wrote:\n{lines}"


def references_for(parsed: ParsedMessage) -> str | None:
    if not parsed.message_id:
        return parsed.references
    chain = f"{parsed.references} {parsed.message_id}" if parsed.references else parsed.message_id
    return chain[-MAX_REFERENCES:].lstrip()
