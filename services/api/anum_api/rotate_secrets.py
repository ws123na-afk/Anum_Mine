"""Re-encrypt stored secrets with the first key in ``ANUM_SECRETS_KEY``.

    python -m anum_api.rotate_secrets --dry-run   # report only, writes nothing
    python -m anum_api.rotate_secrets             # re-encrypt and audit

Covers every workspace model provider key (``workspace_model_configs.api_key_ciphertext``),
every workspace integration credential (``integration_credentials.ciphertext``), every
developer-mode project token (``developer_projects.token_ciphertext``, migration 0031),
every tyre planning source credential (``tyre_connections.secret_ciphertext``,
migration 0037), every Calls & Meetings Zoom/Teams token
(``meeting_connector_accounts``, migration 0043), every connected marketing account's
access and refresh token (``marketing_accounts``, docs/marketing.md), every WhatsApp
access token and app secret (``whatsapp_accounts``, migration 0033), every mailbox's OAuth
tokens (``email_accounts.access_token_enc`` and ``refresh_token_enc``, migrations 0035 and
0050) and every commerce channel credential and stored buyer detail
(``commerce_connectors``, ``commerce_customers``, migrations 0038 and 0050) and every
automation webhook trigger secret (``automation_triggers``, migrations 0049 and 0050) and
every finance connection credential (``finance_connections``, migration 0057, each in its
owner's own scope because finance rows are private to one person) and every Email Center
mailbox password (``mail_accounts``, migration 0060, also in its owner's scope) and every voice
provider account's auth token and API key secret (``call_provider_accounts``, migration 0071)
in every tenant, every Brevo API key (``brevo_connections``, migration 0074) and every connected
calendar's OAuth tokens (``calendar_connections``, migration 0089).
Pending email connections (``email_oauth_requests``) and OAuth ``state`` values live for
minutes and expire instead; phone push tokens are re-encrypted by the phone. See
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
from typing import Any

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


# Marketing account tokens (migration 0040_marketing): discovered by id, rotated per row.
_DISCOVER_ACCOUNTS = text(
    """
    select id, tenant_id, workspace_id
    from marketing_accounts
    where id > :after
    order by id
    limit :limit
    """
)
_LOCK_ACCOUNT = text(
    """
    select access_token_ciphertext, refresh_token_ciphertext
    from marketing_accounts
    where id = :id and tenant_id = :tenant_id and workspace_id = :workspace_id
    for update
    """
)
_REWRITE_ACCOUNT = text(
    """
    update marketing_accounts
    set access_token_ciphertext = :access_new, refresh_token_ciphertext = :refresh_new
    where id = :id and tenant_id = :tenant_id and workspace_id = :workspace_id
      and access_token_ciphertext is not distinct from :access_old
      and refresh_token_ciphertext is not distinct from :refresh_old
    """
)


def _rotate_account(session_factory: SessionFactory, cipher: SecretCipher, row: Any, report: RotationReport) -> None:
    scope = {"id": row.id, "tenant_id": row.tenant_id, "workspace_id": row.workspace_id}
    with scoped_unit(session_factory, row.tenant_id, row.workspace_id) as session:
        found = session.execute(_LOCK_ACCOUNT, scope).one_or_none()
        if found is None or (found.access_token_ciphertext is None and found.refresh_token_ciphertext is None):
            report.changed_concurrently += 1
            return
        old = (found.access_token_ciphertext, found.refresh_token_ciphertext)
        if all(value is None or cipher.is_current(value) for value in old):
            report.already_current += 1
            return
        try:
            new = tuple(None if value is None else cipher.rotate(value) for value in old)
        except SecretDecryptionError:
            report.failed += 1
            report.failures.append({"tenant_id": row.tenant_id, "workspace_id": row.workspace_id})
            return
        if report.dry_run:
            report.rotated += 1
            session.rollback()
            return
        updated = session.execute(
            _REWRITE_ACCOUNT,
            {**scope, "access_new": new[0], "refresh_new": new[1], "access_old": old[0], "refresh_old": old[1]},
        ).rowcount
        if updated != 1:
            report.changed_concurrently += 1
            return
        from .db.repository import SqlAlchemyRepository

        SqlAlchemyRepository(session, created_by_user_id=ACTOR).record_audit(
            AuditRecord(
                id=new_id("audit"), tenant_id=row.tenant_id, workspace_id=row.workspace_id, actor=ACTOR, action=ACTION,
                target=f"marketing_account:{row.id}", outcome="succeeded", correlation_id=report.correlation_id,
                created_at=utc_now(), metadata={"store": "marketing_accounts", "result": "re-encrypted"},
            )
        )
        report.rotated += 1


def rotate_marketing_tokens(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    correlation_id: str | None = None,
) -> RotationReport:
    """Re-encrypt connected marketing accounts' tokens (both columns in one update)."""
    report = RotationReport(dry_run=dry_run, correlation_id=correlation_id or new_id("rotate_secrets"))
    after = ""
    while True:
        rows = discover(maintenance_session_factory or session_factory, _DISCOVER_ACCOUNTS, {"after": after, "limit": batch_size})
        for row in rows:
            report.scanned += 1
            _rotate_account(session_factory, cipher, row, report)
        if len(rows) < batch_size:
            return report
        after = rows[-1].id


# -- several ciphertext columns per row: WhatsApp accounts and email mailboxes ---------------
#
# WhatsApp (migration 0033): the access token and the app secret of each workspace's number,
# discovered through the webhook-routing grant (scope columns only). Email (migrations 0035
# and 0050): each mailbox's OAuth access and refresh tokens, discovered through the column
# grant on ids and scope; 0050's policy admits every mailbox that holds a token, including
# those waiting for the owner to reconnect.


@dataclass(frozen=True)
class _MultiColumnStore:
    store: str
    target_prefix: str
    columns: tuple[str, ...]
    key_columns: tuple[str, ...]
    discover: Any
    lock: Any
    rewrite: Any
    # Rows private to one person (finance, owner-only RLS): the owner is set as anum.user_id.
    user_column: str | None = None


def _multi_store(
    table: str, target_prefix: str, columns: tuple[str, ...], key_columns: tuple[str, ...], *, user_column: str | None = None,
) -> _MultiColumnStore:
    # Table and column names come from the constants below, never from input.
    keys = ", ".join(key_columns)
    after = ", ".join(f":after_{index}" for index in range(len(key_columns)))
    selected = keys + (f", {user_column}" if user_column else "")
    where_scope = "tenant_id = :tenant_id and workspace_id = :workspace_id"
    if "id" in key_columns:
        where_scope += " and id = :row_id"
    if user_column:
        where_scope += f" and {user_column} = :owner_user_id"
    old_match = " and ".join(
        f"{column} is not distinct from cast(:old_{index} as text)" for index, column in enumerate(columns)
    )
    assignments = ", ".join(f"{column} = cast(:new_{index} as text)" for index, column in enumerate(columns))
    return _MultiColumnStore(
        store=table,
        target_prefix=target_prefix,
        columns=columns,
        key_columns=key_columns,
        discover=text(f"select {selected} from {table} where ({keys}) > ({after}) order by {keys} limit :limit"),  # nosec B608
        lock=text(f"select {', '.join(columns)} from {table} where {where_scope} for update"),  # nosec B608
        # Text SQL on purpose: updated_at keeps meaning "a person changed this".
        rewrite=text(f"update {table} set {assignments} where {where_scope} and {old_match}"),  # nosec B608
        user_column=user_column,
    )


WHATSAPP_ACCOUNTS = _multi_store(
    "whatsapp_accounts", "whatsapp_account", ("access_token_ciphertext", "app_secret_ciphertext"), ("tenant_id", "workspace_id")
)
EMAIL_ACCOUNTS = _multi_store(
    "email_accounts", "email_account", ("access_token_enc", "refresh_token_enc"), ("tenant_id", "workspace_id", "id")
)
# Commerce (migrations 0038 and 0050): each sales channel's encrypted credentials, and the
# buyer details kept only when the owner turned them on.
COMMERCE_CONNECTORS = _multi_store(
    "commerce_connectors", "commerce_connector", ("credentials_ciphertext",), ("tenant_id", "workspace_id", "id")
)
# Automation webhook triggers (migrations 0049 and 0050): each trigger's HMAC secret.
AUTOMATION_TRIGGERS = _multi_store(
    "automation_triggers", "automation_trigger", ("secret_ciphertext",), ("tenant_id", "workspace_id", "id")
)
# Finance (migration 0057): each read-only connection's credentials, private to its owner.
FINANCE_CONNECTIONS = _multi_store(
    "finance_connections", "finance_connection", ("credentials_encrypted",), ("tenant_id", "workspace_id", "id"),
    user_column="owner_user_id",
)
# Email Center (migration 0060): each mailbox's password, private to its owner.
MAIL_ACCOUNTS = _multi_store(
    "mail_accounts", "mail_account", ("password_enc",), ("tenant_id", "workspace_id", "id"),
    user_column="owner_user_id",
)
# Phone calls (migration 0071): the voice provider account's auth token and API key secret.
CALL_PROVIDER_ACCOUNTS = _multi_store(
    "call_provider_accounts", "call_provider", ("credentials_ciphertext",), ("tenant_id", "workspace_id")
)
# Calendar sync (migration 0089): each connected calendar's OAuth access and refresh tokens.
CALENDAR_CONNECTIONS = _multi_store(
    "calendar_connections", "calendar_connection", ("access_token_enc", "refresh_token_enc"), ("tenant_id", "workspace_id", "id")
)
# Brevo email campaigns (migration 0074): each workspace's API key.
BREVO_CONNECTIONS = _multi_store(
    "brevo_connections", "brevo_connection", ("api_key_ciphertext",), ("tenant_id", "workspace_id")
)
COMMERCE_CUSTOMERS = _multi_store(
    "commerce_customers", "commerce_customer",
    ("name_ciphertext", "email_ciphertext", "phone_ciphertext", "address_ciphertext"),
    ("tenant_id", "workspace_id", "id"),
)


def _rotate_multi(
    session_factory: SessionFactory,
    cipher: SecretCipher,
    spec: _MultiColumnStore,
    scope: dict[str, str],
    report: RotationReport,
) -> None:
    target = f"{spec.target_prefix}:{scope.get('row_id') or scope['workspace_id']}"

    def audit(session, outcome: str, result: str) -> None:  # type: ignore[no-untyped-def]
        _audit(
            session, scope["tenant_id"], scope["workspace_id"], report.correlation_id, outcome, result,
            store=spec.store, target=target,
        )

    with scoped_unit(session_factory, scope["tenant_id"], scope["workspace_id"], user_id=scope.get("owner_user_id")) as session:
        row = session.execute(spec.lock, scope).one_or_none()
        if row is None:
            report.changed_concurrently += 1  # disconnected since discovery
            return
        old = list(row)
        if all(value is None or cipher.is_current(value) for value in old):
            report.already_current += 1
            return
        try:
            new = [value if value is None or cipher.is_current(value) else cipher.rotate(value) for value in old]
        except SecretDecryptionError:
            report.failed += 1
            report.failures.append(dict(scope))
            if not report.dry_run:
                audit(session, "failed", "undecryptable")
            return
        if report.dry_run:
            report.rotated += 1
            session.rollback()
            return
        params: dict[str, Any] = {**scope}
        params.update({f"old_{index}": value for index, value in enumerate(old)})
        params.update({f"new_{index}": value for index, value in enumerate(new)})
        if session.execute(spec.rewrite, params).rowcount != 1:
            report.changed_concurrently += 1
            return
        audit(session, "succeeded", "re-encrypted")
        report.rotated += 1


def _rotate_store(
    spec: _MultiColumnStore,
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory,
    maintenance_session_factory: SessionFactory | None,
    dry_run: bool,
    batch_size: int,
    correlation_id: str | None,
) -> RotationReport:
    report = RotationReport(dry_run=dry_run, correlation_id=correlation_id or new_id("rotate_secrets"))
    after: tuple[str, ...] = ("",) * len(spec.key_columns)
    while True:
        params: dict[str, Any] = {f"after_{index}": value for index, value in enumerate(after)}
        rows = discover(maintenance_session_factory or session_factory, spec.discover, {**params, "limit": batch_size})
        for row in rows:
            report.scanned += 1
            scope = {"tenant_id": row.tenant_id, "workspace_id": row.workspace_id}
            if "id" in spec.key_columns:
                scope["row_id"] = row.id
            if spec.user_column:
                scope["owner_user_id"] = getattr(row, spec.user_column)
            _rotate_multi(session_factory, cipher, spec, scope, report)
        if len(rows) < batch_size:
            return report
        after = tuple(getattr(rows[-1], column) for column in spec.key_columns)


def rotate_whatsapp_secrets(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    correlation_id: str | None = None,
) -> RotationReport:
    """Re-encrypt every workspace's WhatsApp access token and app secret."""
    return _rotate_store(
        WHATSAPP_ACCOUNTS, cipher, session_factory=session_factory,
        maintenance_session_factory=maintenance_session_factory, dry_run=dry_run, batch_size=batch_size,
        correlation_id=correlation_id,
    )


def rotate_email_tokens(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    correlation_id: str | None = None,
) -> RotationReport:
    """Re-encrypt every connected mailbox's OAuth access and refresh tokens."""
    return _rotate_store(
        EMAIL_ACCOUNTS, cipher, session_factory=session_factory,
        maintenance_session_factory=maintenance_session_factory, dry_run=dry_run, batch_size=batch_size,
        correlation_id=correlation_id,
    )


def rotate_commerce_secrets(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    correlation_id: str | None = None,
) -> RotationReport:
    """Re-encrypt every sales channel's credentials and every stored buyer detail."""
    report = _rotate_store(
        COMMERCE_CONNECTORS, cipher, session_factory=session_factory,
        maintenance_session_factory=maintenance_session_factory, dry_run=dry_run, batch_size=batch_size,
        correlation_id=correlation_id,
    )
    customers = _rotate_store(
        COMMERCE_CUSTOMERS, cipher, session_factory=session_factory,
        maintenance_session_factory=maintenance_session_factory, dry_run=dry_run, batch_size=batch_size,
        correlation_id=report.correlation_id,
    )
    for name in ("scanned", "rotated", "already_current", "changed_concurrently", "failed"):
        setattr(report, name, getattr(report, name) + getattr(customers, name))
    report.failures.extend(customers.failures)
    return report


def rotate_automation_trigger_secrets(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    correlation_id: str | None = None,
) -> RotationReport:
    """Re-encrypt every automation webhook trigger's HMAC secret."""
    return _rotate_store(
        AUTOMATION_TRIGGERS, cipher, session_factory=session_factory,
        maintenance_session_factory=maintenance_session_factory, dry_run=dry_run, batch_size=batch_size,
        correlation_id=correlation_id,
    )


def rotate_finance_credentials(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    correlation_id: str | None = None,
) -> RotationReport:
    """Re-encrypt every finance connection's credentials, each in its owner's own scope."""
    return _rotate_store(
        FINANCE_CONNECTIONS, cipher, session_factory=session_factory,
        maintenance_session_factory=maintenance_session_factory, dry_run=dry_run, batch_size=batch_size,
        correlation_id=correlation_id,
    )


def rotate_mail_passwords(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    correlation_id: str | None = None,
) -> RotationReport:
    """Re-encrypt every Email Center mailbox password, each in its owner's own scope."""
    return _rotate_store(
        MAIL_ACCOUNTS, cipher, session_factory=session_factory,
        maintenance_session_factory=maintenance_session_factory, dry_run=dry_run, batch_size=batch_size,
        correlation_id=correlation_id,
    )


def rotate_call_provider_secrets(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    correlation_id: str | None = None,
) -> RotationReport:
    """Re-encrypt every workspace's voice provider credentials (docs/calls.md#provider)."""
    return _rotate_store(
        CALL_PROVIDER_ACCOUNTS, cipher, session_factory=session_factory,
        maintenance_session_factory=maintenance_session_factory, dry_run=dry_run, batch_size=batch_size,
        correlation_id=correlation_id,
    )


def rotate_brevo_keys(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    correlation_id: str | None = None,
) -> RotationReport:
    """Re-encrypt every workspace's Brevo API key."""
    return _rotate_store(
        BREVO_CONNECTIONS, cipher, session_factory=session_factory,
        maintenance_session_factory=maintenance_session_factory, dry_run=dry_run, batch_size=batch_size,
        correlation_id=correlation_id,
    )


def rotate_calendar_tokens(
    cipher: SecretCipher,
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    correlation_id: str | None = None,
) -> RotationReport:
    """Re-encrypt every connected calendar's OAuth access and refresh tokens."""
    return _rotate_store(
        CALENDAR_CONNECTIONS, cipher, session_factory=session_factory,
        maintenance_session_factory=maintenance_session_factory, dry_run=dry_run, batch_size=batch_size,
        correlation_id=correlation_id,
    )


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
            "Re-encrypt stored provider keys, integration credentials, developer project tokens, tyre source credentials, "
            "meeting connector and marketing tokens, WhatsApp credentials, mailbox tokens, commerce secrets "
            "automation webhook secrets, finance connection credentials, voice provider credentials and Brevo API keys "
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
    nested = {
        name: rotate(cipher, dry_run=args.dry_run, batch_size=args.batch_size, correlation_id=report.correlation_id)
        for name, rotate in (
            ("developer_projects", rotate_developer_project_tokens),
            ("meeting_connectors", rotate_meeting_connector_tokens),
            ("marketing_accounts", rotate_marketing_tokens),
            ("whatsapp_accounts", rotate_whatsapp_secrets),
            ("email_accounts", rotate_email_tokens),
            ("commerce", rotate_commerce_secrets),
            ("automation_triggers", rotate_automation_trigger_secrets),
            ("finance_connections", rotate_finance_credentials),
            ("mail_accounts", rotate_mail_passwords),
            ("call_providers", rotate_call_provider_secrets),
            ("brevo_connections", rotate_brevo_keys),
            ("calendar_connections", rotate_calendar_tokens),
        )
    }
    # Provider keys, integration and tyre credentials at the top level (as before); the rest nested.
    print(json.dumps({**asdict(report), **{name: asdict(item) for name, item in nested.items()}}, indent=2))
    return 1 if report.failed or any(item.failed for item in nested.values()) else 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
