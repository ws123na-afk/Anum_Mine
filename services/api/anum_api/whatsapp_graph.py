"""Meta WhatsApp Cloud API (Graph API) calls for the WhatsApp inbox (docs/whatsapp.md).

Everything ANUM asks of Meta goes through :class:`WhatsAppGraphClient`: read the phone
number's profile (health check), list the business account's message templates, send a
text or template message, and fetch an inbound media file on demand.

The access token is a permanent system-user token held as a ``SecretStr``; it is only
ever placed in the ``Authorization`` header. Errors carry the HTTP status, Meta's error
code and a sentence safe to show, never the request headers, body or token.

Media URLs come from Meta's answer, so they are untrusted: only ``https`` URLs on Meta's
own media hosts are fetched, redirects are not followed, the body is read up to a byte
cap and compressed bodies are refused (threat model G1, SSRF; G3, untrusted output).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import SecretStr

TIMEOUT_SECONDS = 15.0
# Hosts Meta serves WhatsApp media from (the `url` in GET /{media-id}).
MEDIA_HOST_SUFFIXES = (".fbsbx.com", ".facebook.com", ".whatsapp.net", ".fbcdn.net")
_DIGITS = re.compile(r"^[0-9]{5,20}$")

# Meta error codes ANUM explains in plain words (developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes).
ERROR_EXPLANATIONS: dict[int, str] = {
    0: "Meta could not authenticate the request. Check the access token.",
    3: "The app does not have permission for this call. Give the system user the whatsapp_business_messaging permission.",
    10: "Permission denied. Give the system user the whatsapp_business_messaging and whatsapp_business_management permissions.",
    100: "Meta rejected a parameter. Check the phone number id, the business account id and the message.",
    190: "The access token is invalid or expired. Create a permanent system-user token in Meta Business Settings and save it again.",
    200: "Permission denied. Give the system user access to the WhatsApp account.",
    4: "Meta's API rate limit was reached. Wait a few minutes and try again.",
    80007: "The WhatsApp account's rate limit was reached. Wait a few minutes and try again.",
    130429: "WhatsApp's throughput limit for this number was reached. Try again shortly.",
    131000: "WhatsApp could not send the message. Try again later.",
    131005: "Access denied. Check the system user's permissions for this WhatsApp account.",
    131008: "A required parameter is missing.",
    131009: "A parameter value is not valid.",
    131016: "WhatsApp is temporarily unavailable. Try again later.",
    131021: "The recipient is the sender's own number. Use another number for the test.",
    131026: "The message could not be delivered: the number is not on WhatsApp or has not accepted the latest terms.",
    131030: "The recipient is not in the test number's allowed list. Add it in the Meta app's WhatsApp > API Setup, or use the business number.",
    131031: "The WhatsApp account is locked or restricted. Check WhatsApp Manager.",
    131047: "More than 24 hours passed since the customer's last message. Only an approved template can be sent now.",
    131051: "This message type is not supported.",
    131053: "The media file could not be uploaded or downloaded.",
    131056: "Too many messages to this customer in a short time. Wait before sending again.",
    132000: "The number of template parameters does not match the template.",
    132001: "The template does not exist in this language, or is not approved yet.",
    132005: "The template text is too long after filling in the parameters.",
    132007: "The template content breaks WhatsApp's policy.",
    132012: "A template parameter has the wrong format.",
    132015: "The template is paused because of low quality.",
    132016: "The template is disabled because of low quality.",
    133010: "The phone number is not registered with the Cloud API. Register it in WhatsApp Manager first.",
    368: "The WhatsApp account is temporarily blocked for policy violations. Check WhatsApp Manager.",
}


class WhatsAppGraphError(Exception):
    """A Graph API call failed. ``message`` is safe to show; ``code`` is Meta's error code."""

    def __init__(self, status: int, message: str, code: int | None = None) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)

    @property
    def outside_window(self) -> bool:
        return self.code == 131047


@dataclass(frozen=True)
class PhoneNumberProfile:
    id: str
    display_phone_number: str | None
    verified_name: str | None
    quality_rating: str | None


@dataclass(frozen=True)
class MessageTemplate:
    name: str
    language: str
    status: str
    category: str
    body: str
    parameter_count: int
    header: str | None = None
    footer: str | None = None
    buttons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "language": self.language,
            "status": self.status,
            "category": self.category,
            "body": self.body,
            "parameter_count": self.parameter_count,
            "header": self.header,
            "footer": self.footer,
            "buttons": list(self.buttons),
        }


@dataclass(frozen=True)
class MediaFile:
    content: bytes
    mime_type: str
    size: int


_PLACEHOLDER = re.compile(r"\{\{\s*([0-9]+|[a-z_][a-z0-9_]*)\s*\}\}")


def count_parameters(text: str) -> int:
    """Distinct ``{{1}}``/``{{name}}`` placeholders in a template component's text."""
    return len({match.group(1) for match in _PLACEHOLDER.finditer(text or "")})


def fill_template(body: str, parameters: list[str]) -> str:
    """The template body as the customer will read it (for previews and the stored copy)."""
    order: dict[str, int] = {}

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        index = order.setdefault(key, len(order)) if not key.isdigit() else int(key) - 1
        return parameters[index] if 0 <= index < len(parameters) else match.group(0)

    return _PLACEHOLDER.sub(replace, body or "")


def is_media_url_allowed(url: str) -> bool:
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError):
        return False
    host = (parsed.host or "").lower()
    return parsed.scheme == "https" and parsed.port in (None, 443) and any(
        host.endswith(suffix) for suffix in MEDIA_HOST_SUFFIXES
    )


def valid_graph_id(value: str) -> bool:
    return bool(_DIGITS.match(value or ""))


def _error_from(response: httpx.Response) -> WhatsAppGraphError:
    code: int | None = None
    detail = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        raw_code = error.get("code")
        code = raw_code if isinstance(raw_code, int) else None
        data = error.get("error_data")
        details = data.get("details") if isinstance(data, dict) else None
        detail = str(details or error.get("message") or "")[:300]
    explanation = ERROR_EXPLANATIONS.get(code) if code is not None else None
    if explanation is None:
        if response.status_code in (401, 403):
            explanation = ERROR_EXPLANATIONS[190]
        elif response.status_code == 429:
            explanation = ERROR_EXPLANATIONS[4]
        else:
            explanation = f"Meta answered HTTP {response.status_code}."
    message = explanation if not detail or detail in explanation else f"{explanation} ({detail})"
    return WhatsAppGraphError(response.status_code, message, code)


class WhatsAppGraphClient:
    def __init__(
        self,
        *,
        access_token: SecretStr,
        api_url: str = "https://graph.facebook.com",
        api_version: str = "v23.0",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> None:
        self._token = access_token
        self._base = f"{api_url.rstrip('/')}/{api_version.strip('/')}"
        self._transport = transport
        self._timeout = timeout_seconds

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,
            headers={
                "authorization": f"Bearer {self._token.get_secret_value()}",
                "accept-encoding": "identity",
            },
        )

    async def _call(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.request(method, f"{self._base}{path}", params=params, json=json)
        except httpx.TimeoutException:
            raise WhatsAppGraphError(504, "Meta did not answer in time. Try again.") from None
        except httpx.TransportError:
            raise WhatsAppGraphError(502, "Meta's API could not be reached. Check the server's internet access.") from None
        if response.status_code >= 400:
            raise _error_from(response)
        try:
            body = response.json()
        except ValueError:
            raise WhatsAppGraphError(502, "Meta answered with something that is not JSON.") from None
        return body if isinstance(body, dict) else {"data": body}

    async def phone_number(self, phone_number_id: str) -> PhoneNumberProfile:
        body = await self._call(
            "GET", f"/{phone_number_id}", params={"fields": "display_phone_number,verified_name,quality_rating"}
        )
        return PhoneNumberProfile(
            id=str(body.get("id") or phone_number_id),
            display_phone_number=_opt_str(body.get("display_phone_number")),
            verified_name=_opt_str(body.get("verified_name")),
            quality_rating=_opt_str(body.get("quality_rating")),
        )

    async def templates(self, business_account_id: str, *, max_pages: int = 5) -> list[MessageTemplate]:
        """Every template of the business account (all statuses), following paging."""
        found: list[MessageTemplate] = []
        params: dict[str, Any] = {"fields": "name,status,category,language,components", "limit": 100}
        for _ in range(max_pages):
            body = await self._call("GET", f"/{business_account_id}/message_templates", params=params)
            for item in body.get("data") or []:
                if isinstance(item, dict):
                    parsed = _template(item)
                    if parsed is not None:
                        found.append(parsed)
            after = ((body.get("paging") or {}).get("cursors") or {}).get("after")
            if not after or not (body.get("paging") or {}).get("next"):
                break
            params = {**params, "after": after}
        return found

    async def send(self, phone_number_id: str, payload: dict[str, Any]) -> str:
        """POST a message; returns its ``wamid``."""
        body = await self._call(
            "POST",
            f"/{phone_number_id}/messages",
            json={"messaging_product": "whatsapp", "recipient_type": "individual", **payload},
        )
        messages = body.get("messages") or []
        wamid = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
        if not isinstance(wamid, str) or not wamid:
            raise WhatsAppGraphError(502, "Meta accepted the message but returned no message id.")
        return wamid

    async def send_text(self, phone_number_id: str, to: str, text: str, *, reply_to: str | None = None) -> str:
        payload: dict[str, Any] = {"to": to, "type": "text", "text": {"preview_url": False, "body": text}}
        if reply_to:
            payload["context"] = {"message_id": reply_to}
        return await self.send(phone_number_id, payload)

    async def send_template(
        self, phone_number_id: str, to: str, name: str, language: str, parameters: list[str]
    ) -> str:
        template: dict[str, Any] = {"name": name, "language": {"code": language}}
        if parameters:
            template["components"] = [
                {"type": "body", "parameters": [{"type": "text", "text": value} for value in parameters]}
            ]
        return await self.send(phone_number_id, {"to": to, "type": "template", "template": template})

    async def media(self, media_id: str, *, max_bytes: int) -> MediaFile:
        info = await self._call("GET", f"/{media_id}")
        url = info.get("url")
        mime_type = str(info.get("mime_type") or "application/octet-stream")[:100]
        declared = info.get("file_size")
        if isinstance(declared, int) and declared > max_bytes:
            raise WhatsAppGraphError(413, f"The file is {declared} bytes; the limit is {max_bytes} bytes.")
        if not isinstance(url, str) or not is_media_url_allowed(url):
            raise WhatsAppGraphError(502, "Meta returned a media address ANUM does not fetch.")
        try:
            async with self._client() as client, asyncio.timeout(self._timeout * 2):
                async with client.stream("GET", url) as response:
                    if response.status_code >= 400:
                        raise _error_from(response)
                    encoding = response.headers.get("content-encoding", "identity").strip().lower()
                    if encoding not in {"", "identity"}:
                        raise WhatsAppGraphError(502, "Meta sent the file compressed; ANUM accepts identity only.")
                    buffer = bytearray()
                    async for chunk in response.aiter_bytes():
                        buffer.extend(chunk)
                        if len(buffer) > max_bytes:
                            raise WhatsAppGraphError(413, f"The file is larger than the {max_bytes}-byte limit.")
        except TimeoutError:
            raise WhatsAppGraphError(504, "Downloading the file from Meta timed out.") from None
        except httpx.TransportError:
            raise WhatsAppGraphError(502, "Meta's media server could not be reached.") from None
        return MediaFile(content=bytes(buffer), mime_type=mime_type, size=len(buffer))


def _opt_str(value: Any) -> str | None:
    return str(value)[:200] if isinstance(value, (str, int)) and str(value) else None


def _template(item: dict[str, Any]) -> MessageTemplate | None:
    name, language = item.get("name"), item.get("language")
    if not isinstance(name, str) or not isinstance(language, str):
        return None
    body = header = footer = None
    buttons: list[str] = []
    parameters = 0
    for component in item.get("components") or []:
        if not isinstance(component, dict):
            continue
        kind = str(component.get("type") or "").upper()
        text = component.get("text") if isinstance(component.get("text"), str) else None
        if kind == "BODY":
            body = text or ""
            parameters = count_parameters(body)
        elif kind == "HEADER" and component.get("format", "TEXT") == "TEXT":
            header = text
        elif kind == "FOOTER":
            footer = text
        elif kind == "BUTTONS":
            buttons = [str(button.get("text"))[:60] for button in component.get("buttons") or [] if isinstance(button, dict) and button.get("text")]
    return MessageTemplate(
        name=name[:512],
        language=language[:20],
        status=str(item.get("status") or "UNKNOWN").upper()[:30],
        category=str(item.get("category") or "UNKNOWN").upper()[:30],
        body=(body or "")[:1024],
        parameter_count=parameters,
        header=header[:60] if header else None,
        footer=footer[:60] if footer else None,
        buttons=buttons,
    )
