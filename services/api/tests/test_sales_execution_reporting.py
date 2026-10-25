from datetime import datetime, timedelta, timezone

from anum_api.sales_execution_policy import (
    DeliveryChannel, SalesExecutionPolicy, can_execute_sales_action,
)
from anum_api.sales_api import router
from fastapi.routing import APIRoute

NOW=datetime(2026,10,23,12,tzinfo=timezone.utc)
ENABLED=SalesExecutionPolicy(enabled=True,paused=False,max_actions_per_day=10)


def decision(**overrides):
    data=dict(policy=ENABLED,owner_approved=True,has_consent=True,
              suppressed=False,recent_customer_message_at=NOW-timedelta(hours=1),
              now=NOW,channel=DeliveryChannel.WHATSAPP,action_count_today=0)
    data.update(overrides)
    return can_execute_sales_action(**data)


def test_policy_defaults_fail_closed():
    assert not decision(policy=SalesExecutionPolicy()).permitted


def test_approval_suppression_and_consent_are_mandatory():
    assert not decision(owner_approved=False).permitted
    assert not decision(suppressed=True).permitted
    assert not decision(has_consent=False).permitted


def test_message_window_and_daily_limit():
    assert decision().permitted
    assert not decision(recent_customer_message_at=NOW-timedelta(hours=25)).permitted
    assert not decision(recent_customer_message_at=None).permitted
    assert not decision(action_count_today=10).permitted


def test_no_automated_calls():
    assert not decision(channel=DeliveryChannel.CALL).permitted


def test_reporting_endpoints_registered():
    paths={r.path for r in router.routes if isinstance(r,APIRoute)}
    assert "/api/v1/sales/campaign-conversions" in paths
    assert "/api/v1/sales/operations" in paths
