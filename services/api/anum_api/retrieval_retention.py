"""Delete retrieval index rows of expired or deleted memories and of deleted files.

    python -m anum_api.retrieval_retention --dry-run   # count only
    python -m anum_api.retrieval_retention             # delete

``python -m anum_api.voice_retention`` (the daily retention CronJob) runs this purge
too, so a deployment needs no extra schedule. Search already ignores these chunks the
moment a memory expires or a memory or file is deleted; this job removes the rows
(docs/memory.md#retention).

Workspaces holding sources of expired memories are discovered as ``anum_maintenance``
(scope only, migration ``0015_retrieval_retention``); each workspace's rows are deleted
as the application role inside that tenant and workspace's RLS context
(``anum_api.maintenance``). The output is counts only, never text.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict

from .maintenance import SessionFactory, default_session_factory
from .settings import settings


def purge(
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
) -> dict[str, object]:
    from .db.retrieval_repository import purge_expired_retrieval_sources

    result = purge_expired_retrieval_sources(
        session_factory,
        maintenance_session_factory=maintenance_session_factory,
        dry_run=dry_run,
        batch_size=batch_size,
    )
    return asdict(result)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m anum_api.retrieval_retention",
        description="Delete retrieval index rows of expired or deleted memories and deleted files.",
    )
    parser.add_argument("--dry-run", action="store_true", help="count what would be deleted")
    parser.add_argument("--batch-size", type=int, default=500, help="workspaces discovered per query")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if settings.repository_backend != "postgresql":
        print(
            json.dumps({"error": "retrieval_retention needs ANUM_REPOSITORY_BACKEND=postgresql"}),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(purge(dry_run=args.dry_run, batch_size=args.batch_size), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
