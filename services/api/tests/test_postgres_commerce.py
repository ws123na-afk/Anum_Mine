"""Commerce in PostgreSQL (migration 0038_commerce, docs/commerce.md).

The API runs as the non-owner ``anum_test_app`` role against fake channels. Every
commerce table is under forced RLS on the tenant and workspace; "restart" clears the
in-memory store so everything is read back from PostgreSQL. ``anum_maintenance`` sees
only the ids of due connectors.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import httpx
import pytest
from commerce_fakes import AE, SA, SELLER, FakeChannels
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from anum_api.commerce import http as commerce_http
from anum_api.commerce.clients import amazon as amazon_client
from anum_api.commerce.clients import own_store as own_store_client
from anum_api.commerce.scheduler import _DUE, run_once
from anum_api.commerce.store import commerce_store
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database

API = "/api/v1/commerce"


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, role: str = "owner", user: str = "owner_test") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user, "x-user-roles": role}


A = headers()
A2 = headers(workspace_id=WORKSPACE_A2)
B = headers(TENANT_B, WORKSPACE_B)
VIEWER = headers(role="viewer", user="viewer_test")
AMAZON = {
    "channel": "amazon",
    "marketplaces": [SA, AE],
    "settings": {"seller_id": SELLER, "fetch_buyer_details": True},
    "credentials": {"lwa_client_id": "amzn1.application-oa2-client.example", "lwa_client_secret": "lwa-secret-example", "refresh_token": "atzr-example"},
}
client = TestClient(app)


@pytest.fixture
def channels(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeChannels]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    fake = FakeChannels()

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(commerce_http, "commerce_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(commerce_http, "sleep", no_sleep)
    commerce_store.clear()
    commerce_http.reset_buckets()
    amazon_client.reset_tokens()
    own_store_client.reset_tokens()
    yield fake
    commerce_store.clear()


def connect(payload: dict, scope: dict = A) -> dict:
    response = client.post(f"{API}/connectors", headers=scope, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["connector"]


def test_sync_reads_back_from_postgres_with_rls(channels: FakeChannels, database_engine: Engine) -> None:
    created = connect(AMAZON)
    report = client.post(f"{API}/connectors/{created['id']}/sync", headers=A).json()
    assert report["status"] == "ok", report
    commerce_store.clear()  # restart: everything below comes from PostgreSQL

    product = client.get(f"{API}/products?q=205/55R16", headers=VIEWER).json()["items"][0]
    assert product["sku"] == "MX-2055516" and product["total_stock"] == 13
    orders = client.get(f"{API}/orders?status=open", headers=VIEWER).json()
    assert orders["total"] == 1 and orders["items"][0]["lines"][0]["sku"] == "MX-2055516"
    detail = client.get(f"{API}/orders/{orders['items'][0]['id']}", headers=A).json()
    assert detail["customer"]["name"] == "Example Buyer"
    assert client.get(f"{API}/orders/{orders['items'][0]['id']}", headers=VIEWER).json()["customer"]["name"] is None
    dashboard = client.get(f"{API}/dashboard", headers=VIEWER).json()
    assert {c["currency"]: c["revenue"] for c in dashboard["currencies"]} == {"SAR": 900.0, "AED": 410.0}
    assert dashboard["returns"]["count"] == 1 and dashboard["fees"] == {"AED": -79.5}
    kinds = {a["kind"] for a in client.get(f"{API}/alerts", headers=VIEWER).json()["items"]}
    assert {"low_stock", "late_shipment", "buy_box_lost"} <= kinds

    # A second sync updates in place: no duplicate orders, lines or alerts.
    client.post(f"{API}/connectors/{created['id']}/sync", headers=A)
    assert client.get(f"{API}/orders", headers=VIEWER).json()["total"] == 2
    with database_engine.connect() as connection:
        assert connection.execute(text("select count(*) from commerce_order_lines")).scalar() == 2
        assert connection.execute(text("select count(*) from commerce_alerts where kind = 'late_shipment'")).scalar() == 1
        ciphertext = connection.execute(text("select credentials_ciphertext from commerce_connectors")).scalar()
        assert ciphertext and "atzr-example" not in ciphertext
        assert "Example Buyer" not in (connection.execute(text("select name_ciphertext from commerce_customers")).scalar() or "")


def test_other_workspaces_and_tenants_see_nothing(channels: FakeChannels, app_session) -> None:  # type: ignore[no-untyped-def]
    created = connect(AMAZON)
    client.post(f"{API}/connectors/{created['id']}/sync", headers=A)
    for scope in (A2, B):
        assert client.get(f"{API}/connectors", headers=scope).json() == []
        assert client.get(f"{API}/connectors/{created['id']}", headers=scope).status_code == 404
        assert client.get(f"{API}/orders", headers=scope).json()["total"] == 0
        assert client.get(f"{API}/products", headers=scope).json()["total"] == 0
        assert client.get(f"{API}/alerts", headers=scope).json()["total"] == 0
        assert client.delete(f"{API}/connectors/{created['id']}", headers=scope).status_code == 404
    # Even raw SQL as the application role in another scope reads nothing.
    from conftest import tenant_context

    with app_session(tenant_context(TENANT_B, WORKSPACE_B)) as session:
        for table in ("commerce_connectors", "commerce_orders", "commerce_order_lines", "commerce_listings", "commerce_customers", "commerce_alerts"):
            assert session.execute(text(f"select count(*) from {table}")).scalar() == 0, table  # nosec B608 - fixed table names


def test_rls_refuses_writes_into_another_scope(channels: FakeChannels, app_session) -> None:  # type: ignore[no-untyped-def]
    from conftest import tenant_context

    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A), commit=True) as session:
            session.execute(
                text(
                    "insert into commerce_products (id, tenant_id, workspace_id, sku, title, tyre) "
                    "values ('cprod_x', :tenant, :workspace, 'X', '', '{}')"
                ),
                {"tenant": TENANT_B, "workspace": WORKSPACE_B},
            )


def test_delete_cascades_and_settings_persist(channels: FakeChannels, database_engine: Engine) -> None:
    created = connect(AMAZON)
    client.post(f"{API}/connectors/{created['id']}/sync", headers=A)
    saved = client.put(f"{API}/settings", headers=A, json={"low_stock_threshold": 2, "handling_hours": 24}).json()
    assert saved["low_stock_threshold"] == 2
    commerce_store.clear()
    assert client.get(f"{API}/settings", headers=VIEWER).json()["handling_hours"] == 24
    assert client.delete(f"{API}/connectors/{created['id']}", headers=A).status_code == 204
    with database_engine.connect() as connection:
        for table in ("commerce_listings", "commerce_orders", "commerce_order_lines", "commerce_customers", "commerce_returns", "commerce_fees", "commerce_alerts"):
            assert connection.execute(text(f"select count(*) from {table}")).scalar() == 0, table  # nosec B608 - fixed table names
        # Products stay: they are the owner's catalogue, not the channel's.
        assert connection.execute(text("select count(*) from commerce_products")).scalar() == 2


def test_catalogue_crud_and_tyre_search(channels: FakeChannels) -> None:
    created = client.post(f"{API}/products", headers=A, json={"sku": "GY-1956515", "title": "Goodyear 195/65R15 91H", "title_ar": "جوديير"})
    assert created.status_code == 201
    product_id = created.json()["id"]
    assert client.post(f"{API}/products", headers=A, json={"sku": "GY-1956515"}).status_code == 409
    assert client.get(f"{API}/products?q=1956515", headers=VIEWER).json()["total"] == 1
    assert client.get(f"{API}/products?q=جوديير", headers=VIEWER).json()["total"] == 1
    assert client.get(f"{API}/products?q=100%25", headers=VIEWER).json()["total"] == 0  # LIKE wildcards are escaped
    edited = client.patch(f"{API}/products/{product_id}", headers=A, json={"low_stock_threshold": 6, "title_ar": "جوديير إفيشنت"}).json()
    assert edited["low_stock_threshold"] == 6
    commerce_store.clear()
    assert client.get(f"{API}/products/{product_id}", headers=VIEWER).json()["title_ar"] == "جوديير إفيشنت"
    assert client.delete(f"{API}/products/{product_id}", headers=A).status_code == 204


def test_price_change_after_approval_on_postgres(channels: FakeChannels) -> None:
    created = connect(AMAZON)
    client.post(f"{API}/connectors/{created['id']}/sync", headers=A)
    listing = next(i for i in client.get(f"{API}/listings?q=MX-2055516", headers=VIEWER).json()["items"] if i["marketplace_id"] == SA)
    approval = client.post(f"{API}/listings/{listing['id']}/price-change", headers=A, json={"price": 439}).json()["approval"]
    decided = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=A, json={"payload_hash": approval["payload_hash"]})
    assert decided.status_code == 200, decided.text
    assert channels.patches and channels.patches[0]["sku"] == "MX-2055516"
    commerce_store.clear()
    refreshed = client.get(f"{API}/listings?q=MX-2055516", headers=VIEWER).json()["items"]
    assert next(i for i in refreshed if i["marketplace_id"] == SA)["price"] == 439.0


def test_maintenance_role_schedules_only_due_connectors_and_sees_ids_only(channels: FakeChannels, database_engine: Engine) -> None:
    due = connect(AMAZON)
    later = connect({**AMAZON, "name": "later"}, scope=headers(workspace_id=WORKSPACE_A2))
    with database_engine.begin() as connection:
        connection.execute(text("update commerce_connectors set next_sync_at = now() + interval '1 hour' where id = :id"), {"id": later["id"]})
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            scheduled = {row[2] for row in connection.execute(_DUE, {"limit": 10})}
            # Key rotation (0050_rotation_retention) may find the ids of every connector
            # that holds a ciphertext; the scheduler's own query still picks only due ones.
            rotation = set(connection.execute(text("select id from commerce_connectors")).scalars())
        assert scheduled == {due["id"]}
        assert rotation == {due["id"], later["id"]}
        for statement in (
            "select credentials_ciphertext from commerce_connectors",
            "select settings from commerce_connectors",
            "select id from commerce_orders",
            "select name_ciphertext from commerce_customers",
            "update commerce_connectors set next_sync_at = now()",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement))


def test_the_scheduler_discovers_claims_and_syncs_on_postgres(channels: FakeChannels, database_engine: Engine) -> None:
    created = connect(AMAZON)
    with database_engine.begin() as connection:
        connection.execute(text(f"grant anum_maintenance to {APP_ROLE} with inherit false, set true"))
    try:
        assert asyncio.run(run_once()) == 1
        assert asyncio.run(run_once()) == 0  # claimed and rescheduled: not due again
    finally:
        with database_engine.begin() as connection:
            connection.execute(text(f"revoke anum_maintenance from {APP_ROLE}"))
    assert client.get(f"{API}/connectors/{created['id']}", headers=A).json()["health"] == "ok"
    assert client.get(f"{API}/orders", headers=VIEWER).json()["total"] == 2


def test_notification_preference_for_commerce_alerts_persists(channels: FakeChannels) -> None:
    prefs = client.get("/api/v1/notification-preferences", headers=A).json()
    assert prefs["commerce_alerts"] is True
    client.put("/api/v1/notification-preferences", headers=A, json={**prefs, "commerce_alerts": False})
    assert client.get("/api/v1/notification-preferences", headers=A).json()["commerce_alerts"] is False
