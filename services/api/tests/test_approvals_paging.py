"""Paged approvals: filter, search, keyset cursor and summary (docs/approvals-and-risk.md#paging-filters-and-search)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.approval_paging import (
    ApprovalFilter,
    decode_cursor,
    encode_cursor,
    page_in_memory,
    summary_in_memory,
)
from anum_api.schemas import Approval, ApprovalStatus, RiskLevel, utc_now
from anum_api.store import store

HEADERS = {
    "x-tenant-id": "tenant_paging",
    "x-workspace-id": "workspace_paging",
    "x-user-id": "owner_paging",
    "x-user-roles": "owner",
}
OTHER = {**HEADERS, "x-workspace-id": "workspace_paging_other"}
VIEWER = {**HEADERS, "x-user-id": "viewer_paging", "x-user-roles": "viewer"}
BASE = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean_store() -> Iterator[None]:
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
        collection.clear()
    yield


def _task(title: str = "Quarterly report", headers: dict[str, str] = HEADERS) -> str:
    response = client.post("/api/v1/tasks", headers=headers, json={"title": title, "prompt": "Prepare it"})
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _approval(
    approval_id: str,
    task_id: str,
    *,
    minutes: int,
    status: ApprovalStatus = ApprovalStatus.PENDING,
    action: str = "send_email",
    reason: str = "Needs a person",
    target: str | None = None,
    expires_at: datetime | None = None,
    headers: dict[str, str] = HEADERS,
) -> Approval:
    approval = Approval(
        id=approval_id,
        task_id=task_id,
        action=action,
        risk_level=RiskLevel.HIGH,
        status=status,
        reason=reason,
        created_at=BASE + timedelta(minutes=minutes),
        decided_at=None if status == ApprovalStatus.PENDING else BASE + timedelta(minutes=minutes + 1),
        target=target,
        expires_at=expires_at,
    )
    store.approvals[approval_id] = approval
    return approval


def _page(**params: object) -> dict:
    response = client.get("/api/v1/approvals/page", headers=HEADERS, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_pending_come_first_then_newest_first_across_pages() -> None:
    task = _task()
    _approval("apr_old_pending", task, minutes=1)
    _approval("apr_new_pending", task, minutes=5)
    _approval("apr_decided_new", task, minutes=9, status=ApprovalStatus.APPROVED)
    _approval("apr_decided_old", task, minutes=2, status=ApprovalStatus.REJECTED)
    _approval("apr_lapsed", task, minutes=7, expires_at=BASE)  # stored pending, already past expiry

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params: dict[str, object] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = _page(**params)
        seen += [item["id"] for item in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert seen == ["apr_new_pending", "apr_old_pending", "apr_decided_new", "apr_lapsed", "apr_decided_old"]
    assert pages == 3
    lapsed = next(item for item in _page(status="decided")["items"] if item["id"] == "apr_lapsed")
    assert lapsed["status"] == "expired"


def test_status_filter_splits_pending_from_decided() -> None:
    task = _task()
    _approval("apr_p", task, minutes=1)
    _approval("apr_a", task, minutes=2, status=ApprovalStatus.APPROVED)
    _approval("apr_x", task, minutes=3, expires_at=BASE)
    assert [i["id"] for i in _page(status="pending")["items"]] == ["apr_p"]
    assert [i["id"] for i in _page(status="decided")["items"]] == ["apr_x", "apr_a"]
    assert len(_page()["items"]) == 3
    # Each item carries routing like the unpaged list.
    assert _page(status="pending")["items"][0]["routing"] is not None


def test_search_matches_action_reason_target_id_and_task_title() -> None:
    invoices = _task("Chase invoices")
    other = _task("Something else")
    _approval("apr_mail", other, minutes=1, action="send_email", target="smtp.example.test")
    _approval("apr_post", other, minutes=2, action="publish_post", reason="Posts the 50% sale")
    _approval("apr_inv", invoices, minutes=3, action="noop")
    assert [i["id"] for i in _page(query="SMTP.example")["items"]] == ["apr_mail"]
    assert [i["id"] for i in _page(query="50%")["items"]] == ["apr_post"]
    assert [i["id"] for i in _page(query="invoices")["items"]] == ["apr_inv"]
    assert [i["id"] for i in _page(query="apr_po")["items"]] == ["apr_post"]
    # LIKE wildcards are literal.
    assert [i["id"] for i in _page(query="%")["items"]] == ["apr_post"]
    assert _page(query="nothing like this")["items"] == []


def test_task_filter_and_workspace_isolation() -> None:
    mine = _task()
    second = _task("Second")
    foreign = _task("Foreign", headers=OTHER)
    _approval("apr_mine", mine, minutes=1)
    _approval("apr_second", second, minutes=2)
    _approval("apr_foreign", foreign, minutes=3)
    assert [i["id"] for i in _page(task_id=mine)["items"]] == ["apr_mine"]
    assert {i["id"] for i in _page()["items"]} == {"apr_mine", "apr_second"}
    assert _page(task_id=foreign)["items"] == []
    summary = client.get("/api/v1/approvals/summary", headers=HEADERS).json()
    assert summary == {"pending": 2, "approved": 0, "rejected": 0, "expired": 0}


def test_summary_counts_each_state_with_expiry_applied() -> None:
    task = _task()
    _approval("apr_1", task, minutes=1)
    _approval("apr_2", task, minutes=2, expires_at=BASE)
    _approval("apr_3", task, minutes=3, status=ApprovalStatus.APPROVED)
    _approval("apr_4", task, minutes=4, status=ApprovalStatus.REJECTED)
    _approval("apr_5", task, minutes=5, status=ApprovalStatus.EXPIRED)
    response = client.get("/api/v1/approvals/summary", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"pending": 1, "approved": 1, "rejected": 1, "expired": 2}


def test_invalid_cursor_and_limits_are_refused() -> None:
    assert client.get("/api/v1/approvals/page", headers=HEADERS, params={"cursor": "bm9wZQ"}).status_code == 400
    assert client.get("/api/v1/approvals/page", headers=HEADERS, params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/approvals/page", headers=HEADERS, params={"limit": 101}).status_code == 422
    assert client.get("/api/v1/approvals/page", headers=HEADERS, params={"status": "maybe"}).status_code == 422
    assert client.get("/api/v1/approvals/page", headers=HEADERS, params={"query": "x" * 201}).status_code == 422


def test_viewers_can_read_and_anonymous_cannot() -> None:
    task = _task()
    _approval("apr_v", task, minutes=1)
    assert client.get("/api/v1/approvals/page", headers=VIEWER).status_code == 200
    assert client.get("/api/v1/approvals/summary", headers=VIEWER).status_code == 200
    assert client.get("/api/v1/approvals/page").status_code in (401, 403)


def test_legacy_list_keeps_its_shape() -> None:
    task = _task()
    _approval("apr_legacy", task, minutes=1)
    body = client.get("/api/v1/approvals", headers=HEADERS).json()
    assert isinstance(body, list) and body[0]["id"] == "apr_legacy"
    # The single-approval route still resolves ids (no clash with /page or /summary).
    assert client.get("/api/v1/approvals/apr_legacy", headers=HEADERS).status_code == 200


def test_cursor_round_trip_and_rejects_foreign_values() -> None:
    cursor = encode_cursor(1, BASE, "apr_1", BASE + timedelta(hours=1))
    assert decode_cursor(cursor) == ((1, BASE, "apr_1"), BASE + timedelta(hours=1))
    for bad in ("", "!!", "bm9wZQ"):
        with pytest.raises(ValueError):
            decode_cursor(bad)
    import base64

    naive = base64.urlsafe_b64encode(b"0|2026-10-01T09:00:00|apr|2026-10-01T09:00:00+00:00").decode()
    with pytest.raises(ValueError):
        decode_cursor(naive)
    wrong_group = base64.urlsafe_b64encode(b"7|2026-10-01T09:00:00+00:00|apr|2026-10-01T09:00:00+00:00").decode()
    no_as_of = base64.urlsafe_b64encode(b"0|2026-10-01T09:00:00+00:00|apr").decode()
    with pytest.raises(ValueError):
        decode_cursor(no_as_of)
    with pytest.raises(ValueError):
        decode_cursor(wrong_group)


def test_memory_helpers_page_the_same_way() -> None:
    now = BASE + timedelta(days=1)
    rows = [
        Approval(id=f"apr_{n}", task_id="t", action="a", risk_level=RiskLevel.LOW, status=ApprovalStatus.APPROVED,
                 reason="r", created_at=BASE + timedelta(minutes=n))
        for n in range(5)
    ]
    first, more = page_in_memory(rows, now=now, status=ApprovalFilter.ALL, query=None, task_id=None, cursor=None,
                                 limit=3, task_title=lambda _id: None)
    assert [a.id for a in first] == ["apr_4", "apr_3", "apr_2"] and more
    last = first[-1]
    rest, more = page_in_memory(rows, now=now, status=ApprovalFilter.ALL, query=None, task_id=None,
                                cursor=(1, last.created_at, last.id), limit=3, task_title=lambda _id: None)
    assert [a.id for a in rest] == ["apr_1", "apr_0"] and not more
    assert summary_in_memory(rows, now).approved == 5


def test_legacy_list_accepts_status_and_limit_and_stays_a_plain_list() -> None:
    task = _task()
    _approval("apr_a", task, minutes=1)
    _approval("apr_b", task, minutes=2)
    _approval("apr_c", task, minutes=3, status=ApprovalStatus.APPROVED)
    everything = client.get("/api/v1/approvals", headers=HEADERS).json()
    assert [a["id"] for a in everything] == ["apr_a", "apr_b", "apr_c"]
    pending = client.get("/api/v1/approvals", headers=HEADERS, params={"status": "pending", "limit": 1}).json()
    assert [a["id"] for a in pending] == ["apr_b"]
    assert client.get("/api/v1/approvals", headers=HEADERS, params={"limit": 0}).status_code == 422


def _walk(limit: int, between: list | None = None) -> list[str]:
    """Walk every page, calling each ``between`` step after the first page and so on."""
    seen: list[str] = []
    cursor = None
    steps = list(between or [])
    while True:
        params: dict[str, object] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = _page(**params)
        seen += [item["id"] for item in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            return seen
        if steps:
            steps.pop(0)()


def test_decisions_between_pages_neither_repeat_nor_skip_a_row() -> None:
    task = _task()
    for n in range(1, 7):
        _approval(f"apr_p{n}", task, minutes=n)
    _approval("apr_old_done", task, minutes=0, status=ApprovalStatus.APPROVED)
    baseline = _walk(2)
    assert baseline == ["apr_p6", "apr_p5", "apr_p4", "apr_p3", "apr_p2", "apr_p1", "apr_old_done"]

    def decide(approval_id: str) -> None:
        current = store.approvals[approval_id]
        store.approvals[approval_id] = current.model_copy(
            update={"status": ApprovalStatus.APPROVED, "decided_at": utc_now() + timedelta(seconds=1)}
        )

    # After page one, decide a row already shown (p5) and one not yet shown (p2).
    walked = _walk(2, [lambda: (decide("apr_p5"), decide("apr_p2"))])
    assert walked == baseline
    assert len(walked) == len(set(walked))


def test_a_row_created_during_a_walk_waits_for_the_next_refresh() -> None:
    task = _task()
    for n in range(1, 5):
        _approval(f"apr_n{n}", task, minutes=n)
    first = _page(limit=2)
    fresh = _approval("apr_fresh", task, minutes=0)
    store.approvals["apr_fresh"] = fresh.model_copy(update={"created_at": utc_now() + timedelta(seconds=5)})
    second = _page(limit=2, cursor=first["next_cursor"])
    ids = [i["id"] for i in first["items"]] + [i["id"] for i in second["items"]]
    assert ids == ["apr_n4", "apr_n3", "apr_n2", "apr_n1"]


def test_expiry_after_the_walk_began_does_not_move_a_row() -> None:
    from anum_api.approval_paging import group_of

    task = _task()
    soon = BASE + timedelta(minutes=30)
    approval = _approval("apr_exp", task, minutes=1, expires_at=soon)
    assert group_of(approval, soon - timedelta(minutes=1)) == 0
    assert group_of(approval, soon + timedelta(minutes=1)) == 1
