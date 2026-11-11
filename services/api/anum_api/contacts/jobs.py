"""The daily directory build (docs/contacts.md#the-directory-build): link new source records in every workspace.

Run by the daily retention job (``python -m anum_api.voice_retention``, reported under ``contacts``)
or alone with ``python -m anum_api.contacts.jobs [--dry-run] [--batch-size N]``. Migration 0011's
pattern, never BYPASSRLS:

1. **Discover** as ``anum_maintenance`` the tenant and workspace (nothing else) of every Phone
   calls, Business and WhatsApp contact (migration ``0169_contacts_directory`` grants those two
   columns and a select policy on the three tables).
2. **Act** per workspace as the application role inside that workspace's RLS context, where the
   build reads the source records and writes people, identifiers, links and proposals under the
   same policies as a request. At most ``batch_size`` records per source per workspace and run,
   so one huge workspace never holds the job; the next run continues.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text

from ..schemas import TenantContext
from . import service
from .store import MemoryContactsStore, memory_data

SYSTEM_ACTOR = "system:contacts_directory"

_DISCOVER = text(
    """
    select tenant_id, workspace_id from (
      select tenant_id, workspace_id from call_contacts
      union
      select tenant_id, workspace_id from business_contacts
      union
      select tenant_id, workspace_id from whatsapp_contacts
    ) found
    order by tenant_id, workspace_id
    """
)


def _context(tenant_id: str, workspace_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=SYSTEM_ACTOR, roles=[])


def discover() -> list[tuple[str, str]]:
    from ..settings import settings

    if settings.repository_backend == "memory":
        from .sources import memory_scopes

        return sorted(memory_scopes())
    from ..maintenance import default_session_factory, discover as run_discovery

    return [(row[0], row[1]) for row in run_discovery(default_session_factory, _DISCOVER)]


def run(*, dry_run: bool = False, batch_size: int = service.DEFAULT_BUILD_LIMIT) -> dict[str, Any]:
    """Build the directory in every workspace that has source records. Counts only."""
    from ..settings import settings

    report: dict[str, Any] = {"workspaces": 0, "scanned": 0, "linked": 0, "people_created": 0, "proposals_created": 0, "incomplete_workspaces": 0}
    for tenant_id, workspace_id in discover():
        context = _context(tenant_id, workspace_id)
        report["workspaces"] += 1
        if dry_run:
            if settings.repository_backend == "memory":
                store: Any = MemoryContactsStore(memory_data, context)
                pending = sum(len(store.unlinked_sources(kind, batch_size)[0]) for kind in service.LinkKind)
            else:
                from ..db.contacts_repository import SqlAlchemyContactsStore
                from ..maintenance import default_session_factory, scoped_unit

                with scoped_unit(default_session_factory, tenant_id, workspace_id) as session:
                    store = SqlAlchemyContactsStore(session, context)
                    pending = sum(len(store.unlinked_sources(kind, batch_size)[0]) for kind in service.LinkKind)
                    session.rollback()
            report["scanned"] += pending
            continue
        if settings.repository_backend == "memory":
            built = service.build_directory(MemoryContactsStore(memory_data, context), context, limit=batch_size, actor=SYSTEM_ACTOR)
        else:
            from ..db.contacts_repository import SqlAlchemyContactsStore
            from ..maintenance import default_session_factory, scoped_unit

            with scoped_unit(default_session_factory, tenant_id, workspace_id) as session:
                built = service.build_directory(SqlAlchemyContactsStore(session, context), context, limit=batch_size, actor=SYSTEM_ACTOR)
        for key in ("scanned", "linked", "people_created", "proposals_created"):
            report[key] += getattr(built, key)
        if not built.complete:
            report["incomplete_workspaces"] += 1
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m anum_api.contacts.jobs", description="Link new Phone calls, Business and WhatsApp contacts into the Contacts directory.")
    parser.add_argument("--dry-run", action="store_true", help="count the records a run would link")
    parser.add_argument("--batch-size", type=int, default=service.DEFAULT_BUILD_LIMIT, help="records per source per workspace and run")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    print(json.dumps(run(dry_run=args.dry_run, batch_size=args.batch_size), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
