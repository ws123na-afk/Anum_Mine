"""The action browser's tables in PostgreSQL as the non-owner app role (migration 0130, docs/action-browser.md).

Forced RLS on the tenant and workspace for sessions, steps and saved credentials; the purge
policy of the tenant lifecycle; the scope-only discovery grant to ``anum_maintenance`` for key
rotation; and rotation of the saved credentials' ciphertexts.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from cryptography.fernet import Fernet
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.action_browser.store import CredentialRecord, SessionRecord, SqlActionStore, StepRecord
from anum_api.rotate_secrets import rotate_action_browser_credentials
from anum_api.schemas import utc_now
from anum_api.secret_box import SecretCipher

pytestmark = pytest.mark.database

TABLES = ("action_browser_credentials", "action_browser_sessions", "action_browser_steps")
OLD_KEY = Fernet.generate_key()
NEW_KEY = Fernet.generate_key()


def _session(record_id: str) -> SessionRecord:
    now = utc_now()
    return SessionRecord(id=record_id, task_id="task_1", run_id="run_1", created_by="owner_a", goal="Download my statement",
                         start_url="https://bank.example/login", sites=["bank.example"], logins={"bank": "bank.example"}, cards=[],
                         created_at=now, updated_at=now)


def _credential(record_id: str, ciphertext: str) -> CredentialRecord:
    now = utc_now()
    return CredentialRecord(id=record_id, kind="login", handle="bank", site="bank.example", hint="o***@example.com on bank.example",
                            secret_ciphertext=ciphertext, created_by="owner_a", created_at=now, updated_by="owner_a", updated_at=now)


def test_tables_have_forced_rls_and_the_purge_policy(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            row = connection.execute(text("select relrowsecurity, relforcerowsecurity from pg_class where relname = :t"), {"t": table}).one()
            assert row.relrowsecurity and row.relforcerowsecurity, table
            policies = {r.policyname for r in connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table})}
            assert {f"tenant_isolation_{table}", f"lifecycle_purge_{table}"} <= policies, table


def test_rows_are_isolated_by_tenant_and_workspace(seed_scopes: None, app_session: Callable[..., Iterator[Session]]) -> None:
    a = tenant_context(TENANT_A, WORKSPACE_A)
    with app_session(a, commit=True) as session:
        store = SqlActionStore(session)
        store.create_session(a, _session("abs_a"))
        store.add_step(a, StepRecord(session_id="abs_a", seq=0, kind="opened", summary="Opened bank.example", created_at=utc_now()))
        store.add_step(a, StepRecord(session_id="abs_a", seq=0, kind="typed", summary="Typed the saved login bank (password)",
                                     detail={"handle": "{{login:bank.password}}"}, created_at=utc_now()))
        store.save_credential(a, _credential("abcred_a", SecretCipher([OLD_KEY]).encrypt(json.dumps({"username": "u", "password": "p"}))))
        record = store.get_session(a, "abs_a")
        record.status, record.step_count, record.result = "waiting", 2, {"fields": {"Balance": "AED 10"}}
        store.save_session(a, record)
    with app_session(a) as session:
        store = SqlActionStore(session)
        assert [s.seq for s in store.steps(a, "abs_a")] == [1, 2]
        assert store.get_session(a, "abs_a").result == {"fields": {"Balance": "AED 10"}}
        assert store.count_since(a, utc_now().replace(hour=0, minute=0, second=0, microsecond=0)) == 1
        assert store.credential_by_handle(a, "login", "bank") is not None
        with pytest.raises(ValueError):
            store.save_credential(a, _credential("abcred_other", "x"))
    for other in (tenant_context(TENANT_B, WORKSPACE_B), tenant_context(TENANT_A, WORKSPACE_A2)):
        with app_session(other) as session:
            store = SqlActionStore(session)
            assert store.get_session(other, "abs_a") is None and store.credentials(other) == [] and store.steps(other, "abs_a") == []
            assert session.execute(text("select count(*) from action_browser_steps")).scalar_one() == 0
    # Writing a row for another tenant from tenant A's context is refused by the policy.
    with app_session(a) as session, pytest.raises(DBAPIError):
        session.execute(text(
            "insert into action_browser_steps (tenant_id, workspace_id, session_id, seq, kind, summary) values (:t, :w, 'abs_a', 9, 'x', 'x')"),
            {"t": TENANT_B, "w": WORKSPACE_B})
        session.flush()


def test_maintenance_sees_scope_columns_only_and_rotation_re_encrypts(
    database_engine: Engine, seed_scopes: None, app_session: Callable[..., Iterator[Session]]
) -> None:
    for tenant, workspace, name in ((TENANT_A, WORKSPACE_A, "a"), (TENANT_B, WORKSPACE_B, "b")):
        context = tenant_context(tenant, workspace)
        with app_session(context, commit=True) as session:
            SqlActionStore(session).save_credential(context, _credential(f"abcred_{name}", SecretCipher([OLD_KEY]).encrypt(json.dumps({"password": name}))))
    with database_engine.begin() as connection:
        connection.execute(text("set local role anum_maintenance"))
        rows = connection.execute(text("select tenant_id, workspace_id, id from action_browser_credentials order by id")).all()
        assert [r.id for r in rows] == ["abcred_a", "abcred_b"]
    with database_engine.connect() as connection, pytest.raises(DBAPIError):
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            connection.execute(text("select secret_ciphertext from action_browser_credentials")).all()

    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    report = rotate_action_browser_credentials(SecretCipher([NEW_KEY, OLD_KEY]), session_factory=factory)
    assert report.rotated == 2 and report.failed == 0
    for tenant, workspace, name in ((TENANT_A, WORKSPACE_A, "a"), (TENANT_B, WORKSPACE_B, "b")):
        context = tenant_context(tenant, workspace)
        with app_session(context) as session:
            (record,) = SqlActionStore(session).credentials(context)
        assert json.loads(SecretCipher([NEW_KEY]).decrypt(record.secret_ciphertext)) == {"password": name}
