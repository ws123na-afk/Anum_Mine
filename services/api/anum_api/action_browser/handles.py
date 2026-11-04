"""Saved-secret handles, sites and masking (docs/action-browser.md#saved-logins-and-cards).

The planner model never sees a password or a card number. It writes a handle such as
``{{login:emirates.password}}`` or ``{{card:main.number}}``; the tool replaces it with the
saved value inside the browser service at the moment of typing. Pure: no settings, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

HANDLE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
HANDLE = re.compile(r"^\{\{(login|card):([a-z0-9][a-z0-9-]{0,39})\.([a-z_]{3,10})\}\}$")
LOGIN_PARTS = ("username", "password")
CARD_PARTS = ("number", "expiry", "exp_month", "exp_year", "cvc", "name")
# The security code is a handle the model may write, but it is never saved: the owner types it on
# the approval card for that one step (card_code.py, docs/action-browser.md#the-card-security-code).
CODE_PART = "cvc"
SITE = re.compile(r"^(?=.{3,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
HTTPS_ADDRESS = re.compile(r"https://[^\s<>\"'\u200f\u200e)\]]+", re.IGNORECASE)


@dataclass(frozen=True)
class Handle:
    kind: str  # login or card
    name: str
    part: str

    @property
    def text(self) -> str:
        return f"{{{{{self.kind}:{self.name}.{self.part}}}}}"

    @property
    def secret(self) -> bool:
        """Parts never shown, even masked: the password, the card number and the CVC."""
        return self.part in {"password", "number", "cvc"}


def parse_handle(value: str) -> Handle | None:
    """The handle ``value`` is exactly, or None. A value with a handle and other text is not one."""
    match = HANDLE.match((value or "").strip())
    if not match:
        return None
    kind, name, part = match.groups()
    if (kind == "login" and part not in LOGIN_PARTS) or (kind == "card" and part not in CARD_PARTS):
        return None
    return Handle(kind, name, part)


def valid_handle_name(name: str) -> bool:
    return bool(HANDLE_NAME.match(name or ""))


def luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or not 12 <= len(digits) <= 19:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char)
        if index % 2 == 1:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def looks_like_card_number(text: str) -> bool:
    """13 to 19 digits (spaces and dashes allowed) that pass the Luhn check."""
    compact = re.sub(r"[\s-]", "", (text or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
    return compact.isdigit() and 13 <= len(compact) <= 19 and luhn_ok(compact)


def card_brand(number: str) -> str:
    digits = re.sub(r"\D", "", number or "")
    if digits.startswith("4"):
        return "Visa"
    if digits[:2] in {"51", "52", "53", "54", "55"} or (digits[:4].isdigit() and 2221 <= int(digits[:4] or 0) <= 2720):
        return "Mastercard"
    if digits[:2] in {"34", "37"}:
        return "American Express"
    if digits.startswith("6"):
        return "Discover"
    return "Card"


def site_of(host: str) -> str:
    """The site a host belongs to for the approved list: the host without a leading ``www.``."""
    host = (host or "").strip().lower().rstrip(".")
    return host[4:] if host.startswith("www.") and host.count(".") >= 2 else host


def host_in_sites(host: str, sites: list[str] | set[str] | tuple[str, ...]) -> bool:
    """Whether ``host`` is one of the sites or a subdomain of one (``fly.example.com`` is in ``example.com``)."""
    host = (host or "").strip().lower().rstrip(".")
    return bool(host) and any(host == site or host.endswith("." + site) for site in sites if site)


def normalize_site(value: str) -> str | None:
    """A site the owner typed (``emirates.com``, ``https://www.emirates.com/login``) as ``emirates.com``, or None."""
    text = (value or "").strip().lower()
    if "://" in text:
        try:
            text = urlsplit(text).hostname or ""
        except ValueError:
            return None
    text = site_of(text.split("/", 1)[0].split(":", 1)[0])
    return text if SITE.match(text) else None


def prompt_addresses(prompt: str) -> list[str]:
    """The https addresses in the user's own words, in order, without trailing punctuation."""
    found: list[str] = []
    for match in HTTPS_ADDRESS.finditer(prompt or ""):
        address = match.group(0).rstrip(".,;:!?،؛")
        try:
            parts = urlsplit(address)
        except ValueError:
            continue
        if parts.hostname and address not in found:
            found.append(address)
    return found[:10]


def prompt_sites(prompt: str) -> list[str]:
    sites: list[str] = []
    for address in prompt_addresses(prompt):
        site = normalize_site(address)
        if site and site not in sites:
            sites.append(site)
    return sites[:10]


def mask_username(value: str) -> str:
    text = (value or "").strip()
    if "@" in text:
        local, _, domain = text.partition("@")
        return f"{local[:1]}***@{domain}" if local else f"***@{domain}"
    if len(text) <= 2:
        return "***"
    return f"{text[:1]}***{text[-1:]}"


def card_hint(number: str, expiry: str) -> str:
    digits = re.sub(r"\D", "", number or "")
    return f"{card_brand(digits)} ending {digits[-4:]}, expires {expiry}" if digits else "Card"


def masked_handle(handle: Handle, hints: dict[str, str] | None = None) -> str:
    """How a handle is shown to people: the handle itself, plus the saved record's masked hint."""
    hint = (hints or {}).get(f"{handle.kind}:{handle.name}")
    return f"{handle.text} ({hint})" if hint else handle.text


__all__ = [
    "CARD_PARTS", "CODE_PART", "Handle", "LOGIN_PARTS", "card_brand", "card_hint", "host_in_sites", "looks_like_card_number", "luhn_ok", "mask_username",
    "masked_handle", "normalize_site", "parse_handle", "prompt_addresses", "prompt_sites", "site_of", "valid_handle_name",
]
