"""Delete voice transcripts whose 30-day retention has passed.

    python -m anum_api.voice_retention --dry-run   # count only
    python -m anum_api.voice_retention             # delete

Run it at least daily (a cron job or scheduled container next to the API). Reads already
hide an expired transcript the moment ``expires_at`` passes; this job removes the rows.
Session-only transcripts never need it: they are deleted in the transaction that
completes or cancels the session.

Expired sessions are discovered as ``anum_maintenance`` (ids and scope only) and each
user's transcripts are deleted as the application role inside that tenant, workspace and
user's RLS context (``anum_api.maintenance``). The output is counts only, never text.
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
    from .db.voice_repository import purge_expired_transcripts

    result = purge_expired_transcripts(
        session_factory,
        maintenance_session_factory=maintenance_session_factory,
        dry_run=dry_run,
        batch_size=batch_size,
    )
    return asdict(result)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m anum_api.voice_retention",
        description="Delete voice transcripts past their 30-day retention.",
    )
    parser.add_argument("--dry-run", action="store_true", help="count what would be deleted")
    parser.add_argument("--batch-size", type=int, default=500, help="sessions discovered per query")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if settings.repository_backend != "postgresql":
        print(
            json.dumps({"error": "voice_retention needs ANUM_REPOSITORY_BACKEND=postgresql"}),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(purge(dry_run=args.dry_run, batch_size=args.batch_size), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
