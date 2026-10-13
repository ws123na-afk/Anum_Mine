"""No em dashes or en dashes in any user-facing text or AI reply (owner rule, 13 Oct 2026).

Scans every source a person reads text from: the web app (src, public, index.html), the
API's modules, blueprints and seed data, the Flutter app (lib, l10n), the Android app
(Kotlin and res/values strings) and the desktop shell. Model replies are cleaned at run
time by ``anum_api.punctuation`` (tests in ``test_punctuation.py``).

Use a colon, a comma, "to" for a range or a new sentence; an empty value is "-".
The only allowed long dashes are a blueprint item's ``former_names`` (the names an older
plan wrote, kept so re-applying the plan finds and renames those items).

Docs count too: every Markdown file under ``docs/``, the READMEs, ``CLAUDE.md`` and ANUM's
own skills. Not scanned, on purpose: tests (fixtures that feed long dashes to the filter or
look up former names), third-party skills vendored byte for byte under ``.claude/skills``
(``SOURCES.md``, ``THIRD_PARTY_NOTICES.md``), lockfiles and build output. Code that has to
recognise the characters builds them from code points (``chr(0x2014)``), as this file does.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LONG_DASHES = (chr(0x2013), chr(0x2014), chr(0x2015))
TEXT_SUFFIXES = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".css", ".html", ".json", ".md", ".txt", ".svg", ".webmanifest",
    ".py", ".yaml", ".yml", ".dart", ".arb", ".kt", ".xml", ".rs", ".toml",
}
SKIP_DIRS = {"node_modules", "dist", "build", ".dart_tool", "target", "__pycache__", "test-results", "gen"}
# Where a person can read text the code ships.
UI_SOURCES = (
    "apps/web/src",
    "apps/web/public",
    "apps/web/index.html",
    "services/api/anum_api",
    "apps/mobile/lib",
    "apps/android/app/src/main/java",
    "apps/android/app/src/main/res",
    "apps/desktop/src-tauri/src",
    "apps/desktop/src-tauri/tauri.conf.json",
)


# Where a person reads the docs and the repository's own guidance.
DOC_SOURCES = (
    "docs",
    "README.md",
    "CLAUDE.md",
    "apps/web/README.md",
    "apps/mobile/README.md",
    "apps/desktop/README.md",
    "apps/android/README.md",
    "services/api/README.md",
    ".claude/skills/SOURCES.md",
    ".claude/skills/anum-verify",
    ".claude/skills/anum-release-gate",
    ".claude/skills/steward",
    ".claude/skills/mockup-to-flutter",
)


def _doc_files() -> Iterator[Path]:
    for entry in DOC_SOURCES:
        path = REPO_ROOT / entry
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from (found for found in sorted(path.rglob("*.md")) if not SKIP_DIRS.intersection(found.parts))


def _files() -> Iterator[Path]:
    for entry in UI_SOURCES:
        path = REPO_ROOT / entry
        if path.is_file():
            yield path
            continue
        for found in sorted(path.rglob("*")):
            if found.is_file() and found.suffix in TEXT_SUFFIXES and not SKIP_DIRS.intersection(found.parts):
                yield found


def _without_former_names(value: object) -> object:
    if isinstance(value, dict):
        return {key: _without_former_names(item) for key, item in value.items() if key != "former_names"}
    if isinstance(value, list):
        return [_without_former_names(item) for item in value]
    return value


def _text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json" and "blueprints" in path.parts:
        return json.dumps(_without_former_names(json.loads(text)), ensure_ascii=False)
    return text


def test_the_scan_reaches_every_app() -> None:
    files = list(_files())
    for entry in UI_SOURCES[:6]:
        assert any(str(file).startswith(str(REPO_ROOT / entry)) for file in files), entry
    assert len(files) > 300


def test_no_user_facing_source_contains_a_long_dash() -> None:
    found = []
    for path in _files():
        for number, line in enumerate(_text(path).splitlines(), start=1):
            if any(dash in line for dash in LONG_DASHES):
                found.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()[:120]}")
    assert found == [], 'use a colon, a comma, "to" for a range or a new sentence instead of a long dash:\n' + "\n".join(found)


def test_no_doc_contains_a_long_dash() -> None:
    docs = list(_doc_files())
    assert len(docs) > 50 and REPO_ROOT / "CLAUDE.md" in docs
    found = [
        f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()[:120]}"
        for path in docs
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if any(dash in line for dash in LONG_DASHES)
    ]
    assert found == [], 'use a colon, a comma, "to" for a range or a new sentence instead of a long dash:\n' + "\n".join(found)
