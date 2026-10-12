"""Favorites, knowledge base and reports (memory backend): docs/knowledge-base.md, docs/reports.md."""

from __future__ import annotations

import json
from uuid import uuid4
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

from anum_api.automation import current_engine
from anum_api.conversations import conversation_store
from anum_api.dependencies import memory_note_repository
from anum_api.favorites import favorite_store
from anum_api.files import file_store
from anum_api.knowledge_base import knowledge_store
from anum_api.main import app
from anum_api.model_gateway import MockModelGateway
from anum_api.reports import ReportCreate, render_csv, report_store, split_report_block
from anum_api.retrieval import memory_retrieval_store
from anum_api.voice import voice_model_gateway

TENANT, WORKSPACE = "tenant_library", "workspace_library"


def headers(user: str = "user_lib", role: str = "member", workspace: str = WORKSPACE) -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


H = headers()
OWNER = headers("user_owner", "owner")
OTHER = headers("user_other")
VIEWER = headers("user_viewer", "viewer")


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    for store in (favorite_store, knowledge_store, report_store, conversation_store, memory_retrieval_store):
        store.clear()
    memory_note_repository._notes.clear()
    file_store.clear()
    yield
    app.dependency_overrides.clear()
    for store in (favorite_store, knowledge_store, report_store, conversation_store, memory_retrieval_store):
        store.clear()
    memory_note_repository._notes.clear()
    file_store.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _report(client: TestClient, hdrs: dict[str, str] = H, **body: object) -> dict:
    payload = {
        "type": "tyre",
        "title": "Tyre cost, September",
        "period_start": "2026-09-01",
        "period_end": "2026-09-30",
        "summary": "Costs rose on replacements.",
        "metrics": [{"label": "Total cost", "value": 12400, "unit": "SAR", "change_percent": 8.5}],
        "highlights": ["Puncture repairs doubled"],
        "data": {"columns": ["reason", "cost"], "rows": [["Puncture", 4200], ["=SUM(A1)", 10]]},
        **body,
    }
    response = client.post("/api/v1/reports", headers=hdrs, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- reports


def test_report_crud_paging_and_exports(client: TestClient) -> None:
    created = _report(client)
    assert created["metrics"][0]["value"] == "12,400" and created["can_manage"] is True
    assert created["created_by_kind"] == "user" and created["visibility"] == "workspace"
    assert created["body_markdown"].startswith("# Tyre cost, September")
    for n in range(3):
        _report(client, title=f"Commerce week {n}", type="commerce")

    page = client.get("/api/v1/reports", headers=H, params={"limit": 2}).json()
    assert page["total"] == 4 and len(page["items"]) == 2 and "data" not in page["items"][0]
    assert {t["type"]: t["count"] for t in page["types"]} == {"commerce": 3, "tyre": 1}
    assert client.get("/api/v1/reports", headers=H, params={"type": "tyre"}).json()["total"] == 1
    assert client.get("/api/v1/reports", headers=H, params={"q": "week 2"}).json()["total"] == 1

    csv_response = client.get(f"/api/v1/reports/{created['id']}/export", headers=H, params={"format": "csv"})
    assert csv_response.status_code == 200 and "attachment" in csv_response.headers["content-disposition"]
    text = csv_response.content.decode("utf-8-sig")
    assert text.splitlines()[0] == "reason,cost" and "'=SUM(A1)" in text  # formula cells are defused
    md = client.get(f"/api/v1/reports/{created['id']}/export", headers=H, params={"format": "md"})
    assert md.headers["content-type"].startswith("text/markdown") and "| Total cost |" in md.text

    renamed = client.patch(f"/api/v1/reports/{created['id']}", headers=H, json={"title": "Tyre cost (final)"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Tyre cost (final)"
    assert client.delete(f"/api/v1/reports/{created['id']}", headers=H).status_code == 204
    assert client.get(f"/api/v1/reports/{created['id']}", headers=H).status_code == 404


def test_report_permissions_private_and_owner_rules(client: TestClient) -> None:
    shared = _report(client)
    private = _report(client, visibility="private", title="Only mine")
    # Another member sees the shared report, not the private one, and cannot change it.
    other_list = client.get("/api/v1/reports", headers=OTHER).json()
    assert [r["id"] for r in other_list["items"]] == [shared["id"]]
    assert client.get(f"/api/v1/reports/{private['id']}", headers=OTHER).status_code == 404
    assert client.get(f"/api/v1/reports/{shared['id']}", headers=OTHER).json()["can_manage"] is False
    refused = client.delete(f"/api/v1/reports/{shared['id']}", headers=OTHER)
    assert refused.status_code == 403 and "creator or a workspace owner" in refused.text
    # A viewer reads but cannot create; a workspace owner may delete a shared report.
    assert client.get("/api/v1/reports", headers=VIEWER).status_code == 200
    assert client.post("/api/v1/reports", headers=VIEWER, json={"title": "x"}).status_code == 403
    assert client.patch(f"/api/v1/reports/{shared['id']}", headers=OWNER, json={"visibility": "private"}).status_code == 403
    assert client.delete(f"/api/v1/reports/{shared['id']}", headers=OWNER).status_code == 204
    # Another workspace sees nothing.
    assert client.get("/api/v1/reports", headers=headers(workspace="workspace_elsewhere")).json()["total"] == 0


def test_report_validation(client: TestClient) -> None:
    bad = [
        {"title": "x", "type": "Has Spaces"},
        {"title": "x", "period_start": "2026-10-02", "period_end": "2026-10-01"},
        {"title": "x", "data": {"columns": ["a", "b"], "rows": [["only one"]]}},
        {"title": ""},
    ]
    for body in bad:
        assert client.post("/api/v1/reports", headers=H, json=body).status_code == 422, body


def test_generated_task_summary_counts_the_period(client: TestClient) -> None:
    for prompt in ("Check stock", "Reprice listings"):
        assert client.post("/api/v1/tasks", headers=H, json={"title": prompt, "prompt": prompt}).status_code == 201
    generators = client.get("/api/v1/reports/generators", headers=H).json()
    assert {g["type"] for g in generators} == {"task_summary", "automation_summary", "commerce_summary", "tyre_summary"}
    report = client.post("/api/v1/reports/generate", headers=H, json={"type": "task_summary", "period_days": 7})
    assert report.status_code == 201, report.text
    body = report.json()
    assert body["type"] == "task_summary" and body["metrics"][0]["label"] == "Tasks created"
    assert int(body["metrics"][0]["value"]) >= 2
    assert body["data"]["columns"][0] == "task_id" and len(body["data"]["rows"]) >= 2


def test_automation_step_generates_a_shared_report(client: TestClient) -> None:
    workflow = client.post(
        "/api/v1/automation/workflows",
        headers=H,
        json={"name": "Weekly report", "steps": [
            {"id": "r", "name": "Report", "action": "report.generate", "input": {"type": "task_summary", "period_days": 7}}
        ]},
    ).json()
    run = client.post(f"/api/v1/automation/workflows/{workflow['id']}/runs", headers={**H, "Idempotency-Key": uuid4().hex})
    assert run.status_code in {200, 201}, run.text
    step = run.json()["steps"][0]
    assert run.json()["status"] == "completed" and step["output"]["report_id"]
    stored = client.get(f"/api/v1/reports/{step['output']['report_id']}", headers=OTHER).json()
    assert stored["created_by_kind"] == "automation" and stored["source"]["automation_run_id"] == run.json()["id"]

    refused = client.post(
        "/api/v1/automation/workflows",
        headers=H,
        json={"name": "Bad report", "steps": [{"id": "r", "name": "R", "action": "report.generate", "input": {"type": "nope"}}]},
    )
    assert refused.status_code == 422 and "task_summary" in refused.text
    assert current_engine() is not None


def test_report_block_parsing_is_strict() -> None:
    block = {"title": "Tyre cost", "type": "tyre", "metrics": [{"label": "Total", "value": "12,400", "unit": "SAR"}]}
    text = "Costs rose 8%.\n```anum-report\n" + json.dumps(block) + "\n```"
    clean, parsed = split_report_block(text)
    assert clean == "Costs rose 8%." and parsed is not None and parsed.metrics[0].unit == "SAR"
    # Invalid JSON, a bad type slug, or no figures: the block is dropped and no report is made.
    for body in ("{not json", json.dumps({"title": "x", "type": "Bad Type", "metrics": block["metrics"]}),
                 json.dumps({"title": "x"})):
        clean, parsed = split_report_block("Answer.\n```anum-report\n" + body + "\n```")
        assert clean == "Answer." and parsed is None
    assert split_report_block("No block here") == ("No block here", None)


def test_csv_falls_back_to_metrics_without_a_table() -> None:
    report = ReportCreate(title="t", metrics=[{"label": "Total", "value": "5"}])  # type: ignore[list-item]
    from anum_api.reports import CreatedByKind, Report, ReportSource
    from anum_api.schemas import utc_now

    full = Report(id="r", tenant_id="t", workspace_id="w", type="custom", title="t", period_start=None, period_end=None,
                  summary="", metrics=report.metrics, highlights=[], data=report.data, body_markdown="",
                  visibility="workspace", created_by="u", created_by_kind=CreatedByKind.USER, source=ReportSource(),
                  created_at=utc_now(), updated_at=utc_now())
    assert render_csv(full).splitlines()[:2] == ["metric,value,unit,change_percent", "Total,5,,"]


class ReportingGateway(MockModelGateway):
    async def stream_chat(self, messages, *, max_tokens=None, on_usage=None) -> AsyncIterator[str]:  # type: ignore[no-untyped-def,override]
        block = {
            "title": "Tyre cost, September", "type": "tyre", "period_start": "2026-09-01", "period_end": "2026-09-30",
            "metrics": [{"label": "Total cost", "value": "12,400", "unit": "SAR", "change_percent": 8}],
            "highlights": ["Punctures doubled"],
        }
        for part in ("Your tyre cost rose 8%.", "\n```anum-report\n", json.dumps(block), "\n```"):
            yield part


def _events(response) -> list[tuple[str, dict]]:  # type: ignore[no-untyped-def]
    parsed = []
    for frame in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


def test_structured_chat_answer_becomes_a_private_report_card(client: TestClient) -> None:
    # A question no tool answers (a tyre question would run the tyre tools as a task and get
    # a tool card instead, docs/conversations.md#tools-in-chat).
    app.dependency_overrides[voice_model_gateway] = lambda: ReportingGateway()
    conversation = client.post("/api/v1/conversations", headers=H, json={}).json()
    response = client.post(f"/api/v1/conversations/{conversation['id']}/ask", headers=H, json={"content": "How did our costs move last month?"})
    done = _events(response)[-1]
    assert done[0] == "done"
    assert done[1]["assistant_turn"]["content"] == "Your tyre cost rose 8%."
    card = done[1]["report"]
    assert card["title"] == "Tyre cost, September" and card["metrics"][0]["unit"] == "SAR"
    assert card["turn_id"] == done[1]["assistant_turn"]["id"]
    report = client.get(f"/api/v1/reports/{card['report_id']}", headers=H).json()
    assert report["visibility"] == "private" and report["created_by_kind"] == "agent"
    assert report["source"]["conversation_id"] == conversation["id"]
    assert client.get(f"/api/v1/reports/{card['report_id']}", headers=OTHER).status_code == 404
    detail = client.get(f"/api/v1/conversations/{conversation['id']}", headers=H).json()
    assert [c["report_id"] for c in detail["reports"]] == [card["report_id"]]


# --------------------------------------------------------------------------- favorites


def test_favorites_star_list_filter_unstar(client: TestClient) -> None:
    report = _report(client)
    conversation = client.post("/api/v1/conversations", headers=H, json={"title": "Pricing chat"}).json()
    starred = client.post("/api/v1/favorites", headers=H, json={"item_type": "report", "item_id": report["id"]})
    assert starred.status_code == 201 and starred.json()["label"] == "Tyre cost, September"
    again = client.post("/api/v1/favorites", headers=H, json={"item_type": "report", "item_id": report["id"]})
    assert again.status_code == 200 and again.json()["id"] == starred.json()["id"]
    assert client.post("/api/v1/favorites", headers=H, json={"item_type": "conversation", "item_id": conversation["id"]}).status_code == 201

    page = client.get("/api/v1/favorites", headers=H).json()
    assert page["total"] == 2 and [f["item_type"] for f in page["items"]] == ["conversation", "report"]
    assert client.get("/api/v1/favorites", headers=H, params={"item_type": "report"}).json()["total"] == 1
    # Private to the user.
    assert client.get("/api/v1/favorites", headers=OTHER).json()["total"] == 0
    # Another user's conversation, or something missing, cannot be starred.
    refused = client.post("/api/v1/favorites", headers=OTHER, json={"item_type": "conversation", "item_id": conversation["id"]})
    assert refused.status_code == 404
    assert client.post("/api/v1/favorites", headers=H, json={"item_type": "task", "item_id": "task_missing"}).status_code == 404
    assert client.post("/api/v1/favorites", headers=H, json={"item_type": "planet", "item_id": "x"}).status_code == 422

    assert client.delete("/api/v1/favorites", headers=H, params={"item_type": "conversation", "item_id": conversation["id"]}).status_code == 204
    assert client.delete(f"/api/v1/favorites/{starred.json()['id']}", headers=H).status_code == 204
    assert client.delete(f"/api/v1/favorites/{starred.json()['id']}", headers=H).status_code == 404
    assert client.get("/api/v1/favorites", headers=H).json()["total"] == 0


def test_deleting_a_report_drops_its_star(client: TestClient) -> None:
    report = _report(client)
    client.post("/api/v1/favorites", headers=H, json={"item_type": "report", "item_id": report["id"]})
    client.delete(f"/api/v1/reports/{report['id']}", headers=H)
    assert client.get("/api/v1/favorites", headers=H).json()["total"] == 0


def test_a_viewer_stars_what_they_can_read(client: TestClient) -> None:
    report = _report(client)
    assert client.post("/api/v1/favorites", headers=VIEWER, json={"item_type": "report", "item_id": report["id"]}).status_code == 201


# --------------------------------------------------------------------------- knowledge base


def _collection(client: TestClient, hdrs: dict[str, str] = H, **body: object) -> dict:
    response = client.post("/api/v1/knowledge/collections", headers=hdrs, json={"name": "Tyre specs", **body})
    assert response.status_code == 201, response.text
    return response.json()


def test_collections_documents_notes_search_and_ask(client: TestClient) -> None:
    collection = _collection(client, description="Load and speed ratings")
    assert collection["visibility"] == "workspace" and collection["item_count"] == 0 and collection["can_manage"]
    upload = client.post(
        f"/api/v1/knowledge/collections/{collection['id']}/documents",
        headers={**H, "x-file-name": "ratings.md", "content-type": "text/markdown", "x-item-tags": "Specs, load"},
        content=b"# Load index\nA load index of 91 carries 615 kg per tyre.",
    )
    assert upload.status_code == 201, upload.text
    document = upload.json()
    assert document["kind"] == "file" and document["tags"] == ["specs", "load"]
    assert document["index"]["state"] == "indexed" and document["available"] is True
    note = client.post(
        f"/api/v1/knowledge/collections/{collection['id']}/notes",
        headers=H,
        json={"title": "Winter policy", "content": "Fit winter tyres from November to March.", "tags": ["policy"]},
    )
    assert note.status_code == 201, note.text
    assert note.json()["content"].startswith("Fit winter") and note.json()["index"]["state"] == "indexed"

    items = client.get(f"/api/v1/knowledge/collections/{collection['id']}/items", headers=H).json()
    assert items["total"] == 2 and items["tags"] == ["load", "policy", "specs"]
    assert client.get(f"/api/v1/knowledge/collections/{collection['id']}/items", headers=H, params={"tag": "policy"}).json()["total"] == 1
    assert client.get(f"/api/v1/knowledge/collections/{collection['id']}/items", headers=H, params={"kind": "file"}).json()["total"] == 1

    keyword = client.get("/api/v1/knowledge/search", headers=H, params={"q": "winter", "mode": "keyword"}).json()
    assert [h["item"]["title"] for h in keyword["hits"]] == ["Winter policy"] and keyword["semantic"] == "off"
    hybrid = client.get("/api/v1/knowledge/search", headers=H, params={"q": "load index kg"}).json()
    assert hybrid["semantic"] == "ok" and hybrid["hits"][0]["item"]["title"] == "ratings.md"
    assert "semantic" in hybrid["hits"][0]["matched_by"]

    answer = client.post("/api/v1/knowledge/ask", headers=H, json={"question": "What does load index 91 carry?"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["status"] == "answered" and body["citations"]
    first = body["citations"][0]
    assert first["n"] == 1 and first["provenance"] in {"file", "memory"} and "[1]" in body["answer"]

    # The collection's count and listing reflect the items.
    assert client.get(f"/api/v1/knowledge/collections/{collection['id']}", headers=H).json()["item_count"] == 2


def test_ask_without_sources_and_permissions(client: TestClient) -> None:
    empty = client.post("/api/v1/knowledge/ask", headers=H, json={"question": "Anything?"}).json()
    assert empty["status"] == "no_sources" and empty["citations"] == []
    assert client.post("/api/v1/knowledge/ask", headers=VIEWER, json={"question": "Anything?"}).status_code == 403
    assert client.post("/api/v1/knowledge/collections", headers=VIEWER, json={"name": "x"}).status_code == 403
    assert client.get("/api/v1/knowledge/collections", headers=VIEWER).status_code == 200


def test_private_collections_and_their_notes_stay_private(client: TestClient) -> None:
    private = _collection(client, name="My drafts", visibility="private")
    shared = _collection(client, name="Team")
    note = client.post(
        f"/api/v1/knowledge/collections/{private['id']}/notes",
        headers=H,
        json={"title": "Secret supplier", "content": "Supplier margin is 14 percent."},
    ).json()
    assert [c["id"] for c in client.get("/api/v1/knowledge/collections", headers=OTHER).json()] == [shared["id"]]
    assert client.get(f"/api/v1/knowledge/collections/{private['id']}/items", headers=OTHER).status_code == 404
    assert client.get(f"/api/v1/knowledge/items/{note['id']}", headers=OTHER).status_code == 404
    hits = client.get("/api/v1/knowledge/search", headers=OTHER, params={"q": "supplier margin"}).json()["hits"]
    assert hits == []
    other_answer = client.post("/api/v1/knowledge/ask", headers=OTHER, json={"question": "supplier margin"}).json()
    assert other_answer["status"] == "no_sources"
    # The note is a private memory: the other user cannot read it in Memory either.
    memories = client.get("/api/v1/memories", headers=OTHER).json()
    assert all(m["id"] != note["source_id"] for m in memories)
    mine = client.post("/api/v1/knowledge/ask", headers=H, json={"question": "supplier margin", "collection_id": private["id"]}).json()
    assert mine["status"] == "answered" and mine["citations"][0]["provenance"] == "memory"


def test_manage_rules_edit_and_delete(client: TestClient) -> None:
    collection = _collection(client)
    note = client.post(
        f"/api/v1/knowledge/collections/{collection['id']}/notes", headers=H, json={"title": "Old", "content": "v1"}
    ).json()
    # Another member may add but not rename the collection or edit my note.
    assert client.patch(f"/api/v1/knowledge/collections/{collection['id']}", headers=OTHER, json={"name": "x"}).status_code == 403
    assert client.patch(f"/api/v1/knowledge/items/{note['id']}", headers=OTHER, json={"title": "x"}).status_code == 403
    theirs = client.post(
        f"/api/v1/knowledge/collections/{collection['id']}/notes", headers=OTHER, json={"title": "Theirs", "content": "v"}
    )
    assert theirs.status_code == 201
    edited = client.patch(
        f"/api/v1/knowledge/items/{note['id']}", headers=H, json={"title": "New", "tags": ["A", "a", "b!"], "content": "v2 text"}
    )
    assert edited.status_code == 200 and edited.json()["tags"] == ["a", "b"] and edited.json()["content"] == "v2 text"
    renamed = client.patch(f"/api/v1/knowledge/collections/{collection['id']}", headers=OWNER, json={"name": "Renamed"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Renamed"
    reindex = client.post(f"/api/v1/knowledge/collections/{collection['id']}/reindex", headers=H)
    assert reindex.status_code == 200 and reindex.json()["reused"] + reindex.json()["indexed"] == 2

    assert client.delete(f"/api/v1/knowledge/items/{note['id']}", headers=H).status_code == 204
    assert memory_note_repository.get(note["source_id"], _ctx()) is None  # the note's memory went with it
    assert client.delete(f"/api/v1/knowledge/collections/{collection['id']}", headers=OTHER).status_code == 403
    assert client.delete(f"/api/v1/knowledge/collections/{collection['id']}", headers=H).status_code == 204
    assert client.get(f"/api/v1/knowledge/collections/{collection['id']}", headers=H).status_code == 404


def test_linking_an_existing_file_and_documents_survive_collection_delete(client: TestClient) -> None:
    uploaded = client.post(
        "/api/v1/files", headers={**H, "x-file-name": "price-list.csv", "content-type": "text/csv"}, content=b"sku,price\nA,10\n"
    ).json()
    collection = _collection(client)
    linked = client.post(f"/api/v1/knowledge/collections/{collection['id']}/files", headers=H, json={"file_id": uploaded["id"]})
    assert linked.status_code == 201 and linked.json()["title"] == "price-list.csv"
    duplicate = client.post(f"/api/v1/knowledge/collections/{collection['id']}/files", headers=H, json={"file_id": uploaded["id"]})
    assert duplicate.status_code == 409
    assert client.post(f"/api/v1/knowledge/collections/{collection['id']}/files", headers=H, json={"file_id": "file_x"}).status_code == 404
    client.delete(f"/api/v1/knowledge/collections/{collection['id']}", headers=H)
    assert client.get(f"/api/v1/files/{uploaded['id']}", headers=H).status_code == 200


def _ctx():  # type: ignore[no-untyped-def]
    from anum_api.schemas import TenantContext

    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="user_lib", roles=["member"])


# --------------------------------------------------------------------------- privacy


def test_privacy_counts_exports_and_deletes_only_my_library_data(client: TestClient) -> None:
    shared = _report(client)
    mine = _report(client, visibility="private", title="Mine only")
    _report(client, OTHER, visibility="private", title="Theirs")
    client.post("/api/v1/favorites", headers=H, json={"item_type": "report", "item_id": shared["id"]})
    client.post("/api/v1/favorites", headers=OTHER, json={"item_type": "report", "item_id": shared["id"]})
    private = _collection(client, name="Drafts", visibility="private")
    client.post(f"/api/v1/knowledge/collections/{private['id']}/notes", headers=H, json={"title": "N", "content": "Secret"})
    team = _collection(client, name="Team")

    summary = client.get("/api/v1/privacy", headers=H).json()["private"]
    assert (summary["favorites"], summary["private_collections"], summary["private_reports"]) == (1, 1, 1)
    export = client.get("/api/v1/privacy/export", headers=H).json()
    library = export["library"]
    assert [r["id"] for r in library["private_reports"]] == [mine["id"]]
    assert [c["name"] for c in library["private_collections"]] == ["Drafts"]
    assert library["private_collections"][0]["items"][0]["title"] == "N"
    assert len(library["favorites"]) == 1

    deleted = client.post("/api/v1/privacy/delete", headers=H, json={"confirm": "DELETE MY DATA"}).json()["deleted"]
    assert (deleted["favorites"], deleted["private_collections"], deleted["private_reports"]) == (1, 1, 1)
    assert client.get(f"/api/v1/reports/{mine['id']}", headers=H).status_code == 404
    assert client.get(f"/api/v1/reports/{shared['id']}", headers=H).status_code == 200  # workspace data stays
    assert [c["id"] for c in client.get("/api/v1/knowledge/collections", headers=H).json()] == [team["id"]]
    assert client.get("/api/v1/favorites", headers=OTHER).json()["total"] == 1
    assert client.get("/api/v1/privacy", headers=OTHER).json()["private"]["private_reports"] == 1


# --------------------------------------------------------------------------- commerce generator


def test_commerce_summary_counts_synced_orders_by_channel() -> None:
    from datetime import timedelta

    from anum_api.commerce.models import Channel, Order, OrderLine, OrderStatus
    from anum_api.commerce.store import commerce_store
    from anum_api.schemas import utc_now

    commerce_store.clear()
    now = utc_now()

    def order(n: int, channel: Channel, status: OrderStatus, age_days: float, amount: float) -> Order:
        return Order(
            id=f"ord_{n}", tenant_id=TENANT, workspace_id=WORKSPACE, connector_id="conn_1", channel=channel,
            marketplace_id="m", external_id=f"EXT-{n}", status=status, purchased_at=now - timedelta(days=age_days),
            total_amount=amount, currency="SAR", item_count=2, lines=[OrderLine(sku="TYRE-205", quantity=2)], synced_at=now,
        )

    for item in (
        order(1, Channel.AMAZON, OrderStatus.SHIPPED, 1, 800),
        order(2, Channel.NOON, OrderStatus.DELIVERED, 2, 400),
        order(3, Channel.NOON, OrderStatus.CANCELLED, 2, 999),  # not counted
        order(4, Channel.AMAZON, OrderStatus.SHIPPED, 10, 600),  # the previous period
    ):
        commerce_store.upsert_order(item)
    client = TestClient(app)
    try:
        made = client.post("/api/v1/reports/generate", headers=H, json={"type": "commerce_summary", "period_days": 7})
        assert made.status_code == 201, made.text
        report = made.json()
        metrics = {m["label"]: m for m in report["metrics"]}
        assert metrics["Orders"]["value"] == "2" and metrics["Orders"]["change_percent"] == 100.0
        assert metrics["Revenue"]["value"] == "1,200.00" and metrics["Revenue"]["unit"] == "SAR"
        assert "Best seller TYRE-205: 4 sold" in report["highlights"]
        assert len(report["data"]["rows"]) == 2 and "customer" not in str(report["data"]["columns"])
        assert client.post("/api/v1/reports/generate", headers=VIEWER, json={"type": "commerce_summary"}).status_code == 403
    finally:
        commerce_store.clear()
