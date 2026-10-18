"""Voice provider adapters (docs/calls.md#provider).

``VoiceProvider`` is what the rest of ANUM uses; ``TwilioVoice`` implements it over Twilio's
REST API (``https://api.twilio.com/2010-04-01``, a fixed host: no workspace setting can point
ANUM at another server). Credentials come from the workspace's provider account (Fernet
ciphertext, decrypted only for the call) and are never logged or returned.

A generic SIP trunk is not an adapter of its own: a SIP trunk (an own Asterisk or FreeSWITCH
PBX, or a carrier's trunk) connects through the provider's SIP trunking, and calls still flow
through this interface. A new adapter implements the same five operations.

Webhooks: every provider request is checked with ``verify_signature`` before anything is read
from it; Twilio signs the full URL plus the sorted form parameters with the auth token
(HMAC-SHA1, ``X-Twilio-Signature``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit
from html import escape as _html_escape

import httpx

TWILIO_API = "https://api.twilio.com/2010-04-01"


def escape(value: str) -> str:
    """XML text: &, <, > and both quotes escaped."""
    return _html_escape(value, quote=True)


def quoteattr(value: str) -> str:
    """An XML attribute value, quoted."""
    return f'"{_html_escape(value, quote=True)}"'


TWILIO_HOSTS = frozenset({"api.twilio.com"})


class ProviderError(RuntimeError):
    """The provider refused or could not be reached; ``detail`` is safe to show."""

    def __init__(self, detail: str, *, status_code: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class Credentials:
    account_sid: str
    auth_token: str
    api_key_sid: str | None = None
    api_key_secret: str | None = None
    twiml_app_sid: str | None = None


@dataclass(frozen=True)
class PlacedCall:
    provider_call_id: str
    status: str


class VoiceProvider(Protocol):
    name: str

    async def check(self) -> str: ...
    async def place_call(self, *, to: str, from_: str, answer_url: str, status_url: str, time_limit: int) -> PlacedCall: ...
    async def hang_up(self, provider_call_id: str) -> None: ...
    async def fetch_recording(self, url: str, max_bytes: int) -> tuple[bytes, str]: ...
    def browser_token(self, identity: str, ttl_seconds: int = 600) -> str: ...
    def verify_signature(self, url: str, params: dict[str, str], signature: str) -> bool: ...


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Twilio's request signature: base64(HMAC-SHA1(token, url + key1 + value1 + ...))."""
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()  # Twilio's signature scheme is HMAC-SHA1
    return base64.b64encode(digest).decode("ascii")


class TwilioVoice:
    name = "twilio"

    def __init__(self, credentials: Credentials, *, timeout: float = 15.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.credentials = credentials
        self.timeout = timeout
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=TWILIO_API,
            auth=(self.credentials.account_sid, self.credentials.auth_token),
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=False,
        )

    def _account_path(self, suffix: str = "") -> str:
        return f"/Accounts/{self.credentials.account_sid}{suffix}.json"

    @staticmethod
    def _raise(response: httpx.Response, action: str) -> None:
        if response.status_code < 400:
            return
        message = ""
        try:
            message = str(response.json().get("message") or "")[:200]
        except ValueError:
            pass
        if response.status_code in {401, 403}:
            raise ProviderError(f"Twilio refused the credentials while {action}: check the Account SID and auth token.", status_code=response.status_code)
        raise ProviderError(f"Twilio answered {response.status_code} while {action}" + (f": {message}" if message else "."), status_code=response.status_code)

    async def check(self) -> str:
        try:
            async with self._client() as client:
                response = await client.get(self._account_path())
        except httpx.HTTPError as exc:
            raise ProviderError(f"Twilio could not be reached ({type(exc).__name__}).") from None
        self._raise(response, "checking the account")
        data = response.json()
        status = str(data.get("status") or "unknown")
        if status != "active":
            raise ProviderError(f"The Twilio account is {status}, not active.")
        return f"Connected to Twilio account {data.get('friendly_name') or self.credentials.account_sid[:6] + '...'}."

    async def place_call(self, *, to: str, from_: str, answer_url: str, status_url: str, time_limit: int) -> PlacedCall:
        form = {
            "To": to, "From": from_, "Url": answer_url, "Method": "POST",
            "StatusCallback": status_url, "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": "initiated ringing answered completed",
            "TimeLimit": str(time_limit),
        }
        try:
            async with self._client() as client:
                response = await client.post(self._account_path("/Calls"), data=form)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Twilio could not be reached ({type(exc).__name__}); no call was placed.") from None
        self._raise(response, "placing the call")
        data = response.json()
        sid = str(data.get("sid") or "")
        if not sid:
            raise ProviderError("Twilio did not return a call id.")
        return PlacedCall(provider_call_id=sid, status=str(data.get("status") or "queued"))

    async def hang_up(self, provider_call_id: str) -> None:
        try:
            async with self._client() as client:
                response = await client.post(self._account_path(f"/Calls/{provider_call_id}"), data={"Status": "completed"})
        except httpx.HTTPError as exc:
            raise ProviderError(f"Twilio could not be reached ({type(exc).__name__}).") from None
        self._raise(response, "ending the call")

    async def fetch_recording(self, url: str, max_bytes: int) -> tuple[bytes, str]:
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "") not in TWILIO_HOSTS:
            raise ProviderError("The recording address is not on Twilio's API host; it was not downloaded.")
        target = url if url.endswith(".mp3") else url + ".mp3"
        try:
            async with httpx.AsyncClient(
                auth=(self.credentials.account_sid, self.credentials.auth_token), timeout=self.timeout * 4,
                transport=self.transport, follow_redirects=False,
            ) as client:
                async with client.stream("GET", target) as response:
                    self._raise(response, "downloading the recording")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ProviderError("The recording is larger than this server keeps.")
                        chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise ProviderError(f"The recording could not be downloaded ({type(exc).__name__}).") from None
        return b"".join(chunks), "audio/mpeg"

    def browser_token(self, identity: str, ttl_seconds: int = 600) -> str:
        """An access token for Twilio's Voice JavaScript SDK: outgoing calls through the TwiML app only."""
        c = self.credentials
        if not (c.api_key_sid and c.api_key_secret and c.twiml_app_sid):
            raise ProviderError("Calling from the browser needs an API key SID, its secret and a TwiML app SID.")
        now = int(time.time())
        header = {"typ": "JWT", "alg": "HS256", "cty": "twilio-fpa;v=1"}
        payload: dict[str, Any] = {
            "jti": f"{c.api_key_sid}-{now}",
            "iss": c.api_key_sid,
            "sub": c.account_sid,
            "iat": now,
            "nbf": now,
            "exp": now + ttl_seconds,
            "grants": {"identity": identity, "voice": {"outgoing": {"application_sid": c.twiml_app_sid}, "incoming": {"allow": False}}},
        }
        signing = _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." + _b64url(json.dumps(payload, separators=(",", ":")).encode())
        signature = hmac.new(c.api_key_secret.encode("utf-8"), signing.encode("ascii"), hashlib.sha256).digest()
        return signing + "." + _b64url(signature)

    def verify_signature(self, url: str, params: dict[str, str], signature: str) -> bool:
        if not signature:
            return False
        # Twilio may sign the URL with or without the default port; both are accepted (as its own validator does).
        parts = urlsplit(url)
        candidates = [url]
        if parts.port is None and parts.hostname:
            candidates.append(parts._replace(netloc=f"{parts.netloc}:{443 if parts.scheme == 'https' else 80}").geturl())
        matched = False
        for candidate in candidates:
            matched |= hmac.compare_digest(twilio_signature(self.credentials.auth_token, candidate, params), signature)
        return matched


# -- TwiML ---------------------------------------------------------------------------------

VOICES = {"en": ("Polly.Joanna", "en-US"), "ar": ("Polly.Zeina", "arb")}
GATHER_LANGUAGE = {"en": "en-US", "ar": "ar-AE"}


class TwiML:
    """A small, escaping builder for the provider's call instructions."""

    def __init__(self) -> None:
        self.parts: list[str] = []

    def say(self, text: str, language: str = "en") -> TwiML:
        voice, code = VOICES.get(language, VOICES["en"])
        self.parts.append(f"<Say voice={quoteattr(voice)} language={quoteattr(code)}>{escape(text)}</Say>")
        return self

    def pause(self, seconds: int = 1) -> TwiML:
        self.parts.append(f'<Pause length="{int(seconds)}"/>')
        return self

    def hangup(self) -> TwiML:
        self.parts.append("<Hangup/>")
        return self

    def gather(self, action: str, *, language: str = "en", prompt: str | None = None, timeout: int = 6) -> TwiML:
        inner = ""
        if prompt:
            voice, code = VOICES.get(language, VOICES["en"])
            inner = f"<Say voice={quoteattr(voice)} language={quoteattr(code)}>{escape(prompt)}</Say>"
        self.parts.append(
            f'<Gather input="speech dtmf" numDigits="1" speechTimeout="auto" timeout="{int(timeout)}" '
            f'language={quoteattr(GATHER_LANGUAGE.get(language, "en-US"))} action={quoteattr(action)} method="POST">{inner}</Gather>'
        )
        return self

    def redirect(self, url: str) -> TwiML:
        self.parts.append(f'<Redirect method="POST">{escape(url)}</Redirect>')
        return self

    def dial(self, number: str, *, caller_id: str, time_limit: int, action: str, record: bool, recording_url: str | None, status_url: str) -> TwiML:
        attributes = [f"callerId={quoteattr(caller_id)}", f'timeLimit="{int(time_limit)}"', f"action={quoteattr(action)}", 'method="POST"']
        if record and recording_url:
            attributes += ['record="record-from-answer-dual"', f"recordingStatusCallback={quoteattr(recording_url)}", 'recordingStatusCallbackMethod="POST"']
        self.parts.append(
            f"<Dial {' '.join(attributes)}><Number statusCallback={quoteattr(status_url)} statusCallbackMethod=\"POST\" "
            f'statusCallbackEvent="initiated ringing answered completed">{escape(number)}</Number></Dial>'
        )
        return self

    def render(self) -> str:
        return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(self.parts) + "</Response>"
