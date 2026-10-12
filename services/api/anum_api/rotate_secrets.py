"""Re-encrypt stored secrets with the first key in ``ANUM_SECRETS_KEY``.

    python -m anum_api.rotate_secrets --dry-run   # report only, writes nothing
    python -m anum_api.rotate_secrets             # re-encrypt and audit

Covers every workspace model provider key (``workspace_model_configs.api_key_ciphertext``),
every workspace integration credential (``integration_credentials.ciphertext``), every
developer-mode project token (``developer_projects.token_ciphertext``, migration 0031),
every tyre planning source credential (``tyre_connections.secret_ciphertext``,
migration 0037) and every Calls & Meetings Zoom/Teams token
(``meeting_connector_accounts``, migration 0043) in every tenant. See
docs/runbooks.md#rotating-anum_secrets_key.

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


# Integration credentials: one row per workspace and integration.
_DISCOVER_CREDENTIALS = text(
    """
    select tenant_id, workspace_id, integration_id
    from integration_credentials
    where (tenant_id, workspace_id, integration_id) > (:after_tenant, :after_workspace, :after_integration)
    order by tenant_id, workspace_id, integration_id
    limit :limit
    """
)
_LOCK_CREDENTIAL = text(
    """
    select ciphertext
    from integration_credentials
    where tenant_id = :tenant_id and workspace_id = :workspace_id and integration_id = :integration_id
    for update
    """
)
_REWRITE_CREDENTIAL = text(
    """
    update integration_credentials
    set ciphertext = :new
    where tenant_id = :tenant_id and workspace_id = :workspace_id and integration_id = :integration_id
      and ciphertext = :old
    """
)


def _audit(  # type: ignore[no-untyped-def]
    session,
    tenant_id: str,
    workspace_id: str,
    correlation_id: str,
    outcome: str,
    result: str,
    *,
    store: str = "workspace_model_configs",
    target: str | None = None,
) -> None:
    from .db.repository import SqlAlchemyRepository

    SqlAlchemyRepository(session, created_by_user_id=ACTOR).record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor=ACTOR,
            action=ACTION,
            target=target or f"workspace_model_config:{workspace_id}",
            outcome=outcome,
            correlation_id=correlation_id,
            created_at=utc_now(),
            metadata={"store": store, "result": result},
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


def _rotate_credential(
    session_factory: SessionFactory,
    cipher: SecretCipher,
    tenant_id: str,
    workspace_id: str,
    integration_id: str,
    report: RotationReport,
) -> None:
    scope = {"tenant_id": tenant_id, "workspace_id": workspace_id, "integration_id": integration_id}
    audit = {"store": "integration_credentials", "target": f"integration:{integration_id}"}
    with scoped_unit(session_factory, tenant_id, workspace_id) as session:
        ciphertext = session.execute(_LOCK_CREDENTIAL, scope).scalar_one_or_none()
        if ciphertext is None:
            report.changed_concurrently += 1  # removed since discovery
            return
        if cipher.is_current(ciphertext):
            report.already_current += 1
            return
        try:
            rotated = cipher.rotate(ciphertext)
        except SecretDecryptionError:
            report.failed += 1
            report.failures.append({"tenant_id": tenant_id, "workspace_id": workspace_id, "integration_id": integration_id})
            if not report.dry_run:
                _audit(session, tenant_id, workspace_id, report.correlation_id, "failed", "undecryptable", **audit)
            return
        if report.dry_run:
            report.rotated += 1
            session.rollback()
            return
        if session.execute(_REWRITE_CREDENTIAL, {**scope, "new": rotated, "old": ciphertext}).rowcount != 1:
            report.changed_concurrently += 1
            return
        _audit(session, tenant_id, workspace_id, report.correlation_id, "succeeded", "re-encrypted", **audit)
        report.rotated += 1


def rotate_integration_credentials(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    report: RotationReport | None = None,
) -> RotationReport:
    """Re-encrypt every workspace integration credential; adds to ``report`` when given."""
    report = report or RotationReport(dry_run=dry_run, correlation_id=new_id("rotate_secrets"))
    after = ("", "", "")
    while True:
        rows = discover(
            maintenance_session_factory or session_factory,
            _DISCOVER_CREDENTIALS,
            {"after_tenant": after[0], "after_workspace": after[1], "after_integration": after[2], "limit": batch_size},
        )
        for row in rows:
            report.scanned += 1
            _rotate_credential(session_factory, cipher, row.tenant_id, row.workspace_id, row.integration_id, report)
        if len(rows) < batch_size:
            return report
        after = (rows[-1].tenant_id, rows[-1].workspace_id, rows[-1].integration_id)


# Tyre planning source credentials: one row per workspace (docs/tyre-planning.md).
_DISCOVER_TYRE = text(
    """
    select tenant_id, workspace_id
    from tyre_connections
    where (tenant_id, workspace_id) > (:after_tenant, :after_workspace)
    order by tenant_id, workspace_id
    limit :limit
    """
)
_LOCK_TYRE = text(
    """
    select secret_ciphertext
    from tyre_connections
    where tenant_id = :tenant_id and workspace_id = :workspace_id
    for update
    """
)
_REWRITE_TYRE = text(
    """
    update tyre_connections
    set secret_ciphertext = :new
    where tenant_id = :tenant_id and workspace_id = :workspace_id and secret_ciphertext = :old
    """
)


def _rotate_tyre_one(
    session_factory: SessionFactory,
    cipher: SecretCipher,
    tenant_id: str,
    workspace_id: str,
    report: RotationReport,
) -> None:
    scope = {"tenant_id": tenant_id, "workspace_id": workspace_id}
    audit = {"store": "tyre_connections", "target": f"tyre_connection:{workspace_id}"}
    with scoped_unit(session_factory, tenant_id, workspace_id) as session:
        ciphertext = session.execute(_LOCK_TYRE, scope).scalar_one_or_none()
        if ciphertext is None:
            report.changed_concurrently += 1
            return
        if cipher.is_current(ciphertext):
            report.already_current += 1
            return
        try:
            rotated = cipher.rotate(ciphertext)
        except SecretDecryptionError:
            report.failed += 1
            report.failures.append({"tenant_id": tenant_id, "workspace_id": workspace_id, "store": "tyre_connections"})
            if not report.dry_run:
                _audit(session, tenant_id, workspace_id, report.correlation_id, "failed", "undecryptable", **audit)
            return
        if report.dry_run:
            report.rotated += 1
            session.rollback()
            return
        if session.execute(_REWRITE_TYRE, {**scope, "new": rotated, "old": ciphertext}).rowcount != 1:
            report.changed_concurrently += 1
            return
        _audit(session, tenant_id, workspace_id, report.correlation_id, "succeeded", "re-encrypted", **audit)
        report.rotated += 1


def rotate_tyre_credentials(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    report: RotationReport | None = None,
) -> RotationReport:
    """Re-encrypt every tyre planning source credential; adds to ``report`` when given."""
    report = report or RotationReport(dry_run=dry_run, correlation_id=new_id("rotate_secrets"))
    after = ("", "")
    while True:
        rows = discover(
            maintenance_session_factory or session_factory,
            _DISCOVER_TYRE,
            {"after_tenant": after[0], "after_workspace": after[1], "limit": batch_size},
        )
        for row in rows:
            report.scanned += 1
            _rotate_tyre_one(session_factory, cipher, row.tenant_id, row.workspace_id, report)
        if len(rows) < batch_size:
            return report
        after = (rows[-1].tenant_id, rows[-1].workspace_id)


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


# Developer-mode project tokens: the same pattern, one project per transaction.
_DISCOVER_PROJECTS = text(
    """
    select tenant_id, workspace_id, id
    from developer_projects
    where (tenant_id, workspace_id, id) > (:after_tenant, :after_workspace, :after_id)
    order by tenant_id, workspace_id, id
    limit :limit
    """
)
_LOCK_PROJECT = text(
    """
    select token_ciphertext
    from developer_projects
    where tenant_id = :tenant_id and workspace_id = :workspace_id and id = :project_id
    for update
    """
)
_REWRITE_PROJECT = text(
    """
    update developer_projects
    set token_ciphertext = :new
    where tenant_id = :tenant_id and workspace_id = :workspace_id and id = :project_id and token_ciphertext = :old
    """
)


def _audit_project(session, scope: dict[str, str], correlation_id: str, outcome: str, result: str) -> None:  # type: ignore[no-untyped-def]
    from .db.repository import SqlAlchemyRepository

    SqlAlchemyRepository(session, created_by_user_id=ACTOR).record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            actor=ACTOR,
            action=ACTION,
            target=f"developer_project:{scope['project_id']}",
            outcome=outcome,
            correlation_id=correlation_id,
            created_at=utc_now(),
            metadata={"store": "developer_projects", "result": result},
        )
    )


def _rotate_project(
    session_factory: SessionFactory, cipher: SecretCipher, scope: dict[str, str], report: RotationReport
) -> None:
    with scoped_unit(session_factory, scope["tenant_id"], scope["workspace_id"]) as session:
        ciphertext = session.execute(_LOCK_PROJECT, scope).scalar_one_or_none()
        if ciphertext is None:
            report.changed_concurrently += 1  # token removed or project deleted since discovery
            return
        if cipher.is_current(ciphertext):
            report.already_current += 1
            return
        try:
            rotated = cipher.rotate(ciphertext)
        except SecretDecryptionError:
            report.failed += 1
            report.failures.append(dict(scope))
            if not report.dry_run:
                _audit_project(session, scope, report.correlation_id, "failed", "undecryptable")
            return
        if report.dry_run:
            report.rotated += 1
            session.rollback()
            return
        if session.execute(_REWRITE_PROJECT, {**scope, "new": rotated, "old": ciphertext}).rowcount != 1:
            report.changed_concurrently += 1
            return
        _audit_project(session, scope, report.correlation_id, "succeeded", "re-encrypted")
        report.rotated += 1


def rotate_developer_project_tokens(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    correlation_id: str | None = None,
) -> RotationReport:
    report = RotationReport(dry_run=dry_run, correlation_id=correlation_id or new_id("rotate_secrets"))
    after = ("", "", "")
    while True:
        rows = discover(
            maintenance_session_factory or session_factory,
            _DISCOVER_PROJECTS,
            {"after_tenant": after[0], "after_workspace": after[1], "after_id": after[2], "limit": batch_size},
        )
        for row in rows:
            report.scanned += 1
            scope = {"tenant_id": row.tenant_id, "workspace_id": row.workspace_id, "project_id": row.id}
            _rotate_project(session_factory, cipher, scope, report)
        if len(rows) < batch_size:
            return report
        after = (rows[-1].tenant_id, rows[-1].workspace_id, rows[-1].id)


# Calls & Meetings connector tokens (migration 0043_meetings): user-private rows, so each
# is rewritten with the tenant, workspace *and* user set as RLS context.
_DISCOVER_CONNECTORS = text(
    """
    select tenant_id, workspace_id, user_id, provider
    from meeting_connector_accounts
    where (tenant_id, workspace_id, user_id, provider) > (:after_tenant, :after_workspace, :after_user, :after_provider)
    order by tenant_id, workspace_id, user_id, provider
    limit :limit
    """
)
_LOCK_CONNECTOR = text(
    """
    select token_ciphertext
    from meeting_connector_accounts
    where tenant_id = :tenant_id and workspace_id = :workspace_id and user_id = :user_id and provider = :provider
    for update
    """
)
_REWRITE_CONNECTOR = text(
    """
    update meeting_connector_accounts
    set token_ciphertext = :new
    where tenant_id = :tenant_id and workspace_id = :workspace_id and user_id = :user_id
      and provider = :provider and token_ciphertext = :old
    """
)


def _audit_connector(session, scope: dict[str, str], correlation_id: str, outcome: str, result: str) -> None:  # type: ignore[no-untyped-def]
    from .db.repository import SqlAlchemyRepository

    SqlAlchemyRepository(session, created_by_user_id=ACTOR).record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            actor=ACTOR,
            action=ACTION,
            target=f"meeting_connector:{scope['provider']}",
            outcome=outcome,
            correlation_id=correlation_id,
            created_at=utc_now(),
            metadata={"store": "meeting_connector_accounts", "result": result},
        )
    )


def _rotate_connector(
    session_factory: SessionFactory, cipher: SecretCipher, scope: dict[str, str], report: RotationReport
) -> None:
    with scoped_unit(session_factory, scope["tenant_id"], scope["workspace_id"], user_id=scope["user_id"]) as session:
        ciphertext = session.execute(_LOCK_CONNECTOR, scope).scalar_one_or_none()
        if ciphertext is None:
            report.changed_concurrently += 1  # disconnected since discovery
            return
        if cipher.is_current(ciphertext):
            report.already_current += 1
            return
        try:
            rotated = cipher.rotate(ciphertext)
        except SecretDecryptionError:
            report.failed += 1
            report.failures.append(dict(scope))
            if not report.dry_run:
                _audit_connector(session, scope, report.correlation_id, "failed", "undecryptable")
            return
        if report.dry_run:
            report.rotated += 1
            session.rollback()
            return
        if session.execute(_REWRITE_CONNECTOR, {**scope, "new": rotated, "old": ciphertext}).rowcount != 1:
            report.changed_concurrently += 1
            return
        _audit_connector(session, scope, report.correlation_id, "succeeded", "re-encrypted")
        report.rotated += 1


def rotate_meeting_connector_tokens(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    correlation_id: str | None = None,
) -> RotationReport:
    report = RotationReport(dry_run=dry_run, correlation_id=correlation_id or new_id("rotate_secrets"))
    after = ("", "", "", "")
    while True:
        rows = discover(
            maintenance_session_factory or session_factory,
            _DISCOVER_CONNECTORS,
            {
                "after_tenant": after[0], "after_workspace": after[1], "after_user": after[2],
                "after_provider": after[3], "limit": batch_size,
            },
        )
        for row in rows:
            report.scanned += 1
            scope = {"tenant_id": row.tenant_id, "workspace_id": row.workspace_id, "user_id": row.user_id, "provider": row.provider}
            _rotate_connector(session_factory, cipher, scope, report)
        if len(rows) < batch_size:
            return report
        after = (rows[-1].tenant_id, rows[-1].workspace_id, rows[-1].user_id, rows[-1].provider)


def _configuration_problem() -> str | None:
    if settings.repository_backend != "postgresql":
        return "rotate_secrets needs ANUM_REPOSITORY_BACKEND=postgresql (the memory backend stores nothing at rest)"
    if settings.secrets_key is None or not settings.secrets_key.get_secret_value().strip():
        return "ANUM_SECRETS_KEY is not set; set it to '<new key>,<old key>' first"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m anum_api.rotate_secrets",
        description=(
            "Re-encrypt stored provider keys, integration credentials, developer project tokens and tyre source credentials "
            "with the first key in ANUM_SECRETS_KEY."
        ),
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
    cipher = SecretCipher.from_settings()
    report = rotate_model_config_keys(cipher, dry_run=args.dry_run, batch_size=args.batch_size)
    rotate_integration_credentials(cipher, dry_run=args.dry_run, batch_size=args.batch_size, report=report)
    rotate_tyre_credentials(cipher, dry_run=args.dry_run, batch_size=args.batch_size, report=report)
    projects = rotate_developer_project_tokens(
        cipher, dry_run=args.dry_run, batch_size=args.batch_size, correlation_id=report.correlation_id
    )
    connectors = rotate_meeting_connector_tokens(
        cipher, dry_run=args.dry_run, batch_size=args.batch_size, correlation_id=report.correlation_id
    )
    # Provider keys, integration and tyre credentials at the top level (as before); project
    # and meeting connector tokens nested.
    print(json.dumps({**asdict(report), "developer_projects": asdict(projects), "meeting_connectors": asdict(connectors)}, indent=2))
    return 1 if report.failed or projects.failed or connectors.failed else 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
