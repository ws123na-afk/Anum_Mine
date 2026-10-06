"""``python -m anum_api.rotate_secrets`` against PostgreSQL with RLS.

Configs are written per workspace as the non-owner app role (as the API would), then
re-encrypted across tenants. The command must discover workspaces through
``anum_maintenance`` only, change ciphertexts as the app role inside each tenant's RLS
context, be idempotent, write nothing in dry-run mode, and audit every change.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from conftest import APP_ROLE, FIXED_NOW, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api import rotate_secrets
from anum_api.db import session as db_session
from anum_api.db.model_config_repository import SqlAlchemyModelConfigStore
from anum_api.rotate_secrets import rotate_model_config_keys
from anum_api.secret_box import SecretCipher, reset_secret_cipher
from anum_api.settings import settings

pytestmark = pytest.mark.database

OLD_KEY = Fernet.generate_key()
NEW_KEY = Fernet.generate_key()
# Made-up provider keys; the gitleaks allow-list is not needed for these shapes.
PROVIDER_KEYS = {
    (TENANT_A, WORKSPACE_A): "provider-key-tenant-a-0001",
    (TENANT_A, WORKSPACE_A2): "provider-key-tenant-a2-0002",
    (TENANT_B, WORKSPACE_B): "provider-key-tenant-b-0003",
}


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None) -> sessionmaker[Session]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    return factory


@pytest.fixture
def stored_with_old_key(seed_scopes: None, app_session: Callable[..., Iterator[Session]]) -> None:
    for (tenant_id, workspace_id), api_key in PROVIDER_KEYS.items():
        context = tenant_context(tenant_id, workspace_id)
        with app_session(context, commit=True) as session:
            SqlAlchemyModelConfigStore(session, SecretCipher([OLD_KEY])).save(
                context,
                provider="openai_compatible",
                model="gpt-4.1-mini",
                base_url="https://models.example/v1",
                api_key=api_key,
                updated_at=FIXED_NOW,
            )


def _ciphertexts(database_engine: Engine) -> dict[tuple[str, str], str]:
    with database_engine.connect() as connection:
        rows = connection.execute(
            text("select tenant_id, workspace_id, api_key_ciphertext, updated_at from workspace_model_configs")
        ).all()
    assert all(row.updated_at == FIXED_NOW for row in rows)  # rotation is not a config change
    return {(row.tenant_id, row.workspace_id): row.api_key_ciphertext for row in rows}


def _audit_rows(database_engine: Engine) -> list[tuple[str, str, str, str]]:
    with database_engine.connect() as connection:
        return [
            (row.tenant_id, row.workspace_id, row.action, row.outcome)
            for row in connection.execute(
                text("select tenant_id, workspace_id, action, outcome from audit_records order by tenant_id, workspace_id")
            )
        ]


def test_rotation_re_encrypts_every_tenant_and_is_idempotent(
    stored_with_old_key: None, app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    cipher = SecretCipher([NEW_KEY, OLD_KEY])
    before = _ciphertexts(database_engine)

    dry = rotate_model_config_keys(cipher, session_factory=app_factory, dry_run=True)
    assert (dry.scanned, dry.rotated, dry.already_current, dry.failed) == (3, 3, 0, 0)
    assert _ciphertexts(database_engine) == before and _audit_rows(database_engine) == []

    report = rotate_model_config_keys(cipher, session_factory=app_factory, batch_size=2)
    assert (report.scanned, report.rotated, report.already_current, report.failed) == (3, 3, 0, 0)
    after = _ciphertexts(database_engine)
    for scope, api_key in PROVIDER_KEYS.items():
        assert after[scope] != before[scope]
        assert Fernet(NEW_KEY).decrypt(after[scope].encode()).decode() == api_key
    assert _audit_rows(database_engine) == [
        (TENANT_A, WORKSPACE_A, "secrets.rotated", "succeeded"),
        (TENANT_A, WORKSPACE_A2, "secrets.rotated", "succeeded"),
        (TENANT_B, WORKSPACE_B, "secrets.rotated", "succeeded"),
    ]

    again = rotate_model_config_keys(cipher, session_factory=app_factory)
    assert (again.scanned, again.rotated, again.already_current) == (3, 0, 3)
    assert _ciphertexts(database_engine) == after
    assert len(_audit_rows(database_engine)) == 3
    # The old key can now be removed: the new key alone reads every stored secret.
    assert {SecretCipher([NEW_KEY]).decrypt(value) for value in after.values()} == set(PROVIDER_KEYS.values())


def test_undecryptable_secret_is_reported_and_audited_without_stopping(
    stored_with_old_key: None,
    app_factory: sessionmaker[Session],
    app_session: Callable[..., Iterator[Session]],
    database_engine: Engine,
) -> None:
    lost = SecretCipher([Fernet.generate_key()]).encrypt("unrecoverable")
    with app_session(tenant_context(TENANT_A, WORKSPACE_A2), commit=True) as session:
        session.execute(text("update workspace_model_configs set api_key_ciphertext = :lost"), {"lost": lost})

    report = rotate_model_config_keys(SecretCipher([NEW_KEY, OLD_KEY]), session_factory=app_factory)

    assert (report.rotated, report.failed) == (2, 1)
    assert report.failures == [{"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A2}]
    assert (TENANT_A, WORKSPACE_A2, "secrets.rotated", "failed") in _audit_rows(database_engine)
    assert _ciphertexts(database_engine)[(TENANT_A, WORKSPACE_A2)] == lost


def test_command_line_reports_json_and_exit_status(
    stored_with_old_key: None,
    app_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(db_session, "SessionLocal", app_factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(settings, "secrets_key", SecretStr(f"{NEW_KEY.decode()},{OLD_KEY.decode()}"))
    reset_secret_cipher()
    try:
        assert rotate_secrets.main(["--dry-run"]) == 0
        output = capsys.readouterr().out
        assert '"rotated": 3' in output and '"dry_run": true' in output
        assert all(api_key not in output for api_key in PROVIDER_KEYS.values())
        assert NEW_KEY.decode() not in output and OLD_KEY.decode() not in output
        assert rotate_secrets.main([]) == 0
        assert rotate_secrets.main([]) == 0
        assert '"already_current": 3' in capsys.readouterr().out
    finally:
        reset_secret_cipher()


def test_maintenance_role_cannot_read_ciphertexts_or_write(
    stored_with_old_key: None, database_engine: Engine
) -> None:
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            scopes = connection.execute(
                text("select tenant_id, workspace_id from workspace_model_configs order by 1, 2")
            ).all()
        assert [tuple(row) for row in scopes] == sorted(PROVIDER_KEYS)
        for statement in (
            "select api_key_ciphertext from workspace_model_configs",
            "update workspace_model_configs set api_key_ciphertext = null",
            "select id from audit_records",
            "select id from tasks",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement))


def _app_role_scopes(database_engine: Engine) -> list[tuple[str, str]]:
    with database_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f"set local role {APP_ROLE}"))
            connection.execute(text("select set_config('anum.tenant_id', :value, true)"), {"value": TENANT_A})
            connection.execute(text("select set_config('anum.workspace_id', :value, true)"), {"value": WORKSPACE_A})
            rows = connection.execute(
                text("select tenant_id, workspace_id from workspace_model_configs order by 1, 2")
            ).all()
        finally:
            transaction.rollback()
    return [tuple(row) for row in rows]


@pytest.mark.parametrize(
    ("grant_options", "expected"),
    [
        # How infra/helm/bootstrap-database.sql grants it: SET ROLE only, so the
        # discovery policies never apply to the app role's own queries.
        ("with inherit false, set true", [(TENANT_A, WORKSPACE_A)]),
        # A plain grant inherits: the cross-tenant discovery policy then widens what
        # the app role sees, which is why the bootstrap must not use it.
        ("with inherit true", sorted(PROVIDER_KEYS)),
    ],
)
def test_maintenance_grant_without_inherit_keeps_the_app_role_in_one_workspace(
    stored_with_old_key: None, database_engine: Engine, grant_options: str, expected: list[tuple[str, str]]
) -> None:
    with database_engine.begin() as connection:
        connection.execute(text(f"grant anum_maintenance to {APP_ROLE} {grant_options}"))
    try:
        assert _app_role_scopes(database_engine) == expected
    finally:
        with database_engine.begin() as connection:
            connection.execute(text(f"revoke anum_maintenance from {APP_ROLE}"))
