"""Which action-browser steps may run, which stop for the owner (docs/action-browser.md#which-steps-stop-for-approval).

Pure: the browser service describes the page and the target of a proposed step
(``kernel.inspect``), and :func:`classify` decides, outside the model:

* ``refuse``: never done (uploads, downloads, non-https, a card-like number or an
  out-of-scope handle typed as text, a login typed on another site);
* ``handoff``: the page needs the owner's own hands (a captcha, a one-time code, a bank
  check);
* ``gate``: the run pauses for the owner's approval (spending, sending, signing, binding
  orders, deleting, card entry, leaving the approved sites);
* ``allow``: everything else.

Words are matched in English (whole words) and Arabic (whole tokens, with the common
prefixes و ف ب ل ال removed). The amount is read from the button, the line labelled total,
or the largest on the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .handles import Handle, host_in_sites, looks_like_card_number, parse_handle, site_of

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٬٫", "01234567890123456789,.")

# Words on a control that make a click binding: spending, ordering, sending, signing, deleting.
BINDING_EN = re.compile(
    r"\b(?:buy|pay|purchase|book|reserve|confirm|place\s+(?:my\s+)?order|order\s+now|complete\s+(?:my\s+)?(?:order|purchase|booking|payment|checkout)"
    r"|send|submit|sign(?!\s*(?:in|on|out)\b)|sign\s*up|register|create\s+(?:an\s+)?account|delete|remove|erase|transfer|donate|subscribe"
    r"|cancel\s+(?:my\s+)?(?:order|booking|subscription|reservation|account|plan)|close\s+(?:my\s+)?account|publish|post|reply|agree\s+and)\b",
    re.IGNORECASE,
)
BINDING_AR = frozenset({
    "ادفع", "دفع", "الدفع", "اشتر", "اشتري", "شراء", "الشراء", "اشتراء", "احجز", "حجز", "الحجز", "تأكيد", "تاكيد", "أكد", "اكد",
    "إرسال", "ارسال", "أرسل", "ارسل", "وقع", "توقيع", "التوقيع", "احذف", "حذف", "الحذف", "امسح", "مسح", "تحويل", "التحويل", "حول",
    "اشترك", "اشتراك", "الاشتراك", "إتمام", "اتمام", "أتمم", "اتمم", "تبرع", "انشر", "نشر", "سجل", "التسجيل", "قدم", "تقديم",
})
# "حول" is also "about" ("حول الموقع"): only counted next to money words.
AMBIGUOUS_AR = frozenset({"حول", "سجل", "قدم"})
AR_PREFIXES = ("وال", "فال", "بال", "لل", "ال", "و", "ف", "ب", "ل")

CARD_FIELD = re.compile(
    r"card.?(?:number|num|no\b)|credit.?card|debit.?card|\bcc.?(?:num|number|exp|csc|cvv)|\bcvv|\bcvc|\bcsc\b|security.?code"
    r"|expir|\bexp.?(?:date|month|year|mm|yy)|\biban\b|account.?number|رقم\s*البطاقة|البطاقة|تاريخ\s*الانتهاء|رمز\s*الأمان|رمز\s*الامان|الآيبان|ايبان",
    re.IGNORECASE,
)
OTP_FIELD = re.compile(
    r"\botp\b|one.?time|verification.?code|verify.?code|2fa|two.?factor|auth(?:entication)?.?code|sms.?code|security.?pin|passcode"
    r"|رمز\s*التحقق|رمز\s*التأكيد|رمز\s*التاكيد|كلمة\s*المرور\s*لمرة\s*واحدة|الرمز\s*المرسل",
    re.IGNORECASE,
)
CAPTCHA_HOSTS = ("recaptcha.net", "hcaptcha.com", "challenges.cloudflare.com", "arkoselabs.com", "funcaptcha.com", "geetest.com", "captcha-delivery.com")
CAPTCHA_TEXT = re.compile(
    r"verify\s+(?:that\s+)?you\s+are\s+(?:a\s+)?human|i'?m\s+not\s+a\s+robot|are\s+you\s+a\s+robot|complete\s+the\s+security\s+check"
    r"|just\s+a\s+moment\.\.\.|press\s*&?\s*hold|g-recaptcha|h-captcha|cf-turnstile|captcha"
    r"|تحقق\s+من\s+أنك\s+إنسان|تحقق\s+من\s+انك\s+انسان|لست\s+روبوت|أنا\s+لست\s+برنامج\s+روبوت",
    re.IGNORECASE,
)
THREE_DS_HOSTS = ("cardinalcommerce", "3dsecure", "3ds.", "3ds-", "acs.", "acs-", "securecode", "arcot", "3dss.", "safekey")
THREE_DS_TEXT = re.compile(
    r"3-?d\s*secure|verified\s+by\s+visa|mastercard\s+(?:securecode|id\s+check|identity\s+check)|safekey|amex\s+safekey|\b3ds\b"
    r"|التحقق\s+الآمن|الأمان\s+ثلاثي\s+الأبعاد",
    re.IGNORECASE,
)
PAYMENT_HOSTS = (
    "stripe.com", "stripe.network", "adyen.com", "adyenpayments.com", "checkout.com", "paypal.com", "paypalobjects.com", "braintreegateway.com",
    "braintree-api.com", "squareup.com", "squarecdn.com", "paytabs.com", "telr.com", "network.ae", "ngenius-payments.com", "oppwa.com",
    "hyperpay.com", "tap.company", "gotapnow.com", "moyasar.com", "payfort.com", "amazonpaymentservices.com", "checkout.stripe.com",
)
CHECKOUT_ACTION = re.compile(r"checkout|payment|\bpay\b|/pay|order|purchase|billing|3ds|transfer|donat", re.IGNORECASE)
TOTAL_LABEL = re.compile(r"\btotal\b|amount\s+due|grand\s+total|to\s+pay|you\s+pay|الإجمالي|الاجمالي|المجموع|المبلغ|الإجمالية|المستحق", re.IGNORECASE)

CURRENCY_ALIASES: dict[str, str] = {
    "aed": "AED", "sar": "SAR", "usd": "USD", "eur": "EUR", "gbp": "GBP", "kwd": "KWD", "qar": "QAR", "bhd": "BHD", "omr": "OMR",
    "us$": "USD", "$": "USD", "€": "EUR", "£": "GBP", "dhs": "AED", "dh": "AED",
    "د.إ": "AED", "درهم": "AED", "دراهم": "AED", "ر.س": "SAR", "ريال": "SAR", "ريالا": "SAR", "ريالات": "SAR", "دولار": "USD", "يورو": "EUR",
}
_CUR = r"(?:AED|SAR|USD|EUR|GBP|KWD|QAR|BHD|OMR|US\$|Dhs?|\$|€|£|د\.إ|ر\.س|درهم|دراهم|ريالات|ريالا|ريال|دولار|يورو)"
_NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d{1,3})?|\d+(?:\.\d{1,3})?)"
AMOUNT = re.compile(rf"(?P<c1>{_CUR})\s?(?P<n1>{_NUM})|(?P<n2>{_NUM})\s?(?P<c2>{_CUR})(?![A-Za-z])", re.IGNORECASE)

ALLOW, GATE, HANDOFF, REFUSE = "allow", "gate", "handoff", "refuse"


@dataclass(frozen=True)
class Amount:
    value: float
    currency: str
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"amount": self.value, "currency": self.currency, "amount_text": self.text}


@dataclass(frozen=True)
class Decision:
    """``kind`` is allow, gate, handoff or refuse; ``code`` is for audit and the client."""

    kind: str
    code: str
    reason: str
    amount: Amount | None = None
    handle: Handle | None = None
    host: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _digits(text: str) -> str:
    return (text or "").translate(ARABIC_DIGITS)


def detect_amounts(text: str) -> list[Amount]:
    """Every amount with a currency in ``text`` (EN and AR, Western or Arabic digits)."""
    found: list[Amount] = []
    plain = _digits(text)
    for match in AMOUNT.finditer(plain[:20_000]):
        currency = (match.group("c1") or match.group("c2") or "").strip()
        number = (match.group("n1") or match.group("n2") or "").replace(",", "")
        code = CURRENCY_ALIASES.get(currency.lower()) or CURRENCY_ALIASES.get(currency)
        if not code:
            continue
        try:
            value = float(number)
        except ValueError:
            continue
        if value <= 0 or value > 100_000_000:
            continue
        found.append(Amount(value=round(value, 3), currency=code, text=" ".join(match.group(0).split())))
        if len(found) >= 50:
            break
    return found


def pick_amount(target_text: str, page_text: str, page_amounts: list[str] | None = None) -> Amount | None:
    """The amount a binding step is about: on the control, else on a line labelled total, else the largest."""
    on_target = detect_amounts(target_text)
    if on_target:
        return on_target[0]
    for line in _digits(page_text).splitlines():
        if TOTAL_LABEL.search(line):
            totals = detect_amounts(line)
            if totals:
                return totals[-1]
    candidates = detect_amounts(page_text)
    for text in page_amounts or []:
        candidates.extend(detect_amounts(text))
    if not candidates:
        return None
    return max(candidates, key=lambda amount: amount.value)


def _ar_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in re.findall(r"[؀-ۿ]+", text or ""):
        tokens.add(raw)
        for prefix in AR_PREFIXES:
            if raw.startswith(prefix) and len(raw) - len(prefix) >= 2:
                tokens.add(raw[len(prefix):])
    return tokens


def binding_words(text: str) -> str | None:
    """The binding word in a control's text (EN or AR), or None."""
    match = BINDING_EN.search(text or "")
    if match:
        return " ".join(match.group(0).split()).lower()
    tokens = _ar_tokens(text)
    hits = tokens & BINDING_AR
    strong = hits - AMBIGUOUS_AR
    if strong:
        return sorted(strong)[0]
    if hits and detect_amounts(text):
        return sorted(hits)[0]
    return None


def _field_text(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(key) or "") for key in ("name", "id", "label", "placeholder", "autocomplete", "aria"))


def is_card_field(item: dict[str, Any]) -> bool:
    autocomplete = str(item.get("autocomplete") or "").lower()
    if autocomplete.startswith("cc-") or " cc-" in autocomplete:
        return True
    return bool(CARD_FIELD.search(_field_text(item)))


def is_otp_field(item: dict[str, Any]) -> bool:
    if "one-time-code" in str(item.get("autocomplete") or "").lower():
        return True
    return bool(OTP_FIELD.search(_field_text(item)))


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    host = (host or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def _host_contains(host: str, parts: tuple[str, ...]) -> bool:
    host = (host or "").lower()
    return any(part in host for part in parts)


def _frame_hosts(page: dict[str, Any]) -> list[str]:
    return [str(host).lower() for host in (page.get("frames") or []) if host][:40]


def handoff_reason(page: dict[str, Any]) -> Decision | None:
    """A captcha, a one-time code or a bank check on the page, or None."""
    host = str(page.get("host") or "")
    text = str(page.get("text") or "")[:8000]
    frames = _frame_hosts(page)
    if any(_host_contains(frame, THREE_DS_HOSTS) for frame in frames) or THREE_DS_TEXT.search(text):
        return Decision(HANDOFF, "bank_check", f"Needs you: a bank security check (3-D Secure) on {host}.", host=host)
    if any(_host_matches(frame, CAPTCHA_HOSTS) or frame.endswith("google.com") and "recaptcha" in str(page.get("frame_urls") or "") for frame in frames) or CAPTCHA_TEXT.search(text) or page.get("challenge"):
        return Decision(HANDOFF, "captcha", f"Needs you: a captcha or human check on {host}.", host=host)
    fields = [item for item in (page.get("fields") or []) if isinstance(item, dict)]
    if any(is_otp_field(item) for item in fields if item.get("visible", True)):
        return Decision(HANDOFF, "one_time_code", f"Needs you: a one-time or verification code on {host}.", host=host)
    return None


def _control_text(target: dict[str, Any]) -> str:
    return " ".join(str(target.get(key) or "") for key in ("text", "label", "value", "aria", "title", "name"))[:400]


def _form_is_payment(form: dict[str, Any] | None, page: dict[str, Any]) -> str | None:
    if not form:
        return None
    fields = [item for item in (form.get("fields") or []) if isinstance(item, dict)]
    if any(is_card_field(item) for item in fields):
        return "card_fields"
    action = str(form.get("action") or "")
    if action and CHECKOUT_ACTION.search(urlsplit(action).path or ""):
        return "checkout_address"
    if detect_amounts(str(form.get("text") or "")):
        return "shows_amount"
    return None


def classify(step: dict[str, Any], *, sites: list[str], scope_logins: dict[str, str], scope_cards: set[str], card_grants: set[str] | None = None) -> Decision:
    """Decide one proposed step. ``step`` is ``{"action", "page", "target"}`` from ``kernel.inspect``.

    ``sites`` are the approved sites, ``scope_logins`` maps a login handle to its site,
    ``scope_cards`` are the card handles in scope and ``card_grants`` the ``card:form`` keys the
    owner already approved for card entry.
    """
    action = step.get("action") if isinstance(step.get("action"), dict) else {}
    page = step.get("page") if isinstance(step.get("page"), dict) else {}
    target = step.get("target") if isinstance(step.get("target"), dict) else {}
    kind = str(action.get("type") or "")
    host = str(page.get("host") or "").lower()
    page_text = str(page.get("text") or "")

    if kind == "navigate":
        url = str(action.get("url") or "")
        try:
            parts = urlsplit(url)
        except ValueError:
            return Decision(REFUSE, "refused_url", "That address is not a valid web address.")
        if parts.scheme.lower() != "https" or not parts.hostname:
            return Decision(REFUSE, "refused_scheme", "Only https addresses are opened.")
        if parts.username or parts.password:
            return Decision(REFUSE, "refused_url", "Addresses with a user name or password are not opened.")
        target_host = parts.hostname.lower()
        if not host_in_sites(target_host, sites):
            return Decision(GATE, "off_site", f"Open {site_of(target_host)}, a site outside the approved list.", host=target_host)
        return Decision(ALLOW, "navigate", f"Open {target_host}.", host=target_host)

    if kind in {"read", "scroll", "wait"}:
        return Decision(ALLOW, kind, kind.capitalize(), host=host)

    # Everything below acts on the page: a person must do captchas, codes and bank checks.
    needs_you = handoff_reason(page)
    if kind in {"click", "type", "select"} and needs_you is not None:
        if kind != "type" or is_otp_field(target) or needs_you.code != "one_time_code":
            return needs_you

    tag = str(target.get("tag") or "").lower()
    input_type = str(target.get("type") or "").lower()
    if not target:
        return Decision(REFUSE, "no_target", "That element is not on the page any more.")
    if input_type == "file":
        return Decision(REFUSE, "upload", "File uploads are not done by the action browser.")
    if target.get("download"):
        return Decision(REFUSE, "download", "Downloads are not done by the action browser.")
    if target.get("disabled"):
        return Decision(REFUSE, "disabled", "That control is disabled on the page.")

    form = target.get("form") if isinstance(target.get("form"), dict) else None
    frames = _frame_hosts(page)
    payment_frame = next((frame for frame in frames if _host_matches(frame, PAYMENT_HOSTS)), None)

    if kind == "type":
        value = str(action.get("value") or "")
        handle = parse_handle(value)
        if handle is None:
            if "{{" in value or "}}" in value:
                return Decision(REFUSE, "unknown_handle", "Saved logins and cards are named exactly like {{login:name.password}} or {{card:name.number}}.")
            if looks_like_card_number(value):
                return Decision(REFUSE, "card_number_text", "Card numbers are typed only from a saved card.")
            if input_type == "password":
                return Decision(REFUSE, "password_text", "Passwords are typed only from a saved login.")
            if is_otp_field(target):
                return Decision(HANDOFF, "one_time_code", f"Needs you: a one-time or verification code on {host}.", host=host)
            if is_card_field(target):
                return Decision(GATE, "payment_field", f"Type into a payment field on {host}.", host=host)
            if action.get("submit"):
                return _submit(form, page, target, host, payment_frame)
            return Decision(ALLOW, "type", "Type text.", host=host)
        if handle.kind == "login":
            site = scope_logins.get(handle.name)
            if site is None:
                return Decision(REFUSE, "handle_out_of_scope", f"The saved login {handle.name} is not one this task may use.", handle=handle)
            if not host_in_sites(host, [site]):
                return Decision(REFUSE, "login_wrong_site", f"The saved login {handle.name} is for {site}, not {host}.", handle=handle, host=host)
            if action.get("submit"):
                submitted = _submit(form, page, target, host, payment_frame)
                if submitted.kind != ALLOW:
                    return Decision(submitted.kind, submitted.code, submitted.reason, submitted.amount, handle, host)
            return Decision(ALLOW, "type_login", f"Type the saved login {handle.name} ({handle.part}).", handle=handle, host=host)
        # A card.
        if handle.name not in scope_cards:
            return Decision(REFUSE, "handle_out_of_scope", f"The saved card {handle.name} is not one this task may use.", handle=handle)
        if not host_in_sites(host, sites) and not (payment_frame and str(target.get("frame_host") or "")):
            return Decision(REFUSE, "card_wrong_site", f"Saved cards are typed only on the approved sites, not {host}.", handle=handle, host=host)
        key = card_grant_key(handle.name, step)
        if card_grants and key in card_grants:
            return Decision(ALLOW, "type_card", f"Type the saved card {handle.name} ({handle.part}).", handle=handle, host=host, extra={"grant": key})
        amount = pick_amount("", page_text, page.get("amounts"))
        return Decision(GATE, "card_entry", f"Enter the saved card {handle.name} on {host}.", amount=amount, handle=handle, host=host, extra={"grant": key})

    if kind == "select":
        if is_card_field(target):
            return Decision(GATE, "payment_field", f"Choose a value in a payment field on {host}.", host=host)
        return Decision(ALLOW, "select", "Choose an option.", host=host)

    if kind != "click":
        return Decision(REFUSE, "unknown_action", "That is not an action the browser can take.")

    href = str(target.get("href") or "")
    if href and tag == "a":
        try:
            link = urlsplit(href)
        except ValueError:
            link = None
        if link is not None and link.scheme.lower() in {"http", "https"} and link.hostname:
            if link.scheme.lower() != "https":
                return Decision(REFUSE, "refused_scheme", "Only https links are followed.")
            if not host_in_sites(link.hostname.lower(), sites):
                return Decision(GATE, "off_site", f"Follow a link to {site_of(link.hostname.lower())}, a site outside the approved list.", host=link.hostname.lower())

    text = _control_text(target)
    word = binding_words(text)
    if word is not None:
        amount = pick_amount(text, page_text, page.get("amounts"))
        return Decision(GATE, "binding_button", f"Press “{_short(text)}” on {host}.", amount=amount, host=host, extra={"word": word})

    is_submit = bool(target.get("submits")) or (tag == "button" and input_type in {"", "submit"} and form is not None) or (tag == "input" and input_type in {"submit", "image"})
    if is_submit:
        submitted = _submit(form, page, target, host, payment_frame)
        if submitted.kind != ALLOW:
            return submitted
    if payment_frame and (tag == "button" or input_type in {"submit", "button", "image"} or str(target.get("role") or "") == "button"):
        amount = pick_amount(text, page_text, page.get("amounts"))
        return Decision(GATE, "payment_frame", f"Press “{_short(text) or 'a button'}” on a page with a payment form ({payment_frame}).", amount=amount, host=host)
    return Decision(ALLOW, "click", f"Click “{_short(text) or tag}”.", host=host)


def _submit(form: dict[str, Any] | None, page: dict[str, Any], target: dict[str, Any], host: str, payment_frame: str | None) -> Decision:
    why = _form_is_payment(form, page)
    if why is None and payment_frame:
        why = "payment_frame"
    if why is None:
        return Decision(ALLOW, "submit", "Send a form.", host=host)
    text = _control_text(target)
    amount = pick_amount(text + " " + str((form or {}).get("text") or ""), str(page.get("text") or ""), page.get("amounts"))
    return Decision(GATE, "payment_form", f"Send a payment or order form on {host}.", amount=amount, host=host, extra={"why": why})


def card_grant_key(card: str, step: dict[str, Any]) -> str:
    """One card-entry approval covers that card's fields in the same form on the same page."""
    page = step.get("page") if isinstance(step.get("page"), dict) else {}
    target = step.get("target") if isinstance(step.get("target"), dict) else {}
    form = target.get("form") if isinstance(target.get("form"), dict) else {}
    return f"{card}:{page.get('host', '')}:{form.get('key') or target.get('frame_host') or page.get('path', '')}"[:300]


def _short(text: str) -> str:
    words = " ".join((text or "").split())
    return words[:80] + ("..." if len(words) > 80 else "")


__all__ = [
    "ALLOW", "GATE", "HANDOFF", "REFUSE", "Amount", "Decision", "binding_words", "card_grant_key", "classify", "detect_amounts",
    "handoff_reason", "is_card_field", "is_otp_field", "pick_amount",
]
