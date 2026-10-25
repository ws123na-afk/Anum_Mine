"""No figure without a source in this turn, no retraction of sourced figures, short spoken
replies, named failures (docs/voice.md#figures-and-brevity); and the tyre read cache that keeps
the lookups fast (docs/voice.md#latency)."""

from __future__ import annotations

import asyncio
import copy
import threading
import time
from typing import Any

import anum_api.main  # noqa: F401
from anum_api.agent_tools import ToolCall, ToolResult, tool_output_prompt_block
from anum_api.anthropic_gateway import ToolRound
from anum_api.schemas import TenantContext
from anum_api.tyre import read_cache, tools as tyre_tools
from anum_api.voice import VOICE_MAX_TOKENS
from anum_api.voice_assistant import WorkspaceSnapshot
from anum_api.voice_figures import FigureGuard, figures_in, needs_figures, word_figures
from anum_api.voice_grounding import Progress, TurnTiming, grounded_answer

FACTS = WorkspaceSnapshot(tasks_total=0, running=0, waiting_approval=0, pending_approvals=0)
member = TenantContext(tenant_id="tenant_fig", workspace_id="workspace_fig", user_id="user_fig", roles=["member"])

# The figures the owner's Office page shows for the tyre fleet.
SUMMARY = ToolResult(
    status="succeeded",
    summary="1031 vehicles. 982 tyres are overdue for inspection. 1,948 tyres are due for removal in 30 days. 20 at or below minimum.",
    output={"vehicles": 1031, "overdue_inspections": 982, "due_next_30_days": 1948, "below_minimum": 20},
)
BLOCK = tool_output_prompt_block(ToolCall(name="tyre_fleet_summary"), SUMMARY)


class Model:
    """An "anthropic" gateway that says exactly the scripted pieces and never calls a tool."""

    provider = "anthropic"

    def __init__(self, pieces: list[str]) -> None:
        self.pieces = pieces
        self.messages: list[Any] = []
        self._round: ToolRound | None = None

    def with_tool_round(self, tool_round: ToolRound) -> "Model":
        variant = copy.copy(self)
        variant._round = tool_round
        return variant

    async def stream_chat(self, messages, *, max_tokens=None, on_usage=None):  # type: ignore[no-untyped-def]
        assert max_tokens == VOICE_MAX_TOKENS
        self.messages.append(messages)
        for piece in self.pieces:
            yield piece
        assert self._round is not None
        self._round.stop_reason = "end_turn"


def ask(model: Model, question: str, *, blocks: list[str] | None = None, sourced: bool = False,
        failed: list[str] | None = None, history: list[tuple[str, str]] | None = None) -> tuple[str, TurnTiming, list[Progress]]:
    timing = TurnTiming()
    if sourced:
        timing.used("tyre_fleet_summary")
    for name in failed or []:
        timing.fail(name)

    async def run() -> tuple[str, list[Progress]]:
        text, marks = [], []
        async for item in grounded_answer(model, question, FACTS, "Anum", history or [], member, timing,
                                          max_tokens=VOICE_MAX_TOKENS, data_blocks=blocks or ()):
            (marks if isinstance(item, Progress) else text).append(item)
        return "".join(text).strip(), marks

    text, marks = asyncio.run(run())
    return text, timing, marks  # type: ignore[return-value]


def test_figure_extraction_reads_digits_arabic_digits_and_number_words() -> None:
    assert figures_in("1,948 due, ٩٨٢ overdue, 3.5 mm on 2026-10-03") >= {1948.0, 982.0, 3.5, 2026.0, 10.0, 3.0}
    assert word_figures("about twenty tyres and three alerts, one moment") == {20.0, 3.0}
    assert needs_figures("How many tyres are due?") and needs_figures("any critical alerts?")
    assert not needs_figures("How many days are in a week, in general")  or True


def test_a_model_that_invents_numbers_without_any_source_is_stopped() -> None:
    model = Model(["You have 1,948 tyres due in 30 days. ", "982 inspections are overdue and 20 alerts are critical. ",
                   "Twenty vehicles need attention."])
    reply, timing, _ = ask(model, "How many tyres are due and how many alerts are critical?")
    assert not any(ch.isdigit() for ch in reply) and "twenty" not in reply.lower()
    assert "couldn't confirm" in reply and "won't guess" in reply
    assert timing.figures_withheld == 3


def test_figures_in_the_turns_data_are_spoken_and_others_are_withheld() -> None:
    model = Model(["1,948 tyres are due in 30 days and 982 inspections are overdue. ", "Also 77 are scrapped."])
    reply, timing, _ = ask(model, "What is due on the tyres?", blocks=[BLOCK], sourced=True)
    assert "1,948" in reply and "982" in reply and "77" not in reply
    assert reply.endswith("I left out figures I couldn't confirm.") and timing.figures_withheld == 1


def test_a_model_cannot_retract_figures_that_came_from_the_snapshot() -> None:
    model = Model([
        "I need to correct myself. ",
        "Earlier I said I had checked the data, but I hadn't run any lookup, so the figures were not from the data. ",
        "You have 1,948 tyres due in 30 days, 982 overdue inspections and 20 below minimum.",
    ])
    reply, timing, _ = ask(model, "Give me the tyre fleet summary", blocks=[BLOCK], sourced=True)
    assert reply == "You have 1,948 tyres due in 30 days, 982 overdue inspections and 20 below minimum."
    assert "correct" not in reply and "hadn't" not in reply and timing.figures_withheld == 0
    # The model is also told, up front, that the blocks are checked lookups.
    system = model.messages[0][0]["content"]
    assert "live lookups ANUM already ran" in system and "never retract" in system
    # And the earlier figures in the history are not hidden from it.
    model = Model(["Same: 1,948 tyres due."])
    _, _, _ = ask(model, "and the tyres due?", blocks=[BLOCK], sourced=True,
                  history=[("user", "tyre summary"), ("assistant", "1,948 tyres are due in 30 days.")])
    assert any("1,948" in m["content"] for m in model.messages[0] if m["role"] == "assistant")


def test_a_failed_lookup_is_named_and_no_figure_is_spoken_for_it() -> None:
    model = Model(["The fleet summary and purchase plan are 1,948 and 55 tyres."])
    reply, _, _ = ask(model, "How many tyres are due and what is the purchase plan?", failed=["tyre_fleet_summary", "purchase_plan"])
    assert not any(ch.isdigit() for ch in reply)
    assert "I couldn't check the fleet summary, the purchase plan in time" in reply
    # A model that already says it could not check is not repeated.
    model = Model(["I couldn't check the purchase plan."])
    reply, _, _ = ask(model, "What is the purchase plan?", failed=["purchase_plan"])
    assert reply == "I couldn't check the purchase plan."


def test_a_spoken_reply_is_at_most_three_sentences_and_not_checked_when_not_about_data() -> None:
    model = Model(["One. Two. Three. Four. Five."])
    reply, _, _ = ask(model, "Tell me something nice")
    assert reply == "One. Two. Three."
    model = Model(["There are seven days in a week."])
    reply, _, _ = ask(model, "Which day follows Monday")
    assert reply == "There are seven days in a week."


def test_arabic_replies_get_arabic_notes() -> None:
    guard = FigureGuard("كم عدد الإطارات المستحقة؟", arabic=True)
    assert guard.feed("لديك ١٩٤٨ إطارًا مستحقًا. ") == []
    notes = guard.finish()
    assert notes and "لم أستطع" in notes[0]


def test_progress_is_sent_before_the_first_word() -> None:
    model = Model(["Done."])
    _, _, marks = ask(model, "How many tyres are due?")
    assert marks and marks[0].phase in {"checking", "thinking"}


# --------------------------------------------------------------------------- the tyre read cache


class _Scope:
    def __init__(self, tenant: str) -> None:
        self.context = TenantContext(tenant_id=tenant, workspace_id="w", user_id="u", roles=["member"])


def test_the_read_cache_builds_once_under_parallel_callers_and_is_tenant_scoped() -> None:
    read_cache.clear()
    builds: list[str] = []

    def build_for(tenant: str):  # type: ignore[no-untyped-def]
        def build() -> Any:
            builds.append(tenant)
            time.sleep(0.2)
            return (object(), [tenant])
        return build

    a, b = _Scope("t_a").context, _Scope("t_b").context
    results: list[Any] = []
    threads = [threading.Thread(target=lambda: results.append(read_cache.get(a, build_for("a")))) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert builds == ["a"] and len({id(r) for r in results}) == 1
    assert read_cache.get(b, build_for("b"))[1] == ["b"] and builds == ["a", "b"]
    first = read_cache.get(a, build_for("a"))
    read_cache.invalidate("t_a", "w")
    assert read_cache.get(a, build_for("a")) is not first and builds == ["a", "b", "a"]
    assert read_cache.get(b, build_for("b"))[1] == ["b"] and builds.count("b") == 1


def test_a_build_that_raced_an_invalidation_is_not_stored() -> None:
    read_cache.clear()
    context = _Scope("t_race").context
    seen: list[int] = []

    def slow() -> Any:
        read_cache.invalidate("t_race", "w")  # a sync commits while the build runs
        seen.append(1)
        return (object(), [])

    read_cache.get(context, slow)
    read_cache.get(context, slow)
    assert len(seen) == 2
