"""The card security code (CVC) entered on the approval card (docs/action-browser.md#the-card-security-code).

PCI DSS forbids keeping a card's security code after authorisation, encrypted or not, so ANUM
never stores it: saved cards hold the number, the expiry and the name only. When a step types
``{{card:NAME.cvc}}`` it always waits for the owner, and the approval's arguments carry the
fixed marker ``"cvc": "entered at approval"`` (so the payload hash binds the marker, never the
digits). The owner types the code into a masked field on the web approval card; it travels with
the approve request over TLS, is held only in this process's memory for that one execution
(:func:`with_card_code`), is taken once by the tool (:func:`take_card_code`) and handed to the
browser service in the single ``act`` request that types it, then dropped. It is never written
to the approval, the run, a step, an event, the audit log, a log line or the result.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Mapping
from contextvars import ContextVar
from typing import Any, TypeVar

from . import ACT_TOOL

CVC_MARKER = "entered at approval"
CODE_PATTERN = re.compile(r"^\d{3,4}$")

NEEDS_CODE = "Type the card's security code (3 or 4 digits) on the approval card to approve this step."
BAD_CODE = "The card's security code is 3 or 4 digits."
NOT_ASKED = "This approval does not ask for a card security code; leave it out."
NOT_INLINE = (
    "The card's security code can be entered only while tasks run inside the API (ANUM_RUNTIME_BACKEND=inline). "
    "Reject this step and finish the payment on the site yourself."
)
NO_CODE_AT_RUN = "The card's security code was not entered at approval, so nothing was typed. Start the task again and type it on the web approval card."
CVC_NOT_STORED = (
    "ANUM never stores a card's security code (CVC). Leave it out: you type it on the approval card "
    "each time a payment needs it."
)

T = TypeVar("T")


class CardCode:
    """One security code, usable once. Its text never appears in a repr or a traceback."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value: str | None = value

    def take(self) -> str | None:
        value, self._value = self._value, None
        return value

    def clear(self) -> None:
        self._value = None

    def __repr__(self) -> str:
        return "CardCode(<hidden>)"


_CURRENT: ContextVar[CardCode | None] = ContextVar("anum_action_browser_card_code", default=None)


def needs_card_code(action: str, arguments: Mapping[str, Any] | None) -> bool:
    """Whether approving this call needs the owner to type the card's security code."""
    return action == ACT_TOOL and isinstance(arguments, Mapping) and arguments.get("cvc") == CVC_MARKER


def card_code_problem(*, needed: bool, code: str | None, inline: bool) -> tuple[int, str] | None:
    """Why an approve request cannot go ahead with this code, as (status, message), or None."""
    if not needed:
        return (422, NOT_ASKED) if code else None
    if not code:
        return 422, NEEDS_CODE
    if not CODE_PATTERN.match(code):
        return 422, BAD_CODE
    if not inline:
        return 409, NOT_INLINE
    return None


async def with_card_code(code: str, work: Awaitable[T]) -> T:
    """Run ``work`` (one approved execution) with the code available once, then drop it."""
    holder = CardCode(code)
    token = _CURRENT.set(holder)
    try:
        return await work
    finally:
        holder.clear()
        _CURRENT.reset(token)


def take_card_code() -> str | None:
    """The code the owner typed for the approval being executed, once; None when there is none."""
    holder = _CURRENT.get()
    return holder.take() if holder is not None else None


__all__ = [
    "BAD_CODE",
    "CVC_MARKER",
    "CVC_NOT_STORED",
    "NEEDS_CODE",
    "NOT_ASKED",
    "NOT_INLINE",
    "NO_CODE_AT_RUN",
    "CardCode",
    "card_code_problem",
    "needs_card_code",
    "take_card_code",
    "with_card_code",
]
