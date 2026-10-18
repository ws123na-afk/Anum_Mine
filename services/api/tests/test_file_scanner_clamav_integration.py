"""The ClamAV upload scanner against a real clamd (docs/files.md#malware-scanning).

Start one (``docker compose -f infra/docker/compose.yaml up clamd``; the first start can
take a minute while it loads its signatures) and point ``ANUM_TEST_CLAMD_HOST`` /
``ANUM_TEST_CLAMD_PORT`` at it (default ``127.0.0.1:3310``). Tests marked ``clamav`` are
skipped when clamd is not reachable locally; CI's "API integration" job runs clamd and
fails on any skip.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from anum_api import file_policy, files
from anum_api.file_scanner_clamav import ClamdScanner
from anum_api.files import file_store
from anum_api.main import app

CLAMD_HOST = os.getenv("ANUM_TEST_CLAMD_HOST", "127.0.0.1")
CLAMD_PORT = int(os.getenv("ANUM_TEST_CLAMD_PORT", "3310"))
# The standard EICAR test file, assembled at run time so this source file is not itself
# flagged by a virus scanner on a developer's machine.
EICAR = "".join(
    [r"X5O!P%@AP[4\PZX54(P^)7CC)7}$", "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!", "$H+H*"]
).encode("ascii")
MEMBER = {
    "x-tenant-id": "tenant_clamd",
    "x-workspace-id": "workspace_clamd",
    "x-user-id": "member_1",
    "x-user-roles": "member",
}


def _reachable() -> bool:
    try:
        with socket.create_connection((CLAMD_HOST, CLAMD_PORT), 1):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.clamav,
    pytest.mark.skipif(not _reachable(), reason=f"clamd is not reachable at {CLAMD_HOST}:{CLAMD_PORT}"),
]

client = TestClient(app)


@pytest.fixture
def scanner(monkeypatch: pytest.MonkeyPatch) -> Iterator[ClamdScanner]:
    monkeypatch.setattr(file_store, "storage", files.InMemoryObjectStorage())
    file_store.clear()
    clamd = ClamdScanner(host=CLAMD_HOST, port=CLAMD_PORT, timeout_seconds=30)
    file_policy.set_file_scanner(clamd)
    yield clamd
    file_policy.set_file_scanner(None)
    file_store.clear()


def test_clamd_answers_ping(scanner: ClamdScanner) -> None:
    assert scanner.ping() is True


def test_clamd_flags_eicar_and_passes_clean_bytes(scanner: ClamdScanner) -> None:
    verdict = scanner.scan("eicar.txt", "text/plain", EICAR)
    assert verdict.clean is False and "Eicar" in verdict.reason
    assert scanner.scan("notes.txt", "text/plain", b"Quarterly notes, nothing to see.\n" * 4000).clean


def test_an_eicar_upload_answers_422_with_a_clear_message(scanner: ClamdScanner) -> None:
    refused = client.post("/api/v1/files", params={"name": "eicar.txt"}, headers=MEMBER, content=EICAR)
    assert refused.status_code == 422, refused.text
    message = refused.json()["error"]["message"]
    assert message.startswith("The malware scanner found ") and "Eicar" in message
    assert "in eicar.txt, so it was not stored." in message
    kept = client.post("/api/v1/files", params={"name": "clean.txt"}, headers=MEMBER, content=b"clean")
    assert kept.status_code == 201, kept.text
    assert [record.name for record in file_store.records.values()] == ["clean.txt"]
