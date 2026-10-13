// Voice call requests (docs/voice.md#call-mode): capabilities, the streamed answers with and
// without a camera frame, server speech, the end-of-call summary. The call loop itself is
// pure (callEngine.ts) and receives these as its `api`.
import { apiBaseUrl, authHeaders } from './api';
import { postSse } from './chatApi';
import { apiErrorFromResponse } from './errors';
import { appendTranscript, completeVoiceSession, createVoiceSession, submitVoiceCommand, type TranscriptRetention, type TranscriptSegment } from './voice';
import { callsPath, type CallPage } from './callHistory';
import { DEVICE_ONLY, type VoiceCapabilities } from './call';
import type { AskMeta, CallApi, CallSummaryResult, Frame, StreamHandlers } from './callEngine';

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(`${apiBaseUrl}${path}`, { ...init, headers: { ...(await authHeaders()), ...init.headers } });
  if (!response.ok) throw await apiErrorFromResponse(response, 'ANUM voice request failed');
  return response;
}

/** What this workspace's calls can use; device engines only when the API cannot say. */
export async function getVoiceCapabilities(): Promise<VoiceCapabilities> {
  try {
    return await (await request('/api/v1/voice/capabilities')).json() as VoiceCapabilities;
  } catch {
    return DEVICE_ONLY;
  }
}

async function streamAnswer(path: string, body: unknown, signal: AbortSignal, handlers: StreamHandlers): Promise<void> {
  let failure: string | null = null;
  let finished = false;
  await postSse(path, body, signal, (event) => {
    const data = (event.data ?? {}) as Record<string, unknown>;
    if (event.event === 'meta') handlers.onMeta(data as unknown as AskMeta);
    else if (event.event === 'delta' && typeof data.text === 'string') handlers.onText(data.text);
    else if (event.event === 'done') finished = true;
    else if (event.event === 'error') failure = typeof data.message === 'string' ? data.message : 'I could not answer.';
  });
  if (failure) throw new Error(failure);
  if (!finished && !signal.aborted) throw new Error('The answer ended early. Try again.');
}

export async function summarizeCall(sessionId: string, durationSeconds: number, save: boolean): Promise<CallSummaryResult> {
  const response = await request(`/api/v1/voice/sessions/${encodeURIComponent(sessionId)}/summary`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ duration_seconds: durationSeconds, save }),
  });
  return response.json() as Promise<CallSummaryResult>;
}

/** One or two sentences in the server voice, as audio. */
export async function serverSpeech(sessionId: string, text: string, signal?: AbortSignal): Promise<Blob> {
  const response = await request(`/api/v1/voice/sessions/${encodeURIComponent(sessionId)}/speech`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text }),
    signal,
  });
  return response.blob();
}

export const callApi: CallApi = {
  createSession: (locale, retention, name) => createVoiceSession(locale, retention as TranscriptRetention, name),
  appendTranscript: (sessionId, text, sequence) => appendTranscript(sessionId, text, sequence),
  ask: (sessionId, segmentId, signal, handlers) =>
    streamAnswer(`/api/v1/voice/sessions/${encodeURIComponent(sessionId)}/ask/stream`, { transcript_segment_id: segmentId }, signal, handlers),
  look: (sessionId, segmentId, frame: Frame, signal, handlers) =>
    streamAnswer(
      `/api/v1/voice/sessions/${encodeURIComponent(sessionId)}/look/stream`,
      { transcript_segment_id: segmentId, image_base64: frame.base64, media_type: frame.mediaType },
      signal,
      handlers,
    ),
  async transcribe(sessionId, audio, sequence, durationMs) {
    const query = `client_sequence=${sequence}&duration_ms=${Math.min(120000, Math.max(0, Math.round(durationMs)))}`;
    const response = await request(`/api/v1/voice/sessions/${encodeURIComponent(sessionId)}/transcribe?${query}`, {
      method: 'POST',
      headers: { 'content-type': audio.type.split(';')[0] || 'audio/webm' },
      body: audio,
    });
    return response.json() as Promise<{ id: string; text: string }>;
  },
  async createTask(sessionId, segmentId) {
    const created = await submitVoiceCommand(sessionId, segmentId);
    return { title: created.task.title };
  },
  summarize: summarizeCall,
  async complete(sessionId) { await completeVoiceSession(sessionId); },
};

/** One page of the caller's own calls, newest first (docs/voice.md#recent-calls). */
export async function listCalls(cursor: string | null, limit?: number): Promise<CallPage> {
  const page = await (await request(callsPath(cursor, limit))).json() as Partial<CallPage> | null;
  // A reply without a list (an older API or a proxy page) is an error, never a crash of the page.
  if (!page || !Array.isArray(page.items)) throw new Error('The call list answer had no items.');
  return { items: page.items, next_cursor: typeof page.next_cursor === 'string' ? page.next_cursor : null };
}

/** A call's stored transcript, in order; empty once erased or expired. */
export async function getCallTranscript(sessionId: string): Promise<TranscriptSegment[]> {
  return (await request(`/api/v1/voice/sessions/${encodeURIComponent(sessionId)}/transcript`)).json() as Promise<TranscriptSegment[]>;
}
