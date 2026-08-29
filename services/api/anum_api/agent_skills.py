from collections.abc import Iterable
import re

from pydantic import BaseModel, Field

from .schemas import RiskLevel


class SkillManifest(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    name: str
    description: str
    triggers: frozenset[str] = Field(default_factory=frozenset)
    required_tools: frozenset[str] = Field(default_factory=frozenset)
    risk_level: RiskLevel = RiskLevel.LOW


class SkillRegistry:
    def __init__(self, manifests: Iterable[SkillManifest]) -> None:
        manifest_list = list(manifests)
        self._manifests = {manifest.id: manifest for manifest in manifest_list}
        if len(self._manifests) != len(manifest_list):
            raise ValueError("skill ids must be unique")

    def get(self, skill_id: str) -> SkillManifest | None:
        return self._manifests.get(skill_id)

    def select(self, prompt: str, available_tools: set[str]) -> list[SkillManifest]:
        words = set(re.findall(r"[a-z0-9_-]+", prompt.lower()))
        return [
            manifest
            for manifest in self._manifests.values()
            if (not manifest.triggers or bool(words & manifest.triggers))
            and manifest.required_tools <= available_tools
        ]


def default_skill_registry() -> SkillRegistry:
    return SkillRegistry(
        [
            SkillManifest(
                id="anum.task-planning",
                version="1.0.0",
                name="Task planning",
                description="Turns a task into an observable, policy-checked execution plan.",
            ),
            SkillManifest(
                id="anum.document-drafting",
                version="1.0.0",
                name="Document drafting",
                description="Drafts and summarizes text without external side effects.",
                triggers=frozenset({"draft", "write", "summarize", "prepare"}),
                required_tools=frozenset({"anum.respond"}),
            ),
            SkillManifest(
                id="anum.external-action",
                version="1.0.0",
                name="External action",
                description="Plans governed actions that affect systems outside ANUM.",
                triggers=frozenset(
                    {"delete", "send", "publish", "spend", "pay", "permission", "credential"}
                ),
                required_tools=frozenset({"external.action"}),
                risk_level=RiskLevel.HIGH,
            ),
        ]
    )
