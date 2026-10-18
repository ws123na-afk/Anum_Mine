"""The ClamAV upload scanner (anum_api/file_scanner_clamav.py) against an in-process fake
clamd that speaks INSTREAM: verdicts, chunking, timeouts, fail closed, the settings and
the startup refusal. The real clamd is exercised in test_file_scanner_clamav_integration.py.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
from collections.abc import Callable, Iterator

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import ValidationError

from anum_api import file_policy, files
from anum_api.file_scanner_clamav import (
    CHUNK_BYTES,
    ClamdScanner,
    ScannerUnavailable,
    install_configured_scanner,
    scanner_from_settings,
)
from anum_api.files import file_store
from anum_api.hardening import insecure_configuration_problems
from anum_api.main import app
from anum_api.settings import Settings

client = TestClient(app)
MEMBER = {"x-tenant-id": "tenant_scan", "x-workspace-id": "workspace_scan", "x-user-id": "member_1", "x-user-roles": "member"}


class FakeClamd:
    """Accepts one connection at a time and answers with ``reply(received_bytes)``."""

    def __init__(self, reply: Callable[[bytes], bytes | None]) -> None:
        self.reply = reply
        self.received: list[bytes] = []
        self.chunk_sizes: list[int] = []
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _read_exact(self, conn: socket.socket, size: int) -> bytes:
        data = b""
        while len(data) < size:
            part = conn.recv(size - len(data))
            if not part:
                raise ConnectionError("closed")
            data += part
        return data

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                command = b""
                while not command.endswith(b"\0"):
                    part = conn.recv(1)
                    if not part:
                        break
                    command += part
                if command == b"zPING\0":
                    conn.sendall(b"PONG\0")
                    continue
                body = b""
                try:
                    while True:
                        (size,) = struct.unpack("!I", self._read_exact(conn, 4))
                        if size == 0:
                            break
                        self.chunk_sizes.append(size)
                        body += self._read_exact(conn, size)
                except ConnectionError:
                    continue
                self.received.append(body)
                answer = self.reply(body)
                if answer is not None:
                    conn.sendall(answer)
                else:
                    # Never answer: the client must time out.
                    threading.Event().wait(2)

    def scanner(self, **options: object) -> ClamdScanner:
        return ClamdScanner(host="127.0.0.1", port=self.port, timeout_seconds=options.pop("timeout_seconds", 2.0), **options)  # type: ignore[arg-type]

    def close(self) -> None:
        self.server.close()


def _signature_reply(body: bytes) -> bytes:
    return b"stream: Eicar-Test-Signature FOUND\0" if b"MALWARE" in body else b"stream: OK\0"


@pytest.fixture
def clamd() -> Iterator[FakeClamd]:
    server = FakeClamd(_signature_reply)
    yield server
    server.close()


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(file_store, "storage", files.InMemoryObjectStorage())
    file_store.clear()
    file_policy.set_file_scanner(None)
    yield
    file_store.clear()
    file_policy.set_file_scanner(None)


def _unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# --------------------------------------------------------------------------- protocol


def test_clean_bytes_pass_and_a_signature_is_refused(clamd: FakeClamd) -> None:
    scanner = clamd.scanner()
    assert scanner.ping() is True
    assert scanner.scan("ok.txt", "text/plain", b"hello") == file_policy.ScanVerdict(clean=True)
    verdict = scanner.scan("bad.txt", "text/plain", b"MALWARE here")
    assert verdict.clean is False and verdict.reason == "Eicar-Test-Signature"
    assert clamd.received == [b"hello", b"MALWARE here"]


def test_large_files_are_streamed_in_bounded_chunks(clamd: FakeClamd) -> None:
    content = b"a" * (CHUNK_BYTES * 3 + 17)
    assert clamd.scanner().scan("big.txt", "text/plain", content).clean
    assert clamd.received[-1] == content
    assert max(clamd.chunk_sizes) == CHUNK_BYTES and sum(clamd.chunk_sizes) == len(content)


@pytest.mark.parametrize(
    ("reply", "clean", "reason"),
    [
        ("stream: OK", True, ""),
        ("stream: Win.Test.EICAR_HDB-1 FOUND", False, "Win.Test.EICAR_HDB-1"),
    ],
)
def test_replies_are_parsed(reply: str, clean: bool, reason: str) -> None:
    verdict = ClamdScanner.parse_reply(reply)
    assert (verdict.clean, verdict.reason) == (clean, reason)


@pytest.mark.parametrize("reply", ["INSTREAM size limit exceeded. ERROR", "stream: lstat() failed ERROR", "", "UNKNOWN COMMAND"])
def test_error_replies_never_count_as_clean(reply: str) -> None:
    with pytest.raises(ScannerUnavailable):
        ClamdScanner.parse_reply(reply)


def test_an_unreachable_clamd_fails_closed_when_required() -> None:
    scanner = ClamdScanner(host="127.0.0.1", port=_unused_port(), timeout_seconds=1)
    assert scanner.ping() is False
    with pytest.raises(ScannerUnavailable, match="unreachable"):
        scanner.scan("a.txt", "text/plain", b"x")


def test_a_silent_clamd_times_out_and_fails_closed() -> None:
    server = FakeClamd(lambda body: None)
    try:
        with pytest.raises(ScannerUnavailable):
            server.scanner(timeout_seconds=0.3).scan("a.txt", "text/plain", b"x")
    finally:
        server.close()


def test_an_error_answer_fails_closed() -> None:
    server = FakeClamd(lambda body: b"INSTREAM size limit exceeded. ERROR\0")
    try:
        with pytest.raises(ScannerUnavailable, match="size limit"):
            server.scanner().scan("a.txt", "text/plain", b"x")
    finally:
        server.close()


def test_optional_scanning_lets_the_file_through_unscanned_when_clamd_is_down() -> None:
    scanner = ClamdScanner(host="127.0.0.1", port=_unused_port(), timeout_seconds=1, required=False)
    verdict = scanner.scan("a.txt", "text/plain", b"x")
    assert verdict.clean is True and verdict.scanned is False


# --------------------------------------------------------------------------- through the API


def _upload(name: str, content: bytes):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/files", params={"name": name}, headers=MEMBER, content=content)


def test_uploads_are_scanned_and_infected_files_answer_422(clamd: FakeClamd) -> None:
    file_policy.set_file_scanner(clamd.scanner())
    refused = _upload("invoice.txt", b"MALWARE payload")
    assert refused.status_code == 422
    message = refused.json()["error"]["message"]
    assert message == (
        "The malware scanner found Eicar-Test-Signature in invoice.txt, so it was not stored. "
        "Delete the file and ask the sender for a clean copy."
    )
    assert _upload("notes.txt", b"clean notes").status_code == 201
    assert [record.name for record in file_store.records.values()] == ["notes.txt"]


def test_uploads_fail_with_503_and_store_nothing_while_clamd_is_down() -> None:
    file_policy.set_file_scanner(ClamdScanner(host="127.0.0.1", port=_unused_port(), timeout_seconds=1))
    failed = _upload("notes.txt", b"clean notes")
    assert failed.status_code == 503 and "nothing was stored" in failed.json()["error"]["message"]
    assert not file_store.records


# --------------------------------------------------------------------------- settings


def test_settings_select_and_install_the_scanner() -> None:
    assert scanner_from_settings(Settings(_env_file=None)) is None
    config = Settings(_env_file=None, file_scanner="clamav", clamav_host="clamd", clamav_port=3311, clamav_timeout_seconds=5)
    scanner = install_configured_scanner(config)
    assert scanner == ClamdScanner(host="clamd", port=3311, timeout_seconds=5, required=True)
    assert file_policy.file_scanner() is scanner
    assert install_configured_scanner(Settings(_env_file=None)) is None
    assert file_policy.file_scanner() is None


@pytest.mark.parametrize(
    "options",
    [
        {"file_scanner": "clamav"},
        {"file_scanner": "clamav", "clamav_host": "tcp://clamd:3310"},
        {"file_scanner": "virustotal", "clamav_host": "clamd"},
        {"file_scanner": "clamav", "clamav_host": "clamd", "clamav_timeout_seconds": 0},
        {"file_scanner": "clamav", "clamav_host": "clamd", "clamav_port": 0},
    ],
)
def test_invalid_scanner_settings_are_refused(options: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **options)  # type: ignore[arg-type]


def test_optional_scanning_is_refused_outside_local() -> None:
    def problems(**options: object) -> list[str]:
        config = Settings(_env_file=None, environment="staging", secrets_key=Fernet.generate_key().decode(), file_scanner="clamav", clamav_host="clamd", **options)  # type: ignore[arg-type]
        return [problem for problem in insecure_configuration_problems(config) if "FILE_SCAN" in problem]

    assert problems() == []
    assert problems(file_scan_required=False) == [
        "ANUM_FILE_SCAN_REQUIRED must stay true outside local when ANUM_FILE_SCANNER=clamav "
        "(uploads would be stored unscanned while clamd is down)"
    ]
    local = Settings(_env_file=None, file_scanner="clamav", clamav_host="clamd", file_scan_required=False)
    assert insecure_configuration_problems(local) == []


def test_the_helm_chart_mirrors_the_scanner_refusals() -> None:
    from pathlib import Path

    helm = Path(__file__).resolve().parents[3] / "infra" / "helm"
    validate = (helm / "anum" / "templates" / "_validate.tpl").read_text(encoding="utf-8")
    lint = (helm / "ci" / "lint.sh").read_text(encoding="utf-8")
    for refusal in ("ANUM_FILE_SCAN_REQUIRED must stay true", "ANUM_CLAMAV_HOST is required", "ANUM_FILE_SCANNER"):
        assert refusal in validate and refusal in lint, refusal


# --------------------------------------------------------------------------- starting up


def test_a_clamd_that_never_answered_reports_that_it_is_starting() -> None:
    from anum_api import file_scanner_clamav

    port = _unused_port()
    scanner = ClamdScanner(host="127.0.0.1", port=port, timeout_seconds=1)
    with pytest.raises(file_scanner_clamav.ClamdStarting):
        scanner.scan("a.txt", "text/plain", b"x")
    file_policy.set_file_scanner(scanner)
    starting = _upload("notes.txt", b"clean notes")
    assert starting.status_code == 503
    assert starting.headers["retry-after"] == "60"
    assert starting.json()["error"]["message"] == files.SCANNER_STARTING
    # Once clamd has answered, a later refusal means it is down, not starting.
    file_scanner_clamav._mark_answered("127.0.0.1", port)
    down = _upload("notes.txt", b"clean notes")
    assert down.status_code == 503 and down.json()["error"]["message"] == files.SCANNER_UNAVAILABLE
    assert not file_store.records


def test_a_successful_scan_marks_clamd_as_answered(clamd: FakeClamd) -> None:
    from anum_api.file_scanner_clamav import has_answered

    assert not has_answered("127.0.0.1", clamd.port)
    clamd.scanner().scan("ok.txt", "text/plain", b"hello")
    assert has_answered("127.0.0.1", clamd.port)


# --------------------------------------------------------------------------- quarantine and rescan


def _stored(name: str, content: bytes) -> dict:
    response = _upload(name, content)
    assert response.status_code == 201, response.text
    return response.json()


def test_scanned_uploads_are_marked_clean_and_unscanned_ones_stay_unmarked(clamd: FakeClamd) -> None:
    unscanned = _stored("before.txt", b"stored before scanning")
    assert unscanned["scan_status"] is None and unscanned["scanned_at"] is None
    file_policy.set_file_scanner(clamd.scanner())
    scanned = _stored("after.txt", b"stored after")
    assert scanned["scan_status"] == "clean" and scanned["scanned_at"]


def test_the_rescan_job_quarantines_infected_files_and_skips_scanned_ones(
    clamd: FakeClamd, capsys: pytest.CaptureFixture[str]
) -> None:
    from anum_api import file_rescan

    old_bad = _stored("old-invoice.txt", b"MALWARE from last year")
    old_good = _stored("old-notes.txt", b"plain notes")
    file_policy.set_file_scanner(clamd.scanner())
    new_good = _stored("new.txt", b"new notes")
    file_policy.set_file_scanner(None)
    scans_before = len(clamd.received)

    assert file_rescan.main(["--dry-run"], discovery=file_rescan.memory_discovery()) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["unscanned"] == 2 and dry["scanned"] == 0 and len(clamd.received) == scans_before

    assert file_rescan.main([], scanner=clamd.scanner(), discovery=file_rescan.memory_discovery()) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["scanned"], result["clean"], result["quarantined"]) == (2, 1, 1)
    assert result["quarantined_ids"] == [old_bad["id"]]
    assert "old-invoice" not in str(result)
    assert file_store.records[old_bad["id"]].scan_status == "infected"
    assert file_store.records[old_bad["id"]].scan_signature == "Eicar-Test-Signature"
    assert file_store.records[old_good["id"]].scan_status == "clean"
    assert file_store.records[new_good["id"]].scan_status == "clean"
    # A second run finds nothing left to scan.
    assert file_rescan.main([], scanner=clamd.scanner(), discovery=file_rescan.memory_discovery()) == 0
    assert json.loads(capsys.readouterr().out)["unscanned"] == 0

    # Quarantined: listed (to be deleted), never served.
    listed = client.get("/api/v1/files", headers=MEMBER).json()
    bad = next(item for item in listed if item["id"] == old_bad["id"])
    assert bad["scan_status"] == "infected" and bad["previewable"] is False
    for response in (
        client.get(f"/api/v1/files/{old_bad['id']}/content", headers=MEMBER),
        client.get(f"/api/v1/files/{old_bad['id']}/preview", headers=MEMBER),
        client.post(f"/api/v1/files/{old_bad['id']}/links", headers=MEMBER, json={}),
    ):
        assert response.status_code == 409
        assert "is quarantined: the malware scanner found Eicar-Test-Signature" in response.json()["error"]["message"]
    assert client.get(f"/api/v1/files/{old_good['id']}/content", headers=MEMBER).status_code == 200
    assert client.delete(f"/api/v1/files/{old_bad['id']}", headers=MEMBER).status_code == 204


def test_the_rescan_job_stops_without_recording_when_clamd_fails(capsys: pytest.CaptureFixture[str]) -> None:
    from anum_api import file_rescan

    stored = _stored("old.txt", b"plain")
    down = ClamdScanner(host="127.0.0.1", port=_unused_port(), timeout_seconds=1)
    assert file_rescan.main([], scanner=down, discovery=file_rescan.memory_discovery()) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["scanned"] == 0 and "nothing was recorded" in result["stopped"]
    assert file_store.records[stored["id"]].scan_status is None


def test_the_rescan_job_refuses_to_run_without_a_scanner(capsys: pytest.CaptureFixture[str]) -> None:
    from anum_api import file_rescan

    assert file_rescan.main([], discovery=file_rescan.memory_discovery()) == 2
    assert "ANUM_FILE_SCANNER=clamav" in capsys.readouterr().err
