"""Commerce (docs/commerce.md) against fake channels (httpx.MockTransport), in-memory stores."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from datetime import date, timedelta

import httpx
import pytest
from commerce_fakes import AE, SA, SELLER, FakeChannels
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.agent_planning import AgentPlanner
from anum_api.agent_skills import default_skill_registry
from anum_api.authorization import ROLE_PERMISSIONS, Permission, Role
from anum_api.commerce import http as commerce_http
from anum_api.commerce.clients import amazon as amazon_client
from anum_api.commerce.clients import own_store as own_store_client
from anum_api.commerce.config import commerce_config
from anum_api.commerce.http import TokenBucket, UnsafeEndpointError, check_owner_url
from anum_api.commerce.insights import PERIODS, period_range, zone
from anum_api.commerce.models import AlertKind, Channel
from anum_api.commerce.scheduler import run_once
from anum_api.commerce.store import commerce_store
from anum_api.commerce.tools import _PERIOD, change_tool, plan_call
from anum_api.commerce.tyre import parse_dot, parse_tyre, size_query
from anum_api.dependencies import memory_repository
from anum_api.model_egress import EgressPolicy
from anum_api.voice import voice_store

TENANT, WORKSPACE = "tenant_shop", "workspace_shop"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_shop", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_shop", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_shop", "x-user-roles": "viewer"}
OTHER_WORKSPACE = {**OWNER, "x-workspace-id": "workspace_elsewhere"}
API = "/api/v1/commerce"

client = TestClient(main.app)

AMAZON = {
    "channel": "amazon",
    "marketplaces": [SA, AE],
    "settings": {"seller_id": SELLER},
    "credentials": {"lwa_client_id": "amzn1.application-oa2-client.example", "lwa_client_secret": "lwa-secret-example", "refresh_token": "atzr-example"},
}
NOON = {"channel": "noon", "marketplaces": ["SA"], "credentials": {"api_key": "noon-key-example"}}
STORE = {
    "channel": "own_store",
    "name": "ANUM 360",
    "settings": {"mode": "anum360", "base_url": "https://store.example.test"},
    "credentials": {"email": "anum-sync@store.example.test", "password": "example-password"},
}


@pytest.fixture(autouse=True)
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeChannels]:
    channels = FakeChannels()
    monkeypatch.setattr(commerce_http, "commerce_transport", httpx.MockTransport(channels))

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(commerce_http, "sleep", no_sleep)
    commerce_store.clear()
    commerce_http.reset_buckets()
    amazon_client.reset_tokens()
    own_store_client.reset_tokens()
    yield channels
    commerce_store.clear()


def connect(payload: dict, headers: dict = OWNER) -> dict:
    response = client.post(f"{API}/connectors", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["connector"]


def sync(connector_id: str, headers: dict = OWNER) -> dict:
    response = client.post(f"{API}/connectors/{connector_id}/sync", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# -- pure helpers -------------------------------------------------------------------------


def test_tyre_attributes_are_read_from_titles() -> None:
    spec = parse_tyre("Michelin Primacy 4 205/55R16 91V tyre DOT 2324")
    assert (spec.size, spec.width_mm, spec.aspect_ratio, spec.rim_inches) == ("205/55R16", 205, 55, 16.0)
    assert (spec.load_index, spec.speed_index, spec.dot_week, spec.dot_year) == ("91", "V", 23, 2024)
    assert parse_tyre("Pirelli P Zero 245/40 ZR18 97Y").size == "245/40ZR18"
    assert parse_tyre("LT265/70R17 121/118S all-season").load_index == "121/118"
    assert parse_tyre("LT265/70R17 121/118S all-season").season == "all_season"
    assert parse_tyre("Leather Tote Example").is_tyre is False
    assert parse_dot("DOT 4ZX3 ABCD 0125") == (1, 2025)
    assert size_query("2055516") == size_query("205 55 16") == size_query("205/55r16") == "205/55R16"
    assert parse_tyre("205/55R16").age_years(date(2030, 1, 1)) is None
    assert parse_tyre("205/55R16 DOT 0120").age_years(date(2025, 1, 10)) == 5


@pytest.mark.parametrize(
    ("text", "tool", "args"),
    [
        ("ANUM, how were Amazon sales today?", "commerce.sales_summary", {"channel": "amazon", "period": "today"}),
        ("How did noon do yesterday?", "commerce.sales_summary", {"channel": "noon", "period": "yesterday"}),
        ("noon sales yesterday", "commerce.sales_summary", {"channel": "noon", "period": "yesterday"}),
        ("What were my sales this week", "commerce.sales_summary", {"channel": None, "period": "last_7_days"}),
        ("كيف كانت مبيعات أمازون اليوم؟", "commerce.sales_summary", {"channel": "amazon", "period": "today"}),
        ("What is low on stock on noon?", "commerce.low_stock", {"channel": "noon"}),
        ("show order 402-0000001-0000001", "commerce.get_order", {"order": "402-0000001-0000001"}),
        ("Amazon orders not shipped", "commerce.list_orders", {"channel": "amazon", "status": "unshipped"}),
        ("Set the price of SKU MX-2055516 on Amazon.sa to 450", "commerce.amazon.update_price", {"sku": "MX-2055516", "marketplace_id": SA, "price": 450.0}),
        ("set noon stock of sku MX-2055516 to 12", "commerce.noon.update_stock", {"sku": "MX-2055516", "quantity": 12}),
        ("change the price on my website for WEB-1 to 799.50", "commerce.own_store.update_price", {"sku": "WEB-1", "price": 799.5}),
    ],
)
def test_requests_are_parsed_from_the_users_own_words(text: str, tool: str | None, args: dict | None) -> None:
    call = plan_call(text)
    if tool is None:
        assert call is None
        return
    assert call is not None and call.name == tool
    for key, value in (args or {}).items():
        assert call.arguments.get(key) == value, key


def test_an_incomplete_change_request_is_never_proposed_for_approval() -> None:
    call = plan_call("Amazon: lower the price please")
    assert call is not None and call.name == "anum.respond"
    assert "SKU" in call.arguments["text"]


def test_period_names_match_and_cover_local_days() -> None:
    assert set(PERIODS) == {period for period, _ in _PERIOD}
    start, end, first, last = period_range("today", zone("Asia/Riyadh"))
    assert end - start == timedelta(days=1) and first == last
    assert zone("Not/AZone").key == "Asia/Riyadh"


def test_token_bucket_waits_by_the_documented_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(commerce_http, "monotonic", lambda: clock[0])
    bucket = TokenBucket(rate=0.5, burst=1)
    assert bucket.wait_time() == 0
    bucket.take()
    assert bucket.wait_time() == pytest.approx(2.0)
    clock[0] = 2.0
    assert bucket.wait_time() == 0


def test_owner_urls_pass_the_ssrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commerce_http, "owner_url_policy", lambda: EgressPolicy(enforce=True))
    for bad in ("http://store.example.test", "https://127.0.0.1", "https://169.254.169.254/latest", "https://user:pw@shop.example.test", "https://10.0.0.5"):
        with pytest.raises(UnsafeEndpointError):
            check_owner_url(bad)
    assert check_owner_url("https://store.example.test/") == "https://store.example.test"
    payload = {**STORE, "settings": {"mode": "anum360", "base_url": "https://192.168.1.10"}}
    response = client.post(f"{API}/connectors", headers=OWNER, json=payload)
    assert response.status_code == 422 and "store URL" in response.json()["error"]["message"]


def test_commerce_permissions_by_role() -> None:
    assert Permission.COMMERCE_READ in ROLE_PERMISSIONS[Role.VIEWER]
    assert Permission.COMMERCE_OPERATE in ROLE_PERMISSIONS[Role.MEMBER]
    assert Permission.COMMERCE_MANAGE not in ROLE_PERMISSIONS[Role.MEMBER]
    assert Permission.COMMERCE_PII not in ROLE_PERMISSIONS[Role.MEMBER]
    assert {Permission.COMMERCE_MANAGE, Permission.COMMERCE_PII} <= ROLE_PERMISSIONS[Role.OWNER]


# -- connectors ------------------------------------------------------------------------------


def test_connector_credentials_are_encrypted_and_never_returned() -> None:
    created = connect(AMAZON)
    assert created["credentials_configured"] is True
    assert created["credential_hint"] == "app …mple"
    assert created["health"] == "pending"
    assert created["marketplace_names"] == ["Amazon.sa", "Amazon.ae"]
    stored = commerce_store.ciphertexts[created["id"]]
    assert "atzr-example" not in stored and "lwa-secret-example" not in stored
    listed = client.get(f"{API}/connectors", headers=VIEWER)
    assert listed.status_code == 200
    text = listed.text
    assert "atzr-example" not in text and "lwa-secret-example" not in text and "cursors" not in text


def test_connector_setup_explains_what_is_missing() -> None:
    missing_seller = client.post(f"{API}/connectors", headers=OWNER, json={**AMAZON, "settings": {}})
    assert missing_seller.status_code == 422 and "Merchant Token" in missing_seller.json()["error"]["message"]
    missing_secret = client.post(f"{API}/connectors", headers=OWNER, json={**AMAZON, "credentials": {"lwa_client_id": "x"}})
    assert missing_secret.status_code == 422 and "lwa_client_secret" in missing_secret.json()["error"]["message"]
    bad_marketplace = client.post(f"{API}/connectors", headers=OWNER, json={**AMAZON, "marketplaces": ["A1PA6795UKMFR9"]})
    assert bad_marketplace.status_code == 422


def test_only_owners_connect_channels_and_members_may_sync() -> None:
    denied = client.post(f"{API}/connectors", headers=MEMBER, json=AMAZON)
    assert denied.status_code == 403 and "owners" in denied.json()["error"]["message"]
    created = connect(AMAZON)
    assert client.patch(f"{API}/connectors/{created['id']}", headers=MEMBER, json={"name": "x"}).status_code == 403
    assert client.delete(f"{API}/connectors/{created['id']}", headers=VIEWER).status_code == 403
    assert client.post(f"{API}/connectors/{created['id']}/sync", headers=VIEWER).status_code == 403
    assert client.post(f"{API}/connectors/{created['id']}/sync", headers=MEMBER).status_code == 200
    setup = client.get(f"{API}/setup", headers=VIEWER).json()
    assert setup["permissions"] == {"read": True, "operate": False, "manage": False, "pii": False}


def test_another_workspace_sees_nothing() -> None:
    created = connect(AMAZON)
    sync(created["id"])
    assert client.get(f"{API}/connectors/{created['id']}", headers=OTHER_WORKSPACE).status_code == 404
    assert client.get(f"{API}/connectors", headers=OTHER_WORKSPACE).json() == []
    assert client.get(f"{API}/orders", headers=OTHER_WORKSPACE).json()["total"] == 0
    assert client.post(f"{API}/connectors/{created['id']}/sync", headers=OTHER_WORKSPACE).status_code == 404


def test_test_connection_records_health(fake: FakeChannels) -> None:
    created = connect(AMAZON)
    tested = client.post(f"{API}/connectors/{created['id']}/test", headers=OWNER).json()
    assert tested["ok"] is True and tested["message"] == "Connected to Amazon.sa, Amazon.ae"
    assert tested["connector"]["health"] == "ok"
    fake.lwa_refuse = True
    amazon_client.reset_tokens()
    refused = client.post(f"{API}/connectors/{created['id']}/test", headers=OWNER).json()
    assert refused["ok"] is False and "Authorize the app again" in refused["message"]
    assert refused["connector"]["health"] == "auth_failed"


def test_replacing_credentials_keeps_the_others_and_resets_health() -> None:
    created = connect(AMAZON)
    updated = client.patch(f"{API}/connectors/{created['id']}", headers=OWNER, json={"credentials": {"refresh_token": "atzr-example"}, "name": "Amazon KSA+UAE"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Amazon KSA+UAE" and updated.json()["health"] == "pending"
    off = client.patch(f"{API}/connectors/{created['id']}", headers=OWNER, json={"enabled": False}).json()
    assert off["health"] == "disabled"
    assert sync(created["id"])["status"] == "skipped"


# -- Amazon sync ------------------------------------------------------------------------------


def test_amazon_sync_brings_listings_orders_stock_money_and_competition(fake: FakeChannels) -> None:
    created = connect({**AMAZON, "settings": {"seller_id": SELLER, "fetch_buyer_details": True}})
    report = sync(created["id"])
    assert report["status"] == "ok", report
    assert report["counts"]["listings"] == 3
    assert report["counts"]["orders"] == 2
    assert report["counts"]["fba_stock"] == 1
    assert report["counts"]["returns"] == 1
    assert report["counts"]["fees"] >= 4  # two fees, one refund, one settlement
    assert report["counts"]["price_checks"] == 3
    assert fake.lwa_calls == 1  # the access token is reused for every call

    products = client.get(f"{API}/products?q=2055516", headers=VIEWER).json()
    assert products["total"] == 1
    product = products["items"][0]
    assert product["sku"] == "MX-2055516"
    assert product["tyre"]["size"] == "205/55R16" and product["tyre"]["dot_year"] == 2024
    by_market = {item["marketplace_id"]: item for item in product["listings"]}
    assert by_market[SA]["quantity"] == 3 and by_market[SA]["price"] == 450.0
    assert by_market[AE]["quantity"] == 10 and by_market[AE]["fulfillment"] == "amazon"
    assert by_market[SA]["buy_box_winner"] is False and by_market[SA]["buy_box_price"] == 430.0
    assert product["total_stock"] == 13

    orders = client.get(f"{API}/orders?status=open", headers=VIEWER).json()
    assert orders["total"] == 1
    late = orders["items"][0]
    assert late["external_id"] == "402-0000001-0000001" and late["late"] is True
    assert late["lines"][0]["quantity"] == 2

    # Buyer details: encrypted, owners only.
    owner_view = client.get(f"{API}/orders/{late['id']}", headers=OWNER).json()
    assert owner_view["customer"]["pii_visible"] is True
    assert owner_view["customer"]["name"] == "Example Buyer" and owner_view["customer"]["city"] == "Riyadh"
    member_view = client.get(f"{API}/orders/{late['id']}", headers=MEMBER).json()
    assert member_view["customer"]["pii_visible"] is False and member_view["customer"]["name"] is None
    stored = next(iter(commerce_store.customers.values()))
    assert "Example Buyer" not in (stored.name_ciphertext or "")
    restricted = [call for call in fake.calls if call[1].endswith("/restrictedDataToken")]
    assert len(restricted) == 1  # only the unshipped merchant-fulfilled order

    alerts = client.get(f"{API}/alerts", headers=VIEWER).json()
    kinds = {item["kind"] for item in alerts["items"]}
    assert {"low_stock", "late_shipment", "buy_box_lost", "competitor_price"} <= kinds

    dashboard = client.get(f"{API}/dashboard", headers=VIEWER).json()
    assert dashboard["currency"] == "SAR"
    assert {c["currency"]: c["revenue"] for c in dashboard["currencies"]} == {"SAR": 900.0, "AED": 410.0}
    assert dashboard["top_products"][0]["sku"] == "MX-2055516" and dashboard["top_products"][0]["tyre_size"] == "205/55R16"
    assert dashboard["pending_total"] == 1 and dashboard["late_total"] == 1
    assert dashboard["returns"]["count"] == 1 and dashboard["returns"]["amount"] == {"SAR": 450.0}
    assert dashboard["fees"] == {"AED": -79.5}
    assert dashboard["low_stock"][0]["sku"] == "MX-2055516"
    assert any(row["sku"] == "MX-2055516" for row in dashboard["price_alerts"])
    aed = client.get(f"{API}/dashboard?currency=AED", headers=VIEWER).json()
    assert aed["currency"] == "AED" and aed["by_day"][0]["revenue"] == 410.0


def test_incremental_sync_uses_the_cursor(fake: FakeChannels) -> None:
    created = connect(AMAZON)
    sync(created["id"])
    first = [c for c in fake.calls if c[1].endswith("/orders/v0/orders")][0][2]["query"]
    assert "LastUpdatedAfter" in first
    fake.calls.clear()
    sync(created["id"])
    second = [c for c in fake.calls if c[1].endswith("/orders/v0/orders")][0][2]["query"]
    assert second["LastUpdatedAfter"] > first["LastUpdatedAfter"]
    # Orders are upserted by their channel id: no duplicates.
    assert client.get(f"{API}/orders", headers=VIEWER).json()["total"] == 2


def test_a_rate_limit_keeps_what_was_fetched_and_waits(fake: FakeChannels) -> None:
    created = connect(AMAZON)
    fake.throttle.add("/orders/v0/orders")
    report = sync(created["id"])
    assert report["status"] == "throttled"
    connector = client.get(f"{API}/connectors/{created['id']}", headers=OWNER).json()
    assert connector["health"] == "throttled"
    assert client.get(f"{API}/listings", headers=VIEWER).json()["total"] == 3  # listings already saved
    from anum_api.schemas import utc_now

    assert commerce_store.connectors[created["id"]].next_sync_at > utc_now() + timedelta(seconds=100)


def test_refused_credentials_raise_a_reconnect_alert(fake: FakeChannels) -> None:
    created = connect(AMAZON)
    fake.lwa_refuse = True
    report = sync(created["id"])
    assert report["status"] == "auth_failed"
    alerts = client.get(f"{API}/alerts", headers=VIEWER).json()["items"]
    assert alerts[0]["kind"] == "sync_failed" and alerts[0]["title"].startswith("Reconnect")
    fake.lwa_refuse = False
    amazon_client.reset_tokens()
    assert sync(created["id"])["status"] == "ok"
    open_kinds = {a["kind"] for a in client.get(f"{API}/alerts", headers=VIEWER).json()["items"]}
    assert "sync_failed" not in open_kinds


def test_alerts_can_be_acknowledged_by_members_only() -> None:
    created = connect(AMAZON)
    sync(created["id"])
    alert = client.get(f"{API}/alerts", headers=VIEWER).json()["items"][0]
    assert client.post(f"{API}/alerts/{alert['id']}/acknowledge", headers=VIEWER).status_code == 403
    acked = client.post(f"{API}/alerts/{alert['id']}/acknowledge", headers=MEMBER).json()
    assert acked["status"] == "acknowledged" and acked["acknowledged_by"] == "member_shop"
    assert alert["id"] not in {a["id"] for a in client.get(f"{API}/alerts", headers=VIEWER).json()["items"]}
    # Syncing again does not reopen the acknowledged alert.
    sync(created["id"])
    assert alert["id"] not in {a["id"] for a in client.get(f"{API}/alerts", headers=VIEWER).json()["items"]}


def test_alert_rules_are_owner_settings_and_drive_alerts() -> None:
    created = connect(AMAZON)
    assert client.put(f"{API}/settings", headers=MEMBER, json={"low_stock_threshold": 0}).status_code == 403
    saved = client.put(f"{API}/settings", headers=OWNER, json={"low_stock_threshold": 0, "price_alert_percent": 50}).json()
    assert saved["low_stock_threshold"] == 0
    sync(created["id"])
    kinds = {a["kind"] for a in client.get(f"{API}/alerts", headers=VIEWER).json()["items"]}
    assert "low_stock" not in kinds and "competitor_price" not in kinds


def test_new_alerts_are_published_on_the_event_stream() -> None:
    created = connect(AMAZON)
    sync(created["id"])
    events = [e for e in memory_repository.store.events if e.type == "commerce.alert.raised" and e.tenant_id == TENANT]
    assert events and {e.payload["kind"] for e in events} >= {"low_stock", "late_shipment"}


def test_deleting_a_connector_removes_its_data(fake: FakeChannels) -> None:
    created = connect(AMAZON)
    sync(created["id"])
    assert client.delete(f"{API}/connectors/{created['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/orders", headers=VIEWER).json()["total"] == 0
    assert client.get(f"{API}/listings", headers=VIEWER).json()["total"] == 0
    assert created["id"] not in commerce_store.ciphertexts


# -- noon and the own store ------------------------------------------------------------------------


def test_noon_sync(fake: FakeChannels) -> None:
    created = connect(NOON)
    report = sync(created["id"])
    assert report["status"] == "ok", report
    assert report["counts"] == {"listings": 1, "orders": 1, "returns": 1}
    listing = client.get(f"{API}/listings", headers=VIEWER).json()["items"][0]
    assert listing["price"] == 440.0 and listing["quantity"] == 0 and listing["external_id"] == "ZEXAMPLE1"
    product = client.get(f"{API}/products", headers=VIEWER).json()["items"][0]
    assert product["title_ar"] == "ميشلان برايمسي 4 205/55R16"
    order = client.get(f"{API}/orders", headers=VIEWER).json()["items"][0]
    assert order["status"] == "unshipped" and order["total_amount"] == 880.0 and order["ship_city"] == "Jeddah"


def test_anum360_store_sync_with_a_view_only_staff_account(fake: FakeChannels) -> None:
    created = connect(STORE)
    assert created["credential_hint"] == "an…@store.example.test"
    report = sync(created["id"])
    assert report["status"] == "ok", report
    assert report["counts"] == {"listings": 2, "orders": 2, "returns": 1}  # the 400-day-old order is outside the window
    listings = {item["sku"]: item for item in client.get(f"{API}/listings", headers=VIEWER).json()["items"]}
    assert listings["BAG-TOTE-01"]["price"] == 999.0 and listings["BAG-TOTE-01"]["currency"] == "AED"
    assert listings["prod-scarf-2"]["price"] == 350.0  # no SKU in the store: its product id stands in
    products = {p["sku"]: p for p in client.get(f"{API}/products", headers=VIEWER).json()["items"]}
    assert products["BAG-TOTE-01"]["title_ar"] == "حقيبة جلدية مثال"
    orders = {o["external_id"]: o for o in client.get(f"{API}/orders", headers=VIEWER).json()["items"]}
    assert orders["ANM-20261011-0001"]["status"] == "unshipped" and orders["ANM-20261011-0001"]["ship_country"] == "AE"
    assert orders["ANM-20261011-0001"]["lines"][0]["sku"] == "BAG-TOTE-01"
    assert orders["ANM-20261010-0002"]["status"] == "delivered"
    assert orders["ANM-20261011-0001"]["customer_id"] is None  # contact stays in the store
    returns = client.get(f"{API}/returns", headers=VIEWER).json()["items"]
    assert returns[0]["sku"] == "prod-scarf-2" and returns[0]["amount"] == 350.0
    logins = [c for c in fake.calls if c[1].endswith("/api/admin/login")]
    assert len(logins) == 1


def test_anum360_store_with_two_step_verification_explains_the_fix(fake: FakeChannels) -> None:
    fake.store_two_factor = True
    created = connect(STORE)
    report = sync(created["id"])
    assert report["status"] == "auth_failed"
    assert "view keys" in report["message"]


def test_rest_and_supabase_own_store_modes(fake: FakeChannels) -> None:
    rest = connect({"channel": "own_store", "settings": {"mode": "rest", "base_url": "https://shop.example.test", "currency": "SAR"}, "credentials": {"api_key": "shop-key-example"}})
    assert sync(rest["id"])["counts"] == {"listings": 1, "orders": 1}
    web = next(p for p in client.get(f"{API}/products", headers=VIEWER).json()["items"] if p["sku"] == "WEB-1")
    assert web["tyre"]["size"] == "245/40ZR18"
    supabase = connect({
        "channel": "own_store",
        "name": "App (Supabase)",
        "settings": {
            "mode": "supabase",
            "base_url": "https://project.supabase.example",
            "tables": {"products": "items"},
            "columns": {"products": {"sku": "code", "title": "name", "price": "price_sar", "quantity": "qty"}},
        },
        "credentials": {"api_key": "supabase-read-example"},
    })
    assert sync(supabase["id"])["counts"] == {"listings": 1, "orders": 1}
    order_query = next(q for q in fake.supabase_queries if q["path"] == "/rest/v1/orders")["query"]
    assert order_query["updated_at"].startswith("gt.") and order_query["order"] == "updated_at.asc"
    listing = next(i for i in client.get(f"{API}/listings?q=SB-1", headers=VIEWER).json()["items"])
    refused = client.post(f"{API}/listings/{listing['id']}/price-change", headers=OWNER, json={"price": 600})
    assert refused.status_code == 409 and "read-only" in refused.json()["error"]["message"]
    bad_column = client.post(f"{API}/connectors", headers=OWNER, json={**{"channel": "own_store", "credentials": {"api_key": "k"}}, "settings": {"mode": "supabase", "base_url": "https://project.supabase.example", "columns": {"orders": {"id": "id; drop"}}}})
    assert bad_column.status_code == 422


def _signed(secret: str, body: bytes, sent_at: int | None = None) -> dict:
    sent_at = int(time.time()) if sent_at is None else sent_at
    signature = "sha256=" + hmac.new(secret.encode(), f"{sent_at}.".encode() + body, hashlib.sha256).hexdigest()
    return {"x-anum-timestamp": str(sent_at), "x-anum-signature": signature, "content-type": "application/json"}


def test_webhook_events_are_accepted_only_when_signed() -> None:
    response = client.post(f"{API}/connectors", headers=OWNER, json={"channel": "own_store", "settings": {"mode": "webhook", "currency": "SAR"}})
    assert response.status_code == 201
    secret = response.json()["webhook_secret"]
    connector = response.json()["connector"]
    assert secret.startswith("whsec_") and connector["webhook_path"].endswith(connector["id"])
    body = json.dumps({"events": [
        {"type": "product", "data": {"sku": "APP-1", "title": "Yokohama BluEarth 195/65R15 91H", "price": 300, "quantity": 2}},
        {"type": "order", "data": {"id": "APP-ORDER-1", "status": "paid", "total": 600, "items": [{"sku": "APP-1", "quantity": 2, "unit_price": 300}]}},
    ]}).encode()
    path = connector["webhook_path"]
    assert client.post(path, content=body, headers=_signed("whsec_wrong", body)).status_code == 401
    assert client.post(path, content=body, headers=_signed(secret, body, int(time.time()) - 3600)).status_code == 401
    accepted = client.post(path, content=body, headers=_signed(secret, body))
    assert accepted.status_code == 200 and accepted.json()["counts"] == {"listings": 1, "orders": 1}
    assert client.get(f"{API}/orders", headers=VIEWER).json()["items"][0]["external_id"] == "APP-ORDER-1"
    rotated = client.post(f"{API}/connectors/{connector['id']}/webhook-secret", headers=OWNER).json()["webhook_secret"]
    assert client.post(path, content=body, headers=_signed(secret, body)).status_code == 401
    assert client.post(path, content=body, headers=_signed(rotated, body)).status_code == 200
    # The webhook URL of another workspace does not resolve.
    other = path.replace(WORKSPACE, "workspace_elsewhere")
    assert client.post(other, content=body, headers=_signed(rotated, body)).status_code == 404


# -- catalogue CRUD -----------------------------------------------------------------------------


def test_catalogue_crud_and_search_by_tyre_size() -> None:
    created = client.post(f"{API}/products", headers=MEMBER, json={"sku": "GY-1956515", "title": "Goodyear EfficientGrip 195/65R15 91H", "title_ar": "جوديير 195/65R15"})
    assert created.status_code == 201
    product = created.json()
    assert product["tyre"]["size"] == "195/65R15" and product["tyre"]["speed_index"] == "H"
    assert client.post(f"{API}/products", headers=MEMBER, json={"sku": "GY-1956515"}).status_code == 409
    assert client.post(f"{API}/products", headers=VIEWER, json={"sku": "X-1"}).status_code == 403
    assert client.get(f"{API}/products?q=195 65 15", headers=VIEWER).json()["total"] == 1
    assert client.get(f"{API}/products?q=جوديير", headers=VIEWER).json()["total"] == 1
    edited = client.patch(
        f"{API}/products/{product['id']}",
        headers=MEMBER,
        json={"low_stock_threshold": 8, "tyre": {"size": "195/65R15", "width_mm": 195, "aspect_ratio": 65, "rim_inches": 15, "dot_week": 12, "dot_year": 2025}},
    ).json()
    assert edited["low_stock_threshold"] == 8 and edited["tyre"]["dot_year"] == 2025
    bad = client.patch(f"{API}/products/{product['id']}", headers=MEMBER, json={"tyre": {"speed_index": "X"}})
    assert bad.status_code == 422
    assert client.delete(f"{API}/products/{product['id']}", headers=MEMBER).status_code == 204
    assert client.get(f"{API}/products/{product['id']}", headers=VIEWER).status_code == 404


def test_a_listed_product_cannot_be_deleted() -> None:
    created = connect(AMAZON)
    sync(created["id"])
    product = client.get(f"{API}/products?q=MX-2055516", headers=VIEWER).json()["items"][0]
    refused = client.delete(f"{API}/products/{product['id']}", headers=OWNER)
    assert refused.status_code == 409 and "still listed" in refused.json()["error"]["message"]


def test_paging_is_bounded_and_counts_everything() -> None:
    for index in range(30):
        client.post(f"{API}/products", headers=OWNER, json={"sku": f"SKU-{index:03d}", "title": f"Product {index:03d}"})
    page = client.get(f"{API}/products?limit=10&offset=20", headers=VIEWER).json()
    assert page["total"] == 30 and len(page["items"]) == 10 and page["items"][0]["sku"] == "SKU-020"
    assert client.get(f"{API}/products?limit=1000", headers=VIEWER).status_code == 422


# -- approved changes -----------------------------------------------------------------------------


def _approve(approval: dict, headers: dict = OWNER) -> httpx.Response:
    return client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=headers, json={"payload_hash": approval["payload_hash"]})


def test_a_price_change_waits_for_approval_then_patches_amazon(fake: FakeChannels) -> None:
    created = connect(AMAZON)
    sync(created["id"])
    listing = next(i for i in client.get(f"{API}/listings?q=MX-2055516", headers=VIEWER).json()["items"] if i["marketplace_id"] == SA)
    assert client.post(f"{API}/listings/{listing['id']}/price-change", headers=VIEWER, json={"price": 435}).status_code == 403
    proposal = client.post(f"{API}/listings/{listing['id']}/price-change", headers=MEMBER, json={"price": 435})
    assert proposal.status_code == 200, proposal.text
    approval = proposal.json()["approval"]
    assert approval["action"] == change_tool(Channel.AMAZON, "price") and approval["risk_level"] == "high"
    assert approval["arguments"]["price"] == 435.0 and approval["arguments"]["previous_price"] == 450.0
    assert approval["arguments"]["target_host"] == "sellingpartnerapi-eu.amazon.com"
    assert approval["payload_hash"]
    assert fake.patches == []  # nothing sent before approval
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": "0" * 64}).status_code == 409
    decided = _approve(approval)
    assert decided.status_code == 200, decided.text
    assert decided.json()["run"]["status"] == "completed"
    patch = fake.patches[0]
    assert patch["sku"] == "MX-2055516" and patch["query"]["marketplaceIds"] == SA
    offer = patch["body"]["patches"][0]
    assert offer["path"] == "/attributes/purchasable_offer"
    assert offer["value"][0]["our_price"][0]["schedule"][0]["value_with_tax"] == 435.0
    assert patch["body"]["productType"] == "TIRE"
    updated = client.get(f"{API}/listings?q=MX-2055516", headers=VIEWER).json()["items"]
    assert next(i for i in updated if i["marketplace_id"] == SA)["price"] == 435.0


def test_a_stock_change_on_the_anum360_store_adjusts_by_the_difference(fake: FakeChannels) -> None:
    created = connect(STORE)
    sync(created["id"])
    listing = next(i for i in client.get(f"{API}/listings?q=BAG-TOTE-01", headers=VIEWER).json()["items"])
    approval = client.post(f"{API}/listings/{listing['id']}/stock-change", headers=OWNER, json={"quantity": 6}).json()["approval"]
    assert approval["action"] == "commerce.own_store.update_stock" and approval["arguments"]["target_host"] == "store.example.test"
    assert _approve(approval).json()["run"]["status"] == "completed"
    assert fake.store_adjustments == [{"product_id": "prod-bag-1", "adjustment": "4", "reason": "anum"}]
    # The store keeps price changes for two-step-verified staff: the run fails and says why.
    price = client.post(f"{API}/listings/{listing['id']}/price-change", headers=OWNER, json={"price": 950}).json()["approval"]
    result = _approve(price).json()
    assert result["run"]["status"] == "failed" and "store's admin" in result["run"]["result"]


def test_noon_stock_change_after_approval(fake: FakeChannels) -> None:
    created = connect(NOON)
    sync(created["id"])
    listing = client.get(f"{API}/listings", headers=VIEWER).json()["items"][0]
    approval = client.post(f"{API}/listings/{listing['id']}/stock-change", headers=MEMBER, json={"quantity": 16}).json()["approval"]
    assert _approve(approval).status_code == 200
    assert fake.noon_patches == [{"sku": "MX-2055516", "body": {"country_code": "SA", "stock": 16}}]


# -- assistant and voice --------------------------------------------------------------------------


def _run_task(prompt: str, headers: dict = OWNER) -> dict:
    task = client.post("/api/v1/tasks", headers=headers, json={"title": prompt[:60], "prompt": prompt}).json()
    response = client.post(f"/api/v1/tasks/{task['id']}/run", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_commerce_skill_is_selectable() -> None:
    planner = AgentPlanner(main.model_gateway, default_skill_registry(), main.tool_registry)
    selected = planner.skills.select("how were amazon sales today", planner.tools.names)
    assert "anum.commerce" in {skill.id for skill in selected}


def test_asking_for_sales_runs_the_read_tool_without_approval() -> None:
    created = connect(AMAZON)
    sync(created["id"])
    result = _run_task("ANUM, how were Amazon sales today?")
    assert result["run"]["status"] == "completed" and result["approval"] is None
    assert result["run"]["result"].startswith("Amazon sales today: 2 orders, 3 units, SAR 900.00 and AED 410.00.")


def test_asking_for_a_price_change_pauses_for_approval() -> None:
    created = connect(AMAZON)
    sync(created["id"])
    result = _run_task("Set the price of SKU MX-2055516 on Amazon.sa to 445")
    assert result["approval"] is not None
    assert result["approval"]["action"] == "commerce.amazon.update_price"
    assert result["approval"]["arguments"] == {"sku": "MX-2055516", "marketplace_id": SA, "price": 445.0}


def test_the_change_tools_refuse_viewers_even_when_called_directly() -> None:
    import asyncio

    from anum_api.agent_tools import ToolCall
    from anum_api.schemas import TenantContext

    viewer = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="viewer_shop", roles=["viewer"])
    call = ToolCall(name="commerce.amazon.update_price", arguments={"sku": "MX-2055516", "price": 1.0})
    result = asyncio.run(main.tool_registry.execute(call, viewer))
    assert result.status == "blocked" and "owners and members" in result.summary
    from anum_api.agent_tools import ToolPolicy

    decision = ToolPolicy().evaluate(call, main.tool_registry.definition(call.name), viewer)
    assert decision.outcome.value == "block"


def _ask(text: str, locale: str = "en-US", headers: dict = OWNER) -> dict:
    session = client.post("/api/v1/voice/sessions", headers=headers, json={"locale": locale}).json()
    segment = client.post(
        f"/api/v1/voice/sessions/{session['id']}/transcript", headers=headers, json={"text": text, "is_final": True, "client_sequence": 0}
    ).json()
    response = client.post(f"/api/v1/voice/sessions/{session['id']}/ask", headers=headers, json={"transcript_segment_id": segment["id"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_voice_answers_sales_questions_from_the_synced_data() -> None:
    voice_store.clear()
    assert "No sales channel" in _ask("Anum, how were Amazon sales today?")["reply"]
    created = connect(AMAZON)
    sync(created["id"])
    spoken = _ask("Anum, how were Amazon sales today?")
    assert spoken["risk_tier"] == "read"
    assert spoken["reply"].startswith("Amazon sales today: 2 orders")
    arabic = _ask("أنوم، كيف كانت مبيعات أمازون اليوم؟", locale="ar-SA")
    assert arabic["reply"].startswith("مبيعات أمازون اليوم: 2 طلب")
    low = _ask("Which tyres are low on stock?")
    assert "low on stock" in low["reply"]
    before = len(client.get("/api/v1/approvals", headers=OWNER).json())
    change = _ask("Set the price of SKU MX-2055516 on Amazon.sa to 445")
    assert change["risk_tier"] == "confirm" and change["proposed_task"]
    assert len(client.get("/api/v1/approvals", headers=OWNER).json()) == before  # voice only proposes


def test_summary_endpoint_in_both_languages() -> None:
    created = connect(NOON)
    sync(created["id"])
    english = client.get(f"{API}/summary?period=today&channel=noon", headers=VIEWER).json()
    assert english["text"].startswith("noon sales today: 1 order, 2 units, SAR 880.00")
    arabic = client.get(f"{API}/summary?period=today&channel=noon&lang=ar", headers=VIEWER).json()
    assert arabic["text"].startswith("مبيعات نون اليوم")
    assert client.get(f"{API}/summary?period=forever", headers=VIEWER).status_code == 422


# -- background sync --------------------------------------------------------------------------------


def test_the_scheduler_syncs_due_connectors_once(fake: FakeChannels) -> None:
    import asyncio

    created = connect(AMAZON)
    assert asyncio.run(run_once()) == 1
    assert client.get(f"{API}/connectors/{created['id']}", headers=OWNER).json()["health"] == "ok"
    # Not due again until the interval passes.
    assert asyncio.run(run_once()) == 0


def test_the_dashboard_refuses_unbounded_ranges() -> None:
    too_long = client.get(f"{API}/dashboard?from=2020-01-01&to=2026-01-01", headers=VIEWER)
    assert too_long.status_code == 422
    backwards = client.get(f"{API}/dashboard?from=2026-02-01&to=2026-01-01", headers=VIEWER)
    assert backwards.status_code == 422
    empty = client.get(f"{API}/dashboard", headers=VIEWER).json()
    assert empty["currencies"] == [] and empty["connectors"] == 0 and empty["currency"] is None


def test_notification_preferences_carry_commerce_alerts() -> None:
    prefs = client.get("/api/v1/notification-preferences", headers=OWNER).json()
    assert prefs["commerce_alerts"] is True
    saved = client.put("/api/v1/notification-preferences", headers=OWNER, json={**prefs, "commerce_alerts": False}).json()
    assert saved["commerce_alerts"] is False


def test_config_bounds_are_sane() -> None:
    assert commerce_config.max_retry_wait_seconds <= commerce_config.sync_time_budget_seconds
    assert AlertKind.SYNC_FAILED.value == "sync_failed"


def test_commerce_migration_is_expand_only_with_forced_rls() -> None:
    from pathlib import Path

    versions = Path(__file__).parents[1] / "migrations" / "versions"
    source = (versions / "0038_commerce.py").read_text(encoding="utf-8")
    assert 'revision = "0038_commerce"' in source and len("0038_commerce") <= 32
    assert 'down_revision = "0037_tyre_planning"' in source
    for table in ("commerce_connectors", "commerce_products", "commerce_listings", "commerce_orders", "commerce_order_lines",
                  "commerce_customers", "commerce_returns", "commerce_fees", "commerce_alerts", "commerce_settings"):
        assert f'"{table}"' in source
    assert "force row level security" in source and "bypassrls" not in source.lower()
    assert "security definer" not in source.lower()
    upgrade = source[source.index("def upgrade"):source.index("def downgrade")]
    assert "drop_" not in upgrade and "alter_column" not in upgrade


def test_new_alerts_become_phone_pushes_for_owners_and_members() -> None:
    from anum_api.push import PREFERENCE, TEXT, PushKind, intent_for

    created = connect(AMAZON)
    sync(created["id"])
    event = next(e for e in memory_repository.store.events if e.type == "commerce.alert.raised" and e.tenant_id == TENANT)
    intent = intent_for(event)
    assert intent is not None and intent.kind == PushKind.COMMERCE_ALERT
    assert intent.data()["commerce_alert_id"] == event.subject
    assert PREFERENCE[PushKind.COMMERCE_ALERT] == "commerce_alerts"
    title, body = TEXT[PushKind.COMMERCE_ALERT]
    assert "MX-2055516" not in title + body  # a push never carries the alert's content


def test_price_and_stock_tools_always_ask_under_autonomy() -> None:
    from anum_api.autonomy import PROTECTED_KINDS, category_of, kind_of

    for channel in Channel:
        for kind in ("price", "stock"):
            name = change_tool(channel, kind)
            definition = main.tool_registry.definition(name)
            assert definition is not None and definition.category == "commerce"
            assert kind_of(name, definition) in PROTECTED_KINDS
            assert category_of(name, definition).value == "commerce"
    read = main.tool_registry.definition("commerce.sales_summary")
    assert read is not None and read.action_kind == "read"


def _chat(question: str, headers: dict = OWNER) -> list[dict]:
    """Ask in the Assistant chat; the tool cards streamed back (docs/conversations.md#tools-in-chat)."""
    conversation = client.post("/api/v1/conversations", headers=headers, json={}).json()["id"]
    response = client.post(f"/api/v1/conversations/{conversation}/ask", headers=headers, json={"content": question})
    assert response.status_code == 200, response.text
    frames = [dict(line.split(": ", 1) for line in frame.splitlines()) for frame in response.text.strip().split("\n\n")]
    return [json.loads(frame["data"])["card"] for frame in frames if frame["event"] == "tool"]


def test_store_questions_in_chat_answer_with_commerce_cards() -> None:
    created = connect(AMAZON)
    sync(created["id"])
    skills, tools = default_skill_registry(), main.tool_registry.names
    from anum_api.chat_tools import chat_intent

    assert chat_intent("ANUM, how were Amazon sales today?", skills, tools) == "commerce"
    assert chat_intent("كم المبيعات اليوم على أمازون؟", skills, tools) == "commerce"
    # A price word that is not about a sales channel is not a commerce question.
    assert chat_intent("send the price list to the buyer", skills, tools) != "commerce"

    sales = _chat("ANUM, how were Amazon sales today?")[-1]
    assert sales["state"] == "results" and sales["category"] == "commerce" and sales["tool"] == "commerce.sales_summary"
    assert sales["summary"].startswith("Amazon sales today: 2 orders")

    orders = _chat("show unshipped amazon orders")[-1]
    assert orders["state"] == "results" and orders["tool"] == "commerce.list_orders"
    assert orders["items"] and all(item["kind"] == "order" and item["title"] for item in orders["items"])
    assert orders["total"] >= len(orders["items"])


def test_a_price_change_asked_in_chat_waits_for_approval_on_its_card() -> None:
    created = connect(AMAZON)
    sync(created["id"])
    card = _chat("Set the price of SKU MX-2055516 on Amazon.sa to 445")[-1]
    assert card["state"] == "approval" and card["category"] == "commerce"
    assert card["approval"]["action"] == "commerce.amazon.update_price" and card["approval"]["payload_hash"]
    assert card["approval"]["can_decide"] is True
