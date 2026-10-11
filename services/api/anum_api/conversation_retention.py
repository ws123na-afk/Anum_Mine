"""Delete assistant conversations idle for longer than their retention.

    python -m anum_api.conversation_retention --dry-run   # count only
    python -m anum_api.conversation_retention             # delete

A conversation whose last activity (``updated_at``: its creation or its latest turn) is
more than ``ANUM_CONVERSATION_RETENTION_DAYS`` days old (default 30; 0 keeps every
conversation) is deleted with its turns (docs/conversations.md#retention).
``python -m anum_api.voice_retention`` (the daily retention CronJob) runs this purge too
and reports it under ``conversations``, so a deployment needs no extra schedule.

Idle conversations are discovered as ``anum_maintenance`` (ids, scope and
``updated_at`` only, migration ``0019_conversation_retention``); each user's
conversations are deleted as the application role inside that tenant, workspace and
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
    retention_days: int | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
) -> dict[str, object]:
    from .db.conversation_repository import purge_idle_conversations

    result = purge_idle_conversations(
        session_factory,
        retention_days=settings.conversation_retention_days if retention_days is None else retention_days,
        maintenance_session_factory=maintenance_session_factory,
        dry_run=dry_run,
        batch_size=batch_size,
    )
    return asdict(result)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m anum_api.conversation_retention",
        description="Delete assistant conversations idle for longer than ANUM_CONVERSATION_RETENTION_DAYS.",
    )
    parser.add_argument("--dry-run", action="store_true", help="count what would be deleted")
    parser.add_argument("--batch-size", type=int, default=500, help="conversations discovered per query")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if settings.repository_backend != "postgresql":
        print(
            json.dumps({"error": "conversation_retention needs ANUM_REPOSITORY_BACKEND=postgresql"}),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(purge(dry_run=args.dry_run, batch_size=args.batch_size), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
