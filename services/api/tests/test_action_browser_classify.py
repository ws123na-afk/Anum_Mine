"""Which action-browser steps run, stop for approval, need the owner or are refused (docs/action-browser.md)."""

from __future__ import annotations

from typing import Any

import pytest

from anum_api.action_browser.classify import (
    ALLOW, GATE, HANDOFF, REFUSE, binding_words, classify, detect_amounts, handoff_reason, pick_amount,
)

SITES = ["shop.example"]
LOGINS = {"shop": "shop.example"}


def step(action: dict[str, Any], *, target: dict[str, Any] | None = None, text: str = "", frames: list[str] | None = None,
         fields: list[dict[str, Any]] | None = None, host: str = "shop.example", challenge: bool = False) -> dict[str, Any]:
    return {
        "action": action,
        "page": {"url": f"https://{host}/p", "host": host, "path": "/p", "text": text, "frames": frames or [], "fields": fields or [], "challenge": challenge},
        "target": target if target is not None else {},
    }


def decide(s: dict[str, Any], **kw: Any) -> Any:
    return classify(s, sites=SITES, scope_logins=kw.get("logins", LOGINS), scope_cards=kw.get("cards", {"main"}), card_grants=kw.get("grants"))


def button(text: str, **extra: Any) -> dict[str, Any]:
    return {"tag": "button", "type": "button", "text": text, **extra}


@pytest.mark.parametrize("text", [
    "Pay AED 1,240", "Buy now", "Book flight", "Confirm booking", "Place order", "Place my order", "Send", "Submit", "Sign contract",
    "Delete account", "Remove item", "Transfer funds", "Subscribe", "Complete purchase", "Cancel my subscription", "Donate", "Sign up",
])
def test_english_binding_buttons_stop_for_approval(text: str) -> None:
    decision = decide(step({"type": "click", "ref": "e1"}, target=button(text)))
    assert decision.kind == GATE and decision.code == "binding_button", text


@pytest.mark.parametrize("text", [
    "ادفع الآن", "اشتر الآن", "احجز", "تأكيد الحجز", "إتمام الطلب", "أرسل", "إرسال", "وقع العقد", "احذف الحساب", "تحويل الأموال", "اشترك", "الدفع ٥٠٠ درهم",
])
def test_arabic_binding_buttons_stop_for_approval(text: str) -> None:
    decision = decide(step({"type": "click", "ref": "e1"}, target=button(text)))
    assert decision.kind == GATE, text


@pytest.mark.parametrize("text", ["Search flights", "Next", "Continue", "Select", "Sign in", "Log in", "Show details", "التالي", "بحث", "حول الموقع", "تسجيل الدخول"])
def test_ordinary_buttons_run(text: str) -> None:
    assert decide(step({"type": "click", "ref": "e1"}, target=button(text))).kind == ALLOW, text


def test_amounts_in_english_and_arabic() -> None:
    found = detect_amounts("Total AED 1,240.50 or US$ 340 and ٣٬٤٥٠ ر.س then 99 درهم and €12 and SAR1200")
    pairs = {(a.currency, a.value) for a in found}
    assert ("AED", 1240.5) in pairs and ("USD", 340.0) in pairs and ("SAR", 3450.0) in pairs and ("AED", 99.0) in pairs
    assert ("EUR", 12.0) in pairs and ("SAR", 1200.0) in pairs
    assert detect_amounts("Seat 12A, gate 4") == []


def test_amount_prefers_the_button_then_the_total_line_then_the_largest() -> None:
    assert pick_amount("Pay AED 1,240", "Subtotal AED 2,000").value == 1240
    picked = pick_amount("Confirm", "Fare AED 1,100\nTaxes AED 140\nTotal AED 1,240\nInsurance AED 3,000")
    assert picked is not None and picked.value == 1240 and picked.currency == "AED"
    assert pick_amount("Confirm", "AED 10 and AED 99").value == 99
    assert pick_amount("Confirm", "المجموع ١٬٢٤٠ درهم").value == 1240


def test_the_gate_carries_the_amount() -> None:
    decision = decide(step({"type": "click", "ref": "e9"}, target=button("Pay AED 1,240"), text="Total AED 1,240"))
    assert decision.amount is not None and decision.amount.as_dict() == {"amount": 1240.0, "currency": "AED", "amount_text": "AED 1,240"}


def test_submit_of_a_form_with_card_fields_or_a_checkout_address_or_a_price_stops() -> None:
    card_form = {"action": "https://shop.example/next", "fields": [{"name": "cc-number", "autocomplete": "cc-number"}], "text": ""}
    plain_form = {"action": "https://shop.example/search", "fields": [{"name": "q", "type": "text"}], "text": "Where to?"}
    checkout = {"action": "https://shop.example/checkout/complete", "fields": [{"name": "note"}], "text": ""}
    priced = {"action": "https://shop.example/next", "fields": [{"name": "note"}], "text": "You pay SAR 300"}
    go = button("Go", type="submit", submits=True)
    assert decide(step({"type": "click", "ref": "e1"}, target={**go, "form": card_form})).code == "payment_form"
    assert decide(step({"type": "click", "ref": "e1"}, target={**go, "form": checkout})).code == "payment_form"
    assert decide(step({"type": "click", "ref": "e1"}, target={**go, "form": priced})).code == "payment_form"
    assert decide(step({"type": "click", "ref": "e1"}, target={**go, "form": plain_form})).kind == ALLOW
    # Enter in a field submits the form too.
    typed = {"tag": "input", "type": "text", "name": "note", "form": checkout}
    assert decide(step({"type": "type", "ref": "e2", "value": "hello", "submit": True}, target=typed)).code == "payment_form"


def test_a_click_while_a_payment_frame_is_shown_stops() -> None:
    decision = decide(step({"type": "click", "ref": "e1"}, target=button("Continue"), frames=["js.stripe.com"]))
    assert decision.kind == GATE and decision.code == "payment_frame"


def test_card_fields_need_approval_even_for_plain_text() -> None:
    field = {"tag": "input", "type": "text", "name": "card_number", "label": "Card number"}
    assert decide(step({"type": "type", "ref": "e3", "value": "hello"}, target=field)).code == "payment_field"
    arabic = {"tag": "input", "type": "text", "label": "رقم البطاقة"}
    assert decide(step({"type": "type", "ref": "e3", "value": "x"}, target=arabic)).code == "payment_field"


def test_saved_card_entry_is_gated_once_per_form() -> None:
    field = {"tag": "input", "type": "text", "name": "cardnumber", "autocomplete": "cc-number", "form": {"key": "0:cardnumber/text"}}
    s = step({"type": "type", "ref": "e3", "value": "{{card:main.number}}"}, target=field, text="Total AED 1,240")
    first = decide(s)
    assert first.kind == GATE and first.code == "card_entry" and first.amount is not None and first.extra["grant"]
    assert decide(s, grants={first.extra["grant"]}).kind == ALLOW
    other_form = step({"type": "type", "ref": "e3", "value": "{{card:main.cvc}}"}, target={**field, "form": {"key": "1:other/text"}})
    assert decide(other_form, grants={first.extra["grant"]}).kind == GATE


def test_refusals() -> None:
    assert decide(step({"type": "click", "ref": "e1"}, target={"tag": "input", "type": "file"})).code == "upload"
    # Downloads run (the file goes to Files, docs/action-browser.md#downloads); one from another site asks first.
    assert decide(step({"type": "click", "ref": "e1"}, target={"tag": "a", "href": "https://shop.example/x.pdf", "download": True, "text": "Statement"})).kind == ALLOW
    assert decide(step({"type": "click", "ref": "e1"}, target={"tag": "a", "href": "https://files.other.example/x.pdf", "download": True, "text": "Statement"})).code == "off_site"
    assert decide(step({"type": "navigate", "url": "http://shop.example/"})).code == "refused_scheme"
    assert decide(step({"type": "navigate", "url": "https://user:pw@shop.example/"})).kind == REFUSE
    field = {"tag": "input", "type": "text", "name": "notes"}
    assert decide(step({"type": "type", "ref": "e1", "value": "4242 4242 4242 4242"}, target=field)).code == "card_number_text"
    assert decide(step({"type": "type", "ref": "e1", "value": "{{card:other.number}}"}, target=field)).code == "handle_out_of_scope"
    assert decide(step({"type": "type", "ref": "e1", "value": "{{login:bank.password}}"}, target=field)).code == "handle_out_of_scope"
    assert decide(step({"type": "type", "ref": "e1", "value": "{{login:shop.password }}"}, target=field)).code == "unknown_handle"
    assert decide(step({"type": "type", "ref": "e1", "value": "hunter2"}, target={"tag": "input", "type": "password"})).code == "password_text"
    assert decide(step({"type": "click", "ref": "e1"}, target={})).code == "no_target"
    assert decide(step({"type": "drag", "ref": "e1"}, target=button("x"))).code == "unknown_action"


def test_a_login_is_typed_only_on_its_own_site() -> None:
    field = {"tag": "input", "type": "password", "name": "password"}
    assert decide(step({"type": "type", "ref": "e1", "value": "{{login:shop.password}}"}, target=field)).kind == ALLOW
    assert decide(step({"type": "type", "ref": "e1", "value": "{{login:shop.password}}"}, target=field, host="accounts.shop.example")).kind == ALLOW
    wrong = classify(step({"type": "type", "ref": "e1", "value": "{{login:shop.password}}"}, target=field, host="evil.example"),
                     sites=[*SITES, "evil.example"], scope_logins=LOGINS, scope_cards=set())
    assert wrong.kind == REFUSE and wrong.code == "login_wrong_site"


def test_leaving_the_approved_sites_needs_approval() -> None:
    assert decide(step({"type": "navigate", "url": "https://shop.example/cart"})).kind == ALLOW
    assert decide(step({"type": "navigate", "url": "https://pay.shop.example/"})).kind == ALLOW
    off = decide(step({"type": "navigate", "url": "https://other.example/x"}))
    assert off.kind == GATE and off.code == "off_site" and off.host == "other.example"
    link = decide(step({"type": "click", "ref": "e1"}, target={"tag": "a", "href": "https://evil.example/a", "text": "More"}))
    assert link.code == "off_site"
    assert decide(step({"type": "navigate", "url": "https://shop.example.evil.com/"})).code == "off_site"


@pytest.mark.parametrize(("page", "code"), [
    ({"frames": ["www.recaptcha.net"]}, "captcha"),
    ({"frames": ["challenges.cloudflare.com"]}, "captcha"),
    ({"text": "Please verify you are human"}, "captcha"),
    ({"text": "تحقق من أنك إنسان"}, "captcha"),
    ({"challenge": True}, "captcha"),
    ({"fields": [{"name": "otp", "autocomplete": "one-time-code"}]}, "one_time_code"),
    ({"fields": [{"label": "Enter the verification code we sent"}]}, "one_time_code"),
    ({"fields": [{"label": "رمز التحقق"}]}, "one_time_code"),
    ({"frames": ["acs.mybank.example"]}, "bank_check"),
    ({"frames": ["centinelapi.cardinalcommerce.com"]}, "bank_check"),
    ({"text": "Verified by Visa: confirm in your app"}, "bank_check"),
])
def test_handoffs(page: dict[str, Any], code: str) -> None:
    full = {"host": "shop.example", "text": "", "frames": [], "fields": [], **page}
    found = handoff_reason(full)
    assert found is not None and found.kind == HANDOFF and found.code == code
    # And a click on such a page hands over instead of acting.
    s = {"action": {"type": "click", "ref": "e1"}, "page": full, "target": button("Next")}
    assert decide(s).kind == HANDOFF


def test_a_page_without_checks_needs_no_one() -> None:
    assert handoff_reason({"host": "shop.example", "text": "Flights to Riyadh", "frames": ["cdn.example"], "fields": [{"name": "q"}]}) is None


def test_typing_into_a_code_field_is_handed_to_the_owner() -> None:
    field = {"tag": "input", "type": "text", "name": "otp_code", "label": "One-time code"}
    assert decide(step({"type": "type", "ref": "e1", "value": "123456"}, target=field)).kind == HANDOFF


def test_reading_scrolling_and_waiting_always_run() -> None:
    for kind in ("read", "scroll", "wait"):
        assert decide(step({"type": kind}, challenge=True)).kind == ALLOW


def test_binding_words_helper() -> None:
    assert binding_words("Proceed to pay") == "pay"
    assert binding_words("Payment options") is None
    assert binding_words("Sign in") is None
    assert binding_words("حول ٥٠٠ درهم") == "حول"
    assert binding_words("حول") is None
