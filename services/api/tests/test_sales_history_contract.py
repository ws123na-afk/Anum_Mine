"""Sales history route and scoped-read contract."""
from fastapi.routing import APIRoute
from anum_api.sales_api import router
from anum_api.db.sales_repository import SalesCRMStore

def test_sales_history_endpoint_registered():
    matches=[r for r in router.routes if isinstance(r,APIRoute)
             and r.path=="/api/v1/sales/leads/{lead_id}/history"]
    assert len(matches)==1
    assert "GET" in matches[0].methods

def test_sales_store_history_contract():
    assert callable(SalesCRMStore.lead_history)
