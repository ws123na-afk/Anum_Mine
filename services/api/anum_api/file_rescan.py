"""Scan workspace files stored before malware scanning was turned on; quarantine infected ones.

    python -m anum_api.file_rescan --dry-run   # count unscanned files only
    python -m anum_api.file_rescan             # scan them

Opt-in and run by an operator (docs/files.md#rescanning-older-files). It needs
``ANUM_FILE_SCANNER=clamav`` and a reachable clamd, and always fails closed: when clamd
stops answering the job stops, records nothing for the file in hand and exits ``1``, so a
later run continues where it stopped.

Unscanned files are discovered as ``anum_maintenance`` (``id``, scope and ``scan_status``
only, migration ``0075_file_scan_status``); reading a file's record and bytes and recording
the verdict run as the application role inside that workspace's RLS context
(``anum_api.maintenance``). No transaction is open while a file is scanned: the verdict is
recorded in its own short unit of work, and only if the file still has the bytes that were
scanned. An infected file is marked ``infected`` (quarantined: listed so it can be deleted,
never served or retrieved) and its retrieval chunks are dropped. The output is counts and
file ids only, never names or content.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import text

from . import file_policy
from .maintenance import SessionFactory, default_session_factory, discover
from .schemas import TenantContext
from .settings import settings

# The job acts as no person; RLS scopes on tenant and workspace only.
JOB_USER = "system:file-rescan"

_UNSCANNED = text(
    """
    select id, tenant_id, workspace_id
    from workspace_files
    where scan_status is null and id > :after_id
    order by id
    limit :limit
    """
)


@dataclass
class RescanResult:
    dry_run: bool = False
    unscanned: int = 0
    scanned: int = 0
    clean: int = 0
    quarantined: int = 0
    missing: int = 0
    changed: int = 0
    stopped: str = ""
    quarantined_ids: list[str] = field(default_factory=list)


Discovery = Callable[[str, int], list[tuple[str, str, str]]]


def postgres_discovery(maintenance_session_factory: SessionFactory) -> Discovery:
    def find(after_id: str, limit: int) -> list[tuple[str, str, str]]:
        rows = discover(maintenance_session_factory, _UNSCANNED, {"after_id": after_id, "limit": limit})
        return [(row.id, row.tenant_id, row.workspace_id) for row in rows]

    return find


def memory_discovery() -> Discovery:
    from .files import file_store

    def find(after_id: str, limit: int) -> list[tuple[str, str, str]]:
        return [(r.id, r.tenant_id, r.workspace_id) for r in file_store.unscanned(after_id, limit)]

    return find


def rescan(
    scanner: file_policy.FileScanner,
    discovery: Discovery,
    *,
    dry_run: bool = False,
    batch_size: int = 200,
    limit: int | None = None,
) -> RescanResult:
    from .files import file_store, open_file_metadata_store
    from .retrieval_api import forget_file

    result = RescanResult(dry_run=dry_run)
    after_id = ""
    while True:
        rows = discovery(after_id, batch_size)
        if not rows:
            return result
        after_id = rows[-1][0]
        for file_id, tenant_id, workspace_id in rows:
            if limit is not None and result.unscanned >= limit:
                return result
            result.unscanned += 1
            if dry_run:
                continue
            context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=JOB_USER)
            with open_file_metadata_store(context) as store:
                record = store.get(context, file_id)
            if record is None or record.scan_status is not None:
                result.changed += 1
                continue
            try:
                content = file_store.storage.get(record.storage_key)
            except FileNotFoundError:
                result.missing += 1
                continue
            try:
                verdict = scanner.scan(record.name, record.content_type, content)
            except Exception as exc:  # noqa: BLE001 - fail closed: stop, record nothing
                result.stopped = f"the scanner failed ({exc.__class__.__name__}); nothing was recorded for {file_id}"
                return result
            if not verdict.scanned:
                result.stopped = f"the scanner did not scan {file_id}; set ANUM_FILE_SCAN_REQUIRED=true"
                return result
            status = "clean" if verdict.clean else "infected"
            signature = None if verdict.clean else (verdict.reason or "malware")[:200]
            with open_file_metadata_store(context) as store:
                recorded = store.record_scan(context, file_id, record.sha256, status, signature)
            if not recorded:
                result.changed += 1
                continue
            result.scanned += 1
            if verdict.clean:
                result.clean += 1
            else:
                result.quarantined += 1
                result.quarantined_ids.append(file_id)
                forget_file(context, file_id)
        if len(rows) < batch_size:
            return result


def main(argv: Sequence[str] | None = None, *, scanner: Any = None, discovery: Discovery | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m anum_api.file_rescan",
        description="Scan workspace files stored before malware scanning was on; quarantine infected ones.",
    )
    parser.add_argument("--dry-run", action="store_true", help="count unscanned files, scan nothing")
    parser.add_argument("--batch-size", type=int, default=200, help="files discovered per query")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many files")
    args = parser.parse_args(argv)
    if args.batch_size < 1 or (args.limit is not None and args.limit < 1):
        parser.error("--batch-size and --limit must be positive")
    if scanner is None and not args.dry_run:
        from .file_scanner_clamav import scanner_from_settings

        scanner = scanner_from_settings(settings)
        if scanner is None:
            print(json.dumps({"error": "file_rescan needs ANUM_FILE_SCANNER=clamav and ANUM_CLAMAV_HOST"}), file=sys.stderr)
            return 2
        if not scanner.required:
            print(json.dumps({"error": "file_rescan needs ANUM_FILE_SCAN_REQUIRED=true"}), file=sys.stderr)
            return 2
    if discovery is None:
        if settings.repository_backend == "postgresql":
            discovery = postgres_discovery(default_session_factory)
        else:
            discovery = memory_discovery()
    result = rescan(scanner, discovery, dry_run=args.dry_run, batch_size=args.batch_size, limit=args.limit)
    print(json.dumps(asdict(result), indent=2))
    return 1 if result.stopped else 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
