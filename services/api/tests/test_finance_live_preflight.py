"""Live eligibility preflight (docs/finance.md#live-eligibility-preflight).

One answer to "what is still missing before live could ever be considered": the
person's checklist, the server's own live preconditions and informational notes.
Whatever is ticked, live trading stays off, no order is sent and nothing is saved
except an audit row with ids only.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from anum_api import main
from anum_api.dependencies import memory_repository
from anum_api.finance.broker_readiness import (
    SA_CRYPTO_SOURCE,
    AssetKind,
    BrokerKind,
    BrokerProfile,
    BrokerReadiness,
    IntegrationStage,
    evaluate_live_preflight,
)
from anum_api.finance.config import finance_config
from anum_api.finance.live import Precondition
from anum_api.finance.store import finance_store

TENANT, WORKSPACE = "tenant_preflight", "workspace_preflight"
MEMBER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "member_preflight", "x-user-roles": "member"}
VIEWER = {**MEMBER, "x-user-id": "viewer_preflight", "x-user-roles": "viewer"}
OTHER_TENANT = {**MEMBER, "x-tenant-id": "tenant_preflight_b", "x-workspace-id": "workspace_preflight_b"}
URL = "/api/v1/finance/broker/readiness"
ALL_TRUE = {field: True for field in BrokerReadiness.model_fields}
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    memory_repository.store.audit_records.clear()
    yield
    finance_store.clear()


def _audits() -> list:
    return [r for r in memory_repository.store.audit_records if r.action == "finance.broker.live_preflight"]


def _system(met: bool) -> list[Precondition]:
    return [Precondition(code="deployment_flag", met=met, message="x"), Precondition(code="live_executor", met=met, message="x")]


def test_sa_crypto_stays_off_even_if_every_item_is_ticked() -> None:
    profile = BrokerProfile(broker=BrokerKind.ALPACA, country_of_residence="sa", assets=[AssetKind.STOCK, AssetKind.CRYPTO])
    result = evaluate_live_preflight(BrokerReadiness(**ALL_TRUE), profile, _system(True))
    assert [n.code for n in result.notes] == ["sa_crypto_not_licensed"]
    assert result.notes[0].source == SA_CRYPTO_SOURCE
    assert result.stage is IntegrationStage.LIVE_BLOCKED
    assert result.live_trading_enabled is False and result.orders_sent == 0
    assert result.eligibility_confirmed is False and result.credentials_connected is False
    assert result.deposits_into_anum_supported is False


def test_notes_only_for_saudi_residents_with_crypto() -> None:
    for country, assets in (("SA", [AssetKind.STOCK]), ("AE", [AssetKind.CRYPTO]), ("AE", [AssetKind.STOCK, AssetKind.CRYPTO])):
        profile = BrokerProfile(broker=BrokerKind.INTERACTIVE_BROKERS, country_of_residence=country, assets=assets)
        assert evaluate_live_preflight(BrokerReadiness(), profile, []).notes == []
    assert evaluate_live_preflight(BrokerReadiness(), None, []).notes == []


def test_empty_checklist_lists_the_new_eligibility_items() -> None:
    result = evaluate_live_preflight(BrokerReadiness(), None, _system(False))
    assert result.stage is IntegrationStage.NOT_CONNECTED
    assert {"broker_country_eligibility_verified", "asset_permissions_verified"} <= set(result.missing_data_requirements)
    assert "paper_testing_passed" in result.missing_research_requirements
    assert "credentials_in_secret_store" in result.missing_live_review_requirements
    assert [p.code for p in result.system_preconditions] == ["deployment_flag", "live_executor"]


@pytest.mark.parametrize("country", ["S", "SAU", "1A", "s-", ""])
def test_profile_rejects_bad_countries(country: str) -> None:
    with pytest.raises(ValidationError):
        BrokerProfile(broker=BrokerKind.ALPACA, country_of_residence=country, assets=[AssetKind.STOCK])


def test_profile_rejects_repeated_or_missing_assets_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        BrokerProfile(broker=BrokerKind.ALPACA, country_of_residence="AE", assets=[AssetKind.STOCK, AssetKind.STOCK])
    with pytest.raises(ValidationError):
        BrokerProfile(broker=BrokerKind.ALPACA, country_of_residence="AE", assets=[])
    with pytest.raises(ValidationError):
        BrokerProfile(broker=BrokerKind.ALPACA, country_of_residence="AE", assets=[AssetKind.STOCK], account_number="1")  # type: ignore[call-arg]


def test_the_answer_cannot_claim_live_even_when_built_by_hand() -> None:
    result = evaluate_live_preflight(BrokerReadiness(), None, [])
    with pytest.raises(ValidationError):
        type(result).model_validate({**result.model_dump(), "live_trading_enabled": True})
    with pytest.raises(ValidationError):
        type(result).model_validate({**result.model_dump(), "orders_sent": 1})


def test_api_returns_the_live_preconditions_and_keeps_live_off() -> None:
    body = {"facts": ALL_TRUE, "profile": {"broker": "alpaca", "country_of_residence": "SA", "assets": ["stock", "crypto"]}}
    before = client.get("/api/v1/finance/execution/live", headers=MEMBER).json()
    response = client.post(URL, json=body, headers=MEMBER)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["live_trading_enabled"] is False and answer["orders_sent"] == 0
    assert answer["eligibility_confirmed"] is False and answer["credentials_connected"] is False
    met = {p["code"]: p["met"] for p in answer["system_preconditions"]}
    assert met["deployment_flag"] is False and met["live_executor"] is False and met["write_connector"] is False
    assert [n["code"] for n in answer["notes"]] == ["sa_crypto_not_licensed"]
    # Read only: nothing about live changed.
    assert client.get("/api/v1/finance/execution/live", headers=MEMBER).json() == before
    assert client.get("/api/v1/finance/execution/intents", headers=MEMBER).json()["total"] == 0


def test_api_keeps_live_off_even_with_the_flag_forced_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(finance_config, "live_enabled", True)
    answer = client.post(URL, json={"facts": ALL_TRUE}, headers=MEMBER).json()
    met = {p["code"]: p["met"] for p in answer["system_preconditions"]}
    assert met["deployment_flag"] is True and met["live_executor"] is False
    assert answer["live_trading_enabled"] is False and answer["orders_sent"] == 0


def test_api_audits_with_ids_only() -> None:
    body = {"facts": {"regulated_provider_verified": True}, "profile": {"broker": "other", "country_of_residence": "AE", "assets": ["stock"]}}
    assert client.post(URL, json=body, headers=MEMBER).status_code == 200
    [record] = _audits()
    assert record.actor == "member_preflight" and record.target == "member_preflight"
    assert record.tenant_id == TENANT and record.workspace_id == WORKSPACE
    assert record.metadata == {}
    assert "AE" not in repr(record) and "stock" not in repr(record)


def test_api_accepts_an_old_style_body_and_an_empty_body() -> None:
    old = client.post(URL, json={"facts": {}}, headers=MEMBER)
    assert old.status_code == 200 and old.json()["stage"] == "not_connected"
    assert client.post(URL, json={}, headers=MEMBER).status_code == 200


def test_api_refuses_viewers_secrets_and_bad_profiles() -> None:
    denied = client.post(URL, json={"facts": {}}, headers=VIEWER)
    assert denied.status_code == 403 and "viewer to member" in denied.json()["error"]["message"]
    assert client.post(URL, json={"facts": {"api_secret": "not_real"}}, headers=MEMBER).status_code == 422
    assert client.post(URL, json={"facts": {}, "api_key": "not_real"}, headers=MEMBER).status_code == 422
    bad = {"facts": {}, "profile": {"broker": "alpaca", "country_of_residence": "Saudi", "assets": ["stock"]}}
    assert client.post(URL, json=bad, headers=MEMBER).status_code == 422
    assert _audits() == []


def test_each_tenant_reads_only_its_own_preconditions() -> None:
    limits = {"max_per_action": [{"currency": "AED", "amount": "100"}], "max_per_day": [{"currency": "AED", "amount": "200"}],
              "max_per_month": [{"currency": "AED", "amount": "1000"}], "currencies": ["AED"], "instruments": ["exmpl"], "cooling_off_hours": 24}
    change = client.post("/api/v1/finance/execution/live/changes", json={"limits": limits}, headers=MEMBER).json()
    approved = client.post(f"/api/v1/finance/execution/intents/{change['id']}/approve", json={"payload_hash": change["payload_hash"]}, headers=MEMBER)
    assert approved.status_code == 200
    mine = {p["code"]: p["met"] for p in client.post(URL, json={}, headers=MEMBER).json()["system_preconditions"]}
    theirs = {p["code"]: p["met"] for p in client.post(URL, json={}, headers=OTHER_TENANT).json()["system_preconditions"]}
    assert mine["limits"] is True and theirs["limits"] is False
