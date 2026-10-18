"""What the files API accepts, how it names downloads and how it signs short-lived links.

* :func:`resolve_content_type` decides a file's type from its name (the extension is
  authoritative) and the declared ``Content-Type``; anything outside
  :data:`ALLOWED_TYPES` is refused with ``415`` and a sentence naming what is accepted.
* :func:`content_matches_type` checks the leading bytes of images and PDFs, so a file
  cannot claim to be a PNG while carrying HTML.
* :func:`content_disposition` builds an RFC 6266 header that survives quotes and
  non-ASCII names.
* :func:`sign_link` / :func:`verify_link` mint and check download links: an HMAC over
  tenant, workspace, user, file, digest, disposition and expiry, keyed from
  ``ANUM_SECRETS_KEY`` (every key verifies, the first signs, so rotation keeps links
  valid until they expire).
* :class:`FileScanner` is the malware-scan hook point (docs/files.md#malware-scanning).
  ANUM ships a ClamAV adapter (:mod:`anum_api.file_scanner_clamav`), installed at startup
  when ``ANUM_FILE_SCANNER=clamav``; :func:`set_file_scanner` installs any other.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Protocol
from urllib.parse import quote

from .settings import settings


class FileKind(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"
    DATA = "data"
    IMAGE = "image"
    PDF = "pdf"
    DOCUMENT = "document"
    ARCHIVE = "archive"
    # Marketing media (docs/marketing.md): short videos for Reels, TikTok and YouTube.
    VIDEO = "video"


@dataclass(frozen=True)
class AllowedType:
    content_type: str
    kind: FileKind
    extensions: tuple[str, ...]


# Extension first: a name's extension decides the stored type, so the declared type of a
# browser or phone (often application/octet-stream or empty) never matters for these.
ALLOWED_TYPES: tuple[AllowedType, ...] = (
    AllowedType("text/plain", FileKind.TEXT, (".txt", ".log", ".text")),
    AllowedType("text/markdown", FileKind.MARKDOWN, (".md", ".markdown")),
    AllowedType("text/x-rst", FileKind.TEXT, (".rst",)),
    AllowedType("text/csv", FileKind.DATA, (".csv",)),
    AllowedType("text/tab-separated-values", FileKind.DATA, (".tsv",)),
    AllowedType("application/json", FileKind.DATA, (".json",)),
    AllowedType("application/yaml", FileKind.DATA, (".yaml", ".yml")),
    AllowedType("application/xml", FileKind.DATA, (".xml",)),
    AllowedType("image/png", FileKind.IMAGE, (".png",)),
    AllowedType("image/jpeg", FileKind.IMAGE, (".jpg", ".jpeg")),
    AllowedType("image/gif", FileKind.IMAGE, (".gif",)),
    AllowedType("image/webp", FileKind.IMAGE, (".webp",)),
    AllowedType("application/pdf", FileKind.PDF, (".pdf",)),
    AllowedType(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", FileKind.DOCUMENT, (".docx",)
    ),
    AllowedType("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", FileKind.DOCUMENT, (".xlsx",)),
    AllowedType(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation", FileKind.DOCUMENT, (".pptx",)
    ),
    AllowedType("application/vnd.oasis.opendocument.text", FileKind.DOCUMENT, (".odt",)),
    AllowedType("application/vnd.oasis.opendocument.spreadsheet", FileKind.DOCUMENT, (".ods",)),
    AllowedType("application/vnd.oasis.opendocument.presentation", FileKind.DOCUMENT, (".odp",)),
    AllowedType("application/zip", FileKind.ARCHIVE, (".zip",)),
    AllowedType("video/mp4", FileKind.VIDEO, (".mp4", ".m4v")),
    AllowedType("video/quicktime", FileKind.VIDEO, (".mov",)),
    AllowedType("video/webm", FileKind.VIDEO, (".webm",)),
)
_BY_EXTENSION = {extension: allowed for allowed in ALLOWED_TYPES for extension in allowed.extensions}
_BY_TYPE = {allowed.content_type: allowed for allowed in ALLOWED_TYPES}
# Declared aliases that mean an allowed type.
_ALIASES = {
    "text/x-markdown": "text/markdown",
    "application/x-yaml": "application/yaml",
    "text/yaml": "application/yaml",
    "text/x-yaml": "application/yaml",
    "text/xml": "application/xml",
    "image/jpg": "image/jpeg",
    "application/x-zip-compressed": "application/zip",
}
_GENERIC_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
ACCEPTED_SUMMARY = (
    "text (.txt, .md, .csv, .json, .yaml, .xml), images (.png, .jpg, .gif, .webp), PDF, "
    "Office and OpenDocument files (.docx, .xlsx, .pptx, .odt, .ods, .odp), .zip and video (.mp4, .mov, .webm)"
)


class UnsupportedFileType(ValueError):
    """The name or the bytes are not a type the workspace accepts."""


def resolve_content_type(name: str, declared: str | None) -> AllowedType:
    """The stored type of an upload, or :class:`UnsupportedFileType` with a reason."""
    extension = PurePosixPath(name.lower()).suffix
    if extension in _BY_EXTENSION:
        return _BY_EXTENSION[extension]
    media = (declared or "").split(";", 1)[0].strip().lower()
    media = _ALIASES.get(media, media)
    if extension == "" and media in _BY_TYPE:
        return _BY_TYPE[media]
    label = f"{extension} files" if extension else ("files without an extension" if media in _GENERIC_TYPES else media)
    raise UnsupportedFileType(f"This workspace does not accept {label}. Accepted: {ACCEPTED_SUMMARY}.")


_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "application/pdf": (b"%PDF-",),
    "application/zip": (b"PK\x03\x04", b"PK\x05\x06"),
}
_ZIP_CONTAINERS = frozenset(
    allowed.content_type for allowed in ALLOWED_TYPES if allowed.kind == FileKind.DOCUMENT
)


def content_matches_type(content: bytes, allowed: AllowedType) -> bool:
    """Leading bytes agree with the type for binary formats; text must not carry NUL bytes."""
    head = content[:4096]
    if allowed.content_type == "image/webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if allowed.content_type in {"video/mp4", "video/quicktime"}:
        # ISO base media: a box size, then "ftyp" (QuickTime may start with other atoms).
        return head[4:8] in {b"ftyp", b"moov", b"mdat", b"wide", b"free"}
    if allowed.content_type == "video/webm":
        return head.startswith(b"\x1a\x45\xdf\xa3")
    if allowed.content_type in _ZIP_CONTAINERS:
        return head.startswith(_SIGNATURES["application/zip"])
    if allowed.content_type in _SIGNATURES:
        return head.startswith(_SIGNATURES[allowed.content_type])
    if allowed.kind in {FileKind.TEXT, FileKind.MARKDOWN, FileKind.DATA}:
        return b"\x00" not in head
    return True


# Kinds a client may render in place (images and PDFs from bytes, text as text); never
# HTML or SVG, which are not accepted at all.
PREVIEWABLE_KINDS = frozenset({FileKind.TEXT, FileKind.MARKDOWN, FileKind.DATA, FileKind.IMAGE, FileKind.PDF})
TEXT_PREVIEW_KINDS = frozenset({FileKind.TEXT, FileKind.MARKDOWN, FileKind.DATA})


def kind_of(content_type: str, name: str) -> FileKind | None:
    """The kind of a stored file (files uploaded before the allow-list may have none)."""
    media = content_type.split(";", 1)[0].strip().lower()
    allowed = _BY_TYPE.get(_ALIASES.get(media, media)) or _BY_EXTENSION.get(PurePosixPath(name.lower()).suffix)
    if allowed is not None:
        return allowed.kind
    return FileKind.TEXT if media.startswith("text/") and media not in {"text/html", "text/javascript"} else None


def content_disposition(disposition: str, name: str) -> str:
    """``attachment``/``inline`` with an ASCII fallback and the exact UTF-8 name."""
    fallback = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\;' else "_" for ch in name) or "download"
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


# --------------------------------------------------------------------------- links

_LINK_CONTEXT = b"anum-file-link-v1"
# Without ANUM_SECRETS_KEY (local and test only, settings refuses it elsewhere) links
# are signed with a per-process key: they stop working when the process restarts.
_PROCESS_LINK_KEY = secrets.token_bytes(32)


def _link_keys() -> list[bytes]:
    raw = settings.secrets_key.get_secret_value() if settings.secrets_key else ""
    keys = [part.strip() for part in raw.split(",") if part.strip()]
    if not keys:
        return [_PROCESS_LINK_KEY]
    return [hmac.new(key.encode(), _LINK_CONTEXT, hashlib.sha256).digest() for key in keys]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class LinkClaims:
    tenant_id: str
    workspace_id: str
    user_id: str
    file_id: str
    sha256: str
    inline: bool
    expires_at: datetime


class InvalidLink(ValueError):
    """Tampered, malformed or expired."""


def sign_link(claims: LinkClaims) -> str:
    payload = json.dumps(
        {
            "t": claims.tenant_id,
            "w": claims.workspace_id,
            "u": claims.user_id,
            "f": claims.file_id,
            "h": claims.sha256,
            "i": claims.inline,
            "e": int(claims.expires_at.timestamp()),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.new(_link_keys()[0], payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def verify_link(value: str, now: datetime | None = None) -> LinkClaims:
    try:
        encoded_payload, encoded_signature = value.split(".", 1)
        payload, signature = _unb64(encoded_payload), _unb64(encoded_signature)
    except ValueError as exc:
        raise InvalidLink("malformed") from exc
    if not any(hmac.compare_digest(hmac.new(key, payload, hashlib.sha256).digest(), signature) for key in _link_keys()):
        raise InvalidLink("signature")
    try:
        data = json.loads(payload)
        claims = LinkClaims(
            tenant_id=str(data["t"]),
            workspace_id=str(data["w"]),
            user_id=str(data["u"]),
            file_id=str(data["f"]),
            sha256=str(data["h"]),
            inline=bool(data["i"]),
            expires_at=datetime.fromtimestamp(int(data["e"]), tz=UTC),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidLink("payload") from exc
    if claims.expires_at <= (now or datetime.now(UTC)):
        raise InvalidLink("expired")
    return claims


# --------------------------------------------------------------------------- malware scanning


@dataclass(frozen=True)
class ScanVerdict:
    clean: bool
    # Shown to the uploader when the file is refused, e.g. the signature name.
    reason: str = ""
    # False when the scanner was unavailable and scanning is optional (local only), so
    # the bytes were accepted without being looked at.
    scanned: bool = True


class FileScanner(Protocol):
    """Called with every upload's bytes before they are stored (docs/files.md#malware-scanning).

    Return ``ScanVerdict(clean=False, reason=...)`` to refuse the upload (``422``); raise
    to fail it (``503``, nothing stored). It runs inside the request, so keep it bounded.
    """

    def scan(self, name: str, content_type: str, content: bytes) -> ScanVerdict: ...


_scanner: FileScanner | None = None


def set_file_scanner(scanner: FileScanner | None) -> None:
    """Install (or remove, with ``None``) the scanner every upload passes through."""
    global _scanner
    _scanner = scanner


def file_scanner() -> FileScanner | None:
    return _scanner
