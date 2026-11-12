"""Courier tracking for Purchases (docs/purchases.md#courier-tracking), in-memory stores.

Tracking numbers here are made up to fit each carrier's format and check digit; none is a
real shipment.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.dependencies import memory_repository
from anum_api.purchases import carriers as C
from anum_api.purchases.store import purchase_memory_store
from anum_api.schemas import WorkspaceMembership, utc_now
from anum_api.settings import settings

TENANT, WORKSPACE = "tenant_trk", "ws_trk"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_trk", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_trk", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_trk", "x-user-roles": "viewer"}
API = "/api/v1/purchases"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    purchase_memory_store.clear()
    memberships = dict(memory_repository.store.memberships)
    now = utc_now()
    for user, role in (("owner_trk", "owner"), ("member_trk", "member"), ("viewer_trk", "viewer")):
        memory_repository.store.memberships[(TENANT, WORKSPACE, user)] = WorkspaceMembership(
            tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, role=role, active=True, created_at=now, updated_at=now)
    yield
    memory_repository.store.memberships.clear()
    memory_repository.store.memberships.update(memberships)
    purchase_memory_store.clear()
    C.PROVIDERS.pop("fake", None)


def _shipment(headers: dict = OWNER, ownership: str = "business", **body: str) -> tuple[dict, dict]:
    created = client.post(API, headers=headers, json={"merchant": "Example shop", "ordered_on": utc_now().date().isoformat(), "currency": "AED",
                                                      "total": "100", "ownership": ownership})
    assert created.status_code in (200, 201), created.text
    shipment = client.post(f"{API}/{created.json()['id']}/shipments", headers=headers, json=body)
    assert shipment.status_code == 201, shipment.text
    return created.json(), shipment.json()


# ---- detection ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("number", "carrier"), [
    ("1Z999AA10123456784", "ups"),
    ("1z 999 aa1 0123 4567 84", "ups"),
    ("RR123456785AE", "emirates_post"),
    ("RR123456785SA", "saudi_post"),
    ("JJD0099999999", "dhl"),
    ("123456789012345", "fedex"),
])
def test_a_number_with_one_clear_format_names_its_carrier(number: str, carrier: str) -> None:
    found = C.detect(number, None)
    assert found.carrier == carrier and found.detected_from == "number"
    assert found.url is not None and found.url.startswith("https://") and found.number in found.url


def test_an_ambiguous_number_lists_every_candidate_and_picks_none() -> None:
    # Ten digits that pass DHL Express's check are also an Aramex format.
    found = C.detect("1234567891", None)
    assert found.carrier is None and found.url is None
    assert [(c.carrier, c.confidence) for c in found.candidates] == [("dhl", "likely"), ("aramex", "possible")]
    # Twelve digits: FedEx (check digit), Aramex and SMSA.
    assert {c.carrier for c in C.detect("123456789012", None).candidates} == {"fedex", "aramex", "smsa"}


def test_a_bad_check_digit_or_a_foreign_post_number_is_not_guessed() -> None:
    assert C.detect("RR123456786AE", None).candidates == []
    assert C.detect("RR123456785GB", None).candidates == []
    assert C.detect("abc", None).number is None


def test_the_named_carrier_wins_over_the_number() -> None:
    found = C.detect("1234567891", "Aramex")
    assert found.carrier == "aramex" and found.detected_from == "name"
    assert found.url == "https://www.aramex.com/us/en/track/results?ShipmentNumber=1234567891"
    arabic = C.detect("SM99887766", "سمسا")
    assert arabic.carrier == "smsa" and arabic.url is not None and arabic.url.endswith("SM99887766")
    # A known carrier without a usable number: named, no link.
    assert C.detect(None, "DHL").url is None and C.detect(None, "DHL").carrier == "dhl"


def test_links_never_carry_anything_but_the_cleaned_number() -> None:
    found = C.detect("1Z999AA1012345678<", "UPS")
    assert found.number is None and found.url is None


# ---- the shipment and the tracking route -----------------------------------------------------------


def test_shipments_carry_their_tracking_link() -> None:
    _, shipment = _shipment(carrier="", tracking_number="1Z999AA10123456784")
    assert shipment["tracking"]["carrier"] == "ups" and shipment["tracking"]["status_provider"] == "off"
    assert shipment["tracking"]["url"] == "https://www.ups.com/track?tracknum=1Z999AA10123456784"


def test_tracking_route_without_a_provider_is_links_only() -> None:
    _, shipment = _shipment(tracking_number="RR123456785AE")
    got = client.get(f"{API}/shipments/{shipment['id']}/tracking", headers=MEMBER)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["tracking"]["carrier"] == "emirates_post" and body["status"] is None and body["status_error"] is None


class FakeProvider:
    name = "fake"

    def __init__(self, key: str) -> None:
        assert key == "example-key"
        self.calls: list[tuple[str, str]] = []

    def supports(self, carrier: str) -> bool:
        return carrier in ("ups", "aramex")

    def status(self, carrier: str, number: str) -> C.TrackingStatus:
        if number.endswith("0"):
            raise C.ProviderError("The provider is busy. Try again in a minute.")
        return C.TrackingStatus(state="out_for_delivery", description="With the courier", checked_at=datetime(2026, 11, 12, tzinfo=UTC))


def test_a_configured_provider_answers_the_status(monkeypatch: pytest.MonkeyPatch) -> None:
    C.PROVIDERS["fake"] = FakeProvider
    monkeypatch.setattr(settings, "purchases_tracking_provider", "fake")
    monkeypatch.setattr(settings, "purchases_tracking_api_key", "example-key")
    _, ups = _shipment(tracking_number="1Z999AA10123456784")
    assert ups["tracking"]["status_provider"] == "fake"
    body = client.get(f"{API}/shipments/{ups['id']}/tracking", headers=OWNER).json()
    assert body["status"]["state"] == "out_for_delivery" and body["status_error"] is None
    # A carrier the provider does not cover, and a provider failure, say so plainly.
    _, post = _shipment(tracking_number="RR123456785AE")
    assert "does not cover Emirates Post" in client.get(f"{API}/shipments/{post['id']}/tracking", headers=OWNER).json()["status_error"]
    _, busy = _shipment(carrier="Aramex", tracking_number="1234567890")
    assert client.get(f"{API}/shipments/{busy['id']}/tracking", headers=OWNER).json()["status_error"].startswith("The provider is busy")


def test_a_provider_name_without_a_key_or_registration_stays_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "purchases_tracking_provider", "fake")
    monkeypatch.setattr(settings, "purchases_tracking_api_key", "example-key")
    assert C.configured_provider() is None  # not registered
    C.PROVIDERS["fake"] = FakeProvider
    monkeypatch.setattr(settings, "purchases_tracking_api_key", "")
    assert C.configured_provider() is None  # no key


def test_tracking_follows_the_purchase_rules() -> None:
    _, shipment = _shipment(tracking_number="1Z999AA10123456784")
    assert client.get(f"{API}/shipments/{shipment['id']}/tracking", headers=VIEWER).status_code == 403
    assert client.get(f"{API}/shipments/purs_missing/tracking", headers=OWNER).status_code == 404
    # Someone else's personal purchase answers 404, as the purchase itself does.
    _, personal = _shipment(MEMBER, ownership="personal", tracking_number="RR123456785AE")
    assert client.get(f"{API}/shipments/{personal['id']}/tracking", headers=OWNER).status_code == 404
    assert client.get(f"{API}/shipments/{personal['id']}/tracking", headers=MEMBER).status_code == 200
