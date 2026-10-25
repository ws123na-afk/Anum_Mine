"""Sales CRM persistence metadata and scope tests (no live DB credentials required)."""
from sqlalchemy import inspect
from anum_api.db.sales_repository import (
    SalesLeadRecord, SalesLeadEventRecord, SalesQuoteRecord,
    SalesFollowUpRecord, SalesCRMStore,
)


def test_all_records_include_workspace_scope():
    for model in (SalesLeadRecord, SalesLeadEventRecord, SalesQuoteRecord, SalesFollowUpRecord):
        columns = {column.name for column in inspect(model).columns}
        assert {"tenant_id", "workspace_id", "id"}.issubset(columns)


def test_inbound_message_idempotency_constraint():
    lead = SalesLeadRecord.__table__
    assert any(
        constraint.name == "uq_sales_leads_inbound_message"
        for constraint in lead.constraints
    )


def test_repository_denies_cross_workspace_write_without_db_access():
    store = SalesCRMStore(None, "tenant-one", "workspace-one")
    try:
        store._check("tenant-one", "workspace-two")
    except PermissionError:
        pass
    else:
        raise AssertionError("Workspace crossing was not rejected")


def test_quote_and_followup_have_scoped_lead_foreign_keys():
    for model in (SalesQuoteRecord, SalesFollowUpRecord, SalesLeadEventRecord):
        constraints = list(model.__table__.foreign_key_constraints)
        assert any(len(key.columns) == 3 and key.referred_table.name == "sales_leads" for key in constraints)
