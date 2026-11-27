"""Phone calls read local numbers with the workspace's country (docs/calls.md#numbers).

A typed ``05...`` number is a UAE mobile in a workspace whose Books settings say AE and a Saudi
mobile when no country is set; contacts, logged calls, opt-outs and inbound callers all use it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from test_calls import (  # noqa: F401
    MEMBER,
    TENANT,
    WORKSPACE,
    _clean,
    client,
    connect,
    contact,
    gateway,
    hook,
    twilio,
)

from anum_api.business.books_models import BookSettings
from anum_api.business.books_store import books_memory_store
from anum_api.calls.store import memory_data
from anum_api.schemas import TenantContext, utc_now

CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_calls", roles=["owner"])


@pytest.fixture(autouse=True)
def _books() -> Iterator[None]:
    books_memory_store.clear()
    yield
    books_memory_store.clear()


def set_country(country: str | None) -> None:
    now = utc_now()
    scoped = books_memory_store.scoped(CONTEXT)
    try:
        assert scoped.save_settings(BookSettings(id="books_calls", tenant_id=TENANT, workspace_id=WORKSPACE, created_at=now, base_currency="SAR",
                                                 country=country, version=1, updated_by="owner_calls", updated_at=now),
                                    expected_version=None)
    finally:
        scoped.release_books()


def test_a_uae_workspace_reads_a_local_number_as_uae(client: TestClient) -> None:  # noqa: F811
    set_country("AE")
    sara = contact(client, phone="050 123 4567")
    assert sara["phone_normalized"] == "+971501234567"
    # A dialer call logged with the local form finds her.
    logged = client.post("/api/v1/calls/log", headers=MEMBER, json={"kind": "phone", "phone": "0501234567", "direction": "outbound", "outcome": "answered"})
    assert logged.status_code == 201, logged.text
    assert logged.json()["contact_id"] == sara["id"]
    changed = client.patch(f"/api/v1/calls/contacts/{sara['id']}", headers=MEMBER, json={"phone": "055 765 4321"})
    assert changed.json()["phone_normalized"] == "+971557654321"


def test_no_country_set_reads_a_local_number_as_saudi(client: TestClient) -> None:  # noqa: F811
    assert contact(client, phone="050 123 4567")["phone_normalized"] == "+966501234567"
    opt_out = client.post("/api/v1/calls/opt-outs", headers=MEMBER, json={"phone": "0509999999"})
    assert opt_out.status_code == 201, opt_out.text
    assert contact(client, name="Omar", phone="+966509999999")["opted_out"] is True


def test_an_opt_out_stored_under_the_other_gulf_code_is_still_found(client: TestClient) -> None:  # noqa: F811
    # Recorded as UAE (as every typed 05 number was before November 2026); the workspace is now Saudi.
    assert client.post("/api/v1/calls/opt-outs", headers=MEMBER, json={"phone": "+971501234567"}).status_code == 201
    sara = contact(client, phone="050 123 4567")
    assert sara["phone_normalized"] == "+966501234567" and sara["opted_out"] is True


@pytest.mark.parametrize(("country", "caller"), [("AE", "+971501234567"), (None, "+966501234567")])
def test_an_inbound_caller_matches_the_contact_typed_in_the_local_form(client: TestClient, gateway, country: str | None, caller: str) -> None:  # noqa: F811
    set_country(country)
    connect(client, inbound_ai_enabled=False)
    sara = contact(client, phone="050 123 4567")
    hook(client, "voice", {"CallSid": "CAcountry0001", "From": caller, "To": "+97145550100"})
    (entry,) = [item for item in memory_data.calls.values() if item.provider_call_id == "CAcountry0001"]
    assert entry.contact_id == sara["id"] and entry.phone_normalized == caller
