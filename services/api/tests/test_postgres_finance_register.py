"""The register and account overview in PostgreSQL, as the non-owner app role (docs/finance.md#register-and-account-overview).

The joins read journal entries, sources, links and imports through the same RLS-scoped stores as the rest of
Finance: the owner sees their payment with both sources and a linked receipt; a colleague sees nothing.
Messages are SYNTHETIC.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_postgres_finance_links import API, ME, _fuel, client  # noqa: F401  (the fixture is used by name)

from anum_api.finance import links as K

pytestmark = pytest.mark.database
STRANGER = {**ME, "x-user-id": "someone_else", "x-user-roles": "member"}


def test_register_joins_sources_receipts_and_running_balance_under_rls(client: TestClient) -> None:  # noqa: F811
    canonical, card = _fuel(client)
    K.register_target("document", lambda context, kind, target: kind == "vault_document" and target == "vdoc_receipt")
    try:
        made = client.post(f"{API}/links", headers=ME, json={"canonical_id": canonical, "dimension": "document", "role": "evidence",
                                                             "target_type": "vault_document", "target_id": "vdoc_receipt"})
        assert made.status_code == 201, made.text
        page = client.get(f"{API}/register", headers=ME, params={"account_id": card})
        assert page.status_code == 200, page.text
        [row] = page.json()["items"]
        assert row["canonical_id"] == canonical and row["origin"] == "sms" and row["sources"] == ["sms", "wallet"]
        assert row["receipts"] == 1 and row["status"] == "linked" and row["running_balance"] == "-150.00"
        detail = client.get(f"{API}/register/{row['id']}", headers=ME).json()
        assert {s["kind"] for s in detail["sources"]} == {"sms", "wallet"} and detail["receipt_document_ids"] == ["vdoc_receipt"]
        overview = client.get(f"{API}/accounts/overview", headers=ME).json()
        item = next(a for a in overview["accounts"] if a["account_id"] == card)
        assert {"sms", "apple_wallet"} <= set(item["sources"])
        assert client.get(f"{API}/accounts/{card}/activity", headers=ME, params={"days": 7}).json()["days"][-1]["outflow"] == "150.00"
        assert client.get(f"{API}/register", headers=STRANGER).json()["total"] == 0
        assert client.get(f"{API}/register/{row['id']}", headers=STRANGER).status_code == 404
    finally:
        K.unregister_target("document")
