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


# -- developer-mode project tokens (migration 0032_developer_projects) ---------------------

PROJECT_TOKENS = {
    (TENANT_A, WORKSPACE_A, "devproj_a1"): "example-project-token-a1",
    (TENANT_A, WORKSPACE_A2, "devproj_a2"): "example-project-token-a2",
    (TENANT_B, WORKSPACE_B, "devproj_b1"): "example-project-token-b1",
}


@pytest.fixture
def projects_with_old_key(seed_scopes: None, app_session: Callable[..., Iterator[Session]]) -> None:
    from anum_api.db.developer_repository import SqlAlchemyProjectStore
    from anum_api.developer_projects import DeveloperProject

    old = SecretCipher([OLD_KEY])
    for (tenant_id, workspace_id, project_id), token in PROJECT_TOKENS.items():
        context = tenant_context(tenant_id, workspace_id)
        with app_session(context, commit=True) as session:
            store = SqlAlchemyProjectStore(session, context)
            for suffix, ciphertext in (("", old.encrypt(token)), ("_env", None)):
                store.add(
                    DeveloperProject(
                        id=f"{project_id}{suffix}", tenant_id=tenant_id, workspace_id=workspace_id,
                        name=f"Project {project_id}{suffix}", github_owner="acme", github_repo=f"repo{project_id}{suffix}",
                        token_ciphertext=ciphertext, created_by="owner", created_at=FIXED_NOW, updated_at=FIXED_NOW,
                    )
                )


def _project_ciphertexts(database_engine: Engine) -> dict[tuple[str, str, str], str]:
    with database_engine.connect() as connection:
        rows = connection.execute(
            text("select tenant_id, workspace_id, id, token_ciphertext from developer_projects where token_ciphertext is not null")
        ).all()
    return {(row.tenant_id, row.workspace_id, row.id): row.token_ciphertext for row in rows}


def test_project_tokens_are_re_encrypted_across_tenants_and_audited(
    projects_with_old_key: None, app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    from anum_api.rotate_secrets import rotate_developer_project_tokens

    cipher = SecretCipher([NEW_KEY, OLD_KEY])
    before = _project_ciphertexts(database_engine)
    dry = rotate_developer_project_tokens(cipher, session_factory=app_factory, dry_run=True)
    assert (dry.scanned, dry.rotated, dry.failed) == (3, 3, 0)  # projects without a stored token are not seen
    assert _project_ciphertexts(database_engine) == before

    report = rotate_developer_project_tokens(cipher, session_factory=app_factory, batch_size=2)
    assert (report.scanned, report.rotated, report.already_current, report.failed) == (3, 3, 0, 0)
    after = _project_ciphertexts(database_engine)
    for scope, token in PROJECT_TOKENS.items():
        assert after[scope] != before[scope] and Fernet(NEW_KEY).decrypt(after[scope].encode()).decode() == token
    with database_engine.connect() as connection:
        targets = sorted(
            connection.execute(text("select target from audit_records where action = 'secrets.rotated'")).scalars()
        )
    assert targets == ["developer_project:devproj_a1", "developer_project:devproj_a2", "developer_project:devproj_b1"]
    again = rotate_developer_project_tokens(cipher, session_factory=app_factory)
    assert (again.rotated, again.already_current) == (0, 3)


def test_maintenance_role_sees_only_project_ids_with_a_token(projects_with_old_key: None, database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            rows = connection.execute(text("select tenant_id, workspace_id, id from developer_projects order by 1, 2, 3")).all()
        assert [tuple(row) for row in rows] == sorted(PROJECT_TOKENS)
        for statement in (
            "select token_ciphertext from developer_projects",
            "select name from developer_projects",
            "update developer_projects set token_ciphertext = null",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement))


# -- WhatsApp credentials (0033_whatsapp) and mailbox tokens (0035_email_inbox, 0050) -------

WHATSAPP_SECRETS = {
    (TENANT_A, WORKSPACE_A, "1000000001"): ("example-wa-token-a", "example-wa-secret-a"),
    (TENANT_B, WORKSPACE_B, "1000000002"): ("example-wa-token-b", "example-wa-secret-b"),
}
# (tenant, workspace, mailbox id): (status, access token, refresh token)
MAILBOX_TOKENS = {
    (TENANT_A, WORKSPACE_A, "mbox_a1"): ("connected", "example-access-a1", "example-refresh-a1"),
    (TENANT_A, WORKSPACE_A2, "mbox_a2"): ("reauth_required", None, "example-refresh-a2"),
    (TENANT_B, WORKSPACE_B, "mbox_b1"): ("error", "example-access-b1", "example-refresh-b1"),
}


@pytest.fixture
def module_secrets_with_old_key(seed_scopes: None, app_session: Callable[..., Iterator[Session]]) -> None:
    from anum_api.db.email_repository import SqlAlchemyEmailStore
    from anum_api.db.whatsapp_repository import SqlAlchemyWhatsAppStore
    from anum_api.email_inbox import EmailAccount
    from anum_api.whatsapp_store import WhatsAppAccount

    old = SecretCipher([OLD_KEY])
    for (tenant_id, workspace_id, phone_id), (token, secret) in WHATSAPP_SECRETS.items():
        context = tenant_context(tenant_id, workspace_id)
        with app_session(context, commit=True) as session:
            SqlAlchemyWhatsAppStore(session, context, old).save_account(
                WhatsAppAccount(
                    tenant_id=tenant_id, workspace_id=workspace_id, phone_number_id=phone_id,
                    business_account_id="2000000001", verify_token_hash="a" * 64,
                    created_at=FIXED_NOW, updated_at=FIXED_NOW, updated_by="owner",
                ),
                access_token=token, app_secret=secret,
            )
    for (tenant_id, workspace_id, mailbox_id), (status, access, refresh) in MAILBOX_TOKENS.items():
        context = tenant_context(tenant_id, workspace_id)
        with app_session(context, commit=True) as session:
            store = SqlAlchemyEmailStore(session, context)
            store.add_account(
                EmailAccount(
                    id=mailbox_id, tenant_id=tenant_id, workspace_id=workspace_id, provider="gmail",
                    address=f"{mailbox_id}@example.com", connected_by="owner", status=status,
                    access_token_enc=old.encrypt(access) if access else None,
                    refresh_token_enc=old.encrypt(refresh) if refresh else None,
                    created_at=FIXED_NOW, updated_at=FIXED_NOW,
                )
            )
            if mailbox_id == "mbox_a2":
                # Needs reconnecting and holds no token: neither discovery policy admits it.
                store.add_account(
                    EmailAccount(
                        id="mbox_a2_empty", tenant_id=tenant_id, workspace_id=workspace_id, provider="zoho",
                        address="empty@example.com", connected_by="owner", status="reauth_required",
                        created_at=FIXED_NOW, updated_at=FIXED_NOW,
                    )
                )


def _module_ciphertexts(database_engine: Engine) -> dict[str, list[str | None]]:
    with database_engine.connect() as connection:
        whatsapp_rows = connection.execute(
            text(
                "select phone_number_id, access_token_ciphertext, app_secret_ciphertext, updated_at "
                "from whatsapp_accounts order by 1"
            )
        ).all()
        email_rows = connection.execute(
            text("select id, access_token_enc, refresh_token_enc, updated_at from email_accounts order by 1")
        ).all()
    assert all(row.updated_at == FIXED_NOW for row in [*whatsapp_rows, *email_rows])  # not a settings change
    return {row[0]: [row[1], row[2]] for row in [*whatsapp_rows, *email_rows]}


def test_whatsapp_and_mailbox_secrets_are_re_encrypted_across_tenants_and_audited(
    module_secrets_with_old_key: None, app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    from anum_api.rotate_secrets import rotate_email_tokens, rotate_whatsapp_secrets

    cipher = SecretCipher([NEW_KEY, OLD_KEY])
    before = _module_ciphertexts(database_engine)

    dry_whatsapp = rotate_whatsapp_secrets(cipher, session_factory=app_factory, dry_run=True)
    dry_email = rotate_email_tokens(cipher, session_factory=app_factory, dry_run=True)
    assert (dry_whatsapp.scanned, dry_whatsapp.rotated) == (2, 2)
    # The reauth_required mailbox with a token is found (0050); the one without a token is not.
    assert (dry_email.scanned, dry_email.rotated, dry_email.failed) == (3, 3, 0)
    assert _module_ciphertexts(database_engine) == before

    whatsapp_report = rotate_whatsapp_secrets(cipher, session_factory=app_factory, batch_size=1)
    email_report = rotate_email_tokens(cipher, session_factory=app_factory, batch_size=2)
    assert (whatsapp_report.rotated, whatsapp_report.failed) == (2, 0)
    assert (email_report.scanned, email_report.rotated, email_report.failed) == (3, 3, 0)

    after = _module_ciphertexts(database_engine)
    new_only = SecretCipher([NEW_KEY])
    for (_, _, phone_id), secrets in WHATSAPP_SECRETS.items():
        assert [new_only.decrypt(value) for value in after[phone_id] if value] == list(secrets)
    for (_, _, mailbox_id), (_, access, refresh) in MAILBOX_TOKENS.items():
        assert [new_only.decrypt(value) if value else None for value in after[mailbox_id]] == [access, refresh]
    assert after["mbox_a2_empty"] == [None, None]

    with database_engine.connect() as connection:
        audited = sorted(
            tuple(row)
            for row in connection.execute(
                text(
                    "select tenant_id, target, metadata->>'store', outcome from audit_records "
                    "where action = 'secrets.rotated'"
                )
            )
        )
    assert audited == sorted(
        [
            (TENANT_A, f"whatsapp_account:{WORKSPACE_A}", "whatsapp_accounts", "succeeded"),
            (TENANT_B, f"whatsapp_account:{WORKSPACE_B}", "whatsapp_accounts", "succeeded"),
            (TENANT_A, "email_account:mbox_a1", "email_accounts", "succeeded"),
            (TENANT_A, "email_account:mbox_a2", "email_accounts", "succeeded"),
            (TENANT_B, "email_account:mbox_b1", "email_accounts", "succeeded"),
        ]
    )
    again = rotate_email_tokens(cipher, session_factory=app_factory)
    assert (again.rotated, again.already_current) == (0, 3)
    assert rotate_whatsapp_secrets(cipher, session_factory=app_factory).already_current == 2


def test_undecryptable_mailbox_token_fails_without_touching_the_row(
    module_secrets_with_old_key: None,
    app_factory: sessionmaker[Session],
    app_session: Callable[..., Iterator[Session]],
    database_engine: Engine,
) -> None:
    from anum_api.rotate_secrets import rotate_email_tokens

    lost = SecretCipher([Fernet.generate_key()]).encrypt("unrecoverable")
    with app_session(tenant_context(TENANT_B, WORKSPACE_B), commit=True) as session:
        session.execute(text("update email_accounts set access_token_enc = :lost where id = 'mbox_b1'"), {"lost": lost})
    before = _module_ciphertexts(database_engine)["mbox_b1"]

    report = rotate_email_tokens(SecretCipher([NEW_KEY, OLD_KEY]), session_factory=app_factory)

    assert (report.rotated, report.failed) == (2, 1)
    assert report.failures == [{"tenant_id": TENANT_B, "workspace_id": WORKSPACE_B, "row_id": "mbox_b1"}]
    assert _module_ciphertexts(database_engine)["mbox_b1"] == before
    assert (TENANT_B, WORKSPACE_B, "secrets.rotated", "failed") in _audit_rows(database_engine)


def test_command_line_reports_whatsapp_and_mailboxes_without_secrets(
    module_secrets_with_old_key: None,
    app_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    monkeypatch.setattr(db_session, "SessionLocal", app_factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(settings, "secrets_key", SecretStr(f"{NEW_KEY.decode()},{OLD_KEY.decode()}"))
    reset_secret_cipher()
    try:
        assert rotate_secrets.main([]) == 0
        output = capsys.readouterr().out
        report = json.loads(output)
        assert report["whatsapp_accounts"]["rotated"] == 2 and report["email_accounts"]["rotated"] == 3
        assert report["whatsapp_accounts"]["correlation_id"] == report["correlation_id"]
        secrets = [value for pair in WHATSAPP_SECRETS.values() for value in pair]
        secrets += [value for _, *tokens in MAILBOX_TOKENS.values() for value in tokens if value]
        assert all(secret not in output for secret in secrets)
    finally:
        reset_secret_cipher()


def test_maintenance_role_sees_mailbox_and_number_scope_but_no_tokens(
    module_secrets_with_old_key: None, database_engine: Engine
) -> None:
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            mailboxes = connection.execute(text("select id from email_accounts order by 1")).scalars().all()
            numbers = connection.execute(text("select workspace_id from whatsapp_accounts order by 1")).scalars().all()
        assert mailboxes == ["mbox_a1", "mbox_a2", "mbox_b1"]
        assert numbers == [WORKSPACE_A, WORKSPACE_B]
        for statement in (
            "select access_token_enc from email_accounts",
            "select refresh_token_enc from email_accounts",
            "select address from email_accounts",
            "select access_token_ciphertext from whatsapp_accounts",
            "select app_secret_ciphertext from whatsapp_accounts",
            "update email_accounts set access_token_enc = null",
            "update whatsapp_accounts set access_token_ciphertext = ''",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement))


# -- commerce channel credentials and buyer details (0038_commerce, 0050) -----------------


@pytest.fixture
def commerce_with_old_key(seed_scopes: None, app_session: Callable[..., Iterator[Session]]) -> None:
    old = SecretCipher([OLD_KEY])
    for tenant_id, workspace_id, label, enabled in ((TENANT_A, WORKSPACE_A, "a", False), (TENANT_B, WORKSPACE_B, "b", True)):
        with app_session(tenant_context(tenant_id, workspace_id), commit=True) as session:
            session.execute(
                text(
                    "insert into commerce_connectors (id, tenant_id, workspace_id, channel, name, marketplaces, settings, "
                    "enabled, credentials_ciphertext, health, sync_counts, cursors, created_by) values "
                    "(:id, :tenant, :workspace, 'own_store', 'Shop', '[]', '{}', :enabled, :secret, 'pending', '{}', '{}', 'owner')"
                ),
                {"id": f"cconn_{label}", "tenant": tenant_id, "workspace": workspace_id, "enabled": enabled,
                 "secret": old.encrypt(f"example-channel-secret-{label}")},
            )
            session.execute(
                text(
                    "insert into commerce_customers (id, tenant_id, workspace_id, connector_id, ref_hash, name_ciphertext, "
                    "email_ciphertext, first_seen_at, last_seen_at) values "
                    "(:id, :tenant, :workspace, :connector, :hash, :name, :email, now(), now())"
                ),
                {"id": f"ccust_{label}", "tenant": tenant_id, "workspace": workspace_id, "connector": f"cconn_{label}",
                 "hash": label * 64, "name": old.encrypt(f"example buyer {label}"), "email": old.encrypt(f"buyer-{label}@example.com")},
            )


def test_commerce_credentials_and_buyer_details_are_re_encrypted(
    commerce_with_old_key: None, app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    from anum_api.rotate_secrets import rotate_commerce_secrets

    cipher = SecretCipher([NEW_KEY, OLD_KEY])
    dry = rotate_commerce_secrets(cipher, session_factory=app_factory, dry_run=True)
    # Both connectors (one disabled, invisible to the sync policy) and both customers.
    assert (dry.scanned, dry.rotated, dry.failed) == (4, 4, 0)
    report = rotate_commerce_secrets(cipher, session_factory=app_factory)
    assert (report.rotated, report.failed) == (4, 0)
    new_only = SecretCipher([NEW_KEY])
    with database_engine.connect() as connection:
        secrets = connection.execute(text("select id, credentials_ciphertext from commerce_connectors order by id")).all()
        buyers = connection.execute(
            text("select id, name_ciphertext, email_ciphertext, phone_ciphertext from commerce_customers order by id")
        ).all()
        targets = sorted(connection.execute(text("select target from audit_records where action = 'secrets.rotated'")).scalars())
    assert [new_only.decrypt(row.credentials_ciphertext) for row in secrets] == ["example-channel-secret-a", "example-channel-secret-b"]
    assert [new_only.decrypt(row.name_ciphertext) for row in buyers] == ["example buyer a", "example buyer b"]
    assert all(row.phone_ciphertext is None for row in buyers)
    assert targets == ["commerce_connector:cconn_a", "commerce_connector:cconn_b", "commerce_customer:ccust_a", "commerce_customer:ccust_b"]
    assert rotate_commerce_secrets(cipher, session_factory=app_factory).already_current == 4


def test_maintenance_role_sees_commerce_ids_but_no_ciphertext(commerce_with_old_key: None, database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            assert connection.execute(text("select id from commerce_customers order by 1")).scalars().all() == ["ccust_a", "ccust_b"]
            assert connection.execute(text("select id from commerce_connectors order by 1")).scalars().all() == ["cconn_a", "cconn_b"]
        for statement in (
            "select credentials_ciphertext from commerce_connectors",
            "select name_ciphertext from commerce_customers",
            "select city from commerce_customers",
            "update commerce_customers set name_ciphertext = null",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement))


# -- automation webhook trigger secrets (0049_automation_triggers, 0050) --------------------


def test_webhook_trigger_secrets_are_re_encrypted_enabled_or_not(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
    app_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    from anum_api.rotate_secrets import rotate_automation_trigger_secrets

    old = SecretCipher([OLD_KEY])
    for tenant_id, workspace_id, label, enabled in ((TENANT_A, WORKSPACE_A, "a", False), (TENANT_B, WORKSPACE_B, "b", True)):
        with app_session(tenant_context(tenant_id, workspace_id), commit=True) as session:
            session.execute(
                text(
                    "insert into automation_workflows (id, tenant_id, workspace_id, name, steps, status, version, created_by) "
                    "values (:id, :tenant, :workspace, 'Hook', '[]', 'active', 1, 'owner')"
                ),
                {"id": f"wf_{label}", "tenant": tenant_id, "workspace": workspace_id},
            )
            session.execute(
                text(
                    "insert into automation_triggers (id, tenant_id, workspace_id, workflow_id, name, kind, enabled, "
                    "secret_ciphertext, created_by, created_at, updated_at) values "
                    "(:id, :tenant, :workspace, :workflow, 'Store hook', 'webhook', :enabled, :secret, 'owner', now(), now())"
                ),
                {"id": f"trig_{label}", "tenant": tenant_id, "workspace": workspace_id, "workflow": f"wf_{label}",
                 "enabled": enabled, "secret": old.encrypt(f"example-hook-secret-{label}")},
            )
    cipher = SecretCipher([NEW_KEY, OLD_KEY])
    assert rotate_automation_trigger_secrets(cipher, session_factory=app_factory, dry_run=True).rotated == 2
    report = rotate_automation_trigger_secrets(cipher, session_factory=app_factory)
    assert (report.scanned, report.rotated, report.failed) == (2, 2, 0)
    with database_engine.connect() as connection:
        stored = connection.execute(text("select secret_ciphertext from automation_triggers order by id")).scalars().all()
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            assert connection.execute(text("select id from automation_triggers order by 1")).scalars().all() == ["trig_a", "trig_b"]
    assert [SecretCipher([NEW_KEY]).decrypt(value) for value in stored] == ["example-hook-secret-a", "example-hook-secret-b"]
    assert rotate_automation_trigger_secrets(cipher, session_factory=app_factory).already_current == 2


# -- finance connection credentials (0057_finance_plan_guard_conn) -------------------------


def test_finance_credentials_are_re_encrypted_in_each_owners_scope(
    seed_scopes: None, app_session: Callable[..., Iterator[Session]], app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    from anum_api.rotate_secrets import rotate_finance_credentials

    old = SecretCipher([OLD_KEY])
    for tenant_id, workspace_id, owner in ((TENANT_A, WORKSPACE_A, "user_a"), (TENANT_B, WORKSPACE_B, "user_b")):
        with app_session(tenant_context(tenant_id, workspace_id), commit=True) as session:
            session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": owner})
            session.execute(
                text(
                    "insert into finance_connections (id, tenant_id, workspace_id, owner_user_id, provider, kind, label, status, scopes, "
                    "credentials_encrypted) values (:id, :t, :w, :u, 'test_bank', 'bank', 'Bank', 'active', '[\"transactions:read\"]', :c)"
                ),
                {"id": f"fconn_{owner}", "t": tenant_id, "w": workspace_id, "u": owner, "c": old.encrypt(f"example-finance-key-{owner}")},
            )
    cipher = SecretCipher([NEW_KEY, OLD_KEY])
    assert (rotate_finance_credentials(cipher, session_factory=app_factory, dry_run=True).rotated) == 2
    report = rotate_finance_credentials(cipher, session_factory=app_factory)
    assert (report.scanned, report.rotated, report.failed) == (2, 2, 0)
    with database_engine.connect() as connection:
        rows = connection.execute(text("select owner_user_id, credentials_encrypted from finance_connections order by id")).all()
        connection.rollback()
        # The maintenance role finds them but can never read the ciphertext.
        with pytest.raises(DBAPIError, match="permission denied"):
            with connection.begin():
                connection.execute(text("set local role anum_maintenance"))
                connection.execute(text("select credentials_encrypted from finance_connections"))
    assert [SecretCipher([NEW_KEY]).decrypt(row.credentials_encrypted) for row in rows] == ["example-finance-key-user_a", "example-finance-key-user_b"]
    assert rotate_finance_credentials(cipher, session_factory=app_factory).already_current == 2
