"""Sales CRM HTTP route contract and safety regression tests."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.routing import APIRoute
from anum_api.sales_api import (
    router, InboundLeadRequest, QuoteRequest, QuoteLine,
    FollowUpRequest, LeadEditRequest,
)


def test_complete_crm_route_catalog():
    routes = {(route.path, method) for route in router.routes if isinstance(route, APIRoute) for method in route.methods}
    for name, method in (
        ("/api/v1/sales/leads", "GET"),
        ("/api/v1/sales/leads/inbound", "POST"),
        ("/api/v1/sales/leads/{lead_id}", "GET"),
        ("/api/v1/sales/leads/{lead_id}", "PATCH"),
        ("/api/v1/sales/leads/{lead_id}/transition", "POST"),
        ("/api/v1/sales/leads/{lead_id}/quotes", "POST"),
        ("/api/v1/sales/leads/{lead_id}/followups", "POST"),
        ("/api/v1/sales/overview", "GET"),
    ):
        assert (name, method) in routes


def test_inbound_does_not_claim_marketing_consent():
    data = InboundLeadRequest(
        conversation_id="c1", message_id="wamid1", contact_id="customer1",
        message="أحتاج موقع لشركتي"
    )
    assert data.message


def test_quote_requires_known_explicit_price():
    line = QuoteLine(description="Arabic and English website", amount=Decimal("1800.00"))
    request = QuoteRequest(items=[line])
    assert request.items[0].amount == Decimal("1800.00")


def test_followup_is_not_immediate_send():
    request = FollowUpRequest(
        due_at=datetime.now(timezone.utc) + timedelta(days=2),
        channel="whatsapp", purpose="Check customer requirements",
    )
    assert request.channel == "whatsapp"


def test_revision_required_for_updates():
    update = LeadEditRequest(expected_revision=4, company_name="Construction company")
    assert update.expected_revision == 4
