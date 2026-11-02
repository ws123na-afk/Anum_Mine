"""Routes that used to open stores on the event loop now do their database work in worker
threads (pre-sale review open item P7, docs/security.md; docs/realtime.md#event-loop).

One route per pattern the fix used, through the whole app on the in-memory backend:

* a plain ``def`` route (FastAPI runs it in its thread pool): favorites, calendar events;
* an ``async`` route that reads the body or calls out, with each store step in
  ``asyncio.to_thread``: a file upload (with retrieval indexing), file versions;
* the retrieval indexer, whose store calls run in worker threads, each in its own short
  unit of work for committed sources: knowledge notes and their edits, a collection
  re-index, the workspace re-index;
* Home's sources, each run in a worker thread unless it is itself ``async``.

Every store opener is wrapped and any call made from a thread that runs an event loop is
recorded, as ``test_ask_off_event_loop.py`` does for the assistant answers.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import dependencies, file_policy, files, scoped_store
from anum_api.files import file_store
from anum_api.main import app
from anum_api.retrieval import memory_retrieval_store

client = TestClient(app)
HEADERS = {"x-tenant-id": "tenant_offloop", "x-workspace-id": "workspace_offloop", "x-user-id": "user_offloop", "x-user-roles": "owner"}


@pytest.fixture
def on_loop(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Names of store openers called on the event loop during the test."""
    calls: list[str] = []

    def guard(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        @contextmanager
        def wrapped(*args: Any, **kwargs: Any) -> Iterator[Any]:
            try:
                asyncio.get_running_loop()
                calls.append(name)
            except RuntimeError:
                pass  # a worker thread: allowed
            with original(*args, **kwargs) as store:
                yield store

        return wrapped

    original_scoped = scoped_store.open_scoped_store
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("anum_api") and getattr(module, "open_scoped_store", None) is original_scoped:
            monkeypatch.setattr(module, "open_scoped_store", guard("open_scoped_store", original_scoped))
    monkeypatch.setattr(dependencies, "_unit_of_work", guard("_unit_of_work", dependencies._unit_of_work))
    monkeypatch.setattr(file_store, "storage", files.InMemoryObjectStorage())
    file_store.clear()
    memory_retrieval_store.clear()
    file_policy.set_file_scanner(None)
    yield calls
    file_store.clear()
    memory_retrieval_store.clear()


def test_plain_def_routes_run_in_the_thread_pool(on_loop: list[str]) -> None:
    assert client.get("/api/v1/favorites", headers=HEADERS).status_code == 200
    assert client.get("/api/v1/calendar/events", headers=HEADERS).status_code == 200
    assert on_loop == [], f"opened on the event loop: {on_loop}"


def test_file_upload_and_new_version_store_and_index_off_the_loop(on_loop: list[str]) -> None:
    uploaded = client.post("/api/v1/files", params={"name": "plan.md"}, headers=HEADERS, content=b"# Plan\nship it")
    assert uploaded.status_code == 201, uploaded.text
    version = client.post(
        f"/api/v1/files/{uploaded.json()['id']}/versions", headers={**HEADERS, "content-type": "text/plain"}, content=b"# Plan\nship it today"
    )
    assert version.status_code == 201, version.text
    assert on_loop == [], f"opened on the event loop: {on_loop}"


def test_knowledge_notes_and_reindex_run_off_the_loop(on_loop: list[str]) -> None:
    collection = client.post("/api/v1/knowledge/collections", headers=HEADERS, json={"name": "Handbook"})
    assert collection.status_code == 201, collection.text
    collection_id = collection.json()["id"]
    note = client.post(
        f"/api/v1/knowledge/collections/{collection_id}/notes", headers=HEADERS, json={"title": "Leave", "content": "Ask a day ahead."}
    )
    assert note.status_code == 201, note.text
    edited = client.patch(f"/api/v1/knowledge/items/{note.json()['id']}", headers=HEADERS, json={"content": "Ask two days ahead."})
    assert edited.status_code == 200, edited.text
    assert edited.json()["content"] == "Ask two days ahead."
    reindexed = client.post(f"/api/v1/knowledge/collections/{collection_id}/reindex", headers=HEADERS)
    assert reindexed.status_code == 200, reindexed.text
    assert reindexed.json()["reused"] == 1, reindexed.json()
    workspace = client.post("/api/v1/retrieval/index", headers=HEADERS)
    assert workspace.status_code == 200, workspace.text
    assert on_loop == [], f"opened on the event loop: {on_loop}"


def test_home_sources_run_in_worker_threads(on_loop: list[str]) -> None:
    assert client.get("/api/v1/home/summary", headers=HEADERS).status_code == 200
    assert client.get("/api/v1/home/insights", headers=HEADERS).status_code == 200
    assert on_loop == [], f"opened on the event loop: {on_loop}"


def test_one_email_and_one_lead_list_read_off_the_loop(on_loop: list[str]) -> None:
    """The unified mailbox list and All inboxes (docs/email.md#one-email-module) and the WhatsApp
    leads waiting for the lead list (docs/business.md#whatsapp-leads)."""
    assert client.get("/api/v1/email/unified/mailboxes", headers=HEADERS).status_code == 200
    assert client.get("/api/v1/email/unified/messages", headers=HEADERS).status_code == 200
    assert client.get("/api/v1/business/lead-sources/whatsapp", headers=HEADERS).status_code == 200
    assert client.get("/api/v1/business/lead-duplicates", headers=HEADERS).status_code == 200
    assert on_loop == [], f"opened on the event loop: {on_loop}"
