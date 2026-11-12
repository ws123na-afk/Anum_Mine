"""Courier tracking for Purchases (docs/purchases.md#courier-tracking).

Two parts, both read only:

* **Carrier detection** from the carrier name a person typed or a mail named, else from the
  tracking number's own format (Aramex, DHL Express, FedEx, UPS, SMSA, Emirates Post, Saudi Post),
  with a deep link to that carrier's public tracking page. ANUM never opens the page or reads
  anything from it: the link is for the person. A number several carriers use (ten digits are
  both DHL Express and Aramex) gives every candidate with its link, never a guess.
* **A status provider interface.** Reading a carrier's status needs a tracking data provider
  with an API key; scraping a public tracking page is not reliable and is not done. No provider
  ships: ``ANUM_PURCHASES_TRACKING_PROVIDER`` names one registered in :data:`PROVIDERS` and
  ``ANUM_PURCHASES_TRACKING_API_KEY`` holds its key. Until both are set the status is "off" and
  the screens show the links only.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from urllib.parse import quote

from pydantic import BaseModel, Field

Confidence = Literal["certain", "likely", "possible"]
TrackingState = Literal["in_transit", "out_for_delivery", "delivered", "exception", "unknown"]


@dataclass(frozen=True)
class Carrier:
    key: str
    name: str
    url: str  # the public tracking page, with {code}
    names: str  # a regular expression for the names people and mails use


CARRIERS: dict[str, Carrier] = {c.key: c for c in (
    Carrier("aramex", "Aramex", "https://www.aramex.com/us/en/track/results?ShipmentNumber={code}", r"aramex|ارامكس|أرامكس"),
    Carrier("dhl", "DHL Express", "https://www.dhl.com/global-en/home/tracking/tracking-express.html?submit=1&tracking-id={code}", r"\bdhl\b|دي ?إتش ?إل"),
    Carrier("fedex", "FedEx", "https://www.fedex.com/fedextrack/?trknbr={code}", r"fed ?ex|فيديكس|فيدكس"),
    Carrier("ups", "UPS", "https://www.ups.com/track?tracknum={code}", r"\bups\b|يو ?بي ?إس"),
    Carrier("smsa", "SMSA Express", "https://www.smsaexpress.com/trackingdetails?tracknumbers={code}", r"\bsmsa\b|سمسا"),
    Carrier("emirates_post", "Emirates Post", "https://www.emiratespost.ae/all-services/track-a-package/step-two?q={code}",
            r"emirates ?post|empost|بريد الإمارات|بريد الامارات"),
    Carrier("saudi_post", "Saudi Post (SPL)", "https://splonline.com.sa/en/shipmentdetailsstatic/?tid={code}",
            r"saudi ?post|\bspl\b|البريد السعودي|سبل"),
)}

_CODE = re.compile(r"^[A-Z0-9]{8,40}$")


class CarrierLink(BaseModel):
    carrier: str
    carrier_name: str
    url: str
    confidence: Confidence


class TrackingEvent(BaseModel):
    at: datetime | None = None
    description: str = Field(max_length=300)
    location: str | None = Field(default=None, max_length=120)


class TrackingStatus(BaseModel):
    """What a status provider answered, already cleaned (plain text, bounded)."""

    state: TrackingState = "unknown"
    description: str = Field(default="", max_length=300)
    checked_at: datetime
    events: list[TrackingEvent] = Field(default_factory=list, max_length=50)


class Tracking(BaseModel):
    """The tracking facts of one shipment: who carries it and where to follow it."""

    number: str | None = None
    carrier: str | None = None
    carrier_name: str | None = None
    url: str | None = None
    detected_from: Literal["name", "number"] | None = None
    candidates: list[CarrierLink] = Field(default_factory=list)
    status_provider: str = "off"


def clean_number(value: str | None) -> str | None:
    """The tracking number as carriers read it: upper case, no spaces or dashes; None when unusable."""
    code = re.sub(r"[\s\-]", "", value or "").upper()
    return code if _CODE.match(code) else None


def carrier_from_name(name: str | None) -> Carrier | None:
    text = (name or "").strip().lower()
    if not text:
        return None
    for carrier in CARRIERS.values():
        if carrier.key == text or re.search(carrier.names, text, re.IGNORECASE):
            return carrier
    return None


def _s10_ok(code: str) -> bool:
    """A UPU S10 item id (two letters, eight digits, a check digit, two letters) with a valid check digit."""
    digits = [int(d) for d in code[2:10]]
    total = sum(d * w for d, w in zip(digits, (8, 6, 4, 2, 3, 5, 9, 7), strict=True))
    check = 11 - total % 11
    check = 0 if check == 10 else 5 if check == 11 else check
    return check == int(code[10])


def _fedex12_ok(code: str) -> bool:
    """FedEx Express 12 digits: the first 11 weighted 1, 3, 7 from the right, sum mod 11 mod 10."""
    weights = (1, 3, 7)
    total = sum(int(d) * weights[i % 3] for i, d in enumerate(reversed(code[:11])))
    return total % 11 % 10 == int(code[11])


def _dhl10_ok(code: str) -> bool:
    """DHL Express waybill: the first nine digits mod 7 is the tenth."""
    return int(code[:9]) % 7 == int(code[9])


def from_number(code: str) -> list[tuple[str, Confidence]]:
    """Carriers whose number format ``code`` fits, most certain first."""
    found: list[tuple[str, Confidence]] = []
    if re.fullmatch(r"1Z[0-9A-Z]{16}", code):
        return [("ups", "certain")]
    if re.fullmatch(r"[A-Z]{2}\d{9}[A-Z]{2}", code) and _s10_ok(code):
        country = code[-2:]
        if country == "AE":
            return [("emirates_post", "certain")]
        if country == "SA":
            return [("saudi_post", "certain")]
        return []  # another country's post: no tracking page of ours fits
    if re.fullmatch(r"JJD\d{10,20}|JD\d{18}", code):
        return [("dhl", "certain")]
    if not code.isdigit():
        return []
    length = len(code)
    if length == 10:
        if _dhl10_ok(code):
            found.append(("dhl", "likely"))
        found.append(("aramex", "possible"))
    elif length == 11:
        found.append(("aramex", "possible"))
    elif length == 12:
        if _fedex12_ok(code):
            found.append(("fedex", "likely"))
        found.append(("aramex", "possible"))
        found.append(("smsa", "possible"))
    elif length in (15, 20, 22):
        found.append(("fedex", "likely"))
    return found


def _link(key: str, code: str, confidence: Confidence) -> CarrierLink:
    carrier = CARRIERS[key]
    return CarrierLink(carrier=key, carrier_name=carrier.name, url=carrier.url.format(code=quote(code, safe="")), confidence=confidence)


def detect(tracking_number: str | None, carrier_name: str | None) -> Tracking:
    """The carrier and its tracking link: the named carrier first, else the number's format."""
    code = clean_number(tracking_number)
    named = carrier_from_name(carrier_name)
    provider = configured_provider()
    status_provider = provider.name if provider is not None else "off"
    if named is not None:
        url = named.url.format(code=quote(code, safe="")) if code else None
        return Tracking(number=code, carrier=named.key, carrier_name=named.name, url=url, detected_from="name",
                        status_provider=status_provider)
    if code is None:
        return Tracking(status_provider=status_provider)
    matches = from_number(code)
    candidates = [_link(key, code, confidence) for key, confidence in matches]
    if len(matches) == 1 and matches[0][1] in ("certain", "likely"):
        only = candidates[0]
        return Tracking(number=code, carrier=only.carrier, carrier_name=only.carrier_name, url=only.url, detected_from="number",
                        candidates=candidates, status_provider=status_provider)
    return Tracking(number=code, candidates=candidates, status_provider=status_provider)


# --------------------------------------------------------------------------- status providers


class ProviderError(Exception):
    """A provider could not answer; the message is shown to the person (no key, no raw body)."""


class StatusProvider(Protocol):
    """A tracking data provider. ``status`` is called outside any database transaction."""

    name: str

    def supports(self, carrier: str) -> bool: ...

    def status(self, carrier: str, number: str) -> TrackingStatus: ...


# name -> factory(api_key). Empty on purpose: no provider is contracted yet. A provider module
# registers itself here (docs/purchases.md#adding-a-status-provider).
PROVIDERS: dict[str, Callable[[str], StatusProvider]] = {}


def configured_provider() -> StatusProvider | None:
    """The provider the operator configured, or None (off) without a name, a key or a registration."""
    from ..settings import settings

    name = (settings.purchases_tracking_provider or "").strip().lower()
    key = settings.purchases_tracking_api_key or ""
    factory = PROVIDERS.get(name)
    if not name or not key or factory is None:
        return None
    return factory(key)


__all__ = [
    "CARRIERS",
    "PROVIDERS",
    "CarrierLink",
    "ProviderError",
    "StatusProvider",
    "Tracking",
    "TrackingStatus",
    "carrier_from_name",
    "clean_number",
    "configured_provider",
    "detect",
    "from_number",
]
