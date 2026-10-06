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

export type VoiceIntent = 'question' | 'status' | 'create_task' | 'visual_only';
export type VoiceRiskTier = 'read' | 'confirm' | 'visual_only';

export interface VoiceAskResult {
  intent: VoiceIntent;
  risk_tier: VoiceRiskTier;
  reply: string;
  proposed_task: string | null;
  workspace: { tasks_total: number; running: number; waiting_approval: number; pending_approvals: number };
  assistant_segment: TranscriptSegment;
}

/** Ask a spoken question. Read-only on the server: it can only answer or propose. */
export async function askVoiceAssistant(sessionId: string, transcriptSegmentId: string): Promise<VoiceAskResult> {
  return voiceRequest(`/api/v1/voice/sessions/${sessionId}/ask`, {
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
  processLocally?: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: Event) => void) | null;
  onend: (() => void) | null;
};

type SpeechRecognitionConstructor = (new () => SpeechRecognitionLike) & {
  available?: (options: { langs: string[]; processLocally: boolean }) => Promise<string>;
};

export type RecognitionMode = 'on-device' | 'cloud' | 'typing';

function recognitionConstructor(): SpeechRecognitionConstructor | undefined {
  const browserWindow = window as typeof window & {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return browserWindow.SpeechRecognition ?? browserWindow.webkitSpeechRecognition;
}

/**
 * Prefer on-device recognition (Chrome 139+) so audio never leaves the machine.
 * Falls back to the browser's cloud recognizer, or to typing when none exists
 * (Firefox, Tauri WebView2/WKWebView).
 */
export function baseRecognitionMode(): RecognitionMode {
  return recognitionConstructor() ? 'cloud' : 'typing';
}

/** Call only from a user gesture: some headless/embedded Chromium builds crash on `available()`. */
export async function detectRecognitionMode(locale: string): Promise<RecognitionMode> {
  const Constructor = recognitionConstructor();
  if (!Constructor) return 'typing';
  if (typeof Constructor.available === 'function') {
    try {
      const availability = await Constructor.available({ langs: [locale], processLocally: true });
      if (availability === 'available') return 'on-device';
    } catch {
      // Older implementations reject the options object; treat as cloud.
    }
  }
  return 'cloud';
}

export function createPushToTalk(
  onTranscript: (text: string, isFinal: boolean) => void,
  onError: (message: string) => void,
  locale = navigator.language || 'en-US',
  onEnd?: () => void,
  processLocally = false,
): { supported: boolean; start: () => void; stop: () => void; cancel: () => void } {
  const Constructor = recognitionConstructor();
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
  if (processLocally) recognition.processLocally = true;
  recognition.onresult = (event) => {
    let text = '';
    let isFinal = true;
    for (let index = 0; index < event.results.length; index += 1) {
      const result = event.results[index];
      text += `${result[0].transcript} `;
      isFinal &&= result.isFinal;
    }
    onTranscript(text.replace(/\s+/g, ' ').trim(), isFinal);
  };
  recognition.onerror = () => onError('Speech recognition failed. Check microphone permission.');
  recognition.onend = () => onEnd?.();
  return {
    supported: true,
    start: () => recognition.start(),
    stop: () => recognition.stop(),
    cancel: () => recognition.abort(),
  };
}

const voiceServerUrl: string | undefined = import.meta.env.VITE_ANUM_TTS_URL;

export interface SpeakOptions {
  voiceName?: string;
  locale: string;
  onStart?: () => void;
  onEnd?: () => void;
}

/** Voices the browser/OS provides for free. Loads asynchronously in Chrome. */
export function listSystemVoices(): Promise<SpeechSynthesisVoice[]> {
  if (!('speechSynthesis' in window)) return Promise.resolve([]);
  const voices = window.speechSynthesis.getVoices();
  if (voices.length) return Promise.resolve(voices);
  return new Promise((resolve) => {
    const done = () => resolve(window.speechSynthesis.getVoices());
    window.speechSynthesis.addEventListener('voiceschanged', done, { once: true });
    window.setTimeout(done, 1500);
  });
}

export function hasVoiceServer(): boolean {
  return Boolean(voiceServerUrl);
}

let currentAudio: HTMLAudioElement | null = null;

export function stopSpeaking(): void {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  currentAudio?.pause();
  currentAudio = null;
}

/**
 * Speak a reply. Uses the self-hosted voice server (e.g. a consented cloned
 * voice) when VITE_ANUM_TTS_URL is configured and the "server" voice is chosen;
 * otherwise the free system voices.
 */
export async function speak(text: string, options: SpeakOptions): Promise<void> {
  stopSpeaking();
  if (options.voiceName === 'server' && voiceServerUrl) {
    const response = await fetch(voiceServerUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ text, language: options.locale }),
    });
    if (!response.ok) throw new Error(`Voice server failed: ${response.status}`);
    const url = URL.createObjectURL(await response.blob());
    currentAudio = new Audio(url);
    currentAudio.onplay = () => options.onStart?.();
    currentAudio.onended = () => { URL.revokeObjectURL(url); options.onEnd?.(); };
    await currentAudio.play();
    return;
  }
  if (!('speechSynthesis' in window)) { options.onEnd?.(); return; }
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = options.locale;
  const voices = await listSystemVoices();
  const chosen = voices.find((voice) => voice.name === options.voiceName)
    ?? voices.find((voice) => voice.lang.toLowerCase().startsWith(options.locale.slice(0, 2).toLowerCase()));
  if (chosen) utterance.voice = chosen;
  utterance.onstart = () => options.onStart?.();
  utterance.onend = () => options.onEnd?.();
  utterance.onerror = () => options.onEnd?.();
  window.speechSynthesis.speak(utterance);
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
