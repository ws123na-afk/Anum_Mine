"""Regression coverage for ANUM marketing-to-sales agent handoffs."""
import pytest

from anum_api.marketing_sales_orchestration import (
    AgentRole, SalesStage, plan_inbound_handoff, permitted_next_actions,
)
from anum_api.whatsapp_sales import SalesIntent


def make_plan(**overrides):
    arguments = dict(
        tenant_id="tenant-one",
        workspace_id="workspace-one",
        conversation_id="conversation-one",
        message_id="wamid-001",
        inbound_text="مرحبا أريد تصميم موقع لشركتي ANUM-CAMPAIGN:riyadh-websites",
    )
    arguments.update(overrides)
    return plan_inbound_handoff(**arguments)


def test_arabic_website_marketing_handoff():
    result = make_plan()
    assert result.language == "ar"
    assert result.intent == SalesIntent.WEBSITE
    assert result.campaign_hint == "riyadh-websites"
    assert result.stage == SalesStage.NEEDS_DETAILS
    assert result.next_agent == AgentRole.WHATSAPP
    assert not result.customer_opted_in_to_marketing
    assert not result.requires_owner_review
    assert result.suggested_question


def test_human_takeover_blocks_automated_follow_up_plan():
    result = make_plan(inbound_text="عاجل أريد موظف بشكوى")
    assert result.stage == SalesStage.HUMAN_REQUIRED
    assert result.next_agent == AgentRole.SALES
    actions = permitted_next_actions(result)
    assert any(action.operation == "human_takeover" and action.approval_required for action in actions)
    assert not any(action.operation == "propose_follow_up" for action in actions)


def test_no_unapproved_sending_or_quote_submission():
    actions = permitted_next_actions(make_plan())
    assert all(action.operation not in {"send_whatsapp", "publish_campaign", "accept_quote"} for action in actions)
    assert next(action for action in actions if action.agent == AgentRole.QUOTATIONS).approval_required


def test_idempotency_is_scoped_to_tenant_workspace_and_message():
    original = make_plan()
    assert original.idempotency_key == make_plan().idempotency_key
    assert original.idempotency_key != make_plan(tenant_id="another-tenant").idempotency_key
    assert original.idempotency_key != make_plan(workspace_id="another-workspace").idempotency_key
    assert original.idempotency_key != make_plan(message_id="wamid-002").idempotency_key


def test_consent_requires_verified_server_flag():
    assert not make_plan(inbound_text="Please send me marketing updates").customer_opted_in_to_marketing
    assert make_plan(verified_marketing_consent=True).customer_opted_in_to_marketing


@pytest.mark.parametrize("key", ["tenant_id", "workspace_id", "conversation_id", "message_id"])
def test_identifiers_are_required(key):
    with pytest.raises(ValueError):
        make_plan(**{key: ""})


def test_english_app_lead():
    result = make_plan(inbound_text="I need an iPhone app")
    assert result.language == "en"
    assert result.intent == SalesIntent.MOBILE_APP
    assert result.campaign_hint is None
