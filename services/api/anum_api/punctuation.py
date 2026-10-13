"""No long dashes in what people read (owner rule, 13 Oct 2026).

Every model reply passes through :func:`plain_dashes` (a whole text) or
:class:`DashFilter` (a streamed reply, delta by delta) in the model gateways, and the
system prompts carry :data:`PLAIN_PUNCTUATION_RULE`. An em dash (U+2014), an en dash
(U+2013) or a horizontal bar (U+2015) becomes what a person would write instead:

* between two clauses ("word, dash, word", spaced or not): a comma, the Arabic comma
  after an Arabic letter;
* between two numbers ("10, en dash, 15"): a plain hyphen, ``10-15``;
* after punctuation ("done. dash next"): nothing, the punctuation already separates;
* before closing punctuation or a closing quote: nothing;
* at the end of a line after a word or digit: a colon;
* at the start of a line (a list written with dashes): a plain hyphen, ``- item``.

This module and the sources that strip dashes from input are the only places allowed
to contain the characters (``services/api/tests/test_no_long_dashes.py``).
"""

from __future__ import annotations

import re

# Built from code points so that the source itself stays free of the characters.
EN_DASH, EM_DASH, HORIZONTAL_BAR = chr(0x2013), chr(0x2014), chr(0x2015)
LONG_DASHES = EN_DASH + EM_DASH + HORIZONTAL_BAR
PLAIN_PUNCTUATION_RULE = (
    "Never use em dashes or en dashes in anything you write: use a colon, a comma, "
    '"to" for a range or a new sentence instead.'
)

_SPACES = " \t" + chr(0xA0)
_DASH_RUN = re.compile(f"[{_SPACES}]*[{LONG_DASHES}]+[{_SPACES}]*")
_ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
_ARABIC_COMMA, _ARABIC_SEMICOLON, _ARABIC_QUESTION = chr(0x060C), chr(0x061B), chr(0x061F)
# Opening and closing quotes: guillemets and curly quotes.
_OPEN_QUOTES = chr(0xAB) + chr(0x201C) + chr(0x2018)
_CLOSE_QUOTES = chr(0xBB) + chr(0x201D) + chr(0x2019)
_OPENING = set("([{\"'/" + _OPEN_QUOTES)
_SEPARATED_BEFORE = set(",;:.!?" + _ARABIC_COMMA + _ARABIC_SEMICOLON + _ARABIC_QUESTION) | _OPENING
_CLOSING_AFTER = set(",;:.!?)]}\"'" + _ARABIC_COMMA + _ARABIC_SEMICOLON + _ARABIC_QUESTION + _CLOSE_QUOTES)
_LINE_BREAKS = ("", "\n", "\r")


def _is_arabic(char: str) -> bool:
    return bool(char) and any(low <= ord(char) <= high for low, high in _ARABIC_RANGES)


def _replacement(match: re.Match[str]) -> str:
    text = match.string
    before = text[match.start() - 1] if match.start() > 0 else ""
    after = text[match.end()] if match.end() < len(text) else ""
    if before.isdigit() and after.isdigit():
        return "-"
    if before in _LINE_BREAKS:
        return "" if after in _LINE_BREAKS else "- "
    if after in _LINE_BREAKS:
        return ":" if before.isalnum() else ""
    if before in _SEPARATED_BEFORE:
        return "" if before in _OPENING else " "
    if after in _CLOSING_AFTER:
        return ""
    return f"{_ARABIC_COMMA} " if _is_arabic(before) else ", "


def plain_dashes(text: str) -> str:
    """``text`` with every long dash replaced (see the module docstring)."""
    if not text or not any(dash in text for dash in LONG_DASHES):
        return text
    return _DASH_RUN.sub(_replacement, text)


def plain_dashes_in(value: object) -> object:
    """``value`` (parsed JSON: dicts, lists, strings) with every string cleaned."""
    if isinstance(value, str):
        return plain_dashes(value)
    if isinstance(value, list):
        return [plain_dashes_in(item) for item in value]
    if isinstance(value, dict):
        return {key: plain_dashes_in(item) for key, item in value.items()}
    return value


class DashFilter:
    """:func:`plain_dashes` for a streamed reply, one delta at a time.

    A dash's replacement depends on the characters around it, and those may arrive in
    the next delta, so the filter holds back trailing spaces and a trailing dash until
    the next visible character (or :meth:`flush`) shows what follows. Nothing else is
    delayed, and the joined output equals ``plain_dashes`` of the whole reply.
    """

    def __init__(self) -> None:
        self._pending = ""
        self._before = ""  # the last character emitted: the context of the next dash

    def feed(self, delta: str) -> str:
        text = self._pending + delta
        cut = len(text.rstrip(_SPACES))
        if cut and text[cut - 1] in LONG_DASHES:
            # Hold the dash (and the spaces before it) until what follows is known.
            last = None
            for last in _DASH_RUN.finditer(text, 0, cut):
                pass
            if last is not None:
                cut = last.start()
        self._pending = text[cut:]
        return self._emit(text[:cut])

    def flush(self) -> str:
        """Whatever was held back, once the reply is complete."""
        text, self._pending = self._pending, ""
        return self._emit(text)

    def _emit(self, chunk: str) -> str:
        if not chunk:
            return ""
        context = self._before
        self._before = chunk[-1]
        # The context character is never part of a dash run: an emitted chunk never
        # ends in a space or a dash, so it only tells the next dash what precedes it.
        return plain_dashes(context + chunk)[len(context):]
