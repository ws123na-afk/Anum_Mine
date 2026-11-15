"""The debt overview's columns, promises to pay and alert settings in PostgreSQL (migration ``0186_pf_debts``,
docs/finance.md#debt-overview, #financial-alerts).

The new ``finance_debts`` and ``finance_person_loans`` columns are nullable (``promises`` defaults to an empty array) and
checked by the database; ``finance_alert_settings`` has forced RLS with the tenant, owner and purge policies; a profile, a
promise and alert choices round-trip through the API, and another person sees none of them. Every row is SYNTHETIC.
"""

from __future__ import annotations

from datetime import date

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_postgres_control_plane import client, postgres_backend  # noqa: F401 - pytest fixtures

from anum_api.schemas import utc_now

pytestmark = pytest.mark.database
OWNER = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "owner_1", "x-user-roles": "owner"}
ALICE = {**OWNER, "x-user-id": "alice", "x-user-roles": "member"}
FIN = "/api/v1/finance"
DEBT_COLUMNS = ("debt_type", "verified_balance", "verified_on", "disputed_amount", "dispute_note", "fees", "card_limit", "card_statement_day",
                "card_statement_balance", "card_statement_minimum", "card_statement_on")


def _members(engine: Engine) -> None:
    with engine.begin() as connection:
        for user_id, role in (("owner_1", "owner"), ("alice", "member")):
            connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                    "(:u, :t, :w, :r, true) on conflict do nothing"), {"u": user_id, "r": role, "t": TENANT_A, "w": WORKSPACE_A})


def _member_in(engine: Engine, headers: dict[str, str]) -> None:
    with engine.begin() as connection:
        connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                "(:u, :t, :w, 'owner', true) on conflict do nothing"),
                           {"u": headers["x-user-id"], "t": headers["x-tenant-id"], "w": headers["x-workspace-id"]})


def test_the_new_columns_are_nullable_and_the_alert_table_has_forced_rls(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for column in DEBT_COLUMNS:
            assert connection.execute(text("select is_nullable from information_schema.columns where table_name = 'finance_debts' and column_name = :c"),
                                      {"c": column}).scalar_one() == "YES"
        promises = connection.execute(text("select is_nullable, column_default from information_schema.columns "
                                           "where table_name = 'finance_person_loans' and column_name = 'promises'")).one()
        assert promises[0] == "NO" and "'[]'" in str(promises[1])
        table = "finance_alert_settings"
        forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
        policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
        assert forced and {f"tenant_isolation_{table}", f"owner_only_{table}", f"lifecycle_purge_{table}"} <= policies


def test_a_profile_a_promise_and_alert_choices_are_the_owners_own(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    _members(database_engine)
    today = utc_now().date()
    debt = client.post(f"{FIN}/debts", headers=OWNER, json={"name": "Example informal", "kind": "other", "currency": "SAR", "balance": "2000"})
    assert debt.status_code == 201, debt.text
    debt_id = debt.json()["id"]
    profile = client.put(f"{FIN}/debts/{debt_id}/profile", headers=OWNER, json={"debt_type": "informal", "verified_balance": "2500",
                                                                                 "verified_on": today.isoformat(), "fees": "10"})
    assert profile.status_code == 200, profile.text
    view = client.get(f"{FIN}/debt-overview", headers=OWNER).json()
    mine = next(i for i in view["items"] if i["ref_id"] == debt_id)
    assert mine["type"] == "informal" and mine["discrepancy"] is True and mine["fees"] == "10.00"
    # The person comes from Contacts under RLS (docs/finance.md#people-from-contacts): one in this workspace links, one in
    # another workspace or tenant is "not found" for the owner and refused.
    friend = client.post("/api/v1/contacts", headers=OWNER, json={"name": "Synthetic Friend"})
    assert friend.status_code == 201, friend.text
    contact_id = friend.json()["person"]["id"]
    for elsewhere in ({**OWNER, "x-workspace-id": WORKSPACE_A2}, {**OWNER, "x-tenant-id": TENANT_B, "x-workspace-id": WORKSPACE_B}):
        _member_in(database_engine, elsewhere)
        foreign = client.post("/api/v1/contacts", headers=elsewhere, json={"name": "Synthetic Stranger"})
        assert foreign.status_code == 201, foreign.text
        refused = client.post(f"{FIN}/people-loans", headers=OWNER, json={"direction": "lent", "person": "Stranger", "amount": "1", "currency": "SAR",
                                                                           "lent_on": today.isoformat(), "contact_id": foreign.json()["person"]["id"]})
        assert refused.status_code == 422, refused.text
    loan = client.post(f"{FIN}/people-loans", headers=OWNER, json={"direction": "lent", "person": "Synthetic Friend", "amount": "300", "currency": "SAR",
                                                                    "lent_on": today.isoformat(), "contact_id": contact_id})
    assert loan.status_code == 201, loan.text
    loan_id = loan.json()["id"]
    promise = client.post(f"{FIN}/people-loans/{loan_id}/promises", headers=OWNER, json={"amount": "100", "due_by": date(today.year + 1, 1, 1).isoformat()})
    assert promise.status_code == 201, promise.text
    assert client.patch(f"/api/v1/contacts/{contact_id}", headers=OWNER, json={"name": "Synthetic Friend Renamed"}).status_code == 200
    detail = client.get(f"{FIN}/people-loans/{loan_id}", headers=OWNER).json()
    assert (detail["contact_name"], detail["person"]) == ("Synthetic Friend Renamed", "Synthetic Friend")
    grouped = client.get(f"{FIN}/people-loans/people", headers=OWNER).json()
    assert [g["key"] for g in grouped["items"]] == [f"contact:{contact_id}"]
    assert client.get(f"{FIN}/people-loans/people", headers=ALICE).json()["total"] == 0
    assert detail["contact_id"] == contact_id and detail["promises"][0]["amount"] == "100.00" and detail["promise_states"][0]["status"] == "pending"
    assert client.put(f"{FIN}/alerts/settings", headers=OWNER, json={"kinds": {"refund_received": False}}).status_code == 200
    assert next(k for k in client.get(f"{FIN}/alerts/settings", headers=OWNER).json()["kinds"] if k["kind"] == "refund_received")["on"] is False
    # Alice (a member of the same workspace) sees none of it, and her own alert choices start from the defaults.
    assert client.get(f"{FIN}/debt-overview", headers=ALICE).json()["items"] == []
    assert next(k for k in client.get(f"{FIN}/alerts/settings", headers=ALICE).json()["kinds"] if k["kind"] == "refund_received")["on"] is True
    assert client.post(f"{FIN}/people-loans/{loan_id}/promises", headers=ALICE, json={"amount": "1", "due_by": today.isoformat()}).status_code == 404
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true), "
                                "set_config('anum.user_id', 'alice', true)"), {"t": TENANT_A, "w": WORKSPACE_A})
        assert connection.execute(text("select count(*) from finance_alert_settings where owner_user_id = 'owner_1'")).scalar_one() == 0
        assert connection.execute(text("select count(*) from finance_person_loans")).scalar_one() == 0
    with database_engine.begin() as connection, pytest.raises(DBAPIError):  # a lender's balance needs its date
        connection.execute(text("update finance_debts set verified_on = null where id = :i"), {"i": debt_id})


def test_alert_channels_claim_once_per_channel_and_the_push_trigger_is_private(client: TestClient, database_engine: Engine,  # noqa: F811
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """docs/finance.md#alert-channels: the push and email claims fit ``finance_notice_claims`` (no migration), the push trigger
    is the owner's private event, and a rerun tells nothing again."""
    from datetime import timedelta

    from pydantic import SecretStr
    from test_push import SERVICE_ACCOUNT

    from anum_api import notifications
    from anum_api.finance import alerts as A
    from anum_api.schemas import TenantContext
    from anum_api.settings import settings

    _members(database_engine)
    today = utc_now().date()
    made = client.post(f"{FIN}/people-loans", headers=OWNER, json={"direction": "borrowed", "person": "Synthetic Cousin", "amount": "300", "currency": "SAR",
                                                                    "lent_on": (today - timedelta(days=20)).isoformat(),
                                                                    "due_on": (today + timedelta(days=2)).isoformat(),
                                                                    # The loan's own reminder off: with it on, that reminder tells it
                                                                    # and ``loan_due_soon`` stays quiet (docs/finance.md#financial-alerts).
                                                                    "remind": False})
    assert made.status_code == 201, made.text
    monkeypatch.setattr(settings, "fcm_service_account_json", SecretStr(SERVICE_ACCOUNT))
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "anum@example.com")
    mail: list[tuple[str, str]] = []
    monkeypatch.setattr(notifications, "send_email", lambda to, subject, body, *rest: mail.append((subject, body)))
    monkeypatch.setattr(notifications, "recipient_address", lambda context, user_id: "owner@example.com")
    device = client.post("/api/v1/me/devices", headers=OWNER, json={"platform": "android", "token": "fcm-token-example-postgres-phone"})
    assert device.status_code in (200, 201), device.text
    assert client.put("/api/v1/notification-preferences", headers=OWNER, json={"email_enabled": True}).status_code == 200
    saved = client.put(f"{FIN}/alerts/settings", headers=OWNER, json={"channels": {"loan_due_soon": {"email": True}}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["channels"]["push"]["available"] is True and saved.json()["channels"]["email"]["available"] is True
    me = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="owner_1", roles=["owner"])
    sent, failed = A.run(me, now=utc_now())
    assert failed == 0 and sent >= 3 and len(mail) == 1 and "300" not in mail[0][1] and "Cousin" not in mail[0][1]
    assert A.run(me, now=utc_now()) == (0, 0)
    with database_engine.connect() as connection:
        kinds = set(connection.execute(text("select kind from finance_notice_claims where owner_user_id = 'owner_1'")).scalars())
        assert {"alert:loan_due_soon", "alert:push", "alert:email"} <= kinds
        private = connection.execute(text("select private_to from domain_events where type = 'finance.alert.raised'")).scalars().all()
        assert private and set(private) == {"owner_1"}
        refs = connection.execute(text("select payload -> 'claims' from domain_events where type = 'finance.alert.raised'")).scalars().all()
    # A push FCM never took: the dispatcher's report releases its claims under RLS, with a retry slot (gap/finance).
    from anum_api.finance import alert_channels as C

    released = C.push_undelivered(me, [r for found in refs for r in (found or [])])
    assert released >= 1
    with database_engine.connect() as connection:
        kinds = set(connection.execute(text("select kind from finance_notice_claims where owner_user_id = 'owner_1'")).scalars())
        assert C.PUSH_RETRY_CLAIM in kinds and C.PUSH_CLAIM not in kinds
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true), "
                                "set_config('anum.user_id', 'alice', true)"), {"t": TENANT_A, "w": WORKSPACE_A})
        assert connection.execute(text("select count(*) from domain_events where type = 'finance.alert.raised'")).scalar_one() == 0
        assert connection.execute(text("select count(*) from finance_notice_claims where owner_user_id = 'owner_1'")).scalar_one() == 0
