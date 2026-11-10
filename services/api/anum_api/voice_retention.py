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
(``anum_api.app_calls``), reported under ``app_calls``, deletes workspace events older than ``ANUM_EVENT_RETENTION_DAYS``
(``anum_api.event_retention``), reported under ``events``, and deletes meeting transcripts and
reception visitor records past each workspace's retention (``anum_api.record_retention``, owner decision O5),
reported under ``record_retention``, and then sends document expiry reminders and empties the document
vault's trash (``anum_api.vault.jobs``), reported under ``documents``, so the daily retention CronJob
covers every one of them. Last, it writes the weekly Money advisor notes that are due
(``anum_api.finance.advisor_jobs``), reported under ``finance_advisor``, and the due and overdue reminders for
money lent and borrowed with people (``anum_api.finance.personal_jobs``), reported under ``finance_loan_reminders``, and the
owner's Finance automation policies (``anum_api.finance.suggestion_jobs``), reported under ``finance_suggestions``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback
from collections.abc import Sequence
from dataclasses import asdict

from .maintenance import SessionFactory, default_session_factory
from .settings import settings

VOICE_STEP = "voice_transcripts"


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
    report, failed = run_steps(dry_run=args.dry_run, batch_size=args.batch_size)
    print(json.dumps(report, indent=2))
    return 1 if failed else 0


# The daily steps in order: report key, module, function, and whether it takes ``batch_size``.
# The module is imported inside the step's guard, so a broken import fails that step only.
STEPS: tuple[tuple[str, str, str, bool], ...] = (
    (VOICE_STEP, ".voice_retention", "purge", True),
    ("retrieval_index", ".retrieval_retention", "purge", True),
    ("conversations", ".conversation_retention", "purge", True),
    ("whatsapp", ".whatsapp_retention", "purge", True),
    ("mail", ".mail_retention", "purge", True),
    ("call_recordings", ".calls.retention", "purge", True),
    ("app_calls", ".app_calls", "purge_retention", True),
    # Workspace events older than ANUM_EVENT_RETENTION_DAYS (docs/realtime.md#event-history).
    ("events", ".event_retention", "purge", True),
    # On SIGTERM it finishes the workspace in progress and stops (``stopped: true``).
    ("record_retention", ".record_retention", "purge", True),
    ("attendance", ".attendance.jobs", "run", True),  # docs/attendance.md#daily-job
    # The document vault's expiry reminders, retention and trash (docs/documents.md#expiry-reminders-trash-and-retention).
    ("documents", ".vault.jobs", "run", False),
    ("vehicles", ".vehicles.jobs", "run", False),  # docs/vehicles.md#reminders
    ("purchases", ".purchases.jobs", "run", False),  # docs/purchases.md#reminders
    # Business books: automatic ECB rates and recurring bills (docs/business-books.md#daily-job).
    ("business_books", ".business.books_jobs", "run", False),
    # The weekly Money advisor note (docs/finance.md#money-advisor): in-app notice and phone push, never email.
    ("finance_advisor", ".finance.advisor_jobs", "run", False),
    # Money lent and borrowed with people: due and overdue reminders, in-app only (docs/finance.md#lent-and-borrowed).
    ("finance_loan_reminders", ".finance.personal_jobs", "run", False),
    # Finance automation policies the owner turned on (docs/finance.md#automation-policies).
    ("finance_suggestions", ".finance.suggestion_jobs", "run", False),
)


def run_steps(
    steps: Sequence[tuple[str, str, str, bool]] | None = None, *, dry_run: bool, batch_size: int
) -> tuple[dict[str, object], list[str]]:
    """Run every step, each on its own: one that raises is recorded and the rest still run.

    Returns the report and the names of the steps that failed. A failed step is reported as
    ``{"error": "<exception class>"}`` (never the message, which can quote a statement or a
    value) and listed under ``failed_steps``; its traceback goes to stderr. The voice
    transcript counts stay at the top level of the report, as before.
    """
    report: dict[str, object] = {}
    failed: list[str] = []
    for name, module, function, takes_batch_size in STEPS if steps is None else steps:
        try:
            step = getattr(importlib.import_module(module, __package__), function)
            result = step(dry_run=dry_run, batch_size=batch_size) if takes_batch_size else step(dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - one step must not stop the others
            print(f"retention step {name} failed", file=sys.stderr)
            traceback.print_exc()
            failed.append(name)
            report[name] = {"error": type(exc).__name__}
            continue
        if name == VOICE_STEP:
            report.update(result)
        else:
            report[name] = result
    if failed:
        report["failed_steps"] = failed
    return report, failed


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
