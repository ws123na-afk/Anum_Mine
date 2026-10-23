"""Contract tests: authenticated broker API remains non-executing and stateless."""
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from anum_api.main import app
from anum_api.finance.broker_api import router
from anum_api.finance.broker_reconciliation import BrokerSnapshot

STAMP = datetime(2026, 10, 23, 12, tzinfo=timezone.utc).isoformat()
HEADERS = "row_type,broker_id,account_id,as_of,source_reference,currency,symbol,side,quantity,last_price,settled,available\n"
CASH = f"cash,test,owner-account,{STAMP},record,USD,,,,,100,100\n"


def test_broker_routes_are_all_registered_and_have_no_get_or_delete():
    paths = {route.path: set(route.methods) for route in router.routes}
    assert paths == {
        "/api/v1/finance/broker/statement-preview": {"POST"},
        "/api/v1/finance/broker/reconcile": {"POST"},
        "/api/v1/finance/broker/readiness": {"POST"},
        "/api/v1/finance/broker/analyze": {"POST"},
        "/api/v1/finance/broker/history": {"POST"},
        "/api/v1/finance/broker/paper/simulate": {"POST"},
        "/api/v1/finance/broker/live/preflight": {"POST"},
    }
    spec = app.openapi()
    for path in paths:
        assert "post" in spec["paths"][path]
        assert "get" not in spec["paths"][path]


def test_broker_schema_has_no_credentials_or_execution_fields():
    schema = BrokerSnapshot.model_json_schema()
    forbidden = {"password", "api_key", "secret", "transfer", "withdraw", "place_order"}
    assert not (forbidden & {k.lower() for k in schema["properties"]})
    for path in router.routes:
        assert "order" not in path.path and "withdraw" not in path.path


def test_anonymous_broker_routes_are_denied():
    with TestClient(app) as client:
        for path, payload in (
            ("/api/v1/finance/broker/statement-preview", {"statement": HEADERS + CASH}),
            ("/api/v1/finance/broker/reconcile", {}),
            ("/api/v1/finance/broker/readiness", {"facts": {}}),
        ):
            response = client.post(path, json=payload)
            assert response.status_code in (401, 403, 422)
