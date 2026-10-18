"""Upload malware scanning with ClamAV's ``clamd`` over TCP (docs/files.md#malware-scanning).

:class:`ClamdScanner` implements :class:`anum_api.file_policy.FileScanner` with the
``INSTREAM`` command: the bytes go to clamd in length-prefixed chunks over one TCP
connection, and clamd answers ``stream: OK`` or ``stream: <signature> FOUND``. Nothing
touches a disk on the API side, and no third-party client library is needed.

Every socket operation has a timeout (``ANUM_CLAMAV_TIMEOUT_SECONDS``). When clamd cannot
be reached, times out or answers with an error:

* ``ANUM_FILE_SCAN_REQUIRED=true`` (the default, and the only value accepted outside
  ``local``): the scan raises :class:`ScannerUnavailable`, the upload fails with ``503``
  and nothing is stored (fail closed);
* ``false`` (local development only): the upload is accepted unscanned and a warning is
  logged; the verdict says it was not scanned.

:func:`install_configured_scanner` installs the scanner the settings select at startup
(``ANUM_FILE_SCANNER=clamav``) or removes it (``off``).
"""

from __future__ import annotations

import logging
import socket
import struct
from dataclasses import dataclass
from typing import Any

from . import file_policy

logger = logging.getLogger(__name__)

# clamd reads INSTREAM data in chunks; each must be below its StreamMaxLength.
CHUNK_BYTES = 64 * 1024
# A verdict line is short; anything longer is a broken peer.
MAX_REPLY_BYTES = 4096


class ScannerUnavailable(RuntimeError):
    """clamd could not be reached, timed out or answered with an error."""


@dataclass(frozen=True)
class ClamdScanner:
    host: str
    port: int = 3310
    timeout_seconds: float = 30.0
    # False only in local development: an unreachable clamd lets the upload through.
    required: bool = True

    # ------------------------------------------------------------------ protocol

    def _connect(self) -> socket.socket:
        try:
            return socket.create_connection((self.host, self.port), timeout=self.timeout_seconds)
        except OSError as exc:
            raise ScannerUnavailable(f"clamd at {self.host}:{self.port} is unreachable ({exc.__class__.__name__})") from exc

    @staticmethod
    def _read_reply(conn: socket.socket) -> str:
        data = bytearray()
        while len(data) < MAX_REPLY_BYTES:
            chunk = conn.recv(1024)
            if not chunk:
                break
            data.extend(chunk)
            if b"\0" in chunk:
                break
        return data.split(b"\0", 1)[0].decode("utf-8", "replace").strip()

    def ping(self) -> bool:
        """``True`` when clamd answers ``PONG``."""
        try:
            with self._connect() as conn:
                conn.sendall(b"zPING\0")
                return self._read_reply(conn) == "PONG"
        except (OSError, ScannerUnavailable):
            return False

    def _instream(self, content: bytes) -> str:
        with self._connect() as conn:
            try:
                conn.sendall(b"zINSTREAM\0")
                view = memoryview(content)
                for start in range(0, len(view), CHUNK_BYTES):
                    chunk = view[start : start + CHUNK_BYTES]
                    conn.sendall(struct.pack("!I", len(chunk)))
                    conn.sendall(chunk)
                conn.sendall(struct.pack("!I", 0))
            except (BrokenPipeError, ConnectionResetError):
                # clamd closes the stream early when it exceeds StreamMaxLength; its
                # reason is still waiting to be read.
                pass
            except OSError as exc:
                raise ScannerUnavailable(f"sending to clamd failed ({exc.__class__.__name__})") from exc
            try:
                return self._read_reply(conn)
            except OSError as exc:
                raise ScannerUnavailable(f"reading clamd's verdict failed ({exc.__class__.__name__})") from exc

    @staticmethod
    def parse_reply(reply: str) -> file_policy.ScanVerdict:
        """``stream: OK``, ``stream: <name> FOUND``; anything else is an error."""
        _, _, result = reply.partition(": ")
        result = result if reply.startswith("stream") else reply
        if result == "OK":
            return file_policy.ScanVerdict(clean=True)
        if result.endswith(" FOUND"):
            signature = result.removesuffix(" FOUND").strip() or "malware"
            return file_policy.ScanVerdict(clean=False, reason=signature)
        raise ScannerUnavailable(f"clamd answered {reply[:200]!r}")

    # ------------------------------------------------------------------ FileScanner

    def scan(self, name: str, content_type: str, content: bytes) -> file_policy.ScanVerdict:
        try:
            return self.parse_reply(self._instream(content))
        except (ScannerUnavailable, OSError) as exc:
            if self.required:
                raise ScannerUnavailable(str(exc)) from exc
            logger.warning(
                "Upload accepted without a malware scan: clamd failed and ANUM_FILE_SCAN_REQUIRED is false",
                extra={"anum_scanner_error": str(exc)},
            )
            return file_policy.ScanVerdict(clean=True, reason="not scanned", scanned=False)


def scanner_from_settings(config: Any) -> ClamdScanner | None:
    """The scanner ``ANUM_FILE_SCANNER`` selects, or ``None`` for ``off``."""
    if getattr(config, "file_scanner", "off") != "clamav":
        return None
    return ClamdScanner(
        host=config.clamav_host,
        port=config.clamav_port,
        timeout_seconds=config.clamav_timeout_seconds,
        required=config.file_scan_required,
    )


def install_configured_scanner(config: Any) -> ClamdScanner | None:
    """Install (or remove) the configured scanner for every upload; returns it."""
    scanner = scanner_from_settings(config)
    file_policy.set_file_scanner(scanner)
    return scanner
