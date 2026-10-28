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

The same run then purges the retrieval index rows of expired or deleted memories and of
deleted files (``anum_api.retrieval_retention``), reported under ``retrieval_index``, and
deletes assistant conversations idle for longer than ``ANUM_CONVERSATION_RETENTION_DAYS``
(``anum_api.conversation_retention``), reported under ``conversations``, and deletes
WhatsApp messages older than each workspace's retention (``anum_api.whatsapp_retention``),
reported under ``whatsapp``, deletes what ANUM keeps of each Email Center mailbox past its
retention (``anum_api.mail_retention``), reported under ``mail``, deletes call recordings
past their retention (``anum_api.calls.retention``), reported under ``call_recordings``, and
deletes in-app calls that ended more than ``ANUM_CALLS_RETENTION_DAYS`` ago
(``anum_api.app_calls``), reported under ``app_calls``, and, last, deletes meeting transcripts and
reception visitor records past each workspace's retention (``anum_api.record_retention``, owner decision O5),
reported under ``record_retention``, so the daily retention CronJob covers every one of them.
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
        description=(
            "Delete voice transcripts past their 30-day retention, retrieval index rows "
            "of expired or deleted memories and files, idle assistant conversations, WhatsApp "
            "messages past their workspace's retention, Email Center messages and labels past their mailbox's retention, "
            "and meeting transcripts and visitor records past their workspace's retention."
        ),
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
    from .app_calls import purge_retention as purge_app_calls
    from .conversation_retention import purge as purge_conversations
    from .mail_retention import purge as purge_mail
    from .retrieval_retention import purge as purge_retrieval_index
    from .whatsapp_retention import purge as purge_whatsapp

    report = purge(dry_run=args.dry_run, batch_size=args.batch_size)
    report["retrieval_index"] = purge_retrieval_index(dry_run=args.dry_run, batch_size=args.batch_size)
    report["conversations"] = purge_conversations(dry_run=args.dry_run, batch_size=args.batch_size)
    report["whatsapp"] = purge_whatsapp(dry_run=args.dry_run, batch_size=args.batch_size)
    report["mail"] = purge_mail(dry_run=args.dry_run, batch_size=args.batch_size)
    from .calls.retention import purge as purge_call_recordings

    report["call_recordings"] = purge_call_recordings(dry_run=args.dry_run, batch_size=args.batch_size)
    report["app_calls"] = purge_app_calls(dry_run=args.dry_run, batch_size=args.batch_size)
    # Last: on SIGTERM it finishes the workspace in progress and stops (``stopped: true``).
    from .record_retention import purge as purge_records

    report["record_retention"] = purge_records(dry_run=args.dry_run, batch_size=args.batch_size)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
