"""Regression tests for bilingual sales-intent qualification."""
from anum_api.whatsapp_sales import SalesIntent, qualify_lead


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
