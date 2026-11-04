"""Remove the security code from cards saved before ANUM stopped storing it (docs/action-browser.md#the-card-security-code).

Cards saved by the first version of the action browser (migration 0130) kept the CVC inside their
encrypted secret. PCI DSS forbids keeping it after authorisation, encrypted or not, so this pass
re-encrypts every such card without it. It follows the cross-tenant maintenance pattern
(``anum_api/maintenance.py``): the rows are found as ``anum_maintenance``, which may read only the
scope columns ``(tenant_id, workspace_id, id)`` of ``action_browser_credentials``; each row is then
locked, decrypted and rewritten as the application role inside that workspace's RLS context, one
short unit of work per row. Never ``BYPASSRLS``. Idempotent: a card without a code is left alone.

The API runs it once at start-up with PostgreSQL; operators can also run
``python -m anum_api.action_browser.cleanup``. Logs carry counts only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from ..maintenance import SessionFactory, default_session_factory, discover, scoped_unit
from ..schemas import TenantContext

logger = logging.getLogger(__name__)

SYSTEM_USER = "system:card-code-cleanup"
_DISCOVER = text(
    "select tenant_id, workspace_id, id from action_browser_credentials "
    "where (tenant_id, workspace_id, id) > (:t, :w, :i) order by tenant_id, workspace_id, id limit :limit"
)


@dataclass
class CardCodeReport:
    scanned: int = 0
    removed: int = 0
    failed: int = 0


def _context(tenant_id: str, workspace_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=SYSTEM_USER, roles=[])


def strip_saved_card_codes(
    *,
    cipher: Any = None,
    session_factory: SessionFactory | None = None,
    maintenance_session_factory: SessionFactory | None = None,
    batch_size: int = 200,
) -> CardCodeReport:
    """Re-encrypt every saved card that still holds a security code, without it."""
    from ..secret_box import secret_cipher
    from ..settings import settings
    from .store import SqlActionStore, memory_action_store
    from .vault import drop_card_code_in

    box = cipher or secret_cipher()
    report = CardCodeReport()
    if session_factory is None and settings.repository_backend == "memory":
        for tenant_id, workspace_id, record_id in memory_action_store.credential_scopes():
            report.scanned += 1
            report.removed += drop_card_code_in(memory_action_store, _context(tenant_id, workspace_id), record_id, box)
        return report
    factory = session_factory or default_session_factory
    after = ("", "", "")
    while True:
        rows = discover(maintenance_session_factory or factory, _DISCOVER, {"t": after[0], "w": after[1], "i": after[2], "limit": batch_size})
        for row in rows:
            report.scanned += 1
            context = _context(row.tenant_id, row.workspace_id)
            try:
                with scoped_unit(factory, row.tenant_id, row.workspace_id) as session:
                    report.removed += drop_card_code_in(SqlActionStore(session), context, row.id, box)
            except Exception as exc:  # noqa: BLE001  # one bad row never stops the pass; the next start tries again
                report.failed += 1
                logger.warning("card_code_cleanup_failed error_class=%s", type(exc).__name__)
        if len(rows) < batch_size:
            break
        after = (rows[-1].tenant_id, rows[-1].workspace_id, rows[-1].id)
    if report.removed or report.failed:
        logger.info("card_code_cleanup scanned=%s removed=%s failed=%s", report.scanned, report.removed, report.failed)
    return report


def main() -> int:
    report = strip_saved_card_codes()
    print(f"Saved cards checked: {report.scanned}; security codes removed: {report.removed}; failed: {report.failed}.")
    return 1 if report.failed else 0


if __name__ == "__main__":  # pragma: no cover - operator command
    raise SystemExit(main())
