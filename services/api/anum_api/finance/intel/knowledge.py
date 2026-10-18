"""The trading knowledge and skills library (docs/finance-trading-intel.md#skills-and-tools).

Six version-controlled skill packages live next to this file under ``skills/``, one folder
each, in the Claude skill format: ``SKILL.md`` (YAML front matter with name, description,
version and ``anum_skill_id``; then when to use, inputs, outputs, permitted tools, prohibited
actions, failure handling and knowledge), ``references/*.md`` (each ending with a sources
table: link, source date, licence, limitations) and ``tools.json`` (the tools the skill may
call with their input schemas, what it must never do, and how it handles failures).

``load_packages`` reads and checks them; ``skill_manifests`` turns them into the agent
runtime's ``SkillManifest`` entries (selected by trigger words, only where their tools are
registered); ``skill_version`` gives the text a workspace can publish through the skill
registry (docs/skills.md). Tests keep each ``tools.json`` equal to the registered tools.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).with_name("skills")
REQUIRED_SECTIONS = ("When to use", "Inputs", "Outputs", "Permitted tools", "Prohibited actions", "Failure handling", "Knowledge")
SOURCE_COLUMNS = ("Source", "Link", "Source date", "Licence", "Limitations")
_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_LONG_DASHES = re.compile("[" + chr(0x2013) + chr(0x2014) + chr(0x2015) + "]")

# Words that select each skill, in English and Arabic (agent_skills.prompt_words matches them).
TRIGGERS: dict[str, frozenset[str]] = {
    "anum.trading.market-data": frozenset({
        "markets", "venue", "venues", "candles", "ohlcv", "spread", "slippage", "liquidity", "tadawul",
        "nyse", "nasdaq", "dfm", "adx", "crypto", "السوق", "الأسواق", "البورصة", "تداول", "السيولة",
    }),
    "anum.trading.technical-analysis": frozenset({
        "rsi", "macd", "atr", "adx", "sma", "ema", "indicator", "indicators", "trend", "momentum", "volatility", "donchian",
        "مؤشر", "مؤشرات", "الاتجاه", "التقلب",
    }),
    "anum.trading.strategy-research": frozenset({
        "strategy", "strategies", "backtest", "backtests", "walk-forward", "validation", "revalidation", "overfit", "sharpe", "drawdown",
        "benchmark", "استراتيجية", "استراتيجيات", "اختبار", "التحقق",
    }),
    "anum.trading.portfolio": frozenset({
        "portfolio", "portfolios", "positions", "holdings", "allocation", "diversification", "benchmark", "محفظة", "المحفظة", "التنويع",
    }),
    "anum.trading.risk": frozenset({
        "guardian", "risk", "drawdown", "kill-switch", "regulation", "regulator", "vara", "sca",
        "المخاطر", "رفض", "الحارس", "أوقف",
    }),
    "anum.trading.operations": frozenset({
        "supervisor", "cycle", "cycles", "revalidation", "revalidate", "trading", "المشرف", "وكلاء", "التداول",
    }),}


class KnowledgeError(ValueError):
    pass


@dataclass(frozen=True)
class Reference:
    path: str
    title: str
    text: str
    sources: list[dict[str, str]]


@dataclass(frozen=True)
class SkillPackage:
    folder: str
    name: str
    description: str
    version: str
    skill_id: str
    body: str
    sections: dict[str, str]
    references: list[Reference]
    tools: dict[str, Any]
    triggers: frozenset[str] = field(default_factory=frozenset)

    @property
    def permitted_tools(self) -> list[str]:
        return [t["name"] for t in self.tools.get("permitted", [])]

    def instructions(self) -> str:
        """The skill and its references as one text, for publishing in the skill registry."""
        parts = [self.body.strip()]
        for ref in self.references:
            parts.append(ref.text.strip())
        return "\n\n".join(parts)


def _front_matter(text: str, where: str) -> tuple[dict[str, str], str]:
    found = _FRONT.match(text)
    if not found:
        raise KnowledgeError(f"{where}: SKILL.md must start with front matter between --- lines.")
    values: dict[str, str] = {}
    for line in found.group(1).splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise KnowledgeError(f"{where}: front matter line {line!r} is not key: value.")
        values[key.strip()] = value.strip()
    return values, text[found.end():]


def _sections(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in re.split(r"^## ", body, flags=re.M)[1:]:
        title, _, rest = chunk.partition("\n")
        out[title.strip()] = rest.strip()
    return out


def _sources(text: str, where: str) -> list[dict[str, str]]:
    if "## Sources" not in text:
        raise KnowledgeError(f"{where}: every reference ends with a '## Sources' table.")
    table = text.split("## Sources", 1)[1]
    rows = [line for line in table.splitlines() if line.startswith("|")]
    if len(rows) < 3:
        raise KnowledgeError(f"{where}: the sources table needs a header, a divider and at least one source.")
    header = [cell.strip() for cell in rows[0].strip("|").split("|")]
    if tuple(header) != SOURCE_COLUMNS:
        raise KnowledgeError(f"{where}: the sources table columns are {', '.join(SOURCE_COLUMNS)}.")
    out = []
    for row in rows[2:]:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if len(cells) != len(SOURCE_COLUMNS) or not all(cells):
            raise KnowledgeError(f"{where}: every source row fills all five columns: {row[:80]}")
        out.append(dict(zip(SOURCE_COLUMNS, cells)))
    return out


def _load(folder: Path) -> SkillPackage:
    where = folder.name
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    meta, body = _front_matter(text, where)
    for key in ("name", "description", "version", "anum_skill_id"):
        if not meta.get(key):
            raise KnowledgeError(f"{where}: front matter needs {key}.")
    if meta["name"] != folder.name:
        raise KnowledgeError(f"{where}: the name must equal the folder name.")
    if not re.fullmatch(r"\d+\.\d+\.\d+", meta["version"]):
        raise KnowledgeError(f"{where}: the version is MAJOR.MINOR.PATCH.")
    sections = _sections(body)
    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
    if missing:
        raise KnowledgeError(f"{where}: SKILL.md is missing section(s): {', '.join(missing)}.")
    references = []
    for path in sorted((folder / "references").glob("*.md")):
        ref_text = path.read_text(encoding="utf-8")
        title = ref_text.splitlines()[0].lstrip("# ").strip() if ref_text else path.stem
        references.append(Reference(path=f"references/{path.name}", title=title, text=ref_text, sources=_sources(ref_text, f"{where}/{path.name}")))
    if not references:
        raise KnowledgeError(f"{where}: a skill needs at least one reference document.")
    for link in re.findall(r"\]\((references/[^)]+)\)", sections["Knowledge"]):
        if not (folder / link).is_file():
            raise KnowledgeError(f"{where}: the Knowledge section links a missing file {link}.")
    for name, content in [("SKILL.md", text), *((r.path, r.text) for r in references)]:
        if _LONG_DASHES.search(content):
            raise KnowledgeError(f"{where}/{name}: use plain punctuation, no long dashes.")
    tools = json.loads((folder / "tools.json").read_text(encoding="utf-8"))
    for key in ("permitted", "prohibited", "failure_handling"):
        if key not in tools:
            raise KnowledgeError(f"{where}: tools.json needs {key}.")
    return SkillPackage(
        folder=folder.name, name=meta["name"], description=meta["description"], version=meta["version"], skill_id=meta["anum_skill_id"],
        body=body, sections=sections, references=references, tools=tools, triggers=TRIGGERS.get(meta["anum_skill_id"], frozenset()),
    )


@lru_cache(maxsize=1)
def load_packages() -> tuple[SkillPackage, ...]:
    packages = tuple(_load(folder) for folder in sorted(p for p in ROOT.iterdir() if p.is_dir()))
    ids = [p.skill_id for p in packages]
    if len(set(ids)) != len(ids):
        raise KnowledgeError("Skill ids must be unique.")
    return packages


def skill_manifests() -> list[Any]:
    """The packages as agent runtime skills (selected where all their tools are registered)."""
    from ...agent_skills import SkillManifest

    return [
        SkillManifest(id=p.skill_id, version=p.version, name=p.name.replace("-", " ").capitalize(), description=p.description,
                      triggers=p.triggers, required_tools=frozenset(p.permitted_tools))
        for p in load_packages()
    ]


def skill_version(package: SkillPackage) -> dict[str, Any]:
    """What a workspace administrator can publish through the skill registry (``SkillVersionCreate``)."""
    return {"skill_id": package.skill_id, "version": package.version, "name": package.name, "description": package.description[:2000],
            "instructions": package.instructions()[:50_000], "required_tools": package.permitted_tools, "risk_level": "low"}
