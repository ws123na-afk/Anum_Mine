"""Failed data capture closed gaps (docs/finance.md#financial-alerts): a statement of which no row could be read is kept as
a refused import and told by ``capture_failed``; a Tamara email that could not be read when Gmail was read for installments,
and the person's own Tamara Gmail mailbox needing a new sign in, are told too. Memory backend; the mail provider is faked;
every record is SYNTHETIC and low entropy.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from finance_clock import owner_today
from test_finance import API, OWNER, account, client
from test_finance import clean as _clean_finance  # noqa: F401  (autouse: resets the finance store)

from anum_api.finance import alerts as A
from anum_api.finance import tamara_email_api as T
from anum_api.finance.models import InstallmentStatementEntry
from anum_api.finance.rules_store import rules_memory_store
from anum_api.finance.store import finance_store
from anum_api.finance.summary import local_day
from anum_api.schemas import TenantContext, utc_now

ME = TenantContext(tenant_id=OWNER["x-tenant-id"], workspace_id=OWNER["x-workspace-id"], user_id=OWNER["x-user-id"], roles=["owner"])
UNREADABLE = "Date,Description,Amount\nnot a date,EXAMPLE SHOP,-10\nalso not,EXAMPLE SHOP,-20\n"


@pytest.fixture(autouse=True)
def _claims() -> Iterator[None]:
    rules_memory_store.clear()
    yield
    rules_memory_store.clear()


def _today() -> date:
    """Today the way the code reads it: the owner's Finance time zone, never the UTC date."""
    return owner_today()


def _data(**parts: Any) -> A.AlertData:
    return A.AlertData(today=_today(), overview=SimpleNamespace(items=[], calendar=[]), cards=[], accounts=[], rows=[], **parts)


def _capture(data: A.AlertData) -> list[A.Alert]:
    return [a for a in A.compose(data) if a.kind == "capture_failed"]


# -- a statement no row of which could be read ------------------------------------------------------------------------


def test_a_statement_with_no_readable_row_is_refused_kept_as_a_refused_import_and_told() -> None:
    acct = account()
    refused = client.post(f"{API}/imports", headers=OWNER, json={"account_id": acct["id"], "content": UNREADABLE, "filename": "nov.csv"})
    assert refused.status_code == 422 and "No row of this file could be read" in refused.text
    assert finance_store.transactions == {}  # nothing of the file is stored
    listed = client.get(f"{API}/imports", headers=OWNER).json()
    assert listed["total"] == 1
    kept = listed["items"][0]
    assert (kept["filename"], kept["imported_count"], kept["error_count"]) == ("nov.csv", 0, 2)
    detail = client.get(f"{API}/imports/{kept['id']}", headers=OWNER).json()
    assert {r["disposition"] for r in detail["rows"]} == {"dropped_by_reader"} and len(detail["rows"]) == 2
    records = finance_store.list_imports(ME, limit=10, offset=0)[0]
    found = _capture(_data(imports=records))
    assert [a.key for a in found] == [f"import:{kept['id']}"]
    assert "No row of the statement" in found[0].body and "import it again" in found[0].body
    # A refused import can be removed from the history like any other; it took nothing with it.
    assert client.delete(f"{API}/imports/{kept['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/imports", headers=OWNER).json()["total"] == 0


def test_an_import_with_some_unreadable_rows_keeps_its_own_wording() -> None:
    acct = account()
    mixed = "Date,Description,Amount\n2026-10-05,EXAMPLE SHOP,-80.25\nnot a date,EXAMPLE SHOP,-1\n"
    made = client.post(f"{API}/imports", headers=OWNER, json={"account_id": acct["id"], "content": mixed})
    assert made.status_code == 201, made.text
    records = finance_store.list_imports(ME, limit=10, offset=0)[0]
    (alert,) = _capture(_data(imports=records))
    assert alert.body.startswith("1 rows of the statement imported")


# -- Tamara emails and the Gmail mailbox ----------------------------------------------------------------------------------


def _mailbox(status: str = "connected", last_sync_status: str | None = None) -> Any:
    from anum_api.email_inbox import AccountStatus, EmailAccount, Visibility, open_email_store
    from anum_api.email_providers import EmailProvider

    now = utc_now()
    with open_email_store(ME) as store:
        store.add_account(EmailAccount(id="acct_gap_tamara", tenant_id=ME.tenant_id, workspace_id=ME.workspace_id, provider=EmailProvider.GMAIL,
                                       address="owner@gmail.example", connected_by=ME.user_id, visibility=Visibility.WORKSPACE,
                                       status=AccountStatus(status), last_sync_status=last_sync_status, created_at=now, updated_at=now))


@pytest.fixture
def mail() -> Iterator[None]:
    from anum_api.email_inbox import email_backend

    email_backend.clear()
    yield
    email_backend.clear()


def _email_entry() -> None:
    planning = finance_store.get_planning(ME)
    planning.installment_entries = [InstallmentStatementEntry(
        provider="Tamara", order_id="order-example", merchant="Example Store", due_on=_today() + timedelta(days=20), installment=2,
        total_installments=3, amount=Decimal(125), currency="AED", source_status="scheduled", source_email="https://mail.example/m1")]
    finance_store.save_planning(ME, planning)


def test_an_unreadable_tamara_email_is_noted_by_the_preview_and_told_once(monkeypatch: pytest.MonkeyPatch, mail: None) -> None:
    from anum_api.email_inbox import EmailMessage, open_email_store

    _mailbox()
    now = utc_now()
    with open_email_store(ME) as store:
        store.upsert_messages([
            EmailMessage(id="acct_gap_tamara_m1", tenant_id=ME.tenant_id, workspace_id=ME.workspace_id, account_id="acct_gap_tamara",
                         provider_message_id="p1", thread_id="t1", subject="Your order confirmed", from_address="notifications@tamara.co",
                         snippet="tamara", received_at=now - timedelta(days=1), is_read=False, created_at=now, updated_at=now),
            EmailMessage(id="acct_gap_tamara_m2", tenant_id=ME.tenant_id, workspace_id=ME.workspace_id, account_id="acct_gap_tamara",
                         provider_message_id="p2", thread_id="t2", subject="Your order confirmed", from_address="notifications@tamara.co",
                         snippet="tamara", received_at=now - timedelta(days=40), is_read=False, created_at=now, updated_at=now),
        ])

    async def body(context: Any, account: Any, message: Any) -> Any:
        return SimpleNamespace(text="Thank you for your order. EXAMPLE TEXT WITHOUT A REFERENCE", truncated=False)

    monkeypatch.setattr(T, "fetch_body", body)
    preview = client.get(f"{API}/installment-email/preview", headers=OWNER, params={"account_id": "acct_gap_tamara"})
    assert preview.status_code == 200, preview.text
    assert any("no identifiable Tamara order reference" in w for w in preview.json()["warnings"])
    # Only the recent email is noted, by its id and the owner's local day it arrived: no body, subject or amount.
    received = local_day(now - timedelta(days=1), "Asia/Dubai")
    noted = [(k[3], k[4], k[5]) for k in rules_memory_store.notice_claims]
    assert noted == [(T.UNREAD_CLAIM, "acct_gap_tamara_m1", received)]
    # A second read notes nothing new.
    client.get(f"{API}/installment-email/preview", headers=OWNER, params={"account_id": "acct_gap_tamara"})
    assert len(rules_memory_store.notice_claims) == 1
    mailboxes, unread = A._tamara_mail(ME, _today())
    assert mailboxes == [] and unread == [("acct_gap_tamara_m1", received)]
    (alert,) = _capture(_data(unread_mail=unread))
    assert alert.key == "tamara:acct_gap_tamara_m1" and "Tamara email" in alert.body and "125" not in alert.body
    # Told once: the alert's own claim stops a second run.
    monkeypatch.setattr(A, "gather", lambda context, now: _data(unread_mail=unread, user_id=ME.user_id))
    moment = datetime.combine(_today(), time(8), tzinfo=UTC)
    first = A.run(ME, now=moment)
    assert first[0] >= 1 and A.run(ME, now=moment) == (0, 0)


@pytest.mark.parametrize(("status", "sync", "told"), [("reauth_required", "error", "needs you to sign in again"), ("error", "error", "could not be synced"),
                                                       ("connected", None, None)])
def test_the_tamara_gmail_mailbox_needing_sign_in_or_failing_sync_is_told(mail: None, status: str, sync: str | None, told: str | None) -> None:
    _mailbox(status, sync)
    assert A._tamara_mail(ME, _today()) == ([], [])  # Tamara emails are not read from Gmail: the mailbox is not this alert's business
    _email_entry()
    mailboxes, _unread = A._tamara_mail(ME, _today())
    found = _capture(_data(mailboxes=mailboxes))
    if told is None:
        assert found == []
        return
    (alert,) = found
    assert alert.key == f"gmail:acct_gap_tamara:{status}" and told in alert.body and alert.on == _today() - timedelta(days=_today().weekday())


def test_another_persons_mailbox_is_never_read_for_my_alert(mail: None) -> None:
    from anum_api.email_inbox import AccountStatus, EmailAccount, Visibility, open_email_store
    from anum_api.email_providers import EmailProvider

    _email_entry()
    now = utc_now()
    with open_email_store(ME) as store:
        store.add_account(EmailAccount(id="acct_gap_other", tenant_id=ME.tenant_id, workspace_id=ME.workspace_id, provider=EmailProvider.GMAIL,
                                       address="other@gmail.example", connected_by="someone_else", visibility=Visibility.WORKSPACE,
                                       status=AccountStatus.REAUTH_REQUIRED, created_at=now, updated_at=now))
    assert A._tamara_mail(ME, _today()) == ([], [])
