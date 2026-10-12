"""What ANUM learned (docs/learning.md): the pure learning logic and the API, memory backend."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from anum_api import automation as automation_module
from anum_api.automation import LocalAutomationEngine
from anum_api.conversations import build_messages, conversation_store
from anum_api.learning import (
    Activity,
    LearnedPreference,
    LearningProfile,
    LearningSignal,
    RunFailure,
    SignalKind,
    StyleOverrides,
    Surface,
    activity_key,
    derive_style,
    detect_language,
    detect_routines,
    effective_style,
    improvement_suggestions,
    iso_week,
    render_learned_block,
    summarize_edit,
)
from anum_api.learning_store import learned_instructions, learning_data, new_signal, open_learning_store
from anum_api.main import app, store
from anum_api.schemas import Task, TaskStatus, TenantContext
from anum_api.settings import settings
from anum_api.user_preferences import user_preference_store

NOW = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
TENANT, WORKSPACE = "tenant_learn", "workspace_learn"
RIYADH = ZoneInfo("Asia/Riyadh")


def headers(user: str = "user_learn", role: str = "member", workspace: str = WORKSPACE) -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


def ctx(user: str = "user_learn", role: str = "member") -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, roles=[role])


@pytest.fixture(autouse=True)
def _clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    learning_data.clear()
    user_preference_store.clear()
    conversation_store.clear()
    for collection in (store.tasks, store.runs, store.approvals, store.approval_approvers):
        collection.clear()
    store.audit_records.clear()
    monkeypatch.setattr(automation_module, "engine", LocalAutomationEngine(str(tmp_path / "automation.db")))
    yield
    learning_data.clear()
    user_preference_store.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def signal(kind: SignalKind, surface: Surface = Surface.EMAIL, *, at: datetime = NOW, **values: object) -> LearningSignal:
    original, final = values.pop("original", None), values.pop("final", None)
    metadata = dict(values.pop("metadata", {}) or {})  # type: ignore[arg-type]
    if original is not None and final is not None:
        metadata["edit"] = summarize_edit(str(original), str(final)).model_dump()
    return new_signal(kind, surface, original=original, final=final, metadata=metadata, now=at, **values)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- text analysis


def test_language_detection_tells_arabic_english_and_mixed_apart() -> None:
    assert detect_language("مرحبا، طلبك جاهز للشحن اليوم") == "ar"
    assert detect_language("Hello, your order ships today") == "en"
    assert detect_language("Hello مرحبا order طلب") == "mixed"
    assert detect_language("12 34 !!") is None


def test_an_edit_summary_counts_words_and_sees_greeting_and_sign_off_changes() -> None:
    edit = summarize_edit(
        "Hello Ahmed,\nYour tyres will arrive on Sunday afternoon as we planned before.\nThanks",
        "Dear Ahmed,\nYour tyres arrive Sunday.\nBest regards",
    )
    assert edit.words_before == 14 and edit.words_after == 8
    assert edit.words_removed > edit.words_added
    assert 0 < edit.changed_ratio < 1
    assert (edit.greeting_before, edit.greeting_after) == ("Hello", "Dear")
    assert (edit.sign_off_before, edit.sign_off_after) == ("Thanks", "Best regards")


def test_style_is_derived_from_what_the_user_sent_overall_and_per_contact() -> None:
    drafts = [
        signal(SignalKind.DRAFT_EDITED, original="Hi Sara, the order is late, sorry about that, we will fix it soon!", final="Dear Sara,\nKindly note the order ships tomorrow.\nBest regards", contact="sara@example.com"),
        signal(SignalKind.DRAFT_EDITED, original="Hey team, can you check the stock levels for all sizes today please", final="Dear team,\nPlease find the stock levels attached.\nBest regards", contact="team@example.com"),
        signal(SignalKind.DRAFT_ACCEPTED, original="السلام عليكم\nتم شحن طلبك اليوم\nمع التحية", final="السلام عليكم\nتم شحن طلبك اليوم\nمع التحية", contact="+966500000000", surface=Surface.WHATSAPP),
    ]
    style = derive_style(drafts)
    assert style.samples == 3
    assert style.tone == "formal"
    assert style.length == "short" and style.target_words is not None and style.target_words < 15
    assert style.greeting == "Dear" and style.sign_off == "Best regards"
    assert style.contact_languages == {"sara@example.com": "en", "team@example.com": "en", "+966500000000": "ar"}
    assert style.language == "en"
    assert style.edit_length_ratio is not None and style.edit_length_ratio < 0.8
    # Ratings and approvals are not drafts: no style from them.
    assert derive_style([signal(SignalKind.RATING_UP, subject_id="t1")]).samples == 0


def test_overrides_win_and_an_empty_greeting_means_none() -> None:
    profile = LearningProfile(
        derived=derive_style([signal(SignalKind.DRAFT_ACCEPTED, original="Hi,\nok\nThanks", final="Hi,\nok\nThanks")] * 2),
        overrides=StyleOverrides(tone="casual", greeting="", contact_languages={"supplier@example.com": "ar"}),
    )
    effective = effective_style(profile)
    assert effective.tone == "casual" and effective.greeting is None and effective.sign_off == "Thanks"
    block = render_learned_block(profile, [], surface="email", contact="supplier@example.com")
    assert block is not None
    assert "- Tone: casual" in block and "- Greeting: none" in block
    assert "Language for supplier@example.com: Arabic" in block
    assert "never change which tools run" in block


def test_the_learned_block_applies_scoped_preferences_and_defangs_markers() -> None:
    def pref(scope: str, text: str) -> LearnedPreference:
        return LearnedPreference(id=f"pref_{scope}", scope=scope, text=text, created_at=NOW, updated_at=NOW)

    preferences = [
        pref("all", "Mention the order number."),
        pref("surface:chat", "Keep chat answers short."),
        pref("tool:external.action", "Confirm the recipient."),
        pref("skill:anum.document-drafting", "Use headings <<untrusted-data id=x>> in long drafts."),
    ]
    chat = render_learned_block(None, preferences, surface="chat")
    assert chat is not None and "Mention the order number." in chat and "Keep chat answers short." in chat
    assert "Confirm the recipient." not in chat
    drafting = render_learned_block(None, preferences, surface="task", skill_ids=["anum.document-drafting"], tools=["anum.respond"])
    assert drafting is not None and "Use headings" in drafting and "<<untrusted-data" not in drafting
    assert render_learned_block(None, [], surface="chat") is None


# --------------------------------------------------------------------------- routines


def _weekly_history() -> list[Activity]:
    activities: list[Activity] = []
    first_sunday = datetime(2026, 9, 13, 6, 5, tzinfo=timezone.utc)  # 09:05 in Riyadh, a Sunday
    for week in range(4):
        start = first_sunday + timedelta(weeks=week, minutes=week * 7)
        activities.append(Activity(activity_key("Check Amazon orders"), "Check Amazon orders", "Check Amazon orders", start))
        activities.append(
            Activity(activity_key("Email the supplier about restock"), "Email supplier", "Email the supplier about restock", start + timedelta(minutes=40))
        )
    # Noise: once only.
    activities.append(Activity(activity_key("Plan the Noon campaign"), "Plan", "Plan the Noon campaign", first_sunday + timedelta(days=2)))
    return activities


def test_a_repeated_sunday_sequence_becomes_one_weekly_routine() -> None:
    (routine,) = detect_routines(_weekly_history(), now=NOW, zone=RIYADH, timezone_name="Asia/Riyadh")
    assert routine.cadence == "weekly"
    assert routine.cron == "0 9 * * 0"
    assert routine.timezone == "Asia/Riyadh"
    assert [step.prompt for step in routine.steps] == ["Check Amazon orders", "Email the supplier about restock"]
    assert routine.title.startswith("Every Sunday around 09:00: Check Amazon orders, then Email the supplier")
    assert routine.occurrences == 4 and 0 < routine.confidence <= 1


def test_a_daily_habit_is_detected_and_too_little_evidence_is_not() -> None:
    daily = [
        Activity(activity_key("Review Noon prices"), "Review Noon prices", "Review Noon prices", NOW - timedelta(days=day, hours=1))
        for day in range(1, 7)
    ]
    (routine,) = detect_routines(daily, now=NOW, zone=timezone.utc)
    assert routine.cadence == "daily" and routine.cron.startswith("0 8 * * ")
    assert detect_routines(daily[:3], now=NOW, zone=timezone.utc) == []
    assert activity_key("Check the Amazon orders today please") == activity_key("check amazon orders")


# --------------------------------------------------------------------------- suggestions


def test_suggestions_turn_failures_rejections_ratings_and_edits_into_concrete_changes() -> None:
    week = [
        signal(SignalKind.APPROVAL_REJECTED, Surface.APPROVAL, tool="external.action", reason="Wrong customer", metadata={"risk_level": "high"}),
        signal(SignalKind.APPROVAL_REJECTED, Surface.APPROVAL, tool="external.action", reason="Wrong customer", metadata={"risk_level": "high"}),
        signal(SignalKind.RATING_DOWN, Surface.CHAT, subject_id="t1", reason="Too long"),
        signal(SignalKind.RETRY, Surface.CHAT, subject_id="t2"),
        *[
            signal(SignalKind.DRAFT_EDITED, Surface.EMAIL, original="Hello there, " + "this is a long draft that rambles " * 3, final="Dear Ali,\nShort reply.")
            for _ in range(3)
        ],
    ]
    failures = [RunFailure("web_search", ("anum.web-search",), "Search backend timed out")] * 2
    drafts = improvement_suggestions(week, week, failures, medium_risk_requires_approval=False)
    rules = {draft.rule: draft for draft in drafts}
    assert set(rules) == {"tool_failures", "approval_rejections", "low_ratings", "heavy_edits"}
    assert rules["tool_failures"].change == {
        "type": "preference", "scope": "tool:web_search",
        "text": "Before using web_search, confirm the inputs it needs and ask me for anything missing instead of guessing.",
    }
    assert '"Wrong customer"' in rules["approval_rejections"].change["text"]
    assert rules["low_ratings"].change["text"].startswith("Keep chat answers short")
    assert rules["heavy_edits"].change["scope"] == "surface:email"
    assert "shorter" in rules["heavy_edits"].change["text"] and '"Dear"' in rules["heavy_edits"].change["text"]
    assert all(draft.kind.value in {"instruction", "template"} for draft in drafts)


def test_autonomy_suggestions_loosen_after_clean_approvals_and_tighten_after_failures() -> None:
    approved = [signal(SignalKind.APPROVAL_APPROVED, Surface.APPROVAL, tool="crm.update", metadata={"risk_level": "medium"}) for _ in range(5)]
    (loosen,) = improvement_suggestions([], approved, [], medium_risk_requires_approval=True)
    assert loosen.kind.value == "autonomy" and loosen.change == {"type": "approval_policy", "medium_risk_requires_approval": False}
    assert improvement_suggestions([], approved[:4], [], medium_risk_requires_approval=True) == []
    rejected = [*approved, signal(SignalKind.APPROVAL_REJECTED, Surface.APPROVAL, tool="crm.update", metadata={"risk_level": "medium"})]
    assert [d.rule for d in improvement_suggestions([], rejected, [], medium_risk_requires_approval=True)] == []
    failures = [RunFailure("crm.update", (), "Rejected by CRM", risk_level="medium")] * 2
    rules = [d.rule for d in improvement_suggestions([], [], failures, medium_risk_requires_approval=False)]
    assert rules == ["tool_failures", "autonomy_tighten"]


# --------------------------------------------------------------------------- API: switch and feedback


def test_overview_defaults_on_and_the_off_switch_stops_new_signals(client: TestClient) -> None:
    overview = client.get("/api/v1/learning", headers=headers())
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["state"] == "on" and body["profile"]["enabled"] is True
    assert body["can_manage_automations"] is True and body["can_apply_autonomy"] is False

    off = client.put("/api/v1/learning/settings", headers=headers(), json={"enabled": False})
    assert off.status_code == 200 and off.json()["state"] == "off_by_user"
    rated = client.post("/api/v1/learning/feedback", headers=headers(), json={"surface": "chat", "subject_id": "turn_1", "rating": "up"})
    assert rated.json() == {
        "recorded": False, "state": "off_by_user", "signal": None,
        "message": "Learning is off. Turn on Learn from my activity to use this.",
    }
    assert client.get("/api/v1/learning/signals", headers=headers()).json()["total"] == 0
    assert any(record.action == "learning.settings_changed" for record in store.audit_records)
    # Only the caller's own switch changed.
    assert client.get("/api/v1/learning", headers=headers("user_other")).json()["state"] == "on"


def test_remember_things_from_my_chats_off_also_stops_learning(client: TestClient) -> None:
    assert client.put("/api/v1/me/preferences", headers=headers(), json={"remember_conversations": False}).status_code == 200
    assert client.get("/api/v1/learning", headers=headers()).json()["state"] == "off_chat_memory"
    assert learned_instructions(ctx(), surface="chat") is None


def test_the_server_switch_wins(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "learning_enabled", False)
    assert client.get("/api/v1/learning", headers=headers()).json()["state"] == "off_on_server"
    detect = client.post("/api/v1/learning/routines/detect", headers=headers(), json={"timezone": "UTC"})
    assert detect.status_code == 409 and "ANUM_LEARNING_ENABLED" in detect.json()["error"]["message"]


def test_a_new_rating_replaces_the_last_and_ratings_can_be_read_back(client: TestClient) -> None:
    up = client.post("/api/v1/learning/feedback", headers=headers(), json={"surface": "chat", "subject_id": "turn_1", "rating": "up"})
    assert up.status_code == 200 and up.json()["recorded"] is True and up.json()["signal"]["kind"] == "rating_up"
    down = client.post(
        "/api/v1/learning/feedback", headers=headers(), json={"surface": "chat", "subject_id": "turn_1", "rating": "down", "reason": "Too long"}
    )
    assert down.json()["signal"]["kind"] == "rating_down"
    ratings = client.get("/api/v1/learning/ratings", headers=headers(), params={"surface": "chat", "subject_id": ["turn_1", "turn_2"]})
    assert ratings.json()["ratings"] == [
        {"subject_id": "turn_1", "rating": "down", "signal_id": down.json()["signal"]["id"], "reason": "Too long"}
    ]
    assert client.get("/api/v1/learning/signals", headers=headers()).json()["total"] == 1
    # A viewer can rate too: it is their own data.
    viewer = client.post("/api/v1/learning/feedback", headers=headers("user_viewer", "viewer"), json={"surface": "chat", "subject_id": "turn_9", "rating": "up"})
    assert viewer.json()["recorded"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"surface": "chat", "rating": "up"},
        {"surface": "chat", "subject_id": "t", "rating": "up", "original": "a", "final": "b"},
        {"surface": "email", "original": "only the draft"},
        {"surface": "approval", "subject_id": "a", "rating": "up"},
        {"surface": "email", "original": "a", "final": "b", "user_id": "someone_else"},
    ],
)
def test_malformed_feedback_is_refused(client: TestClient, body: dict[str, object]) -> None:
    assert client.post("/api/v1/learning/feedback", headers=headers(), json=body).status_code == 422


def test_edited_drafts_build_the_style_profile_which_the_user_can_edit_and_reset(client: TestClient) -> None:
    for name in ("Sara", "Omar"):
        sent = client.post(
            "/api/v1/learning/feedback",
            headers=headers(),
            json={
                "surface": "email", "contact": f"{name.lower()}@example.com",
                "original": f"Hello {name}, here is a long update about the tyre order that we discussed earlier today.",
                "final": f"Dear {name},\nYour order ships tomorrow.\nBest regards",
            },
        )
        assert sent.json()["signal"]["kind"] == "draft_edited"
        assert sent.json()["signal"]["metadata"]["edit"]["greeting_after"] == "Dear"
    profile = client.get("/api/v1/learning/profile", headers=headers()).json()
    assert profile["derived"]["greeting"] == "Dear" and profile["derived"]["sign_off"] == "Best regards"
    assert profile["effective"]["tone"] == "formal"

    edited = client.put("/api/v1/learning/profile", headers=headers(), json={"tone": "casual", "sign_off": "", "notes": "Use first names."})
    assert edited.status_code == 200
    assert edited.json()["effective"]["tone"] == "casual" and edited.json()["effective"]["sign_off"] is None
    block = learned_instructions(ctx(), surface="email", contact="omar@example.com")
    assert block is not None and "Sign-off: none" in block and "Use first names." in block and "Language for omar@example.com: English" in block
    assert client.put("/api/v1/learning/profile", headers=headers(), json={"tone": "angry"}).status_code == 422

    assert client.post("/api/v1/learning/profile/reset", headers=headers(), json={}).status_code == 422
    reset = client.post("/api/v1/learning/profile/reset", headers=headers(), json={"confirm": True})
    assert reset.status_code == 200 and reset.json()["signals_deleted"] == 2
    assert reset.json()["profile"]["derived"]["samples"] == 0 and reset.json()["profile"]["overrides"] == {
        "tone": None, "length": None, "greeting": None, "sign_off": None, "language": None, "contact_languages": None, "notes": None,
    }
    assert any(record.action == "learning.profile_reset" for record in store.audit_records)


def test_signals_page_search_preview_and_delete_only_my_own(client: TestClient) -> None:
    long_text = "Dear Ali,\n" + "word " * 400
    for index in range(30):
        client.post("/api/v1/learning/feedback", headers=headers(), json={"surface": "chat", "subject_id": f"turn_{index}", "rating": "up"})
    client.post("/api/v1/learning/feedback", headers=headers(), json={"surface": "email", "original": "Hi Ali", "final": long_text, "contact": "ali@example.com"})
    page = client.get("/api/v1/learning/signals", headers=headers(), params={"limit": 10, "offset": 10}).json()
    assert page["total"] == 31 and len(page["items"]) == 10 and page["offset"] == 10
    found = client.get("/api/v1/learning/signals", headers=headers(), params={"q": "ali@"}).json()
    (draft,) = found["items"]
    assert draft["truncated"] is True and len(draft["final_text"]) == 500
    full = client.get(f"/api/v1/learning/signals/{draft['id']}", headers=headers()).json()
    assert full["final_text"] == long_text and full["truncated"] is False
    assert client.get("/api/v1/learning/signals", headers=headers(), params={"kind": "draft_edited"}).json()["total"] == 1

    assert client.delete(f"/api/v1/learning/signals/{draft['id']}", headers=headers("user_other")).status_code == 404
    assert client.get(f"/api/v1/learning/signals/{draft['id']}", headers=headers("user_other")).status_code == 404
    assert client.delete(f"/api/v1/learning/signals/{draft['id']}", headers=headers()).status_code == 204
    assert client.get("/api/v1/learning/profile", headers=headers()).json()["derived"]["samples"] == 0


def test_learned_preferences_crud_search_and_isolation(client: TestClient) -> None:
    created = client.post("/api/v1/learning/preferences", headers=headers(), json={"scope": "surface:whatsapp", "text": "  Reply in Arabic to customers.  "})
    assert created.status_code == 201 and created.json()["text"] == "Reply in Arabic to customers."
    preference_id = created.json()["id"]
    for bad in ({"scope": "surface:fax", "text": "x"}, {"scope": "everything", "text": "x"}, {"text": "   "}, {"text": "x" * 301}):
        assert client.post("/api/v1/learning/preferences", headers=headers(), json=bad).status_code == 422
    client.post("/api/v1/learning/preferences", headers=headers(), json={"text": "Mention the order number."})
    assert client.get("/api/v1/learning/preferences", headers=headers(), params={"q": "arabic"}).json()["total"] == 1
    updated = client.put(f"/api/v1/learning/preferences/{preference_id}", headers=headers(), json={"scope": "contact:+966500000000", "text": "Reply in Arabic."})
    assert updated.json()["scope"] == "contact:+966500000000"
    assert client.put(f"/api/v1/learning/preferences/{preference_id}", headers=headers("user_other"), json={"text": "Hijack"}).status_code == 404
    assert client.get("/api/v1/learning/preferences", headers=headers("user_other")).json()["total"] == 0
    assert client.delete(f"/api/v1/learning/preferences/{preference_id}", headers=headers("user_other")).status_code == 404
    assert client.delete(f"/api/v1/learning/preferences/{preference_id}", headers=headers()).status_code == 204
    assert client.get("/api/v1/learning/preferences", headers=headers()).json()["total"] == 1


def test_learning_needs_an_active_workspace_role(client: TestClient) -> None:
    response = client.get("/api/v1/learning", headers={**headers(), "x-user-roles": "stranger"})
    assert response.status_code == 403


# --------------------------------------------------------------------------- the learned block in prompts


def test_the_learned_block_reaches_the_chat_system_prompt_after_the_base_prompt() -> None:
    with open_learning_store(ctx()) as learning:
        learning.save_preference(LearnedPreference(id="pref_1", scope="surface:chat", text="Answer in Arabic.", created_at=NOW, updated_at=NOW))
    block = learned_instructions(ctx(), surface="chat")
    assert block is not None and "Answer in Arabic." in block
    messages = build_messages([], "What is left?", learned_block=block)
    assert messages[0]["role"] == "system" and messages[0]["content"].endswith("- Answer in Arabic.")
    assert build_messages([], "What is left?")[0]["content"] != messages[0]["content"]
    # Another user's preferences never reach my prompt.
    assert learned_instructions(ctx("user_other"), surface="chat") is None


def test_drafting_tasks_carry_the_learned_block_and_say_so_on_the_run(client: TestClient) -> None:
    client.post("/api/v1/learning/preferences", headers=headers(), json={"text": "Sign every draft as Wael."})
    task = client.post("/api/v1/tasks", headers=headers(), json={"title": "Draft", "prompt": "Draft a reply to the supplier"}).json()
    run = client.post(f"/api/v1/tasks/{task['id']}/run", headers=headers()).json()["run"]
    model_step = next(step for step in run["steps"] if step["type"] == "model_call")
    assert model_step["metadata"]["learned_preferences"] is True
    other = client.post("/api/v1/tasks", headers=headers(), json={"title": "Plan", "prompt": "Plan the week"}).json()
    run = client.post(f"/api/v1/tasks/{other['id']}/run", headers=headers()).json()["run"]
    assert next(step for step in run["steps"] if step["type"] == "model_call")["metadata"]["learned_preferences"] is False


def test_chat_retries_and_approval_decisions_become_signals(client: TestClient) -> None:
    conversation = client.post("/api/v1/conversations", headers=headers(), json={}).json()["id"]
    asked = client.post(f"/api/v1/conversations/{conversation}/ask", headers=headers(), json={"content": "Where is order 42?"})
    turn = next(json.loads(line[6:]) for line in asked.text.splitlines() if line.startswith("data: ") and "user_turn" in line)
    retried = client.post(
        f"/api/v1/conversations/{conversation}/ask", headers=headers(), json={"retry_turn_id": turn["user_turn"]["id"]}
    )
    assert retried.status_code == 200
    owner = headers("user_owner", "owner")
    for verdict in ("approve", "reject"):
        task = client.post("/api/v1/tasks", headers=owner, json={"title": "Send", "prompt": "Send the invoice to the customer"}).json()
        approval = client.post(f"/api/v1/tasks/{task['id']}/run", headers=owner).json()["approval"]
        decided = client.post(
            f"/api/v1/approvals/{approval['id']}/{verdict}",
            headers=owner,
            json={"payload_hash": approval["payload_hash"], "reason": "Wrong amount" if verdict == "reject" else None},
        )
        assert decided.status_code == 200, decided.text
    mine = client.get("/api/v1/learning/signals", headers=headers(), params={"kind": "retry"}).json()["items"]
    assert [(item["surface"], item["subject_id"]) for item in mine] == [("chat", turn["user_turn"]["id"])]
    decisions = client.get("/api/v1/learning/signals", headers=owner, params={"surface": "approval"}).json()["items"]
    assert sorted((item["kind"], item["tool"], item["reason"]) for item in decisions) == [
        ("approval_approved", "external.action", None),
        ("approval_rejected", "external.action", "Wrong amount"),
    ]
    assert decisions[0]["metadata"]["risk_level"] == "high"


# --------------------------------------------------------------------------- routines API


def _seed_tasks(user: str, prompts_at: list[tuple[str, datetime]]) -> None:
    for index, (prompt, at) in enumerate(prompts_at):
        task = Task(
            id=f"task_{user}_{index}", title=prompt, prompt=prompt, status=TaskStatus.COMPLETED,
            tenant_id=TENANT, workspace_id=WORKSPACE, created_at=at, updated_at=at, created_by=user,
        )
        store.tasks[task.id] = task


def _sunday_history(user: str = "user_learn") -> None:
    from anum_api.schemas import utc_now

    now = utc_now()
    last_sunday = (now - timedelta(days=(now.weekday() + 1) % 7 or 7)).replace(hour=6, minute=5, second=0, microsecond=0)
    seeded = []
    for week in range(4):
        start = last_sunday - timedelta(weeks=week)
        seeded += [("Check Amazon orders", start), ("Email the supplier about restock", start + timedelta(minutes=30))]
    _seed_tasks(user, seeded)


def test_routines_are_detected_from_my_tasks_accepted_paused_then_confirmed(client: TestClient) -> None:
    _sunday_history()
    _sunday_history("user_other")  # someone else's habits are not mine
    detected = client.post("/api/v1/learning/routines/detect", headers=headers(), json={"timezone": "Asia/Riyadh"})
    assert detected.status_code == 200, detected.text
    (routine,) = detected.json()["items"]
    assert detected.json()["detected"] == 1 and routine["status"] == "proposed"
    assert routine["cron"] == "0 9 * * 0" and len(routine["steps"]) == 2
    # Detecting again refreshes, never duplicates.
    again = client.post("/api/v1/learning/routines/detect", headers=headers(), json={"timezone": "Asia/Riyadh"}).json()
    assert again["detected"] == 0 and len(again["items"]) == 1
    assert client.post("/api/v1/learning/routines/detect", headers=headers(), json={"timezone": "Mars/Base"}).status_code == 422

    viewer = client.post(f"/api/v1/learning/routines/{routine['id']}/accept", headers=headers(role="viewer"))
    assert viewer.status_code == 403
    accepted = client.post(f"/api/v1/learning/routines/{routine['id']}/accept", headers=headers())
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted" and accepted.json()["workflow_id"] and accepted.json()["schedule_id"]
    schedule = client.get(f"/api/v1/automation/schedules/{accepted.json()['schedule_id']}", headers=headers()).json()
    assert schedule["enabled"] is False and schedule["next_run_at"] is None
    assert schedule["cron"] == "0 9 * * 0" and schedule["timezone"] == "Asia/Riyadh"
    workflows = client.get("/api/v1/automation/workflows", headers=headers()).json()
    assert [step["input"]["prompt"] for step in workflows[0]["steps"]] == ["Check Amazon orders", "Email the supplier about restock"]
    assert client.post(f"/api/v1/learning/routines/{routine['id']}/accept", headers=headers()).status_code == 409

    confirmed = client.post(f"/api/v1/learning/routines/{routine['id']}/confirm", headers=headers())
    assert confirmed.json()["status"] == "confirmed"
    assert client.get(f"/api/v1/automation/schedules/{accepted.json()['schedule_id']}", headers=headers()).json()["enabled"] is True
    actions = [record.action for record in store.audit_records]
    assert "learning.routine_accepted" in actions and "learning.routine_confirmed" in actions
    assert client.get("/api/v1/learning/routines", headers=headers("user_other")).json()["items"] == []


def test_a_dismissed_routine_is_not_proposed_again(client: TestClient) -> None:
    _sunday_history()
    (routine,) = client.post("/api/v1/learning/routines/detect", headers=headers(), json={"timezone": "Asia/Riyadh"}).json()["items"]
    assert client.post(f"/api/v1/learning/routines/{routine['id']}/dismiss", headers=headers()).json()["status"] == "dismissed"
    again = client.post("/api/v1/learning/routines/detect", headers=headers(), json={"timezone": "Asia/Riyadh"}).json()
    assert again["detected"] == 0 and [item["status"] for item in again["items"]] == ["dismissed"]
    assert client.post(f"/api/v1/learning/routines/{routine['id']}/confirm", headers=headers()).status_code == 409
    assert client.delete(f"/api/v1/learning/routines/{routine['id']}", headers=headers()).status_code == 204
    assert client.delete(f"/api/v1/learning/routines/{routine['id']}", headers=headers()).status_code == 404


# --------------------------------------------------------------------------- notes and suggestions API


def _rejections(user_headers: dict[str, str], count: int = 2) -> None:
    for _ in range(count):
        task = client_for().post("/api/v1/tasks", headers=user_headers, json={"title": "Send", "prompt": "Send the price list to the buyer"}).json()
        approval = client_for().post(f"/api/v1/tasks/{task['id']}/run", headers=user_headers).json()["approval"]
        client_for().post(
            f"/api/v1/approvals/{approval['id']}/reject", headers=user_headers,
            json={"payload_hash": approval["payload_hash"], "reason": "Wrong buyer"},
        )


def client_for() -> TestClient:
    return TestClient(app)


def test_the_weekly_note_suggests_and_one_click_applies_an_audited_instruction(client: TestClient) -> None:
    owner = headers("user_owner", "owner")
    _rejections(owner)
    notes = client.get("/api/v1/learning/notes", headers=owner)
    assert notes.status_code == 200, notes.text
    (view,) = notes.json()["items"]
    assert view["note"]["week"] == iso_week(datetime.now(timezone.utc))
    assert view["note"]["summary"]["approvals_rejected"] == 2 and view["note"]["summary"]["run_failures"] == 2
    rules = {item["rule"]: item for item in view["suggestions"]}
    assert set(rules) == {"approval_rejections", "tool_failures"}
    suggestion = rules["approval_rejections"]
    assert suggestion["status"] == "open" and suggestion["needs_permission"] is None

    applied = client.post(f"/api/v1/learning/suggestions/{suggestion['id']}/apply", headers=owner)
    assert applied.status_code == 200 and applied.json()["status"] == "applied"
    (preference,) = [item for item in client.get("/api/v1/learning/preferences", headers=owner).json()["items"] if item["source"] == "suggestion"]
    assert preference["scope"] == "tool:external.action" and "Wrong buyer" in preference["text"]
    assert preference["suggestion_id"] == suggestion["id"]
    assert client.post(f"/api/v1/learning/suggestions/{suggestion['id']}/apply", headers=owner).status_code == 409
    audit = [record for record in store.audit_records if record.action == "learning.suggestion_applied"]
    assert audit and audit[0].metadata["scope"] == "tool:external.action"

    dismissed = client.post(f"/api/v1/learning/suggestions/{rules['tool_failures']['id']}/dismiss", headers=owner)
    assert dismissed.json()["status"] == "dismissed"
    # Refreshing keeps decided suggestions as they are.
    refreshed = client.post("/api/v1/learning/notes/refresh", headers=owner).json()
    assert {item["rule"]: item["status"] for item in refreshed["suggestions"]} == {"approval_rejections": "applied", "tool_failures": "dismissed"}
    # Another user sees no notes of mine.
    assert client.get("/api/v1/learning/notes", headers=headers("user_other")).json()["items"][0]["suggestions"] == []


def test_autonomy_suggestions_need_an_owner_to_apply_and_change_the_policy(client: TestClient) -> None:
    owner, member = headers("user_owner", "owner"), headers("user_member", "member")
    assert client.put("/api/v1/approval-policy", headers=owner, json={"two_person_rule": False, "medium_risk_requires_approval": True}).status_code == 200
    for who in (owner, member):
        ctx_for = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=who["x-user-id"], roles=[who["x-user-roles"]])
        with open_learning_store(ctx_for) as learning:
            for _ in range(5):
                learning.add_signal(
                    new_signal(SignalKind.APPROVAL_APPROVED, Surface.APPROVAL, tool="crm.update", metadata={"risk_level": "medium"})
                )
    member_note = client.get("/api/v1/learning/notes", headers=member).json()["items"][0]
    (member_suggestion,) = member_note["suggestions"]
    assert member_suggestion["kind"] == "autonomy" and member_suggestion["needs_permission"] == "policy:manage"
    refused = client.post(f"/api/v1/learning/suggestions/{member_suggestion['id']}/apply", headers=member)
    assert refused.status_code == 403
    assert client.get("/api/v1/approval-policy", headers=owner).json()["medium_risk_requires_approval"] is True

    (owner_suggestion,) = client.get("/api/v1/learning/notes", headers=owner).json()["items"][0]["suggestions"]
    applied = client.post(f"/api/v1/learning/suggestions/{owner_suggestion['id']}/apply", headers=owner)
    assert applied.status_code == 200, applied.text
    assert client.get("/api/v1/approval-policy", headers=owner).json()["medium_risk_requires_approval"] is False
    policy_audit = [r for r in store.audit_records if r.action == "approval_policy.updated" and r.metadata.get("source") == "learning_suggestion"]
    assert policy_audit and policy_audit[0].metadata["after"]["medium_risk_requires_approval"] is False


def test_notes_are_not_written_while_learning_is_off(client: TestClient) -> None:
    client.put("/api/v1/learning/settings", headers=headers(), json={"enabled": False})
    notes = client.get("/api/v1/learning/notes", headers=headers()).json()
    assert notes == {"items": [], "total": 0, "state": "off_by_user"}
    assert client.post("/api/v1/learning/notes/refresh", headers=headers()).status_code == 409


def test_every_state_changing_learning_route_is_audited_without_content(client: TestClient) -> None:
    from audit_probe import AuditProbe, audit_text

    owner = headers("user_owner", "owner")
    probe = AuditProbe(client, "learning")
    base = "/api/v1/learning"
    probe.call("PUT", f"{base}/settings", owner, json={"enabled": True})
    draft, sent = "Hello, the private original draft", "Hi, the private final text as sent"
    feedback = probe.call("POST", f"{base}/feedback", owner, sent=[draft, sent], json={"surface": "email", "original": draft, "final": sent})
    probe.call("PUT", f"{base}/profile", owner, sent=["Best wishes from the shop"], json={"sign_off": "Best wishes from the shop"})
    probe.call("DELETE", f"{base}/signals/{feedback.json()['signal']['id']}", owner)
    probe.call("POST", f"{base}/profile/reset", owner, json={"confirm": True})
    preference = probe.call("POST", f"{base}/preferences", owner, sent=["Always quote prices in SAR"], json={"text": "Always quote prices in SAR", "scope": "contact:buyer@example.com"}).json()
    probe.call("PUT", f"{base}/preferences/{preference['id']}", owner, sent=["Quote prices in AED"], json={"text": "Quote prices in AED"})
    probe.call("DELETE", f"{base}/preferences/{preference['id']}", owner)
    assert "buyer@example.com" not in audit_text(store.audit_records)

    _sunday_history("user_owner")
    (routine,) = probe.call("POST", f"{base}/routines/detect", owner, json={"timezone": "Asia/Riyadh"}).json()["items"]
    probe.call("POST", f"{base}/routines/{routine['id']}/accept", owner)
    probe.call("POST", f"{base}/routines/{routine['id']}/confirm", owner)
    probe.call("DELETE", f"{base}/routines/{routine['id']}", owner)
    _sunday_history("user_owner")
    (again,) = probe.call("POST", f"{base}/routines/detect", owner, json={"timezone": "Asia/Riyadh"}).json()["items"]
    probe.call("POST", f"{base}/routines/{again['id']}/dismiss", owner)

    _rejections(owner)
    suggestions = {item["rule"]: item for item in probe.call("POST", f"{base}/notes/refresh", owner).json()["suggestions"]}
    probe.call("POST", f"{base}/suggestions/{suggestions['approval_rejections']['id']}/apply", owner)
    probe.call("POST", f"{base}/suggestions/{suggestions['tool_failures']['id']}/dismiss", owner)
    probe.assert_covered()
