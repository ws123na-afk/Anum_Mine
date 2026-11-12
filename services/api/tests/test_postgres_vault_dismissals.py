"""Dismissed receipt matches in PostgreSQL (migration ``0178_refund_requests_vault``, docs/documents.md#receipt-matches).

As the non-owner app role: a dismissal is stored once per document and target, stays in its workspace, and goes with
its document. Every name is SYNTHETIC.
"""

from __future__ import annotations

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_postgres_control_plane import client, postgres_backend  # noqa: F401 - pytest fixtures
from test_postgres_vault import add, count_as

from anum_api.schemas import TenantContext, new_id, utc_now
from anum_api.vault.models import DismissalRecord
from anum_api.vault.store import open_vault_unit

pytestmark = pytest.mark.database
ME = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="alice", roles=["member"])


def _dismiss(document_id: str, target: str) -> DismissalRecord:
    with open_vault_unit(ME) as unit:
        return unit.store.add_dismissal(ME, DismissalRecord(id=new_id("vdis"), document_id=document_id, kind="payment", target_id=target,
                                                            label="Synthetic pharmacy SAR 86.40", dismissed_by="alice", created_at=utc_now()))


def test_a_dismissal_is_kept_once_stays_in_its_workspace_and_goes_with_its_document(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    doc = add(client, category="receipt")
    first = _dismiss(doc["id"], "fin_jrn_synthetic_1")
    again = _dismiss(doc["id"], "fin_jrn_synthetic_1")
    assert again.id == first.id
    _dismiss(doc["id"], "fin_jrn_synthetic_2")
    with open_vault_unit(ME) as unit:
        assert [d.target_id for d in unit.store.dismissals(ME, doc["id"])] == ["fin_jrn_synthetic_1", "fin_jrn_synthetic_2"]
        assert unit.store.delete_dismissal(ME, doc["id"], "payment", "fin_jrn_synthetic_2") is True
        assert unit.store.delete_dismissal(ME, doc["id"], "payment", "fin_jrn_synthetic_2") is False
    assert count_as(database_engine, "select count(*) from vault_match_dismissals") == 1
    assert count_as(database_engine, "select count(*) from vault_match_dismissals", tenant=TENANT_B, workspace=WORKSPACE_B) == 0
    # Deleting the document for good removes its dismissals with it.
    with open_vault_unit(ME) as unit:
        unit.store.remove_document(ME, doc["id"])
    assert count_as(database_engine, "select count(*) from vault_match_dismissals") == 0
