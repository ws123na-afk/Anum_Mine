"""Local Gulf mobile numbers follow the workspace's country, and opt-outs are never missed for the
country code (docs/privacy.md#contact-suppression, docs/contacts.md#identifiers)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.business.books_models import BookSettings
from anum_api.business.books_store import books_memory_store
from anum_api.contacts.store import memory_data
from anum_api.schemas import TenantContext, utc_now
from anum_api.suppression import (
    mask,
    normalize,
    opt_out_readings,
    suppressed_for_call,
    suppression_store,
    workspace_phone_country,
)

TENANT, WORKSPACE = "tenant_phone_country", "workspace_phone_country"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_pc", "x-user-roles": "owner"}
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_pc", roles=["owner"])
SUPPRESSIONS = "/api/v1/contact-suppressions"
CONTACTS = "/api/v1/contacts"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    for data in (suppression_store, memory_data):
        data.clear()
    books_memory_store.clear()
    yield
    for data in (suppression_store, memory_data):
        data.clear()
    books_memory_store.clear()


def set_country(country: str | None) -> None:
    now = utc_now()
    scoped = books_memory_store.scoped(CONTEXT)
    try:
        current = scoped.get_settings()
        version = current.version if current is not None else None
        assert scoped.save_settings(BookSettings(id="books_pc", tenant_id=TENANT, workspace_id=WORKSPACE, created_at=now, base_currency="SAR",
                                                 country=country, version=(version or 0) + 1, updated_by="owner_pc", updated_at=now),
                                    expected_version=version)
    finally:
        scoped.release_books()


def test_a_local_mobile_number_takes_the_country_given_and_saudi_when_none() -> None:
    assert normalize("055 123 4567") == ("phone", "+966551234567")
    assert normalize("0551234567", "SA") == ("phone", "+966551234567")
    assert normalize("0551234567", "ae") == ("phone", "+971551234567")
    assert normalize("0551234567", "EG") == ("phone", "+0551234567")  # no known code: the digits as typed
    # A number with its country code is never changed by the country.
    assert normalize("+971 55 123 4567", "SA") == ("phone", "+971551234567")
    assert normalize("00966551234567", "AE") == ("phone", "+966551234567")


def test_the_workspace_country_comes_from_books_settings() -> None:
    assert workspace_phone_country(CONTEXT) == "SA"
    set_country("AE")
    assert workspace_phone_country(CONTEXT) == "AE"
    set_country(None)
    assert workspace_phone_country(CONTEXT) == "SA"


def test_a_saudi_opt_out_typed_locally_is_stored_as_saudi_and_blocks_the_saudi_number() -> None:
    added = client.post(SUPPRESSIONS, headers=OWNER, json={"recipient": "055 123 4567", "reason": "Asked by phone"})
    assert added.status_code == 201 and added.json()["masked"] == mask("phone", "+966551234567")
    assert suppressed_for_call(CONTEXT, "send_whatsapp", {"to": "+966551234567", "text": "Hi"}, None)
    assert suppressed_for_call(CONTEXT, "send_whatsapp", {"to": "0551234567", "text": "Hi"}, None)


def test_an_old_opt_out_stored_as_uae_still_blocks_the_saudi_number() -> None:
    # Before November 2026 every typed 05... number was stored as UAE (+971).
    set_country("AE")
    assert client.post(SUPPRESSIONS, headers=OWNER, json={"recipient": "0551234567", "reason": "Asked by phone"}).status_code == 201
    set_country(None)
    assert suppressed_for_call(CONTEXT, "send_whatsapp", {"to": "+966551234567", "text": "Hi"}, None)
    assert suppressed_for_call(CONTEXT, "send_whatsapp", {"to": "0551234567", "text": "Hi"}, None)
    # Search finds it under either reading.
    assert client.get(SUPPRESSIONS, headers=OWNER, params={"q": "+966551234567"}).json()["total"] == 1
    # Another number is not blocked.
    assert not suppressed_for_call(CONTEXT, "send_whatsapp", {"to": "+966551234568", "text": "Hi"}, None)


def test_only_gulf_mobile_numbers_have_a_second_reading() -> None:
    assert opt_out_readings("+966551234567") == ["+966551234567", "+971551234567"]
    assert opt_out_readings("+971551234567") == ["+966551234567", "+971551234567"]
    assert opt_out_readings("+97145550000") == ["+97145550000"]  # a Dubai landline
    assert opt_out_readings("+447700900123") == ["+447700900123"]


def test_contacts_follow_the_workspace_country_and_never_merge_across_it() -> None:
    uae = client.post(CONTACTS, headers=OWNER, json={"name": "Sara Ahmed", "phones": ["+971 50 123 4567"]})
    assert uae.status_code == 201
    saudi = client.post(CONTACTS, headers=OWNER, json={"name": "Omar Saleh", "phones": ["050 123 4567"]})
    assert saudi.status_code == 201 and [p["value"] for p in saudi.json()["person"]["phones"]] == ["+966501234567"]
    assert client.get(f"{CONTACTS}/proposals", headers=OWNER).json()["total"] == 0

    set_country("AE")
    same = client.post(CONTACTS, headers=OWNER, json={"name": "Someone Else", "phones": ["050 123 4567"]})
    assert [p["value"] for p in same.json()["person"]["phones"]] == ["+971501234567"]
    assert client.get(f"{CONTACTS}/proposals", headers=OWNER).json()["total"] == 1
