"""Speech-to-text for meeting recordings (docs/meetings.md#transcription).

The deployment chooses one provider with ``ANUM_TRANSCRIPTION_PROVIDER``:

``openai-compatible``
    ``POST {ANUM_TRANSCRIPTION_BASE_URL}/audio/transcriptions`` (multipart, the OpenAI
    audio API). A local Whisper server answers it for free and keeps audio on your own
    machine (faster-whisper server / Speaches, whisper.cpp ``server``, LocalAI); hosted
    providers answer it too (OpenAI ``whisper-1``, Groq ``whisper-large-v3``). Segments
    carry timestamps; Whisper does not label speakers.
``deepgram``
    Deepgram's hosted pre-recorded API with diarization, so segments carry
    ``Speaker 1``, ``Speaker 2`` labels.
``none`` (default)
    No transcription: recordings are refused with a message that says how to set one
    up. ANUM never invents a transcript.

The API key (``ANUM_TRANSCRIPTION_API_KEY``) stays in the server's environment; it is
never stored with a meeting nor returned to a client. Base URLs are operator
configuration, like ``ANUM_SEARXNG_URL``: workspaces cannot point them elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from .meeting_transcripts import ParsedSegment
from .settings import Settings

# Tests replace this with an ``httpx.MockTransport``; production uses the default.
http_transport: httpx.AsyncBaseTransport | None = None

RECORDING_EXTENSIONS = {".mp3", ".mp4", ".m4a", ".wav", ".webm", ".mpeg", ".mpga", ".ogg", ".oga", ".flac", ".mov"}
OPENAI_DEFAULT_MODEL = "whisper-1"
DEEPGRAM_DEFAULT_MODEL = "nova-3"
DEEPGRAM_DEFAULT_BASE_URL = "https://api.deepgram.com/v1"
_LANGUAGE_NAMES = {"english": "en", "arabic": "ar"}


class TranscriptionError(RuntimeError):
    """The provider failed; ``detail`` is safe to show (no key, no audio)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class TranscriptionResult:
    segments: list[ParsedSegment]
    language: str | None
    duration_seconds: int | None


class Transcriber(Protocol):
    provider: str
    model: str
    target: str

    async def transcribe(
        self, content: bytes, *, file_name: str, content_type: str, language: str | None
    ) -> TranscriptionResult: ...


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=http_transport, follow_redirects=False)


def _language_code(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip().lower()
    return _LANGUAGE_NAMES.get(value, value[:2] if value[:2] in {"en", "ar"} else None)


def _ms(value: Any) -> int | None:
    try:
        return max(0, int(round(float(value) * 1000)))
    except (TypeError, ValueError):
        return None


def _raise_for_status(response: httpx.Response, provider: str) -> None:
    if response.status_code < 400:
        return
    if response.status_code in {401, 403}:
        raise TranscriptionError(
            f"The {provider} transcription service refused the API key. Check ANUM_TRANSCRIPTION_API_KEY."
        )
    if response.status_code == 413:
        raise TranscriptionError("The transcription service refused the recording as too large. Upload a shorter one.")
    if response.status_code == 429:
        raise TranscriptionError("The transcription service is rate limiting requests. Try again in a few minutes.")
    raise TranscriptionError(f"The transcription service answered HTTP {response.status_code}. Try again later.")


class OpenAICompatibleTranscriber:
    provider = "openai-compatible"

    def __init__(self, base_url: str, model: str, api_key: str | None, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model or OPENAI_DEFAULT_MODEL
        self._api_key = api_key
        self._timeout = timeout
        self.target = _host(self.base_url)

    async def transcribe(
        self, content: bytes, *, file_name: str, content_type: str, language: str | None
    ) -> TranscriptionResult:
        data: dict[str, Any] = {
            "model": self.model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment",
        }
        if language in {"en", "ar"}:
            data["language"] = language
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with _client(self._timeout) as client:
                response = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    data=data,
                    files={"file": (file_name, content, content_type)},
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise TranscriptionError("The transcription service did not answer in time. Try a shorter recording.") from exc
        except httpx.HTTPError as exc:
            raise TranscriptionError(
                f"The transcription service at {self.target} could not be reached. Check ANUM_TRANSCRIPTION_BASE_URL."
            ) from exc
        _raise_for_status(response, "OpenAI-compatible")
        try:
            body = response.json()
        except ValueError as exc:
            raise TranscriptionError("The transcription service answered with something that is not JSON.") from exc
        segments: list[ParsedSegment] = []
        for item in body.get("segments") or []:
            text = str(item.get("text") or "").strip()
            if text:
                segments.append(ParsedSegment(text=text, start_ms=_ms(item.get("start")), end_ms=_ms(item.get("end"))))
        if not segments and str(body.get("text") or "").strip():
            segments.append(ParsedSegment(text=str(body["text"]).strip(), start_ms=0))
        duration = body.get("duration")
        return TranscriptionResult(
            segments=segments,
            language=_language_code(body.get("language")) or language,
            duration_seconds=int(float(duration)) if isinstance(duration, (int, float)) else None,
        )


class DeepgramTranscriber:
    provider = "deepgram"

    def __init__(self, base_url: str, model: str, api_key: str | None, timeout: float) -> None:
        self.base_url = (base_url or DEEPGRAM_DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEEPGRAM_DEFAULT_MODEL
        self._api_key = api_key
        self._timeout = timeout
        self.target = _host(self.base_url)

    async def transcribe(
        self, content: bytes, *, file_name: str, content_type: str, language: str | None
    ) -> TranscriptionResult:
        params: dict[str, str] = {
            "model": self.model,
            "smart_format": "true",
            "punctuate": "true",
            "diarize": "true",
            "utterances": "true",
        }
        if language in {"en", "ar"}:
            params["language"] = language
        else:
            params["detect_language"] = "true"
        try:
            async with _client(self._timeout) as client:
                response = await client.post(
                    f"{self.base_url}/listen",
                    params=params,
                    content=content,
                    headers={"Authorization": f"Token {self._api_key}", "Content-Type": content_type},
                )
        except httpx.TimeoutException as exc:
            raise TranscriptionError("Deepgram did not answer in time. Try a shorter recording.") from exc
        except httpx.HTTPError as exc:
            raise TranscriptionError("Deepgram could not be reached. Check the server's network access.") from exc
        _raise_for_status(response, "Deepgram")
        try:
            body = response.json()
        except ValueError as exc:
            raise TranscriptionError("Deepgram answered with something that is not JSON.") from exc
        results = body.get("results") or {}
        segments = [
            ParsedSegment(
                text=str(item.get("transcript") or "").strip(),
                speaker=f"Speaker {int(item['speaker']) + 1}" if isinstance(item.get("speaker"), int) else None,
                start_ms=_ms(item.get("start")),
                end_ms=_ms(item.get("end")),
            )
            for item in results.get("utterances") or []
            if str(item.get("transcript") or "").strip()
        ]
        channels = results.get("channels") or [{}]
        detected = channels[0].get("detected_language") if channels else None
        if not segments:
            alternatives = (channels[0].get("alternatives") if channels else None) or [{}]
            text = str(alternatives[0].get("transcript") or "").strip()
            if text:
                segments.append(ParsedSegment(text=text, start_ms=0))
        duration = (body.get("metadata") or {}).get("duration")
        return TranscriptionResult(
            segments=segments,
            language=_language_code(detected) or language,
            duration_seconds=int(float(duration)) if isinstance(duration, (int, float)) else None,
        )


def build_transcriber(config: Settings) -> Transcriber | None:
    """The configured transcriber, or ``None`` when recordings cannot be transcribed."""
    provider = config.transcription_provider
    key = config.transcription_api_key.get_secret_value().strip() if config.transcription_api_key else None
    timeout = config.transcription_timeout_seconds
    if provider == "openai-compatible" and config.transcription_base_url.strip():
        return OpenAICompatibleTranscriber(config.transcription_base_url.strip(), config.transcription_model.strip(), key or None, timeout)
    if provider == "deepgram" and key:
        return DeepgramTranscriber(config.transcription_base_url.strip(), config.transcription_model.strip(), key, timeout)
    return None


def setup_problem(config: Settings) -> str | None:
    """Why recordings cannot be transcribed, in words an owner can act on; None when they can."""
    provider = config.transcription_provider
    if provider == "none":
        return (
            "Recording transcription is off on this server. Ask the server administrator to set "
            "ANUM_TRANSCRIPTION_PROVIDER=openai-compatible with ANUM_TRANSCRIPTION_BASE_URL (a local "
            "Whisper server, or a hosted speech-to-text API with ANUM_TRANSCRIPTION_API_KEY), or "
            "ANUM_TRANSCRIPTION_PROVIDER=deepgram with ANUM_TRANSCRIPTION_API_KEY. Until then, upload a "
            "transcript file or import one from Zoom or Teams."
        )
    if provider == "openai-compatible" and not config.transcription_base_url.strip():
        return "ANUM_TRANSCRIPTION_BASE_URL is not set. Point it at your Whisper server, e.g. http://whisper:8000/v1."
    if provider == "deepgram" and not (config.transcription_api_key and config.transcription_api_key.get_secret_value().strip()):
        return "ANUM_TRANSCRIPTION_API_KEY is not set for Deepgram."
    return None
