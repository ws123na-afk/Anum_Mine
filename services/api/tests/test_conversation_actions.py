"""Message actions and conversation management (docs/conversations.md#message-actions).

Memory backend: rename, pin, search, keyset paging, delete one message, rate an answer,
regenerate and edit-and-resend, each private to its user, workspace and tenant.
"""

import json
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anum_api.conversations import MAX_PINNED_CONVERSATIONS, conversation_store, router
from anum_api.learning_api import router as learning_router
from anum_api.learning_store import learning_data
from anum_api.schemas import utc_now
from anum_api.user_preferences import user_preference_store

app = FastAPI()
app.include_router(router)
app.include_router(learning_router)
client = TestClient(app)
BASE = "/api/v1/conversations"


def headers(user: str = "user_act", tenant: str = "tenant_act", workspace: str = "workspace_act", role: str = "member") -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


H = headers()
OTHERS = (headers(user="user_other"), headers(tenant="tenant_other"), headers(workspace="workspace_other"))


def setup_function() -> None:
    conversation_store.clear()
    learning_data.clear()
    user_preference_store.clear()


def frames(response) -> list[tuple[str, dict]]:  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    out = []
    for frame in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def new(hdrs: dict[str, str] = H, title: str | None = None) -> str:
    response = client.post(BASE, headers=hdrs, json={"title": title} if title else {})
    assert response.status_code == 201
    return response.json()["id"]


def ask(conversation_id: str, body: dict, hdrs: dict[str, str] = H):  # type: ignore[no-untyped-def]
    return client.post(f"{BASE}/{conversation_id}/ask", headers=hdrs, json=body)


def turns(conversation_id: str) -> list[dict]:
    return client.get(f"{BASE}/{conversation_id}", headers=H).json()["turns"]


def test_rename_sets_the_title_and_keeps_it_after_later_questions() -> None:
    conversation_id = new()
    frames(ask(conversation_id, {"content": "first question"}))
    renamed = client.patch(f"{BASE}/{conversation_id}", headers=H, json={"title": "  Quarterly   plan  "})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Quarterly plan"
    frames(ask(conversation_id, {"content": "second question"}))
    assert client.get(f"{BASE}/{conversation_id}", headers=H).json()["title"] == "Quarterly plan"
    for body in ({}, {"title": "   "}, {"title": "x" * 81}, {"color": "red"}):
        assert client.patch(f"{BASE}/{conversation_id}", headers=H, json=body).status_code == 422


def test_pinned_conversations_lead_the_list_and_the_number_of_pins_is_capped() -> None:
    older, newer = new(title="older"), new(title="newer")
    frames(ask(newer, {"content": "bump"}))
    assert [c["id"] for c in client.get(BASE, headers=H).json()] == [newer, older]
    pinned = client.patch(f"{BASE}/{older}", headers=H, json={"pinned": True}).json()
    assert pinned["pinned"] is True and pinned["pinned_at"]
    assert [c["id"] for c in client.get(BASE, headers=H).json()] == [older, newer]
    assert client.patch(f"{BASE}/{older}", headers=H, json={"pinned": False}).json()["pinned"] is False

    for _ in range(MAX_PINNED_CONVERSATIONS):
        assert client.patch(f"{BASE}/{new()}", headers=H, json={"pinned": True}).status_code == 200
    refused = client.patch(f"{BASE}/{older}", headers=H, json={"pinned": True})
    assert refused.status_code == 409 and "Unpin one first" in refused.json()["detail"]


def test_search_matches_titles_and_messages_case_insensitively_and_literally() -> None:
    budget = new(title="Budget review")
    lanterns = new()
    frames(ask(lanterns, {"content": "Which LANTERNS look best?"}))
    percent = new(title="100% done")
    assert [c["id"] for c in client.get(BASE, headers=H, params={"q": "budget"}).json()] == [budget]
    assert [c["id"] for c in client.get(BASE, headers=H, params={"q": "lanterns look"}).json()] == [lanterns]
    # The reply text is searchable too, and LIKE wildcards are matched literally.
    assert [c["id"] for c in client.get(BASE, headers=H, params={"q": "mock reply"}).json()] == [lanterns]
    assert [c["id"] for c in client.get(BASE, headers=H, params={"q": "100%"}).json()] == [percent]
    assert [c["id"] for c in client.get(BASE, headers=H, params={"q": "%"}).json()] == [percent]
    assert client.get(BASE, headers=H, params={"q": "nothing like this"}).json() == []
    assert client.get(BASE, headers=H, params={"q": "x" * 201}).status_code == 422


def test_keyset_paging_walks_every_conversation_once_with_pins_on_the_first_page() -> None:
    ids = [new(title=f"chat {index}") for index in range(7)]
    # Distinct activity times, newest last created.
    for offset, conversation_id in enumerate(ids):
        conversation_store.conversations[conversation_id].updated_at = utc_now() - timedelta(minutes=100 - offset)
    client.patch(f"{BASE}/{ids[0]}", headers=H, json={"pinned": True})
    first = client.get(BASE, headers=H, params={"limit": 3}).json()
    assert first[0]["id"] == ids[0] and len(first) == 4
    seen = [c["id"] for c in first]
    last = first[-1]
    while True:
        page = client.get(
            BASE, headers=H, params={"limit": 3, "before_updated_at": last["updated_at"], "before_id": last["id"]}
        ).json()
        assert all(not c["pinned"] for c in page)
        seen += [c["id"] for c in page]
        if len(page) < 3:
            break
        last = page[-1]
    assert sorted(seen) == sorted(ids) and len(seen) == len(set(seen))
    assert client.get(BASE, headers=H, params={"before_id": "x"}).status_code == 422
    assert client.get(BASE, headers=H, params={"limit": 101}).status_code == 422


def test_deleting_a_question_removes_its_answers_and_an_answer_goes_alone() -> None:
    conversation_id = new()
    first = frames(ask(conversation_id, {"content": "one"}))
    frames(ask(conversation_id, {"content": "two"}))
    stored = turns(conversation_id)
    assert client.delete(f"{BASE}/{conversation_id}/turns/{first[0][1]['user_turn']['id']}", headers=H).status_code == 204
    assert [t["content"][:3] for t in turns(conversation_id)] == ["two", "Moc"]
    assert client.delete(f"{BASE}/{conversation_id}/turns/{stored[3]['id']}", headers=H).status_code == 204
    assert [t["content"] for t in turns(conversation_id)] == ["two"]
    assert client.delete(f"{BASE}/{conversation_id}/turns/turn_missing", headers=H).status_code == 404


def test_rating_an_answer_is_stored_on_the_turn_and_can_be_cleared() -> None:
    conversation_id = new()
    done = frames(ask(conversation_id, {"content": "rate me"}))
    answer, question = done[-1][1]["assistant_turn"]["id"], done[0][1]["user_turn"]["id"]
    url = f"{BASE}/{conversation_id}/turns/{answer}/feedback"
    rated = client.put(url, headers=H, json={"rating": "down", "reason": "  Too   long "})
    assert rated.status_code == 200, rated.text
    assert rated.json()["turn"]["feedback"] == "down" and rated.json()["turn"]["feedback_reason"] == "Too long"
    # Learning is on: the rating is also a learning signal, as POST /learning/feedback records it.
    assert rated.json()["learning_recorded"] is True
    ratings = client.get("/api/v1/learning/ratings", headers=H, params={"surface": "chat", "subject_id": answer}).json()
    assert [(r["subject_id"], r["rating"]) for r in ratings["ratings"]] == [(answer, "down")]
    assert turns(conversation_id)[1]["feedback"] == "down"
    # With learning off the rating is still stored on the answer; the reply says it was not learned from.
    assert client.put("/api/v1/learning/settings", headers=H, json={"enabled": False}).status_code == 200
    off = client.put(url, headers=H, json={"rating": "up"}).json()
    assert off["turn"]["feedback"] == "up" and off["learning_recorded"] is False and off["learning_message"]
    cleared = client.put(url, headers=H, json={"rating": None}).json()["turn"]
    assert cleared["feedback"] is None and cleared["feedback_reason"] is None
    assert client.put(f"{BASE}/{conversation_id}/turns/{question}/feedback", headers=H, json={"rating": "up"}).status_code == 409
    assert client.put(url, headers=H, json={"rating": "meh"}).status_code == 422
    assert client.put(url, headers=H, json={"rating": "up", "reason": "x" * 501}).status_code == 422


def test_regenerate_replaces_the_latest_answer() -> None:
    conversation_id = new()
    first = frames(ask(conversation_id, {"content": "explain"}))
    question = first[0][1]["user_turn"]["id"]
    again = frames(ask(conversation_id, {"retry_turn_id": question}))
    assert again[0][1]["user_turn"]["id"] == question and again[-1][0] == "done"
    stored = turns(conversation_id)
    assert [t["role"] for t in stored] == ["user", "assistant"]
    assert stored[1]["id"] == again[-1][1]["assistant_turn"]["id"] != first[-1][1]["assistant_turn"]["id"]


def test_edit_and_resend_replaces_the_question_and_everything_after_it() -> None:
    conversation_id = new()
    one = frames(ask(conversation_id, {"content": "first wording"}))
    frames(ask(conversation_id, {"content": "follow-up"}))
    edited = frames(ask(conversation_id, {"edit_turn_id": one[0][1]["user_turn"]["id"], "content": "better wording"}))
    assert edited[0][1]["user_turn"]["content"] == "better wording" and edited[-1][0] == "done"
    stored = turns(conversation_id)
    assert [t["content"][:14] for t in stored] == ["better wording", "Mock reply to:"]
    # The automatic title follows the edited first question.
    assert client.get(f"{BASE}/{conversation_id}", headers=H).json()["title"] == "better wording"
    # Only a stored question of this conversation can be edited; the new text is required.
    assert ask(conversation_id, {"edit_turn_id": stored[1]["id"], "content": "x"}).status_code == 409
    assert ask(conversation_id, {"edit_turn_id": "turn_missing", "content": "x"}).status_code == 404
    assert ask(conversation_id, {"edit_turn_id": stored[0]["id"]}).status_code == 422
    assert ask(conversation_id, {"edit_turn_id": stored[0]["id"], "retry_turn_id": stored[0]["id"], "content": "x"}).status_code == 422


def test_a_renamed_title_survives_editing_the_first_question() -> None:
    conversation_id = new()
    one = frames(ask(conversation_id, {"content": "original"}))
    client.patch(f"{BASE}/{conversation_id}", headers=H, json={"title": "My name"})
    frames(ask(conversation_id, {"edit_turn_id": one[0][1]["user_turn"]["id"], "content": "changed"}))
    assert client.get(f"{BASE}/{conversation_id}", headers=H).json()["title"] == "My name"


@pytest.mark.parametrize("other", OTHERS)
def test_every_action_is_private_to_its_user_workspace_and_tenant(other: dict[str, str]) -> None:
    conversation_id = new(title="mine")
    done = frames(ask(conversation_id, {"content": "secret"}))
    question, answer = done[0][1]["user_turn"]["id"], done[-1][1]["assistant_turn"]["id"]
    assert client.patch(f"{BASE}/{conversation_id}", headers=other, json={"title": "stolen"}).status_code == 404
    assert client.patch(f"{BASE}/{conversation_id}", headers=other, json={"pinned": True}).status_code == 404
    assert client.delete(f"{BASE}/{conversation_id}/turns/{question}", headers=other).status_code == 404
    assert client.put(f"{BASE}/{conversation_id}/turns/{answer}/feedback", headers=other, json={"rating": "up"}).status_code == 404
    assert ask(conversation_id, {"edit_turn_id": question, "content": "x"}, other).status_code == 404
    assert client.get(BASE, headers=other, params={"q": "secret"}).json() == []
    detail = client.get(f"{BASE}/{conversation_id}", headers=H).json()
    assert detail["title"] == "mine" and detail["pinned"] is False
    assert [t["feedback"] for t in detail["turns"]] == [None, None] and len(detail["turns"]) == 2


def test_viewers_can_read_but_not_change_conversations() -> None:
    conversation_id = new()
    viewer = headers(role="viewer")
    assert client.get(BASE, headers=viewer).status_code == 200
    assert client.patch(f"{BASE}/{conversation_id}", headers=viewer, json={"title": "x"}).status_code == 403
    assert client.delete(f"{BASE}/{conversation_id}/turns/t", headers=viewer).status_code == 403
    assert client.put(f"{BASE}/{conversation_id}/turns/t/feedback", headers=viewer, json={"rating": "up"}).status_code == 403
