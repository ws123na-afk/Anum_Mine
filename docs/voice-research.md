# Voice Research

This is a comparison of free voice options for the ANUM voice assistant, as of October 2026. The constraint is **free only**: no paid speech APIs. Items marked *uncertain* could not be confirmed against a primary source and should be re-checked before release.

## Speech Recognition

| Option | Cost | Privacy | Where it works | Notes |
|---|---|---|---|---|
| Browser Web Speech API | Free | Chrome cloud mode sends audio to Google; Edge uses Azure; **Chrome 139+ on-device mode keeps audio local** | Chrome, Edge, Safari. Firefox behind a flag. **Not in Tauri WebView2/WKWebView** | Used today. ANUM probes on-device mode first. |
| whisper.cpp / faster-whisper | Free, MIT | Fully local | Server, desktop (Tauri plugin or sidecar), Flutter bindings | Best accuracy including Arabic. `small` int8 handles a short command in well under a second on a modern CPU. |
| Whisper in the browser (transformers.js) | Free | Local | Modern browsers | 40–150 MB model download; slow on low-end devices. |
| Moonshine | Free, MIT for English | Local | Python, JS, mobile | Very low latency. A tiny Arabic model exists, but some non-English models are non-commercial (*uncertain per file*). |
| Vosk | Free, Apache-2.0 | Local | Everywhere | Lower accuracy; good for fixed command grammars. |

## Spoken Replies

| Option | Cost | Quality | Arabic | Notes |
|---|---|---|---|---|
| Browser/OS voices (`speechSynthesis`) | Free | Depends on OS | Yes (Windows, macOS, Android system voices) | Used today. Works inside Tauri. |
| Kokoro-82M / kokoro-js | Free, Apache-2.0 | High | **No** | Runs fully in the browser. |
| Piper | Free, GPL-3.0 (active fork) | Good, very fast on CPU | Yes (`ar_JO-kareem`) | Run as a separate service so GPL stays out of ANUM's code. |

## Voice Cloning (optional, self-hosted)

| Model | Licence | Commercial use | Arabic | Hardware |
|---|---|---|---|---|
| **Chatterbox** (Resemble AI) | MIT | Yes | Yes (multilingual) | GPU recommended; built-in watermark |
| OpenVoice V2 | MIT | Yes | Cross-lingual only (*quality uncertain*) | CPU-capable |
| XTTS-v2 (Coqui) | Coqui Public Model License | **No** (non-commercial; company closed) | Yes | GPU |
| F5-TTS | Weights CC-BY-NC-4.0 | **No** | Community only | GPU |
| Fish-Speech / OpenAudio | Weights CC-BY-NC-SA-4.0 | **No** | Yes | GPU |

Recommendation: **Chatterbox** for a consented cloned voice, behind `VITE_ANUM_TTS_URL`.

## Wake Word

There is no free, commercially usable pretrained wake-word model:
- openWakeWord's pretrained models are non-commercial.
- Porcupine's free tier is reported to end in June 2026 (*uncertain*).

ANUM instead listens for the assistant's name with the browser's own recogniser, on by default but only while the Voice view is open, and with a one-click switch-off:
- It uses on-device recognition when Chrome offers it.
- Otherwise the UI warns that the browser's speech service hears the room.

Tap-to-talk and typing are always available too. A custom "Hey Anum" openWakeWord model trained on synthetic data is a later option for fully offline wake-up.

## Security Threats Considered

- Replayed or cloned voices.
- Inaudible ultrasonic commands.
- Spoken prompt injection, e.g. a video saying "approve everything".
- Accidental activation.
- Cloud speech services hearing sensitive content.

## Drawbacks and How ANUM Turns Them Around

| Drawback | What ANUM does |
|---|---|
| Browser speech recognition can send audio to Google/Microsoft/Apple | Uses Chrome on-device mode when available and shows an **On-device / Browser cloud / Typing only** badge, so the user always knows. |
| No speech recognition in Firefox or the Tauri desktop | Typing fallback is built in; native Whisper in Tauri is the next step and makes desktop voice fully offline. |
| Kokoro has no Arabic | Arabic replies use the OS Arabic voices today; Piper or Chatterbox can serve Arabic from a self-hosted server. |
| Piper is GPL | Run only as a separate HTTP service; ANUM talks to it over HTTP. |
| Good cloning needs a GPU | Cloning is optional and server-side. Default voices are free and need nothing. |
| Many cloning models forbid commercial use | Only MIT/Apache models (Chatterbox, OpenVoice, Kokoro) are recommended. |
| Cloned voices enable impersonation | Consent rule, watermark kept on, and **voice is never accepted as approval or identity**. |
| No free wake word | The assistant's name is detected by the browser recogniser while the Voice view is open, with a one-click switch-off and an explicit privacy warning in cloud mode. |
| System voices sound robotic | Kokoro natural voice in the browser for English. Otherwise the most natural system voice is picked automatically. Replies are written as short spoken sentences, not reports. |
| Voice could be used to trigger risky actions | Risk tiers: answers are read-only, task creation needs a click, and approvals/deletions/payments are refused by voice with a link to Approvals. All of this is enforced server-side. |
| Spoken prompt injection | The transcript is passed to the model as untrusted text. The answer is text only, with no path to tools. There is a per-session rate limit. |
| Paid model APIs | Answers work with a free local model through Ollama's OpenAI-compatible endpoint. |

## Sources

**Speech recognition**
- Chrome 139 on-device speech recognition: https://developer.chrome.com/blog/new-in-chrome-139
- `processLocally`: https://developer.mozilla.org/docs/Web/API/SpeechRecognition/processLocally
- WebView2 speech support: https://learn.microsoft.com/en-us/answers/a/2054895
- faster-whisper: https://pypi.org/project/faster-whisper/
- Moonshine: https://github.com/moonshine-ai/moonshine
- Vosk models: https://alphacephei.com/vosk/models
- Tauri STT plugin: https://docs.rs/tauri-plugin-stt

**Spoken replies**
- kokoro-js: https://www.npmjs.com/package/kokoro-js
- Piper Arabic voice: https://tts.ai/voices/piper-kareem-arabic/

**Voice cloning**
- Chatterbox: https://github.com/resemble-ai/chatterbox
- OpenVoice V2: https://huggingface.co/myshell-ai/OpenVoiceV2
- XTTS-v2 review: https://www.promptquorum.com/power-local-llm/xtts-v2-review
- Fish-Speech: https://hub.docker.com/r/fishaudio/fish-speech

**Wake word**
- openWakeWord licence: https://huggingface.co/davidscripka/openwakeword/raw/main/README.md
- Porcupine free tier: https://community.home-assistant.io/t/fyi-picovoice-confirmed-free-tier-accesskeys-will-stop-working-after-june-30-2026/1012744

**Security**
- DolphinAttack: https://arxiv.org/pdf/1708.09537
