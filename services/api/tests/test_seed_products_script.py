"""scripts/business/seed-products.py: payload building and idempotency, no network.

The payloads are checked against the API's own input models, and the whole seed runs
twice in process against the FastAPI app (in-memory store): the second run writes nothing.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.business.api import OfferInput, ProductInput, ProductUpdate
from anum_api.business.store import business_store

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "business" / "seed-products.py"
OWNER = {"x-tenant-id": "tenant_seed", "x-workspace-id": "workspace_seed", "x-user-id": "owner_seed", "x-user-roles": "owner"}


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("seed_products", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


seed_products = _load_script()
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    business_store.clear()
    yield
    business_store.clear()


def transport(method: str, path: str, body: dict[str, Any] | None) -> Any:
    response = client.request(method, path, json=body, headers=OWNER)
    if response.status_code >= 400:
        raise AssertionError(f"{method} {path}: {response.status_code} {response.text}")
    return response.json() if response.content else None


def test_every_entry_builds_payloads_the_api_accepts() -> None:
    entries = seed_products.load_entries()
    assert {entry["slug"] for entry in entries} == {"anum-os", "tyre-pulse", "voicequote", "anum360-store"}
    for entry in entries:
        product = seed_products.product_payload(entry)
        assert set(product) == set(ProductInput.model_fields)
        ProductInput.model_validate(product)
        assert seed_products.slugify(product["name"]) == entry["slug"]
        assert (ROOT / entry["doc"]).is_file(), entry["doc"]
        offer = seed_products.offer_payload(entry)
        if offer is not None:
            assert set(offer) == set(OfferInput.model_fields)
            OfferInput.model_validate(offer)
            # Prices are the owner's call: nothing is priced until they confirm.
            assert offer["price_type"] == "quote" and offer["price_min"] is None and offer["price_max"] is None
            assert "owner to confirm" in offer["notes"]


def test_target_profile_maps_onto_the_api_fields() -> None:
    from anum_api.business.api_pipeline import TargetProfileInput

    entries = {entry["slug"]: entry for entry in seed_products.load_entries()}
    for entry in entries.values():
        assert "target_profile" not in seed_products.product_payload(entry)  # the product body stays API-only
        target = seed_products.target_payload(entry)
        assert set(target) == set(TargetProfileInput.model_fields)
        TargetProfileInput.model_validate(target)
        assert "-" not in target["industries"] + target["roles"] + target["disqualifiers"] + target["trigger_events"]
    tyre = seed_products.target_payload(entries["tyre-pulse"])
    assert tyre["roles"][0] == "Fleet manager"  # buyer_roles go to roles
    assert tyre["company_sizes"] == ["medium", "large", "enterprise"] and tyre["company_size_note"].startswith("Fleets of about 50 or more")
    assert tyre["trigger_events"] and tyre["value_proposition"]
    assert seed_products.target_payload(entries["anum-os"])["company_sizes"] == ["micro", "small", "medium"]
    voice = seed_products.target_payload(entries["voicequote"])
    assert voice["company_sizes"] == [] and voice["company_size_note"] == "" and voice["disqualifiers"] == []


def test_company_size_words_and_numbers() -> None:
    assert seed_products.company_sizes("20+ vehicles") == ["small", "medium", "large", "enterprise"]
    assert seed_products.company_sizes("about 300 staff") == ["large"]
    assert seed_products.company_sizes("SMEs and enterprise groups") == ["small", "enterprise"]
    assert seed_products.company_sizes("") == []


def test_unknown_product_field_is_refused() -> None:
    entry = {"slug": "x-product", "product": {"kind": "product", "name": "X Product", "industries": ["a"]}}
    with pytest.raises(seed_products.SeedError, match="target_profile"):
        seed_products.product_payload(entry)


def test_a_price_without_a_minimum_is_refused_and_quote_drops_prices() -> None:
    entry = {"slug": "x", "offer": {"price_type": "fixed", "price_min": None}}
    with pytest.raises(seed_products.SeedError, match="quote"):
        seed_products.offer_payload(entry)
    quoted = seed_products.offer_payload({"slug": "x", "offer": {"price_type": "quote", "price_min": 10, "deliverables": [" a  b ", "a b", ""]}})
    assert quoted["price_min"] is None and quoted["deliverables"] == ["a b"]


def test_payload_fields_match_the_update_model() -> None:
    assert set(seed_products.UPDATABLE_PRODUCT_FIELDS) == set(ProductUpdate.model_fields)


def test_dry_run_writes_nothing_and_apply_is_idempotent() -> None:
    entries = seed_products.load_entries()

    plan = seed_products.seed(entries, transport, apply=False)
    assert len(plan.writes) == 11  # four products, four target profiles, three offers (the store has none yet)
    assert transport("GET", "/api/v1/business/products", None)["total"] == 0

    first = seed_products.seed(entries, transport, apply=True)
    assert len(first.writes) == 11
    targets = {item["product_name"]: item for item in transport("GET", "/api/v1/business/pipeline/targets", None)}
    assert targets["Tyre Pulse"]["saved"] and targets["Tyre Pulse"]["roles"][0] == "Fleet manager"
    assert targets["Tyre Pulse"]["company_sizes"] == ["medium", "large", "enterprise"] and targets["Tyre Pulse"]["trigger_events"]
    products = transport("GET", "/api/v1/business/products", None)["items"]
    assert sorted(item["name"] for item in products) == ["ANUM OS", "Anum360 Store", "Tyre Pulse", "VoiceQuote"]
    for item in products:
        detail = transport("GET", f"/api/v1/business/products/{item['id']}", None)
        assert all(offer["status"] == "draft" for offer in detail["offers"])  # never activated by the script

    second = seed_products.seed(entries, transport, apply=True)
    assert second.writes == []


def test_changes_are_patched_and_an_active_offer_gets_a_new_draft() -> None:
    entries = seed_products.load_entries()
    seed_products.seed(entries, transport, apply=True)
    tyre = next(item for item in transport("GET", "/api/v1/business/products", None)["items"] if item["name"] == "Tyre Pulse")
    draft = transport("GET", f"/api/v1/business/products/{tyre['id']}", None)["offers"][0]
    transport("POST", f"/api/v1/business/offers/{draft['id']}/activate", None)

    changed = json.loads(json.dumps(entries))
    target = next(entry for entry in changed if entry["slug"] == "tyre-pulse")
    target["product"]["summary"] = "Changed summary."
    target["offer"]["deliverables"].append("Extra deliverable")
    report = seed_products.seed(changed, transport, apply=True)
    # The summary is also the target profile's value proposition, so that is set again too.
    assert [(step.method, step.path.split("/")[-1]) for step in report.writes] == [("PUT", "target"), ("PATCH", tyre["id"]), ("POST", "versions")]
    detail = transport("GET", f"/api/v1/business/products/{tyre['id']}", None)
    assert detail["summary"] == "Changed summary."
    assert [offer["status"] for offer in detail["offers"]] == ["draft", "active"]
    assert seed_products.seed(changed, transport, apply=True).writes == []


def test_an_existing_product_is_matched_by_alias_slug() -> None:
    transport("POST", "/api/v1/business/products", {"kind": "product", "name": "TyrePulse"})
    report = seed_products.seed(seed_products.load_entries(), transport, apply=False)
    tyre_steps = [step for step in report.steps if step.slug == "tyre-pulse"]
    assert not any(step.action == "create product" for step in tyre_steps)
    assert any(step.method == "PATCH" for step in tyre_steps)


def test_token_is_never_sent_over_plain_http_to_a_remote_host() -> None:
    with pytest.raises(seed_products.SeedError, match="plain HTTP"):
        seed_products.http_transport("http://api.example.com", "example")
    seed_products.http_transport("http://localhost:8000", "example")
    seed_products.http_transport("https://api.example.com", "example")


def test_apply_without_a_token_fails_and_offline_dry_run_prints_payloads(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("ANUM_API_TOKEN", raising=False)
    assert seed_products.main(["--apply"]) == 1
    assert seed_products.main([]) == 0
    out = capsys.readouterr().out
    assert "PUT /pipeline/products/{id}/target" in out and "POST /products" in out
