"""Model commentary on the daily report (docs/finance-trading-intel.md#model-commentary). In memory.

Through the workspace's model gateway and budget; off without a real model; capped per month;
the report goes in as untrusted data; promises of profit are withheld; always labelled.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from test_finance_intel_reports_checks import _store_with_data_into
from test_finance_intel_supervisor import ME, _active

from anum_api import main
from anum_api.finance import config as finance_cfg
from anum_api.finance.intel import commentary
from anum_api.finance.store import finance_store
from anum_api.model_gateway import MockModelGateway, ModelResponse, ModelUsage
from anum_api.schemas import utc_now

client = TestClient(main.app)
HEADERS = {"x-tenant-id": ME.tenant_id, "x-workspace-id": ME.workspace_id, "x-user-id": ME.user_id, "x-user-roles": "member"}


class FakeModel:
    provider = "anthropic"
    model = "claude-test"

    def __init__(self, text: str = "Simulated results only. One order waits for your approval; data for AAA is current.") -> None:
        self.text = text
        self.prompts: list[str] = []
        self.fail: Exception | None = None

    async def generate_text(self, prompt: str) -> ModelResponse:
        self.prompts.append(prompt)
        if self.fail:
            raise self.fail
        return ModelResponse(text=self.text, usage=ModelUsage(input_tokens=100, output_tokens=40, provider="anthropic", model="claude-test"))


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    yield
    finance_store.clear()
    commentary.set_base_gateway(None)


def _report() -> dict:
    _store_with_data_into(finance_store)
    _active(finance_store)
    result = client.post("/api/v1/finance/intel/supervisor/run", json={"phase": "post_close"}, headers=HEADERS).json()
    return client.get(f"/api/v1/finance/intel/daily-reports/{result['report_id']}", headers=HEADERS).json()


def test_without_a_model_there_is_no_commentary_and_the_report_stands() -> None:
    commentary.set_base_gateway(MockModelGateway())
    body = _report()
    assert body["markdown"].startswith("# Trading report")
    assert body["commentary"]["status"] == "skipped" and body["commentary"]["reason"] == "no_model" and body["commentary"]["text"] is None


def test_commentary_is_written_through_the_workspace_model_labelled_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    model = FakeModel("Simulated results only. " + "Data is current and one order waits for you. " * 80)
    commentary.set_base_gateway(model)
    monkeypatch.setattr(finance_cfg.finance_config, "commentary_max_chars", 300)
    body = _report()
    note = body["commentary"]
    assert note["status"] == "ok" and note["model"] == "anthropic/claude-test"
    assert len(note["text"]) <= 303 and note["text"].endswith("...")
    assert "Model commentary on SIMULATED results" in note["label"] and "not advice" in note["label"]
    [prompt] = model.prompts
    # The report is data inside an untrusted block; the instructions forbid predictions and profit claims.
    assert "<<untrusted-data" in prompt and "origin=finance.intel.daily_report" in prompt and "Never predict prices" in prompt


def test_profit_claims_are_withheld_and_failures_leave_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    commentary.set_base_gateway(FakeModel("This strategy is guaranteed to make money and AAA will rise next week."))
    assert _report()["commentary"]["status"] == "withheld"
    finance_store.clear()
    broken = FakeModel()
    broken.fail = RuntimeError("provider down")
    commentary.set_base_gateway(broken)
    body = _report()
    assert body["commentary"]["status"] == "failed" and body["report"]["label"] == "SIMULATED"
    for text in ("Results are simulated; nothing is certain.", "You can't lose here", "risk-free returns", "It will double"):
        expected_ok = text.startswith("Results")
        assert (commentary.clean(text)[0] is not None) is expected_ok, text
    dash = chr(0x2014)
    assert dash not in (commentary.clean(f"A {dash} B")[0] or "")


def test_monthly_cap_and_off_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    commentary.set_base_gateway(FakeModel())
    monkeypatch.setattr(finance_cfg.finance_config, "commentary_monthly_limit", 0)
    assert _report()["commentary"]["reason"] == "monthly_limit"
    finance_store.clear()
    monkeypatch.setattr(finance_cfg.finance_config, "commentary_monthly_limit", 31)
    monkeypatch.setattr(finance_cfg.finance_config, "commentary_enabled", False)
    assert _report()["commentary"]["reason"] == "disabled"


def test_the_workspace_model_budget_caps_commentary(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.model_budget import BudgetKind, BudgetScope, ModelBudgetExceededError

    model = FakeModel()
    model.fail = ModelBudgetExceededError(BudgetScope.WORKSPACE, BudgetKind.COST, utc_now().date())
    commentary.set_base_gateway(model)
    assert _report()["commentary"]["reason"] == "model_budget"


def test_another_person_never_sees_the_commentary() -> None:
    commentary.set_base_gateway(FakeModel())
    body = _report()
    other = {**HEADERS, "x-user-id": "someone_else"}
    assert client.get(f"/api/v1/finance/intel/daily-reports/{body['id']}", headers=other).status_code == 404
    assert commentary.write_commentary(ME.model_copy(update={"user_id": "someone_else"}), body["id"],
                                       now=datetime(2026, 10, 18, tzinfo=timezone.utc)) is None
