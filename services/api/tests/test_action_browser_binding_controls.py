"""Common binding controls stop for approval, and sending a form does once the session is signed
in on the site or the page shows an amount (docs/action-browser.md#which-steps-stop-for-approval)."""

from __future__ import annotations

from typing import Any

import pytest

from anum_api.action_browser.classify import ALLOW, GATE, classify, signed_in_grant, signed_in_sites

SITES = ["shop.example"]
PRICED = "Your basket AED 500"


def step(action: dict[str, Any], target: dict[str, Any], *, text: str = "", host: str = "shop.example") -> dict[str, Any]:
    return {"action": action, "page": {"url": f"https://{host}/p", "host": host, "path": "/p", "text": text, "frames": [], "fields": []}, "target": target}


def decide(s: dict[str, Any], *, signed_in: list[str] | None = None) -> Any:
    return classify(s, sites=SITES, scope_logins={"shop": "shop.example"}, scope_cards={"main"}, signed_in=signed_in)


def click(target: dict[str, Any], **kw: Any) -> Any:
    signed_in = kw.pop("signed_in", None)
    return decide(step({"type": "click", "ref": "e1"}, target, **kw), signed_in=signed_in)


@pytest.mark.parametrize("text", [
    "Order", "Checkout", "Check out", "Proceed", "Proceed to checkout", "Yes", "Accept", "Accept offer", "Apply", "Apply now", "Withdraw",
    "Unsubscribe", "Deactivate account", "Upgrade plan", "Place bid", "Bid", "Renew", "Renew subscription", "Confirm",
    "Order with obligation to pay",
])
def test_common_commit_words_stop_for_approval(text: str) -> None:
    for target in ({"tag": "div", "role": "button", "text": text}, {"tag": "button", "type": "button", "text": text}, {"tag": "a", "text": text}):
        decision = click(target)
        assert decision.kind == GATE and decision.code == "binding_button", (text, target["tag"])


@pytest.mark.parametrize("text", ["اطلب الآن", "نعم", "موافق", "أوافق على الشروط", "قبول", "تجديد الاشتراك", "سحب", "ترقية الباقة", "تعطيل الحساب", "مزايدة"])
def test_arabic_commit_words_stop_for_approval(text: str) -> None:
    assert click({"tag": "button", "type": "button", "text": text}).kind == GATE, text


@pytest.mark.parametrize("text", [
    "Order status", "Order history", "Order details", "Order #1042", "Order 77120", "Order no. 5", "Order date", "Track order", "My order", "View order", "Sort order", "Orders", "Accept all",
    "Accept all cookies", "Accept cookies", "Apply filters", "Apply coupon", "Applications", "Yesterday", "Renewable energy", "Bidding guide",
    "Search flights", "Next", "Continue", "Select", "Sign in", "Log in", "Show details", "Add to cart", "تتبع الطلب", "طلباتي", "التالي", "بحث",
])
def test_navigation_and_reading_controls_still_run(text: str) -> None:
    for target in ({"tag": "button", "type": "button", "text": text}, {"tag": "a", "href": "https://shop.example/x", "text": text}):
        assert click(target, text=PRICED, signed_in=["shop.example"]).kind == ALLOW, (text, target["tag"])


MESSAGE_FORM = {"action": "https://shop.example/messages", "method": "post", "fields": [{"name": "message", "type": "textarea"}], "text": "Write to the seller"}
EMAIL_FORM = {"action": "https://shop.example/account/email", "method": "post", "fields": [{"name": "email", "type": "email"}], "text": "Change email"}
SEARCH_GET = {"action": "https://shop.example/search", "method": "get", "fields": [{"name": "q", "type": "text"}], "text": "Where to?"}
SEARCH_POST = {"action": "https://shop.example/find", "method": "post", "fields": [{"name": "keywords", "type": "text"}], "text": ""}
FILTER_GET = {"action": "https://shop.example/list", "method": "get", "fields": [{"name": "min"}, {"name": "max"}, {"name": "brand"}], "text": ""}
LOGIN_FORM = {"action": "https://shop.example/session", "method": "post", "fields": [{"name": "user", "type": "text"}, {"name": "pass", "type": "password"}], "text": "Sign in"}
GO = {"tag": "button", "type": "submit", "submits": True, "text": "Go"}


def test_enter_in_a_message_form_stops_on_a_page_that_shows_an_amount() -> None:
    typed = {"tag": "textarea", "name": "message", "form": MESSAGE_FORM}
    decision = decide(step({"type": "type", "ref": "e2", "value": "hello all", "submit": True}, typed, text=PRICED))
    assert decision.kind == GATE and decision.code == "form_submit" and decision.extra == {"why": "shows_amount"}
    assert decision.amount is not None and decision.amount.value == 500


def test_a_go_button_on_a_settings_form_stops_once_signed_in() -> None:
    decision = click({**GO, "form": EMAIL_FORM}, signed_in=["shop.example"])
    assert decision.kind == GATE and decision.code == "form_submit" and decision.extra == {"why": "signed_in"}
    assert "signed in" in decision.reason
    assert click({**GO, "form": EMAIL_FORM}, text=PRICED).kind == GATE
    # Signed in elsewhere is not signed in here.
    assert click({**GO, "form": EMAIL_FORM}, signed_in=["other.example"]).kind == ALLOW


def test_a_scripted_button_inside_a_form_is_treated_like_its_submit_button() -> None:
    for target in ({"tag": "div", "role": "button", "text": "Go", "form": MESSAGE_FORM}, {"tag": "button", "type": "button", "text": "Go", "form": MESSAGE_FORM}):
        assert click(target, signed_in=["shop.example"]).code == "form_submit"
        assert click(target).kind == ALLOW


def test_next_and_continue_in_a_multi_step_form_still_run_when_signed_in() -> None:
    wizard = {"action": "https://shop.example/passenger", "method": "post", "fields": [{"name": "passenger_name", "type": "text"}], "text": "Who is travelling?"}
    for text in ("Continue", "Next", "التالي", "متابعة"):
        target = {"tag": "button", "type": "submit", "submits": True, "text": text, "form": wizard}
        assert click(target, signed_in=["shop.example"]).kind == ALLOW, text
    assert click({"tag": "button", "type": "submit", "submits": True, "text": "Save changes", "form": wizard}, signed_in=["shop.example"]).code == "form_submit"


def test_without_a_sign_in_or_an_amount_a_plain_form_is_still_sent() -> None:
    assert click({**GO, "form": EMAIL_FORM}).kind == ALLOW
    typed = {"tag": "textarea", "name": "message", "form": MESSAGE_FORM}
    assert decide(step({"type": "type", "ref": "e2", "value": "hello", "submit": True}, typed)).kind == ALLOW


def test_the_sign_in_form_is_sent_without_an_approval_before_signing_in() -> None:
    assert click({**GO, "form": LOGIN_FORM}, text=PRICED).kind == ALLOW


@pytest.mark.parametrize("form", [SEARCH_GET, SEARCH_POST, FILTER_GET])
def test_search_and_filter_forms_keep_working_when_signed_in_and_priced(form: dict[str, Any]) -> None:
    assert click({**GO, "form": form}, text=PRICED, signed_in=["shop.example"]).kind == ALLOW
    field = {"tag": "input", "type": "text", "name": "q", "form": form}
    assert decide(step({"type": "type", "ref": "e2", "value": "tyres", "submit": True}, field, text=PRICED), signed_in=["shop.example"]).kind == ALLOW


def test_typing_the_saved_password_with_enter_signs_in_without_an_approval() -> None:
    field = {"tag": "input", "type": "password", "name": "pass", "form": LOGIN_FORM}
    decision = decide(step({"type": "type", "ref": "e2", "value": "{{login:shop.password}}", "submit": True}, field, text=PRICED), signed_in=["shop.example"])
    assert decision.kind == ALLOW and decision.code == "type_login"


def test_once_signed_in_a_form_that_asks_for_the_password_again_is_not_taken_for_a_sign_in() -> None:
    confirm = {"action": "https://shop.example/account/email", "method": "post",
               "fields": [{"name": "email", "type": "email"}, {"name": "current_password", "type": "password"}], "text": "Change email"}
    update = {"tag": "button", "type": "submit", "submits": True, "text": "Update", "form": confirm}
    assert click(update, signed_in=["shop.example"]).code == "form_submit"
    # Before signing in the same shape is the sign-in form itself.
    assert click(update).kind == ALLOW
    # "Sign in" on the sign-in form still runs after the password was typed.
    assert click({**update, "text": "Sign in", "form": LOGIN_FORM}, signed_in=["shop.example"]).kind == ALLOW


def test_the_signed_in_mark_round_trips_through_the_session_grants() -> None:
    grants = ["main:shop.example:0:cc/text", signed_in_grant("shop.example")]
    assert signed_in_sites(grants) == ["shop.example"]
    assert signed_in_sites([]) == [] and signed_in_sites(None) == []


def test_through_the_tool_a_form_sent_after_signing_in_stops_for_approval() -> None:
    """The session remembers the sign-in (the saved password was typed), so the next form that
    is not a search or a wizard step pauses the run."""
    import asyncio

    import httpx
    from action_browser_fakes import (
        PASSWORD,
        FakeActionService,
        FakeSite,
        ScriptedPlanner,
        button,
        field,
    )

    from anum_api.action_browser import ACT_TOOL
    from anum_api.action_browser.store import memory_action_store
    from anum_api.action_browser.tool import ActionBrowserTool, ActionServiceClient
    from anum_api.action_browser.vault import CredentialWrite, create_credential
    from anum_api.agent_tools import ToolCall, running_in
    from anum_api.schemas import TenantContext

    context = TenantContext(tenant_id="tenant_bind", workspace_id="workspace_bind", user_id="owner_b", roles=["owner"])
    memory_action_store.clear()
    try:
        create_credential(context, CredentialWrite(kind="login", handle="shop", site="https://shop.example/login", username="owner@example.com", password=PASSWORD))
        site = FakeSite()
        site.pages["/passenger"] = {"title": "Profile", "text": "Your contact email", "action": "/account/email", "elements": [
            field("e1", "email", "Email"), button("e2", "Go")]}
        planner = ScriptedPlanner([
            {"action": "type", "ref": "e1", "value": "{{login:shop.username}}"},
            {"action": "type", "ref": "e2", "value": "{{login:shop.password}}"},
            {"action": "click", "ref": "e3", "reason": "Sign in"},
            {"action": "type", "ref": "e1", "value": "new@example.com"},
            {"action": "click", "ref": "e2"},
            {"action": "done", "answer": "Changed."},
        ])
        client = ActionServiceClient("http://browser:8090", "action-browser-token-for-tests-only-example",
                                     client=httpx.AsyncClient(transport=httpx.MockTransport(FakeActionService(site))))
        tool = ActionBrowserTool(problem=None, client=client, planner_factory=lambda _context: planner)
        scope = {"goal": "Update my contact email", "start_url": "https://shop.example/login", "allowed_domains": ["shop.example"],
                 "logins": {"shop": "shop.example"}, "cards": []}

        async def go() -> Any:
            with running_in("task_bind", "run_bind"):
                return await tool(ToolCall(name=ACT_TOOL, arguments=scope), context)

        result = asyncio.run(go())
        assert result.status == "paused" and result.continuation is not None
        pending = result.continuation.arguments
        assert pending["kind"] == "form_submit" and pending["site"] == "shop.example"
    finally:
        memory_action_store.clear()
