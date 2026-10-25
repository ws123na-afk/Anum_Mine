"""Tests for sales lead lifecycle, quoting, workspace boundaries and review gates."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from anum_api.marketing_sales_orchestration import plan_inbound_handoff
from anum_api.sales_lead_lifecycle import (
    LeadStatus, create_lead, draft_quote, edit_lead, propose_follow_up,
    sales_overview, transition_lead,
)


NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def lead_factory(*, workspace="workspace-one", message="wamid-1"):
    handoff = plan_inbound_handoff(
        tenant_id="tenant-one", workspace_id=workspace,
        conversation_id="conversation-one", message_id=message,
        inbound_text="أريد موقع للشركة ANUM-CAMPAIGN:riyadh-websites",
    )
    return create_lead(handoff, lead_id=f"lead-{message}", contact_id="contact-one", now=NOW)[0]


def test_bilingual_campaign_lead_creation():
    lead = lead_factory()
    assert lead.preferred_language == "ar"
    assert lead.source_campaign_hint == "riyadh-websites"
    assert lead.status == LeadStatus.NEW
    assert not lead.marketing_consent


def test_full_qualified_quoted_won_lifecycle():
    lead = lead_factory()
    history = []
    for status in (LeadStatus.QUALIFYING, LeadStatus.QUALIFIED, LeadStatus.QUOTED, LeadStatus.WON):
        lead, event = transition_lead(
            lead, tenant_id="tenant-one", workspace_id="workspace-one",
            actor_id="owner", expected_revision=lead.revision, to_status=status, now=NOW,
        )
        history.append(event)
    assert lead.revision == 5
    assert lead.status == LeadStatus.WON
    assert [event.to_status for event in history][-1] == LeadStatus.WON


def test_reject_invalid_transition_and_revision_conflict():
    lead = lead_factory()
    with pytest.raises(ValueError, match="Invalid"):
        transition_lead(lead, tenant_id="tenant-one", workspace_id="workspace-one",
                        actor_id="owner", expected_revision=1, to_status=LeadStatus.WON, now=NOW)
    with pytest.raises(ValueError, match="Revision"):
        transition_lead(lead, tenant_id="tenant-one", workspace_id="workspace-one",
                        actor_id="owner", expected_revision=9, to_status=LeadStatus.QUALIFYING, now=NOW)


def test_cross_workspace_mutations_denied():
    lead = lead_factory()
    with pytest.raises(PermissionError):
        edit_lead(lead, tenant_id="tenant-one", workspace_id="wrong",
                  actor_id="owner", expected_revision=1, now=NOW, company_name="A")
    with pytest.raises(PermissionError):
        draft_quote(lead, tenant_id="tenant-one", workspace_id="wrong",
                    line_items=[("Website", "1500")], terms="subject to review")


def test_edit_validation_and_assignment():
    lead = lead_factory()
    changed, event = edit_lead(
        lead, tenant_id="tenant-one", workspace_id="workspace-one",
        actor_id="owner", expected_revision=1, now=NOW,
        company_name="Riyadh Contracting", phone_e164="+966507676416",
        assigned_to="sales-agent", budget_min="1000", budget_max="3000",
    )
    assert changed.assigned_to == "sales-agent"
    assert changed.budget_min == Decimal("1000")
    assert event.event == "lead.edited"
    with pytest.raises(ValueError):
        edit_lead(lead, tenant_id="tenant-one", workspace_id="workspace-one",
                  actor_id="owner", expected_revision=1, now=NOW,
                  budget_min="5000", budget_max="3000")


def test_quote_is_only_draft_with_explicit_items():
    lead = lead_factory()
    quote = draft_quote(lead, tenant_id="tenant-one", workspace_id="workspace-one",
                        line_items=[("Company website", "1800.00"),
                                    ("Arabic translation", "250.00")],
                        terms="Requires owner approval")
    assert quote.total == Decimal("2050.00")
    assert quote.approval_required
    with pytest.raises(ValueError):
        draft_quote(lead, tenant_id="tenant-one", workspace_id="workspace-one",
                    line_items=[("Bad price", "NaN")], terms="")


def test_follow_up_is_proposed_not_sent_and_no_past_dates():
    lead = lead_factory()
    follow_up = propose_follow_up(
        lead, tenant_id="tenant-one", workspace_id="workspace-one",
        due_at=NOW + timedelta(days=1), now=NOW, channel="whatsapp",
        purpose="Ask about website requirements",
    )
    assert follow_up.approval_required
    assert follow_up.status.value == "proposed"
    with pytest.raises(ValueError):
        propose_follow_up(lead, tenant_id="tenant-one", workspace_id="workspace-one",
                          due_at=NOW - timedelta(days=1), now=NOW,
                          channel="whatsapp", purpose="Late")


def test_scoped_overview_excludes_other_workspace():
    lead = replace(lead_factory(), status=LeadStatus.WON)
    outside = replace(lead_factory(workspace="workspace-two", message="wamid-2"), status=LeadStatus.WON)
    result = sales_overview(
        [lead, outside], tenant_id="tenant-one", workspace_id="workspace-one",
        won_quote_totals={lead.id: Decimal("5000"), outside.id: Decimal("100000")},
    )
    assert result.leads == 1
    assert result.won == 1
    assert result.revenue_estimate == Decimal("5000")
