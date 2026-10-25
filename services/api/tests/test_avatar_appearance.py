"""The avatar style of agents and team members (docs/agents.md, docs/ANUM_MINE_HQ_REALISM_ASSETS.md), memory backend.

A style is a drawing choice: validated against a small palette, optional field by field, audited, edited with the
same permission as the profile itself, and never read from or written into a name.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anum_api import avatar_appearance as av
from anum_api.agent_profiles import agent_store
from anum_api.main import app, store
from anum_api.settings import settings
from test_team_work import AHMED, OWNER as TEAM_OWNER, SARA, VERA, client as team_client  # noqa: F401 (fixture)

TENANT, WORKSPACE = "tenant_avatar", "workspace_avatar"
ROOT = Path(__file__).resolve().parents[3]


def headers(user: str = "user_owner", role: str = "member") -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": user, "x-user-roles": role}


OWNER = headers()
OTHER = headers("user_other")
VIEWER = headers("user_viewer", "viewer")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    agent_store.clear()
    store.audit_records.clear()
    yield TestClient(app)
    agent_store.clear()


def _agent(client: TestClient, hdrs: dict[str, str] = OWNER, **fields) -> dict:  # type: ignore[no-untyped-def]
    response = client.post("/api/v1/agents", headers=hdrs, json={"name": "Reception agent", "category": "work"} | fields)
    assert response.status_code == 201, response.text
    return response.json()


def test_an_agent_has_no_avatar_until_the_owner_chooses_one(client: TestClient) -> None:
    created = _agent(client)
    assert created["avatar"] is None
    assert client.get(f"/api/v1/agents/{created['id']}", headers=OWNER).json()["avatar"] is None


def test_set_replace_and_clear_the_avatar_with_audit(client: TestClient) -> None:
    created = _agent(client, avatar={"avatar_style": "feminine", "skin_tone": "tone_4", "hair_style": "bun"})
    assert created["avatar"] == {
        "avatar_style": "feminine", "skin_tone": "tone_4", "hair_style": "bun", "hair_color": None, "outfit": None, "accent": None,
    }
    agent_id = created["id"]
    updated = client.patch(f"/api/v1/agents/{agent_id}", headers=OWNER, json={"avatar": {"outfit": "uniform", "accent": "teal"}, "version": 1})
    assert updated.status_code == 200, updated.text
    # An update replaces the whole style: the earlier fields are not kept.
    assert updated.json()["avatar"]["avatar_style"] is None and updated.json()["avatar"]["outfit"] == "uniform"
    # An unrelated edit leaves the style alone.
    renamed = client.patch(f"/api/v1/agents/{agent_id}", headers=OWNER, json={"description": "Welcomes visitors"})
    assert renamed.json()["avatar"]["accent"] == "teal"
    cleared = client.patch(f"/api/v1/agents/{agent_id}", headers=OWNER, json={"avatar": None})
    assert cleared.status_code == 200 and cleared.json()["avatar"] is None
    records = [r for r in store.audit_records if r.action == "agent.updated" and r.tenant_id == TENANT]
    assert [list(r.metadata["changed"]) for r in records] == [["avatar"], ["description"], ["avatar"]]
    assert records[0].metadata["after"]["avatar"] == {"outfit": "uniform", "accent": "teal"}
    assert records[2].metadata["after"]["avatar"] is None


def test_values_outside_the_palette_and_unknown_fields_are_refused(client: TestClient) -> None:
    for bad in (
        {"avatar_style": "robot"}, {"skin_tone": "#ffcc99"}, {"hair_color": "green"}, {"outfit": "toga"}, {"accent": "#ff0000"},
        {"gender": "female"}, {"name": "Aisha"}, {"avatar_style": "masculine", "hair_style": "bun"},
    ):
        response = client.post("/api/v1/agents", headers=OWNER, json={"name": "Bad", "avatar": bad})
        assert response.status_code == 422, (bad, response.text)
    created = _agent(client)
    response = client.patch(f"/api/v1/agents/{created['id']}", headers=OWNER, json={"avatar": {"hair_style": "bun", "avatar_style": "masculine"}})
    assert response.status_code == 422 and "hair styles" in response.text


def test_the_avatar_follows_the_profile_permissions(client: TestClient) -> None:
    mine = _agent(client)
    shared = _agent(client, name="Team agent", sharing="workspace")
    # A private agent is invisible to someone else; a shared one is readable but only its creator or an owner edits it.
    assert client.patch(f"/api/v1/agents/{mine['id']}", headers=OTHER, json={"avatar": {"outfit": "formal"}}).status_code == 404
    assert client.patch(f"/api/v1/agents/{shared['id']}", headers=OTHER, json={"avatar": {"outfit": "formal"}}).status_code == 403
    assert client.patch(f"/api/v1/agents/{shared['id']}", headers=VIEWER, json={"avatar": {"outfit": "formal"}}).status_code == 403
    seen = client.get(f"/api/v1/agents/{shared['id']}", headers=OTHER).json()
    assert seen["avatar"] is None and seen["can_edit"] is False
    assert client.patch(f"/api/v1/agents/{shared['id']}", headers=headers("workspace_boss", "owner"), json={"avatar": {"outfit": "formal"}}).status_code == 200


def test_the_avatar_is_never_derived_from_the_name(client: TestClient) -> None:
    first = _agent(client, name="Aisha")
    second = _agent(client, name="Omar")
    assert first["avatar"] is None and second["avatar"] is None
    renamed = client.patch(f"/api/v1/agents/{first['id']}", headers=OWNER, json={"name": "Maria"}).json()
    assert renamed["avatar"] is None


def test_a_stored_value_that_no_longer_validates_is_dropped_not_an_error() -> None:
    assert av.avatar_from_record({"skin_tone": "tone_9"}) is None
    assert av.avatar_from_record(None) is None and av.avatar_from_record({}) is None
    assert av.avatar_from_record({"accent": "teal"}).stored() == {"accent": "teal"}  # type: ignore[union-attr]


# ------------------------------------------------------------------------ team members


def test_a_team_profile_keeps_its_avatar_when_the_field_is_omitted(team_client: TestClient) -> None:  # noqa: F811
    base = {"display_name": "Sara Ali", "timezone": "Asia/Riyadh"}
    saved = team_client.put("/api/v1/team/people/user_sara", headers=SARA, json=base | {"avatar": {"avatar_style": "neutral", "skin_tone": "tone_2"}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["avatar"]["skin_tone"] == "tone_2"
    again = team_client.put("/api/v1/team/people/user_sara", headers=SARA, json=base | {"job_title": "Planner"})
    assert again.json()["avatar"]["skin_tone"] == "tone_2" and again.json()["job_title"] == "Planner"
    person = next(p for p in team_client.get("/api/v1/team/people", headers=VERA).json() if p["user_id"] == "user_sara")
    assert person["avatar"]["avatar_style"] == "neutral"
    cleared = team_client.put("/api/v1/team/people/user_sara", headers=SARA, json=base | {"avatar": None})
    assert cleared.json()["avatar"] is None


def test_team_avatar_edit_rules_match_the_profile_rules(team_client: TestClient) -> None:  # noqa: F811
    body = {"display_name": "Ahmed Saleh", "timezone": "Asia/Riyadh", "avatar": {"outfit": "formal"}}
    assert team_client.put("/api/v1/team/people/user_ahmed", headers=SARA, json=body).status_code == 403
    assert team_client.put("/api/v1/team/people/user_ahmed", headers=AHMED, json=body).status_code == 200
    assert team_client.put("/api/v1/team/people/user_ahmed", headers=TEAM_OWNER, json=body | {"avatar": {"outfit": "uniform"}}).status_code == 200
    bad = team_client.put("/api/v1/team/people/user_ahmed", headers=AHMED, json=body | {"avatar": {"avatar_style": "masculine", "hair_style": "long"}})
    assert bad.status_code == 422


# ---------------------------------------------------------------------- palette parity


def _ts_list(source: str, name: str) -> list[str]:
    match = re.search(rf"export const {name}\s*=\s*\[(.*?)\]\s*as const", source, re.S)
    assert match, name
    return re.findall(r"'([^']+)'", match.group(1))


def test_the_palette_matches_the_web_and_the_contracts() -> None:
    expected = {
        "AVATAR_STYLES": av.AVATAR_STYLES, "SKIN_TONES": av.SKIN_TONES, "HAIR_STYLES": av.HAIR_STYLES,
        "HAIR_COLORS": av.HAIR_COLORS, "OUTFITS": av.OUTFITS, "ACCENTS": av.ACCENTS,
    }
    for relative in ("packages/contracts/src/avatar.ts", "apps/web/src/lib/hqAvatar.ts"):
        source = (ROOT / relative).read_text()
        for name, values in expected.items():
            assert _ts_list(source, name) == list(values), (relative, name)
    web = (ROOT / "apps/web/src/lib/hqAvatar.ts").read_text()
    block = re.search(r"export const HAIR_BY_STYLE[^=]*=\s*\{(.*?)\n\}", web, re.S)
    assert block
    for style, hairs in av.HAIR_BY_STYLE.items():
        row = re.search(rf"{style}:\s*\[(.*?)\]", block.group(1))
        assert row and re.findall(r"'([^']+)'", row.group(1)) == list(hairs), style
