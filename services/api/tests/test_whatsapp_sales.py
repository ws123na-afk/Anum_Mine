"""Regression tests for bilingual sales-intent qualification."""
from anum_api.whatsapp_sales import SalesIntent, qualify_lead, whatsapp_campaign_link, inbound_campaign_marker, campaign_marker
from urllib.parse import parse_qs, urlparse
import pytest


def test_arabic_website_enquiry():
    result = qualify_lead("السلام عليكم بكم تصميم موقع لشركة مقاولات؟")
    assert result.intent == SalesIntent.WEBSITE
    assert result.language == "ar"
    assert "موقع" in result.follow_up_question
    assert not result.requires_human


def test_arabic_app_enquiry():
    result = qualify_lead("أحتاج تطبيق آيفون وأندرويد للمطعم")
    assert result.intent == SalesIntent.MOBILE_APP
    assert result.language == "ar"


def test_ecommerce_prioritized_over_generic_website():
    result = qualify_lead("Need an ecommerce website for my shop")
    assert result.intent == SalesIntent.ECOMMERCE
    assert result.language == "en"


def test_business_system_enquiry():
    result = qualify_lead("We need ERP inventory system for 50 users")
    assert result.intent == SalesIntent.BUSINESS_SOFTWARE


def test_urgent_human_handoff():
    result = qualify_lead("عاجل أريد موظف عندي شكوى")
    assert result.urgent
    assert result.requires_human
    assert result.language == "ar"


def test_unknown_and_empty_content():
    assert qualify_lead("").intent == SalesIntent.OTHER
    assert qualify_lead("Hello").language == "en"
    assert qualify_lead("Hello").follow_up_question


def test_no_prices_or_false_commitments_in_questions():
    for intent in SalesIntent:
        sample = {
            SalesIntent.WEBSITE: "website",
            SalesIntent.MOBILE_APP: "mobile app",
            SalesIntent.BUSINESS_SOFTWARE: "erp",
            SalesIntent.ECOMMERCE: "online store",
            SalesIntent.OTHER: "hello",
        }[intent]
        response = qualify_lead(sample)
        assert "SAR" not in response.follow_up_question
        assert "confirmed" not in response.follow_up_question


def test_bilingual_campaign_links_prefill_customer_opt_in():
    arabic = whatsapp_campaign_link("riyadh-websites")
    english = whatsapp_campaign_link("riyadh-apps", language="en")
    assert urlparse(arabic).netloc == "wa.me"
    assert urlparse(arabic).path == "/966507676416"
    assert "ANUM-CAMPAIGN:riyadh-websites" in parse_qs(urlparse(arabic).query)["text"][0]
    assert "ANUM-CAMPAIGN:riyadh-apps" in parse_qs(urlparse(english).query)["text"][0]
    assert inbound_campaign_marker(parse_qs(urlparse(arabic).query)["text"][0]) == "riyadh-websites"


def test_campaign_marker_rejects_injection_and_invalid_phone():
    for slug in ("", "UPPER", "spaces here", "hello&text=bad", "x" * 65):
        with pytest.raises(ValueError):
            campaign_marker(slug)
    with pytest.raises(ValueError):
        whatsapp_campaign_link("riyadh-websites", phone="123")


def test_inbound_campaign_marker_does_not_claim_unknown_source():
    assert inbound_campaign_marker("أريد تصميم موقع") is None
    assert inbound_campaign_marker("ANUM-CAMPAIGN:riyadh-apps") == "riyadh-apps"
