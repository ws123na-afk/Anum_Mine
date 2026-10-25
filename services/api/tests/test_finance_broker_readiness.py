"""Broker readiness stays informational and cannot activate real money."""
import pytest
from pydantic import ValidationError

from anum_api.finance.broker_readiness import (
    BrokerReadiness,
    IntegrationStage,
    evaluate_broker_readiness,
)


def test_blank_broker_is_not_connected_and_cannot_take_deposits():
    result = evaluate_broker_readiness(BrokerReadiness())
    assert result.stage is IntegrationStage.NOT_CONNECTED
    assert "regulated_provider_verified" in result.missing_data_requirements
    assert result.live_trading_enabled is False
    assert result.deposits_into_anum_supported is False


def test_verified_read_only_provider_is_not_live():
    facts = BrokerReadiness(
        regulated_provider_verified=True,
        account_in_owner_name=True,
        funding_only_at_broker=True,
        market_data_terms_accepted=True,
        data_freshness_verified=True,
        read_only_access_verified=True,
    )
    result = evaluate_broker_readiness(facts)
    assert result.stage is IntegrationStage.READ_ONLY
    assert "historical_data_tests_passed" in result.missing_research_requirements
    assert result.live_trading_enabled is False


def test_even_complete_operator_checklist_cannot_enable_live_orders():
    complete = {field: True for field in BrokerReadiness.model_fields}
    result = evaluate_broker_readiness(BrokerReadiness(**complete))
    assert result.stage is IntegrationStage.LIVE_BLOCKED
    assert not result.missing_data_requirements
    assert not result.missing_research_requirements
    assert not result.missing_live_review_requirements
    assert result.live_trading_enabled is False
    assert result.deposits_into_anum_supported is False


def test_unverified_broker_cannot_be_called_connected():
    with pytest.raises(ValidationError):
        BrokerReadiness(read_only_access_verified=True)


def test_unknown_fields_rejected_including_potential_secrets():
    with pytest.raises(ValidationError):
        BrokerReadiness(api_secret="must-not-be-accepted")
