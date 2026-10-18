"""Phone numbers: what may be dialled, how numbers are matched, hashed and masked (docs/calls.md#numbers)."""

from __future__ import annotations

import hashlib
import re

from ..suppression import normalize as normalize_recipient

_SEPARATORS = re.compile(r"[\s\-(). /]")
_DIALABLE = re.compile(r"^\+?[0-9*#]{3,20}$")
# Arabic-Indic and Eastern Arabic-Indic digits, as typed on an Arabic keyboard.
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


class PhoneNumberError(ValueError):
    """Not a number a phone can dial."""


def dialable(value: str) -> str:
    """The number as a phone dials it (``+``, digits, ``*`` and ``#``), or ``PhoneNumberError``.

    Spaces, dashes, dots, slashes and brackets are dropped; ``00`` stays as typed (the
    phone's dialer reads it). Short codes and extensions with ``*`` or ``#`` are allowed.
    """
    text = _SEPARATORS.sub("", (value or "").strip().translate(_DIGITS))
    if not _DIALABLE.match(text) or sum(ch.isdigit() for ch in text) < 3:
        raise PhoneNumberError("Enter a phone number: digits, an optional + at the start, * or #, 3 to 20 characters.")
    return text


def normalized(value: str) -> str:
    """The form numbers are matched on: international ``+<digits>`` when it can be told,
    else the digits as dialled (a short code or an extension)."""
    text = dialable(value)
    found = normalize_recipient(text) if "*" not in text and "#" not in text else None
    if found is not None and found[0] == "phone":
        return found[1]
    return text


def international(value: str) -> str | None:
    """``+<8 to 15 digits>`` for a number a voice provider can call, else None."""
    try:
        text = normalized(value)
    except PhoneNumberError:
        return None
    return text if re.fullmatch(r"\+[0-9]{8,15}", text) else None


def masked(number: str) -> str:
    """For lists and logs: the first four and last two characters."""
    if len(number) <= 6:
        return number[:1] + "•••"
    return f"{number[:4]}•••{number[-2:]}"


def opt_out_hash(tenant_id: str, workspace_id: str, number: str) -> str:
    """What an AI call opt-out stores instead of the number (per workspace, so lists cannot be joined)."""
    material = f"anum-call-optout-v1\x1f{tenant_id}\x1f{workspace_id}\x1f{number}".encode()
    return hashlib.sha256(material).hexdigest()
