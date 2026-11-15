"""One rule for "today" in Personal Finance: the owner's Finance time zone, read from the one API clock (docs/finance.md#local-day).

Every test runs at fixed UTC moments on both sides of the owner's local midnight, at a month end and at a year end, so the
suite gives the same answer whatever time CI runs. In-memory stores; every record is SYNTHETIC and low entropy.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D

import pytest
import test_finance_debt_overview as DEBTS
from finance_clock import EARLY_MORNING, LATE_EVENING, MOMENTS, MONTH_END, YEAR_END, freeze, owner_today
from test_finance_personal_money import ME, OWNER, clean, client  # noqa: F401  (``clean`` resets the stores)

from anum_api.finance.models import FinanceSettings, InstallmentStatementEntry
from anum_api.finance.store import finance_store
from anum_api.finance.summary import local_day, today_in
from anum_api.finance.summary import owner_today as owner_today_for
from anum_api.finance.tamara_email import MailEvidence, extract_tamara_refunds, extract_tamara_statement
from anum_api.finance.tools import plan_call

API = "/api/v1/finance"
IDS = {LATE_EVENING: "late-evening", EARLY_MORNING: "before-local-midnight", MONTH_END: "month-end", YEAR_END: "year-end"}
DUBAI_DAY = {LATE_EVENING: date(2026, 11, 16), EARLY_MORNING: date(2026, 11, 15), MONTH_END: date(2026, 12, 1), YEAR_END: date(2027, 1, 1)}


@pytest.fixture(params=MOMENTS, ids=lambda m: IDS[m])
def moment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> datetime:
    freeze(monkeypatch, request.param)
    monkeypatch.setattr(DEBTS, "utc_now", lambda: request.param)  # the debt tests pass ``now=utc_now()`` themselves
    return request.param  # type: ignore[no-any-return]


def _zone(name: str) -> None:
    assert client.put(f"{API}/settings", headers=OWNER, json={"base_currency": "AED", "timezone": name, "category_rules": []}).status_code == 200


def test_today_is_the_owners_local_day_never_the_utc_date(moment: datetime) -> None:
    assert today_in(FinanceSettings()) == DUBAI_DAY[moment] == owner_today()
    assert owner_today_for(ME) == DUBAI_DAY[moment]  # the caller's own settings, default zone
    if moment != EARLY_MORNING:
        assert DUBAI_DAY[moment] != moment.date()  # the case CI hit: UTC is still on the day before


def test_the_configured_zone_is_used_not_a_hard_coded_one(moment: datetime) -> None:
    _zone("America/New_York")  # UTC-5 in November and December: still the UTC day at these moments
    assert owner_today_for(ME) == moment.date() == today_in(finance_store.get_settings(ME) or FinanceSettings())
    # A transfer window with no end ends on that local day.
    found = client.get(f"{API}/transfers/candidates", headers=OWNER)
    assert found.status_code == 200 and found.json()["end"] == moment.date().isoformat()


def test_the_transfer_window_ends_on_the_dubai_day(moment: datetime) -> None:
    found = client.get(f"{API}/transfers/candidates", headers=OWNER)
    assert found.status_code == 200 and found.json()["end"] == DUBAI_DAY[moment].isoformat()


def test_a_stored_moment_is_told_as_the_owners_local_day() -> None:
    assert local_day(LATE_EVENING, "Asia/Dubai") == date(2026, 11, 16)
    assert local_day(LATE_EVENING.replace(tzinfo=None), "Asia/Dubai") == date(2026, 11, 16)  # naive = UTC
    assert local_day(YEAR_END, "Asia/Riyadh") == date(2027, 1, 1)
    assert local_day(date(2026, 11, 15), "Asia/Dubai") == date(2026, 11, 15)


# -- the two checks CI failed late in the UTC evening, at every moment -------------------------------------------------


def test_installment_alerts_follow_the_local_day(moment: datetime) -> None:
    DEBTS.test_alerts_never_call_a_provider_report_paid_and_tell_each_item_once()


def test_todays_tamara_installment_is_found_on_the_local_day(moment: datetime) -> None:
    DEBTS.test_todays_tamara_installment_is_confirmed_only_by_a_matched_payment()


def test_todays_installment_in_another_zone_is_due_on_that_zones_day(moment: datetime) -> None:
    _zone("America/New_York")
    planning = finance_store.get_planning(ME)
    planning.installment_entries = [InstallmentStatementEntry(provider="Tamara", order_id="order-example", merchant="Example Store", due_on=moment.date(),
                                                              installment=1, total_installments=2, amount=D(125), currency="SAR", source_status="paid")]
    finance_store.save_planning(ME, planning)
    found = client.get(f"{API}/advisor/answer", headers=OWNER, params={"question": "installment_today"}).json()
    assert found["answer"].startswith("Not confirmed.") and found["as_of"] == moment.date().isoformat()


# -- Tamara emails and the assistant's words -------------------------------------------------------------------------


def _mail(id: str, subject: str, body: str, when: datetime) -> MailEvidence:
    return MailEvidence(message_id=id, provider_message_id=id, from_address="notifications@tamara.co", subject=subject, text=body, received_at=when)


@pytest.mark.parametrize("when", MOMENTS, ids=lambda m: IDS[m])
def test_a_tamara_paid_today_is_the_day_the_owner_received_it(when: datetime) -> None:
    confirmation = _mail("m1", "Split in 2 order confirmed at Example Store", "Paid today\n125 SAR\nDue on 15.01.2027\n125 SAR\nOrder Number\nexample-order-1", when)
    rows, warnings = extract_tamara_statement([confirmation])
    assert not warnings and rows[0].due_on == rows[0].purchase_on == DUBAI_DAY[when]
    assert extract_tamara_statement([confirmation], "America/New_York")[0][0].due_on == when.date()
    refund = _mail("m2", "Your order status has been updated - refund", "Refunded amount: SAR 125\nOrder Number\nexample-order-1", when)
    assert extract_tamara_refunds([refund])[0].received_on == DUBAI_DAY[when]


def test_last_month_in_the_assistants_words_is_read_on_the_local_day(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze(monkeypatch, YEAR_END)  # 31 Dec in UTC, 1 Jan 2027 in Dubai: "last month" is December
    call = plan_call("how much did I spend last month?")
    assert call is not None and call.arguments.get("month") == "2026-12"
    assert plan_call("how much did I spend last month?", today=YEAR_END.date()).arguments.get("month") == "2026-11"  # type: ignore[union-attr]

