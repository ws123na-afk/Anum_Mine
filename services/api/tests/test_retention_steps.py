"""The daily retention job runs every step even when one fails (reliability audit, November 2026).

``python -m anum_api.voice_retention`` used to run its steps in a row with nothing around
them: one exception (a missing grant, an object store that could not be reached) ended the
process, and every later purge and reminder was skipped that day.
"""

from __future__ import annotations

import importlib
import json
import sys
import types

import pytest

from anum_api import voice_retention
from anum_api.settings import settings


def _module(monkeypatch: pytest.MonkeyPatch, name: str, **functions: object) -> str:
    module = types.ModuleType(name)
    for key, value in functions.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return name


def test_a_failing_step_is_reported_and_the_rest_still_run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ran: list[tuple[str, bool, int | None]] = []

    def good(name: str, with_batch: bool):
        if with_batch:
            def step(*, dry_run: bool = False, batch_size: int = 500) -> dict[str, object]:
                ran.append((name, dry_run, batch_size))
                return {"deleted": 1}
        else:
            def step(*, dry_run: bool = False) -> dict[str, object]:  # type: ignore[misc]
                ran.append((name, dry_run, None))
                return {"sent": 2}
        return step

    def broken(*, dry_run: bool = False, batch_size: int = 500) -> dict[str, object]:
        raise RuntimeError("secret statement text")

    steps = (
        (voice_retention.VOICE_STEP, _module(monkeypatch, "retention_fake_voice", purge=good("voice", True)), "purge", True),
        ("second", _module(monkeypatch, "retention_fake_broken", purge=broken), "purge", True),
        ("missing", "retention_fake_not_there", "purge", True),
        ("last", _module(monkeypatch, "retention_fake_last", run=good("last", False)), "run", False),
    )

    report, failed = voice_retention.run_steps(steps, dry_run=True, batch_size=7)

    assert ran == [("voice", True, 7), ("last", True, None)]
    assert failed == ["second", "missing"]
    # The voice counts stay at the top level; a failed step names the exception class only.
    assert report == {
        "deleted": 1,
        "second": {"error": "RuntimeError"},
        "missing": {"error": "ModuleNotFoundError"},
        "last": {"sent": 2},
        "failed_steps": ["second", "missing"],
    }
    assert "secret statement text" not in json.dumps(report)
    assert "retention step second failed" in capsys.readouterr().err


def test_main_exits_non_zero_when_a_step_failed_and_zero_otherwise(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    calls: list[str] = []

    def ok(*, dry_run: bool = False, batch_size: int = 500) -> dict[str, object]:
        calls.append("ok")
        return {"dry_run": dry_run}

    def broken(*, dry_run: bool = False, batch_size: int = 500) -> dict[str, object]:
        calls.append("broken")
        raise ValueError("no")

    good_module = _module(monkeypatch, "retention_fake_ok", purge=ok)
    bad_module = _module(monkeypatch, "retention_fake_bad", purge=broken)
    monkeypatch.setattr(voice_retention, "STEPS", (("a", bad_module, "purge", True), ("b", good_module, "purge", True)))

    assert voice_retention.main(["--dry-run"]) == 1
    assert calls == ["broken", "ok"]
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"a": {"error": "ValueError"}, "b": {"dry_run": True}, "failed_steps": ["a"]}

    monkeypatch.setattr(voice_retention, "STEPS", (("b", good_module, "purge", True),))
    assert voice_retention.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"b": {"dry_run": False}}


def test_every_daily_step_names_a_function_that_exists_and_takes_its_arguments() -> None:
    import inspect

    names = [name for name, *_ in voice_retention.STEPS]
    assert len(names) == len(set(names)) and names[0] == voice_retention.VOICE_STEP
    for name, module, function, takes_batch_size in voice_retention.STEPS:
        step = getattr(importlib.import_module(module, "anum_api"), function)
        accepted = inspect.signature(step).parameters
        assert "dry_run" in accepted, name
        assert ("batch_size" in accepted) is takes_batch_size, name
