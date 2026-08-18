"""Skill manifest shape and an in-process skill registry.

Per docs/skills.md, a skill is a declarative capability package: purpose,
trigger conditions, required tools, input/output schema, allowed memory
scopes, approval requirements, and test cases. Implementation code lives in
normal application packages or tool adapters -- a `SkillManifest` never
carries executable logic.

Skills are not trusted code by default. Registering a skill, and selecting
one for a task via `SkillRegistry.select_for_task`, does NOT itself grant
that skill's `required_tools` or `allowed_memory_scopes`. Selection only
decides which skill's guidance the agent should consult next. Whatever
tools and memory scopes actually become available to the run are still
decided by the authorization layer and tool registry elsewhere in the
runtime -- this module has no opinion on, and no ability to enforce, that
decision.
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import RiskLevel

_SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class SkillTestCase(BaseModel):
    """A single example input/expected-behavior pair declared by a skill.

    These are documentation-and-validation fixtures, not a test runner --
    `tests/test_skills.py` is what actually exercises them.
    """

    model_config = ConfigDict(frozen=True)

    input: dict[str, Any]
    expected_output_summary: str = Field(min_length=1, max_length=2000)


class SkillManifest(BaseModel):
    """Declarative description of a reusable agent capability package.

    Immutable once constructed: skill definitions are meant to be versioned
    and published, not mutated in place by whoever consults them.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=40)
    purpose: str = Field(min_length=1, max_length=2000)
    trigger_conditions: list[str] = Field(min_length=1)
    required_tools: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    allowed_memory_scopes: list[str] = Field(default_factory=list)
    approval_requirements: list[str] = Field(default_factory=list)
    risk_level: RiskLevel
    test_cases: list[SkillTestCase] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id_slug(cls, value: str) -> str:
        if not _SLUG_PATTERN.match(value):
            raise ValueError(
                "id must be a lowercase slug matching ^[a-z][a-z0-9_]*$, "
                f"got {value!r}"
            )
        return value

    @field_validator("version")
    @classmethod
    def _validate_semver(cls, value: str) -> str:
        if not _SEMVER_PATTERN.match(value):
            raise ValueError(
                f"version must be semver-shaped (e.g. '1.0.0'), got {value!r}"
            )
        return value

    @field_validator("name", "purpose")
    @classmethod
    def _validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("trigger_conditions")
    @classmethod
    def _validate_trigger_conditions(cls, value: list[str]) -> list[str]:
        cleaned = [condition.strip() for condition in value]
        if not cleaned or any(not condition for condition in cleaned):
            raise ValueError("trigger_conditions must be non-empty, non-blank phrases")
        return cleaned


class SkillRegistry:
    """In-process registry of available `SkillManifest`s.

    This is intentionally a plain lookup + heuristic-match structure with
    no I/O and no side effects on tools or memory. It is meant to be
    imported and queried by the agent runtime (not implemented in this
    slice) when deciding which skill's guidance applies to a task.
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillManifest] = {}

    def register(self, manifest: SkillManifest) -> None:
        if manifest.id in self._skills:
            raise ValueError(f"skill already registered: {manifest.id!r}")
        self._skills[manifest.id] = manifest

    def get(self, skill_id: str) -> SkillManifest | None:
        return self._skills.get(skill_id)

    def list_skills(self) -> tuple[SkillManifest, ...]:
        return tuple(self._skills.values())

    def select_for_task(self, prompt: str) -> SkillManifest | None:
        """Pick the best-matching registered skill for a free-text prompt.

        This performs case-insensitive substring matching of each skill's
        declared `trigger_conditions` against `prompt`. The skill with the
        most matching trigger phrases wins; ties are broken by registration
        order (first registered wins). Returns `None` when no skill has any
        matching trigger condition.

        This is selection guidance only -- it does not grant the returned
        skill's `required_tools` or `allowed_memory_scopes`. The caller
        (agent runtime) is expected to record the selection in the task
        trace and to separately consult the authorization layer / tool
        registry for what is actually permitted.
        """
        needle = prompt.casefold()
        best_manifest: SkillManifest | None = None
        best_score = 0
        for manifest in self._skills.values():
            score = sum(
                1
                for condition in manifest.trigger_conditions
                if condition.casefold() in needle
            )
            if score > best_score:
                best_score = score
                best_manifest = manifest
        return best_manifest
