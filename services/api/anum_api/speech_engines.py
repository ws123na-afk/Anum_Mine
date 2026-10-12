"""Optional server-side speech engines for voice calls (docs/voice.md#speech-engines).

Clients use their device's speech recognition and voices by default. An operator can
add server engines that speak the OpenAI audio API:

- speech to text: ``POST {ANUM_SPEECH_STT_BASE_URL}/audio/transcriptions`` (multipart
  ``file``, ``model``, ``language``), answered with ``{"text": ...}``;
- text to speech: ``POST {ANUM_SPEECH_TTS_BASE_URL}/audio/speech`` (``model``,
  ``voice``, ``input``, ``response_format``), answered with audio bytes.

OpenAI serves both, and so do self-hosted open-source servers (Speaches runs
faster-whisper for recognition and Piper or Kokoro for voices), which is how a Whisper
and Piper pair on the Oracle VM plugs in. An engine whose base URL or model is empty is
off and reported as such; nothing pretends to transcribe or speak.

Each call is charged to the workspace's monthly model budget at the configured price
(per audio minute, per million characters). Audio and text are never logged: only
the engine, the outcome and the error class.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import httpx
from pydantic import BaseModel

from .model_budget import check_model_budget, record_model_usage
from .model_gateway import ModelUsage
from .schemas import TenantContext
from .settings import Settings, settings

logger = logging.getLogger("anum.speech")

# One spoken turn, not a recording: about two minutes of compressed speech.
MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_AUDIO_MS = 120_000
# A spoken reply is spoken a sentence or two at a time.
MAX_SPEECH_CHARS = 600
# Opus at its lowest common call bitrate (about 12 kbit/s, 1.5 kB per second): the
# fewest seconds a clip of this size can hold, so a client cannot under-report length.
_MIN_BYTES_PER_SECOND = 32_000 / 8
AUDIO_TYPES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mp4": "mp4",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/mpeg": "mp3",
}


class SpeechEngineUnavailable(RuntimeError):
    """The engine is not configured; the message says how to turn it on."""


class SpeechEngineFailed(RuntimeError):
    """The engine was configured but did not answer usefully."""


class EngineStatus(BaseModel):
    id: str
    kind: str  # "device" (on the client) or "server"
    available: bool
    label: str
    model: str | None = None
    # Price charged to the model budget; None for device engines and unpriced servers.
    usd_per_minute: float | None = None
    usd_per_million_chars: float | None = None
    reason: str | None = None


@dataclass(frozen=True)
class _Endpoint:
    base_url: str
    api_key: str | None
    model: str

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip() and self.model.strip())

    @property
    def headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"


def _host_label(base_url: str) -> str:
    host = httpx.URL(base_url).host if base_url else ""
    if host in {"api.openai.com"}:
        return "OpenAI"
    if host in {"api.groq.com"}:
        return "Groq"
    return "Self-hosted" if host else "Not configured"


def stt_endpoint(config: Settings | None = None) -> _Endpoint:
    config = config or settings
    return _Endpoint(config.speech_stt_base_url, config.speech_stt_api_key, config.speech_stt_model)


def tts_endpoint(config: Settings | None = None) -> _Endpoint:
    config = config or settings
    return _Endpoint(config.speech_tts_base_url, config.speech_tts_api_key, config.speech_tts_model)


def tts_voice(locale: str, config: Settings | None = None) -> str:
    config = config or settings
    arabic = locale.lower().startswith("ar")
    return (config.speech_tts_voice_ar if arabic else config.speech_tts_voice_en).strip()


def engine_statuses(config: Settings | None = None) -> dict[str, list[EngineStatus]]:
    """What each side of a call can use: the device engines always (they run on the
    client, which checks its own support), the server ones when configured."""
    config = config or settings
    stt, tts = stt_endpoint(config), tts_endpoint(config)
    voices_missing = tts.configured and not (config.speech_tts_voice_en or config.speech_tts_voice_ar)
    return {
        "stt": [
            EngineStatus(id="device", kind="device", available=True, label="Device"),
            EngineStatus(
                id="server",
                kind="server",
                available=stt.configured,
                label=_host_label(stt.base_url),
                model=stt.model or None,
                usd_per_minute=config.speech_stt_usd_per_minute if stt.configured else None,
                reason=None
                if stt.configured
                else "Server recognition is off. An operator sets ANUM_SPEECH_STT_BASE_URL and ANUM_SPEECH_STT_MODEL.",
            ),
        ],
        "tts": [
            EngineStatus(id="device", kind="device", available=True, label="Device"),
            EngineStatus(
                id="server",
                kind="server",
                available=tts.configured and not voices_missing,
                label=_host_label(tts.base_url),
                model=tts.model or None,
                usd_per_million_chars=config.speech_tts_usd_per_million_chars if tts.configured else None,
                reason=None
                if tts.configured and not voices_missing
                else (
                    "Server voices need ANUM_SPEECH_TTS_VOICE_EN or ANUM_SPEECH_TTS_VOICE_AR."
                    if voices_missing
                    else "Server voices are off. An operator sets ANUM_SPEECH_TTS_BASE_URL and ANUM_SPEECH_TTS_MODEL."
                ),
            ),
        ],
    }


def billed_audio_ms(size_bytes: int, reported_ms: int | None) -> int:
    """The duration charged for a clip: what the client measured, never less than the
    clip's size allows and never more than the per-clip limit."""
    floor_ms = math.ceil(size_bytes / _MIN_BYTES_PER_SECOND * 1000) if size_bytes else 0
    return max(0, min(MAX_AUDIO_MS, max(reported_ms or 0, min(floor_ms, MAX_AUDIO_MS))))


def _record(context: TenantContext, provider: str, model: str, cost: float | None) -> None:
    try:
        record_model_usage(
            context,
            ModelUsage(input_tokens=0, output_tokens=0, provider=provider, model=model, estimated_cost_usd=cost),
        )
    except Exception as exc:  # the call already happened; never fail the user's request
        logger.warning("speech_usage_record_failed error_class=%s", type(exc).__name__)


def _client(config: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=config.speech_timeout_seconds, follow_redirects=False)


async def transcribe(
    context: TenantContext,
    audio: bytes,
    content_type: str,
    locale: str,
    duration_ms: int | None,
    *,
    config: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Text of one spoken turn from the server recognizer (empty when nothing was said)."""
    config = config or settings
    endpoint = stt_endpoint(config)
    if not endpoint.configured:
        raise SpeechEngineUnavailable(engine_statuses(config)["stt"][1].reason or "Server recognition is off.")
    extension = AUDIO_TYPES.get(content_type.split(";")[0].strip().lower())
    if extension is None:
        raise ValueError("Unsupported audio type. Send audio/webm, audio/ogg, audio/wav, audio/mp4 or audio/mpeg.")
    check_model_budget(context)
    owns = client is None
    http = client or _client(config)
    try:
        response = await http.post(
            endpoint.url("/audio/transcriptions"),
            headers=endpoint.headers,
            data={"model": endpoint.model, "language": locale.split("-")[0].lower(), "response_format": "json"},
            files={"file": (f"turn.{extension}", audio, content_type.split(";")[0].strip())},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("speech_stt_failed error_class=%s", type(exc).__name__)
        raise SpeechEngineFailed("The speech recognizer did not answer. Try again, or switch to device speech.") from exc
    finally:
        if owns:
            await http.aclose()
    minutes = billed_audio_ms(len(audio), duration_ms) / 60_000
    price = config.speech_stt_usd_per_minute
    _record(context, "speech-stt", endpoint.model, round(minutes * price, 6) if price else None)
    text = payload.get("text") if isinstance(payload, dict) else None
    return text.strip() if isinstance(text, str) else ""


async def synthesize(
    context: TenantContext,
    text: str,
    locale: str,
    *,
    config: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[bytes, str]:
    """Audio (and its media type) for one or two sentences of a reply."""
    config = config or settings
    endpoint = tts_endpoint(config)
    voice = tts_voice(locale, config)
    if not endpoint.configured or not voice:
        raise SpeechEngineUnavailable(engine_statuses(config)["tts"][1].reason or "Server voices are off.")
    check_model_budget(context)
    owns = client is None
    http = client or _client(config)
    try:
        response = await http.post(
            endpoint.url("/audio/speech"),
            headers=endpoint.headers,
            json={"model": endpoint.model, "voice": voice, "input": text, "response_format": "mp3"},
        )
        response.raise_for_status()
        audio = response.content
    except httpx.HTTPError as exc:
        logger.warning("speech_tts_failed error_class=%s", type(exc).__name__)
        raise SpeechEngineFailed("The voice server did not answer. Try again, or switch to the device voice.") from exc
    finally:
        if owns:
            await http.aclose()
    if not audio:
        raise SpeechEngineFailed("The voice server returned no audio. Switch to the device voice.")
    price = config.speech_tts_usd_per_million_chars
    _record(context, "speech-tts", endpoint.model, round(len(text) * price / 1_000_000, 6) if price else None)
    media_type = response.headers.get("content-type", "audio/mpeg").split(";")[0] or "audio/mpeg"
    return audio, media_type
