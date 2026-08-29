import type { Task } from '@anum/contracts';
import { defaultTenantContext } from './api';

const apiBaseUrl = import.meta.env.VITE_ANUM_API_URL ?? 'http://localhost:8000';

export type TranscriptRetention = 'session' | '30_days' | 'permanent';
export type VoiceSessionStatus = 'active' | 'completed' | 'cancelled';

export interface VoiceSession {
  id: string;
  locale: string;
  retention: TranscriptRetention;
  status: VoiceSessionStatus;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
}

export interface TranscriptSegment {
  id: string;
  session_id: string;
  role: 'user' | 'assistant';
  text: string;
  is_final: boolean;
  client_sequence: number;
  created_at: string;
}

interface VoiceCommandResult {
  session: VoiceSession;
  task: {
    id: string;
    title: string;
    prompt: string;
    status: Task['status'];
    tenant_id: string;
    workspace_id: string;
    created_at: string;
    updated_at: string;
  };
  transcript_segment_id: string;
}

export async function createVoiceSession(
  locale = navigator.language || 'en-US',
  retention: TranscriptRetention = 'session',
): Promise<VoiceSession> {
  return voiceRequest('/api/v1/voice/sessions', {
    method: 'POST',
    body: JSON.stringify({ locale, retention }),
  });
}

export async function appendTranscript(
  sessionId: string,
  text: string,
  sequence: number,
  isFinal = true,
): Promise<TranscriptSegment> {
  return voiceRequest(`/api/v1/voice/sessions/${sessionId}/transcript`, {
    method: 'POST',
    body: JSON.stringify({ role: 'user', text, is_final: isFinal, client_sequence: sequence }),
  });
}

export async function submitVoiceCommand(
  sessionId: string,
  transcriptSegmentId: string,
): Promise<VoiceCommandResult> {
  return voiceRequest(`/api/v1/voice/sessions/${sessionId}/commands`, {
    method: 'POST',
    body: JSON.stringify({ transcript_segment_id: transcriptSegmentId }),
  });
}

export async function completeVoiceSession(sessionId: string): Promise<VoiceSession> {
  return voiceRequest(`/api/v1/voice/sessions/${sessionId}/complete`, { method: 'POST' });
}

type SpeechRecognitionEventLike = Event & {
  results: ArrayLike<{ 0: { transcript: string }; isFinal: boolean }>;
};

type SpeechRecognitionLike = EventTarget & {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: Event) => void) | null;
  onend: (() => void) | null;
};

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

export function createPushToTalk(
  onTranscript: (text: string, isFinal: boolean) => void,
  onError: (message: string) => void,
  locale = navigator.language || 'en-US',
): { supported: boolean; start: () => void; stop: () => void; cancel: () => void } {
  const browserWindow = window as typeof window & {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  const Constructor = browserWindow.SpeechRecognition ?? browserWindow.webkitSpeechRecognition;
  if (!Constructor) {
    return {
      supported: false,
      start: () => onError('Speech recognition is not supported by this browser.'),
      stop: () => undefined,
      cancel: () => undefined,
    };
  }

  const recognition = new Constructor();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = locale;
  recognition.onresult = (event) => {
    for (let index = 0; index < event.results.length; index += 1) {
      const result = event.results[index];
      onTranscript(result[0].transcript.trim(), result.isFinal);
    }
  };
  recognition.onerror = () => onError('Speech recognition failed. Check microphone permission.');
  return {
    supported: true,
    start: () => recognition.start(),
    stop: () => recognition.stop(),
    cancel: () => recognition.abort(),
  };
}

async function voiceRequest<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      'content-type': 'application/json',
      'x-tenant-id': defaultTenantContext.tenantId,
      'x-workspace-id': defaultTenantContext.workspaceId,
      'x-user-id': defaultTenantContext.userId,
      'x-user-roles': defaultTenantContext.roles.join(','),
      ...init.headers,
    },
  });
  if (!response.ok) throw new Error(`ANUM voice request failed: ${response.status}`);
  return response.json() as Promise<T>;
}
