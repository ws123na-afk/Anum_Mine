"""Accountant review (docs/business-books.md#accountant-review): one list of what waits for an owner or an accountant.
In-memory stores; vendors, bills and amounts are SYNTHETIC.
"""

from __future__ import annotations

from test_business_books import (  # noqa: F401  (``clean`` is the autouse fixture that resets every store)
    API,
    MEMBER,
    OTHER_OWNER,
    OWNER,
    VIEWER,
    bill,
    clean,
    client,
    setup_books,
    today,
    vendor,
)

from anum_api.business import books_review as RV


def review(headers: dict = OWNER, **params: object):
    return client.get(f"{API}/review", headers=headers, params=params)


def draft(headers: dict = OWNER) -> dict:
    template = client.post(f"{API}/recurring", headers=headers, json={"vendor_id": vendor()["id"], "description": "Synthetic rent", "currency": "SAR",
                                                                      "subtotal": "100", "tax": "0", "category": "rent", "frequency": "monthly",
                                                                      "start_date": today().isoformat(), "create_as": "draft"})
    assert template.status_code == 201, template.text
    return template.json()["bills"][0]


def test_a_draft_bill_waits_for_a_checker_and_its_maker_cannot_approve_it() -> None:
    setup_books()
    made = draft()
    data = review().json()
    item = next(i for i in data["items"] if i["kind"] == "draft_bill")
    assert (item["ref_id"], item["action"], item["severity"], item["currency"]) == (made["id"], "approve", "blocking", "SAR")
    # The owner made the recurring bill, so someone else approves: the list says so instead of offering a dead button.
    assert (item["can_act"], item["why_not"]) == (False, "own_draft")
    assert data["counts"]["draft_bill"] == 1 and data["blocking"] >= 1
    # A member an owner named accountant sees it and may approve it.
    assert client.post(f"{API}/accountants", headers=OWNER, json={"user_id": "member_books"}).status_code == 201
    seen = next(i for i in review(MEMBER).json()["items"] if i["kind"] == "draft_bill")
    assert seen["can_act"] is True
    assert client.post(f"{API}/bills/{made['id']}/approve", headers=MEMBER, json={"version": made["version"]}).status_code == 200
    assert "draft_bill" not in review().json()["counts"]


def test_a_member_who_is_not_an_accountant_and_a_viewer_are_refused() -> None:
    setup_books()
    draft()
    refused = review(MEMBER)
    assert refused.status_code == 403 and "accountant" in refused.json()["detail"]
    assert review(VIEWER).status_code == 403
    # Another business sees none of this one's drafts.
    setup_books(OTHER_OWNER)
    assert review(OTHER_OWNER).json()["counts"] == {}


def test_a_missing_rate_is_an_unconverted_currency_and_the_close_checklist_comes_with_the_list() -> None:
    setup_books()
    bill(vendor("Cloud Host", default_category="hosting")["id"], currency="USD", subtotal="100", tax="0", category="hosting")
    data = review().json()
    item = next(i for i in data["items"] if i["kind"] == "unconverted")
    assert (item["currency"], item["params"]["count"], item["section"]) == ("USD", "1", "settings")
    assert not any(i["kind"] == "posting_issue" for i in data["items"])
    months = data["close_months"]
    assert len(months) == 2 and months[1]["status"] == "not_ended" and months[1]["issues"] == 1
    assert months[0]["status"] in ("open", "closed") and months[0]["end"] < months[1]["start"]
    assert review(kind="unconverted").json()["total"] == 1
    assert review(kind="nonsense").status_code == 422


def test_other_posting_issues_and_a_producer_hook() -> None:
    setup_books()

    def vat(ctx: RV.ReviewContext) -> list[RV.ReviewItem]:
        return [RV.ReviewItem(key="vat:2026-q4", producer="vat", kind="external", detected="A VAT return is due.", section="statements")]

    def broken(ctx: RV.ReviewContext) -> list[RV.ReviewItem]:
        raise RuntimeError("synthetic failure")

    RV.register_producer("vat", vat)
    RV.register_producer("broken", broken)
    try:
        data = review().json()
        assert any(i["key"] == "vat:2026-q4" for i in data["items"]) and data["unavailable"] == ["broken"]
    finally:
        RV.unregister_producer("vat")
        RV.unregister_producer("broken")
    issue = RV.ReviewContext(context=None, today=today(), issues=[type("Issue", (), {"reason": "too_small", "event_key": "bill:x", "source_kind": "bill",
                                                                                    "source_id": "x", "event_date": today(), "currency": "SAR",
                                                                                    "description": "Synthetic"})()])  # type: ignore[arg-type]
    assert [i.kind for i in RV.posting_issues(issue)] == ["posting_issue"]
