"""Re-encrypt stored secrets with the first key in ``ANUM_SECRETS_KEY``.

    python -m anum_api.rotate_secrets --dry-run   # report only, writes nothing
    python -m anum_api.rotate_secrets             # re-encrypt and audit

Covers every workspace model provider key (``workspace_model_configs.api_key_ciphertext``)
in every tenant. See docs/runbooks.md#rotating-anum_secrets_key.

How it crosses tenants without bypassing RLS (migration 0009, ``anum_api.maintenance``):

- It *discovers* which workspaces hold a ciphertext as ``anum_maintenance``. That role
  sees only ``tenant_id`` and ``workspace_id`` of rows with a ciphertext; it cannot read
  the ciphertext and cannot write.
- Each workspace is then handled in its own transaction as the application role with
  that tenant and workspace set as RLS context, exactly like an API request: it locks the
  row, decrypts with any configured key, encrypts with the first key, and writes an
  ``audit_records`` row in the same transaction.

The command is idempotent: a ciphertext the first key already decrypts is left alone, so
re-running after an interruption only finishes the remaining rows. The update only
applies if the ciphertext is still the one that was read, so a concurrent re-save by the
workspace owner wins. Keys, plaintexts and ciphertexts are never printed or logged.
Exit status: 0 when nothing failed, 1 when a ciphertext could not be decrypted with any
configured key, 2 on a configuration error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from sqlalchemy import text

from .audit import AuditRecord
from .maintenance import SessionFactory, default_session_factory, discover, scoped_unit
from .schemas import new_id, utc_now
from .secret_box import SecretCipher, SecretDecryptionError
from .settings import settings

ACTOR = "system:rotate-secrets"
ACTION = "secrets.rotated"

# As anum_maintenance: only rows with a ciphertext are visible, and only these columns.
_DISCOVER = text(
    """
    select tenant_id, workspace_id
    from workspace_model_configs
    where (tenant_id, workspace_id) > (:after_tenant, :after_workspace)
    order by tenant_id, workspace_id
    limit :limit
    """
)
# As the application role inside the workspace's RLS context.
_LOCK = text(
    """
    select api_key_ciphertext
    from workspace_model_configs
    where tenant_id = :tenant_id and workspace_id = :workspace_id
    for update
    """
)
# Text SQL on purpose: updated_at keeps meaning "the owner changed the configuration".
_REWRITE = text(
    """
    update workspace_model_configs
    set api_key_ciphertext = :new
    where tenant_id = :tenant_id and workspace_id = :workspace_id and api_key_ciphertext = :old
    """
)


@dataclass
class RotationReport:
    dry_run: bool
    correlation_id: str
    scanned: int = 0
    rotated: int = 0
    already_current: int = 0
    changed_concurrently: int = 0
    failed: int = 0
    # tenant/workspace ids only: never key material.
    failures: list[dict[str, str]] = field(default_factory=list)


def _audit(session, tenant_id: str, workspace_id: str, correlation_id: str, outcome: str, result: str) -> None:  # type: ignore[no-untyped-def]
    from .db.repository import SqlAlchemyRepository

    SqlAlchemyRepository(session, created_by_user_id=ACTOR).record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor=ACTOR,
            action=ACTION,
            target=f"workspace_model_config:{workspace_id}",
            outcome=outcome,
            correlation_id=correlation_id,
            created_at=utc_now(),
            metadata={"store": "workspace_model_configs", "result": result},
        )
    )


def _rotate_one(
    session_factory: SessionFactory,
    cipher: SecretCipher,
    tenant_id: str,
    workspace_id: str,
    report: RotationReport,
) -> None:
    scope = {"tenant_id": tenant_id, "workspace_id": workspace_id}
    with scoped_unit(session_factory, tenant_id, workspace_id) as session:
        ciphertext = session.execute(_LOCK, scope).scalar_one_or_none()
        if ciphertext is None:
            report.changed_concurrently += 1  # cleared or deleted since discovery
            return
        if cipher.is_current(ciphertext):
            report.already_current += 1
            return
        try:
            rotated = cipher.rotate(ciphertext)
        except SecretDecryptionError:
            report.failed += 1
            report.failures.append({"tenant_id": tenant_id, "workspace_id": workspace_id})
            if not report.dry_run:
                _audit(session, tenant_id, workspace_id, report.correlation_id, "failed", "undecryptable")
            return
        if report.dry_run:
            report.rotated += 1
            session.rollback()
            return
        if session.execute(_REWRITE, {**scope, "new": rotated, "old": ciphertext}).rowcount != 1:
            report.changed_concurrently += 1
            return
        _audit(session, tenant_id, workspace_id, report.correlation_id, "succeeded", "re-encrypted")
        report.rotated += 1


def rotate_model_config_keys(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
) -> RotationReport:
    report = RotationReport(dry_run=dry_run, correlation_id=new_id("rotate_secrets"))
    after = ("", "")
    while True:
        rows = discover(
            maintenance_session_factory or session_factory,
            _DISCOVER,
            {"after_tenant": after[0], "after_workspace": after[1], "limit": batch_size},
        )
        for row in rows:
            report.scanned += 1
            _rotate_one(session_factory, cipher, row.tenant_id, row.workspace_id, report)
        if len(rows) < batch_size:
            return report
        after = (rows[-1].tenant_id, rows[-1].workspace_id)


def _configuration_problem() -> str | None:
    if settings.repository_backend != "postgresql":
        return "rotate_secrets needs ANUM_REPOSITORY_BACKEND=postgresql (the memory backend stores nothing at rest)"
    if settings.secrets_key is None or not settings.secrets_key.get_secret_value().strip():
        return "ANUM_SECRETS_KEY is not set; set it to '<new key>,<old key>' first"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m anum_api.rotate_secrets",
        description="Re-encrypt stored provider keys with the first key in ANUM_SECRETS_KEY.",
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would change and write nothing")
    parser.add_argument("--batch-size", type=int, default=200, help="workspaces discovered per query")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    problem = _configuration_problem()
    if problem:
        print(json.dumps({"error": problem}), file=sys.stderr)
        return 2
    report = rotate_model_config_keys(SecretCipher.from_settings(), dry_run=args.dry_run, batch_size=args.batch_size)
    print(json.dumps(asdict(report), indent=2))
    return 1 if report.failed else 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
