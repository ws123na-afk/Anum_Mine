"""Accept-Language for the API's user-facing sentences (docs/web-localization.md)."""

from __future__ import annotations

import ast
import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anum_api import localization
from anum_api.dependencies import ORGANIZATION_ADMIN_REQUIRED, WORKSPACE_ARCHIVED
from anum_api.localization import ARABIC_MESSAGES, localize_message, negotiate_language
from anum_api.main import app
from anum_api.model_budget import BudgetKind, BudgetScope, budget_exceeded_message
from anum_api.privacy import ACCOUNT_NOTE, SHARED_NOTE

client = TestClient(app)
MEMBER = {"x-tenant-id": "tenant_l10n", "x-workspace-id": "workspace_l10n", "x-user-id": "user_l10n", "x-user-roles": "member"}
ARABIC = re.compile(r"[؀-ۿ]")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "en"),
        ("", "en"),
        ("ar", "ar"),
        ("ar-SA,ar;q=0.9,en;q=0.8", "ar"),
        ("en-US,en;q=0.9,ar;q=0.8", "en"),
        ("fr-FR,ar;q=0.5", "ar"),
        ("fr-FR, de", "en"),
        ("en;q=0.2, ar;q=0.9", "ar"),
        ("ar;q=0, en", "en"),
        ("*", "en"),
        ("ar;q=bogus, en", "en"),
    ],
)
def test_negotiates_the_language(header: str | None, expected: str) -> None:
    assert negotiate_language(header) == expected


def test_messages_stay_english_unless_arabic_is_asked() -> None:
    assert localize_message("Task not found", "en") == "Task not found"
    assert localize_message("Task not found", "ar") == "المهمة غير موجودة."
    # A sentence without an Arabic entry is answered as written.
    assert localize_message("Something new", "ar") == "Something new"


def test_the_budget_refusal_keeps_its_reset_date_in_arabic() -> None:
    english = budget_exceeded_message(BudgetScope.WORKSPACE, BudgetKind.TOKENS, date(2026, 11, 1))
    arabic = localize_message(english, "ar")
    assert arabic == "استهلكت مساحة العمل ميزانية النموذج الشهرية (الرموز). يُعاد ضبطها في 2026-11-01 (UTC)؛ ويستطيع المالك رفعها من الإعدادات."
    tenant = localize_message(budget_exceeded_message(BudgetScope.TENANT, BudgetKind.COST, date(2027, 1, 1)), "ar")
    assert "المؤسسة" in tenant and "التكلفة التقديرية" in tenant and "2027-01-01" in tenant


def test_catalog_keys_are_sentences_the_api_really_writes() -> None:
    """Each Arabic entry must still match a message in the code, or it silently stops applying."""
    assert ORGANIZATION_ADMIN_REQUIRED in ARABIC_MESSAGES
    assert WORKSPACE_ARCHIVED in ARABIC_MESSAGES
    source_strings: set[str] = set()
    for path in Path(localization.__file__).parent.rglob("*.py"):
        if path.name == "localization.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                source_strings.add(node.value)
    standard = {"Bad Request", "Unauthorized", "Forbidden", "Not Found"}  # HTTPStatus phrases
    missing = [key for key in ARABIC_MESSAGES if key not in source_strings and key not in standard]
    assert missing == []
    assert all(ARABIC.search(value) for value in ARABIC_MESSAGES.values())


def test_error_envelope_follows_accept_language_and_keeps_its_code() -> None:
    english = client.get("/api/v1/tasks/task_missing", headers=MEMBER)
    arabic = client.get("/api/v1/tasks/task_missing", headers={**MEMBER, "accept-language": "ar-SA,ar;q=0.9"})
    assert english.status_code == arabic.status_code == 404
    assert english.json()["error"]["code"] == arabic.json()["error"]["code"] == "not_found"
    assert english.json()["error"]["message"] == "Task not found"
    assert arabic.json()["error"]["message"] == "المهمة غير موجودة."
    assert arabic.headers["content-language"] == "ar"
    assert english.headers["content-language"] == "en"
    assert "Accept-Language" in arabic.headers["vary"]


def test_permission_refusal_in_arabic() -> None:
    viewer = {**MEMBER, "x-user-roles": "viewer", "accept-language": "ar"}
    response = client.post("/api/v1/tasks", headers=viewer, json={"title": "t", "prompt": "p"})
    assert response.status_code == 403
    assert response.json()["error"] == {**response.json()["error"], "code": "forbidden", "message": "دورك في مساحة العمل هذه لا يسمح بذلك."}


def test_validation_envelope_in_arabic_keeps_field_details() -> None:
    response = client.post("/api/v1/tasks", headers={**MEMBER, "accept-language": "ar"}, json={})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert body["message"] == "تعذّر التحقق من الطلب."
    assert body["details"] and all(detail["location"] for detail in body["details"])


def test_privacy_notes_follow_accept_language() -> None:
    english = client.get("/api/v1/privacy", headers=MEMBER).json()
    arabic = client.get("/api/v1/privacy", headers={**MEMBER, "accept-language": "ar"}).json()
    assert english["account_note"] == ACCOUNT_NOTE and english["shared_note"] == SHARED_NOTE
    assert english["retention"]["private_memories"].startswith("Kept until you delete them")
    assert arabic["account_note"] == localization.PRIVACY_NOTES_AR["account_note"]
    assert arabic["shared_note"] == localization.PRIVACY_NOTES_AR["shared_note"]
    for key in ("private_memories", "task_memories", "audit_records"):
        assert ARABIC.search(arabic["retention"][key]), key
    # Numbers, codes and the confirmation phrase are the same in both languages.
    assert arabic["retention"]["conversation_days"] == english["retention"]["conversation_days"]
    assert arabic["retention"]["voice_transcript_choices"] == english["retention"]["voice_transcript_choices"]
    assert arabic["delete_confirmation"] == english["delete_confirmation"] == "DELETE MY DATA"


def test_delete_confirmation_refusal_in_arabic_keeps_the_phrase() -> None:
    response = client.post("/api/v1/privacy/delete", headers={**MEMBER, "accept-language": "ar"}, json={"confirm": "nope"})
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "اكتب DELETE MY DATA كما هي تمامًا للتأكيد. لم يُحذف شيء."
