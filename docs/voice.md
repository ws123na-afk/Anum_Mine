# Voice

Voice should become a natural ANUM surface, but it should not be the first core dependency. The platform should first establish identity, task state, approvals, memory, and event streaming so voice sessions can reuse the same runtime.

## Voice Use Cases

- Start and manage tasks hands-free.
- Hear concise task status updates.
- Approve or reject low-friction actions when policy allows.
- Dictate notes and instructions into memory or tasks.
- Use desktop or mobile context while moving between devices.

## Architecture

Voice clients should connect to the same backend task APIs and realtime streams as other clients. Speech-to-text, text-to-speech, and realtime audio models should be provider adapters behind the model gateway when possible.

## Safety

Voice approval requires extra care. The system should confirm high-impact decisions using clear summaries and may require visual confirmation or device authentication for sensitive actions. Voice transcripts should be treated as sensitive memory sources.

## Now

Document voice as a later surface and keep core APIs modality-neutral.

## Later

Add push-to-talk sessions, streaming transcription, spoken task summaries, voice approval policy, Android voice entry, desktop hotkey activation, and configurable transcript retention.