"""No long dashes in model replies (owner rule, 13 Oct 2026; anum_api/punctuation.py).

The filter runs in the gateways on whole replies, structured replies and streamed
deltas; the system prompts carry the rule. The dashes are built from code points.
"""

from __future__ import annotations

import asyncio
import random

import pytest
from pydantic import BaseModel
from test_anthropic_gateway import (
    STOP,
    ScriptedStream,
    _collect,
    _gateway,
    _json,
    _message,
    _message_delta,
    _message_start,
    _streaming,
    _text,
)

from anum_api.conversations import SYSTEM_PROMPT
from anum_api.model_gateway import MockModelGateway
from anum_api.prompt_provenance import with_untrusted_rules
from anum_api.punctuation import (
    LONG_DASHES,
    PLAIN_PUNCTUATION_RULE,
    DashFilter,
    plain_dashes,
    plain_dashes_in,
)

EM, EN, BAR = chr(0x2014), chr(0x2013), chr(0x2015)
ARABIC_COMMA = chr(0x060C)

CASES = [
    (f"We shipped it {EM} on time.", "We shipped it, on time."),
    (f"word{EM}word", "word, word"),
    (f"Prices rose 10{EN}15% in 2020{EN}2024.", "Prices rose 10-15% in 2020-2024."),
    (f"Pages 3 {EN} 9", "Pages 3-9"),
    (f"The plan {EM}\nStep one", "The plan:\nStep one"),
    (f"{EM} first item\n{EM} second", "- first item\n- second"),
    (f"Done. {EM} Next", "Done. Next"),
    (f"Stock is low {EM}, reorder", "Stock is low, reorder"),
    (f"a ({EM} b)", "a (b)"),
    (f"x {EN} y {BAR} z", "x, y, z"),
    (f"Send it {EM}{EM} now", "Send it, now"),
    ("No dashes - just a hyphen.", "No dashes - just a hyphen."),
    ("", ""),
]
ARABIC = [
    (f"تم الشحن {EM} في الموعد", f"تم الشحن{ARABIC_COMMA} في الموعد"),
    (f"الطلبات من 10{EN}20", "الطلبات من 10-20"),
    (f"ملاحظة {EM}\nالتفاصيل", "ملاحظة:\nالتفاصيل"),
]


@pytest.mark.parametrize(("raw", "plain"), CASES + ARABIC)
def test_plain_dashes_replaces_every_long_dash(raw: str, plain: str) -> None:
    assert plain_dashes(raw) == plain
    assert not any(dash in plain_dashes(raw) for dash in LONG_DASHES)


def _streamed(text: str, sizes: list[int]) -> str:
    dash_filter, out, start = DashFilter(), [], 0
    for size in sizes:
        out.append(dash_filter.feed(text[start : start + size]))
        start += size
    out.append(dash_filter.feed(text[start:]))
    out.append(dash_filter.flush())
    return "".join(out)


@pytest.mark.parametrize(("raw", "plain"), CASES + ARABIC)
def test_streaming_gives_the_same_text_however_the_reply_is_split(raw: str, plain: str) -> None:
    rng = random.Random(raw)
    for _ in range(60):
        sizes = [rng.randint(1, 4) for _ in range(len(raw))]
        assert _streamed(raw, sizes) == plain
    assert _streamed(raw, [1] * len(raw)) == plain


def test_streaming_holds_back_only_a_trailing_dash_or_spaces() -> None:
    dash_filter = DashFilter()
    assert dash_filter.feed("Ready") == "Ready"
    assert dash_filter.feed(f" {EM}") == ""  # what follows decides the replacement
    assert dash_filter.feed(" ") == ""
    assert dash_filter.feed("now.") == ", now."
    assert dash_filter.feed(" Done ") == " Done"
    assert dash_filter.flush() == " "


def test_structured_values_are_cleaned_recursively() -> None:
    value = {"title": f"A {EM} B", "items": [f"1{EN}2", {"note": f"x{EM}y"}], "count": 3, "ok": True}
    assert plain_dashes_in(value) == {"title": "A, B", "items": ["1-2", {"note": "x, y"}], "count": 3, "ok": True}


def test_every_system_prompt_carries_the_rule_once() -> None:
    assert PLAIN_PUNCTUATION_RULE in SYSTEM_PROMPT
    wrapped = with_untrusted_rules("You are an assistant.", "<<block>>")
    assert wrapped.count(PLAIN_PUNCTUATION_RULE) == 1
    assert with_untrusted_rules(wrapped).count(PLAIN_PUNCTUATION_RULE) == 1
    assert not any(dash in PLAIN_PUNCTUATION_RULE for dash in LONG_DASHES)


def test_a_streamed_reply_reaches_the_user_without_long_dashes() -> None:
    stream = ScriptedStream(
        [_message_start(), _text("The order shipped "), _text(EM), _text(" today"), _text(f", 2{EN}"), _text("3 days."),
         _message_delta(9), STOP]
    )
    gateway, _ = _gateway([_streaming(stream)])
    assert "".join(_collect(gateway)) == "The order shipped, today, 2-3 days."


def test_a_whole_reply_and_a_structured_reply_have_no_long_dashes() -> None:
    gateway, _ = _gateway([_json(_message(f"Four {EM} as asked."))])
    assert asyncio.run(gateway.generate_text("2+2?")).text == "Four, as asked."

    class Draft(BaseModel):
        subject: str
        body: str

    escaped = '{"subject": "Order \\u2014 update", "body": "Ships in 2\\u20133 days"}'
    gateway, _ = _gateway([_json(_message(escaped))])
    draft, _ = asyncio.run(gateway.generate_structured("Draft", Draft))
    assert (draft.subject, draft.body) == ("Order, update", "Ships in 2-3 days")


def test_the_mock_gateway_echo_has_no_long_dashes() -> None:
    async def run() -> str:
        return "".join([chunk async for chunk in MockModelGateway().stream_chat([{"role": "user", "content": f"Hi {EM} there"}])])

    assert not any(dash in asyncio.run(run()) for dash in LONG_DASHES)
