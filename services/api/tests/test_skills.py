import pytest
from pydantic import ValidationError

from anum_api.schemas import RiskLevel
from anum_api.skills import SkillManifest, SkillRegistry, SkillTestCase
from anum_api.skills_library import (
    DEFAULT_SKILL_REGISTRY,
    DOCUMENT_DRAFTING_SKILL,
    TASK_PLANNING_SKILL,
)


def make_manifest(**overrides: object) -> SkillManifest:
    values: dict[str, object] = {
        "id": "sample_skill",
        "name": "Sample Skill",
        "version": "1.0.0",
        "purpose": "Do a sample thing.",
        "trigger_conditions": ["do the sample thing"],
        "required_tools": ["some_tool"],
        "input_schema": {"type": "object", "properties": {"prompt": {"type": "string"}}},
        "output_schema": {"type": "object", "properties": {"result": {"type": "string"}}},
        "allowed_memory_scopes": ["task_notes"],
        "approval_requirements": [],
        "risk_level": RiskLevel.LOW,
        "test_cases": [
            SkillTestCase(input={"prompt": "hi"}, expected_output_summary="does the thing"),
        ],
    }
    values.update(overrides)
    return SkillManifest(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SkillManifest field validation
# ---------------------------------------------------------------------------


def test_manifest_accepts_well_formed_values() -> None:
    manifest = make_manifest()
    assert manifest.id == "sample_skill"
    assert manifest.risk_level is RiskLevel.LOW


def test_manifest_is_frozen() -> None:
    manifest = make_manifest()
    with pytest.raises(ValidationError):
        manifest.name = "Different Name"  # type: ignore[misc]


@pytest.mark.parametrize(
    "bad_id",
    ["", "Task_Planning", "1_task", "task-planning", "task planning", "TASK"],
)
def test_manifest_rejects_malformed_id_slug(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        make_manifest(id=bad_id)


def test_manifest_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        make_manifest(name="")


def test_manifest_rejects_blank_name() -> None:
    with pytest.raises(ValidationError):
        make_manifest(name="   ")


def test_manifest_rejects_empty_purpose() -> None:
    with pytest.raises(ValidationError):
        make_manifest(purpose="")


def test_manifest_rejects_blank_purpose() -> None:
    with pytest.raises(ValidationError):
        make_manifest(purpose="   ")


@pytest.mark.parametrize(
    "bad_version",
    ["", "1.0", "v1.0.0", "1.0.0-beta", "1.0.0.0", "latest"],
)
def test_manifest_rejects_malformed_version(bad_version: str) -> None:
    with pytest.raises(ValidationError):
        make_manifest(version=bad_version)


def test_manifest_rejects_empty_trigger_conditions() -> None:
    with pytest.raises(ValidationError):
        make_manifest(trigger_conditions=[])


def test_manifest_rejects_blank_trigger_condition() -> None:
    with pytest.raises(ValidationError):
        make_manifest(trigger_conditions=["  "])


def test_manifest_allows_empty_required_tools_and_approvals() -> None:
    manifest = make_manifest(required_tools=[], approval_requirements=[])
    assert manifest.required_tools == []
    assert manifest.approval_requirements == []


# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------


def test_registry_register_and_get() -> None:
    registry = SkillRegistry()
    manifest = make_manifest()
    registry.register(manifest)

    assert registry.get("sample_skill") is manifest
    assert registry.get("does_not_exist") is None


def test_registry_list_skills_returns_all_registered() -> None:
    registry = SkillRegistry()
    first = make_manifest(id="skill_one", trigger_conditions=["do one"])
    second = make_manifest(id="skill_two", trigger_conditions=["do two"])
    registry.register(first)
    registry.register(second)

    listed = registry.list_skills()
    assert isinstance(listed, tuple)
    assert list(listed) == [first, second]


def test_registry_rejects_duplicate_id() -> None:
    registry = SkillRegistry()
    registry.register(make_manifest())

    with pytest.raises(ValueError):
        registry.register(make_manifest())


def test_registry_select_for_task_returns_none_when_empty() -> None:
    registry = SkillRegistry()
    assert registry.select_for_task("do the sample thing") is None


def test_registry_select_for_task_matches_trigger_substring() -> None:
    registry = SkillRegistry()
    manifest = make_manifest()
    registry.register(manifest)

    selected = registry.select_for_task("Please do the sample thing for me today")
    assert selected is manifest


def test_registry_select_for_task_returns_none_for_no_match() -> None:
    registry = SkillRegistry()
    registry.register(make_manifest())

    assert registry.select_for_task("what's the weather like today") is None


def test_registry_select_for_task_prefers_more_matching_triggers() -> None:
    registry = SkillRegistry()
    low_match = make_manifest(
        id="low_match", trigger_conditions=["alpha keyword"]
    )
    high_match = make_manifest(
        id="high_match",
        trigger_conditions=["alpha keyword", "beta keyword", "gamma keyword"],
    )
    registry.register(low_match)
    registry.register(high_match)

    selected = registry.select_for_task(
        "this prompt mentions alpha keyword, beta keyword, and gamma keyword"
    )
    assert selected is high_match


# ---------------------------------------------------------------------------
# Default library skills: task planning + document drafting
# ---------------------------------------------------------------------------


def test_default_registry_contains_both_library_skills() -> None:
    assert DEFAULT_SKILL_REGISTRY.get("task_planning") is TASK_PLANNING_SKILL
    assert DEFAULT_SKILL_REGISTRY.get("document_drafting") is DOCUMENT_DRAFTING_SKILL
    assert len(DEFAULT_SKILL_REGISTRY.list_skills()) == 2


@pytest.mark.parametrize(
    "prompt",
    [
        "Can you help me plan a task for the quarterly audit?",
        "I need to break this down into steps",
        "What are the steps to launch this feature safely?",
    ],
)
def test_select_for_task_picks_task_planning_for_planning_prompts(prompt: str) -> None:
    assert DEFAULT_SKILL_REGISTRY.select_for_task(prompt) is TASK_PLANNING_SKILL


@pytest.mark.parametrize(
    "prompt",
    [
        "Please draft a memo about the outage for leadership",
        "Can you write a summary of today's customer call?",
        "I need to prepare a memo before the board meeting",
    ],
)
def test_select_for_task_picks_document_drafting_for_drafting_prompts(prompt: str) -> None:
    assert DEFAULT_SKILL_REGISTRY.select_for_task(prompt) is DOCUMENT_DRAFTING_SKILL


def test_select_for_task_returns_none_for_unrelated_prompt() -> None:
    assert DEFAULT_SKILL_REGISTRY.select_for_task("what's the weather") is None


# ---------------------------------------------------------------------------
# Internal consistency of each library skill's own declared test_cases
# ---------------------------------------------------------------------------


def _assert_input_matches_schema(manifest: SkillManifest, case_input: dict) -> None:
    required_keys = manifest.input_schema.get("required", [])
    for key in required_keys:
        assert key in case_input, (
            f"{manifest.id}: test case input {case_input!r} is missing "
            f"required key {key!r} declared in input_schema"
        )
    schema_properties = manifest.input_schema.get("properties", {})
    for key in case_input:
        assert key in schema_properties, (
            f"{manifest.id}: test case input key {key!r} is not declared "
            f"in input_schema.properties"
        )


@pytest.mark.parametrize("manifest", [TASK_PLANNING_SKILL, DOCUMENT_DRAFTING_SKILL])
def test_library_skill_manifests_are_well_formed(manifest: SkillManifest) -> None:
    assert manifest.id
    assert manifest.name
    assert manifest.purpose
    assert manifest.trigger_conditions
    assert manifest.input_schema.get("type") == "object"
    assert manifest.output_schema.get("type") == "object"
    assert len(manifest.test_cases) >= 2


@pytest.mark.parametrize("manifest", [TASK_PLANNING_SKILL, DOCUMENT_DRAFTING_SKILL])
def test_library_skill_test_cases_satisfy_declared_input_schema(
    manifest: SkillManifest,
) -> None:
    for case in manifest.test_cases:
        _assert_input_matches_schema(manifest, case.input)
        assert case.expected_output_summary.strip() != ""


def test_task_planning_skill_test_cases_reference_prompt_field() -> None:
    for case in TASK_PLANNING_SKILL.test_cases:
        assert "prompt" in case.input
        assert isinstance(case.input["prompt"], str)
        assert case.input["prompt"].strip() != ""


def test_task_planning_output_schema_declares_steps_and_risk() -> None:
    required = set(TASK_PLANNING_SKILL.output_schema.get("required", []))
    assert required == {"steps", "estimated_risk"}


def test_document_drafting_skill_test_cases_reference_prompt_field() -> None:
    for case in DOCUMENT_DRAFTING_SKILL.test_cases:
        assert "prompt" in case.input
        assert isinstance(case.input["prompt"], str)
        assert case.input["prompt"].strip() != ""


def test_document_drafting_output_schema_declares_title_and_body() -> None:
    required = set(DOCUMENT_DRAFTING_SKILL.output_schema.get("required", []))
    assert required == {"title", "body"}


def test_document_drafting_skill_is_at_least_medium_risk() -> None:
    # Drafts can be copy-pasted straight to an external channel without
    # review, so this skill should not be rated LOW like task_planning.
    assert DOCUMENT_DRAFTING_SKILL.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH)


def test_task_planning_skill_is_low_risk() -> None:
    assert TASK_PLANNING_SKILL.risk_level is RiskLevel.LOW


def test_library_skills_do_not_declare_overlapping_trigger_conditions() -> None:
    planning_triggers = {t.casefold() for t in TASK_PLANNING_SKILL.trigger_conditions}
    drafting_triggers = {t.casefold() for t in DOCUMENT_DRAFTING_SKILL.trigger_conditions}
    assert planning_triggers.isdisjoint(drafting_triggers)
