import type { Task } from '@anum/contracts';
import { authHeaders } from './api';
import { apiErrorFromResponse } from './errors';

const apiBaseUrl = import.meta.env.VITE_ANUM_API_URL ?? 'http://localhost:8000';

export type TranscriptRetention = 'session' | '30_days' | 'permanent';
export type VoiceSessionStatus = 'active' | 'completed' | 'cancelled';

export interface VoiceSession {
  id: string;
  locale: string;
  retention: TranscriptRetention;
  assistant_name: string;
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
  assistantName?: string,
): Promise<VoiceSession> {
  return voiceRequest('/api/v1/voice/sessions', {
    method: 'POST',
    body: JSON.stringify({ locale, retention, ...(assistantName ? { assistant_name: assistantName } : {}) }),
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

export type VoiceIntent = 'question' | 'status' | 'create_task' | 'visual_only' | 'identity' | 'greeting' | 'thanks';
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
  continuous = true,
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
  // continuous=false lets the browser end the turn by itself when the speaker pauses.
  recognition.continuous = continuous;
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

/** `natural` = Kokoro in the browser (free, English); `server` = self-hosted voice; otherwise a system voice name. */
export type ReplyVoice = '' | 'natural' | 'server' | string;

export interface SpeakOptions {
  voice?: ReplyVoice;
  locale: string;
  onStart?: () => void;
  onEnd?: () => void;
  onFallback?: (reason: string) => void;
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

const naturalHints = /natural|neural|online|premium|enhanced|wavenet|siri|google/i;
const femaleHints = /female|aria|jenny|sonia|libby|natasha|samantha|ava|allison|zira|hoda|salma|zariyah|amira|layla|fatima/i;

/** Order voices so the least robotic ones for this language come first. */
export function rankVoices(voices: SpeechSynthesisVoice[], locale: string): SpeechSynthesisVoice[] {
  const language = locale.slice(0, 2).toLowerCase();
  const score = (voice: SpeechSynthesisVoice) =>
    (voice.lang.toLowerCase() === locale.toLowerCase() ? 4 : 0)
    + (naturalHints.test(voice.name) ? 3 : 0)
    + (femaleHints.test(voice.name) ? 1 : 0)
    + (voice.localService ? 0 : 1);
  return voices
    .filter((voice) => voice.lang.toLowerCase().startsWith(language))
    .sort((a, b) => score(b) - score(a));
}

export function hasVoiceServer(): boolean {
  return Boolean(voiceServerUrl);
}

/** Kokoro voices only cover English and a few European/Asian languages, not Arabic. */
export function naturalVoiceSupports(locale: string): boolean {
  return locale.toLowerCase().startsWith('en');
}

let currentAudio: HTMLAudioElement | null = null;
type KokoroInstance = { generate(text: string, options: { voice: string }): Promise<{ toBlob(): Blob }> };
let kokoro: Promise<KokoroInstance> | null = null;

function loadKokoro(): Promise<KokoroInstance> {
  kokoro ??= import('kokoro-js')
    .then(({ KokoroTTS }) => KokoroTTS.from_pretrained('onnx-community/Kokoro-82M-v1.0-ONNX', { dtype: 'q8', device: 'wasm' }))
    .catch((error: unknown) => { kokoro = null; throw error; }) as Promise<KokoroInstance>;
  return kokoro;
}

/** Start downloading the natural voice in the background (about 90 MB, cached by the browser). */
export function prepareNaturalVoice(): Promise<void> {
  return loadKokoro().then(() => undefined);
}

export function stopSpeaking(): void {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  currentAudio?.pause();
  currentAudio = null;
}

function playBlob(blob: Blob, options: SpeakOptions): Promise<void> {
  const url = URL.createObjectURL(blob);
  currentAudio = new Audio(url);
  currentAudio.onplay = () => options.onStart?.();
  currentAudio.onended = () => { URL.revokeObjectURL(url); options.onEnd?.(); };
  currentAudio.onerror = () => { URL.revokeObjectURL(url); options.onEnd?.(); };
  return currentAudio.play();
}

async function speakWithSystemVoice(text: string, options: SpeakOptions): Promise<void> {
  if (!('speechSynthesis' in window)) { options.onEnd?.(); return; }
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = options.locale;
  utterance.rate = 1;
  utterance.pitch = 1;
  const voices = await listSystemVoices();
  const named = options.voice ? voices.find((voice) => voice.name === options.voice) : undefined;
  const chosen = named ?? rankVoices(voices, options.locale)[0];
  if (chosen) utterance.voice = chosen;
  utterance.onstart = () => options.onStart?.();
  utterance.onend = () => options.onEnd?.();
  utterance.onerror = () => options.onEnd?.();
  window.speechSynthesis.speak(utterance);
}

/**
 * Speak a reply with the chosen engine. Natural (Kokoro) and server voices fall
 * back to the best system voice if they fail, so a reply is never lost.
 */
export async function speak(text: string, options: SpeakOptions): Promise<void> {
  stopSpeaking();
  try {
    if (options.voice === 'server' && voiceServerUrl) {
      const response = await fetch(voiceServerUrl, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text, language: options.locale }),
      });
      if (!response.ok) throw new Error(`Voice server failed: ${response.status}`);
      await playBlob(await response.blob(), options);
      return;
    }
    if (options.voice === 'natural' && naturalVoiceSupports(options.locale)) {
      const tts = await loadKokoro();
      const audio = await tts.generate(text, { voice: 'af_heart' });
      await playBlob(audio.toBlob(), options);
      return;
    }
  } catch (error) {
    options.onFallback?.(error instanceof Error ? error.message : 'Voice engine unavailable');
  }
  await speakWithSystemVoice(text, { ...options, voice: options.voice === 'natural' || options.voice === 'server' ? '' : options.voice });
}

/** A soft two-note chime when the assistant wakes up. Generated, so nothing is downloaded. */
export function playWakeChime(): void {
  try {
    const context = new AudioContext();
    const now = context.currentTime;
    [660, 880].forEach((frequency, index) => {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = 'sine';
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0, now + index * 0.09);
      gain.gain.linearRampToValueAtTime(0.08, now + index * 0.09 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + index * 0.09 + 0.35);
      oscillator.connect(gain).connect(context.destination);
      oscillator.start(now + index * 0.09);
      oscillator.stop(now + index * 0.09 + 0.4);
    });
    window.setTimeout(() => void context.close(), 800);
  } catch {
    // Audio output is optional.
  }
}

export interface WakeListener {
  start: () => void;
  stop: () => void;
  pause: () => void;
  resume: () => void;
}

/**
 * Listens continuously and calls `onWake` with whatever was said after the
 * assistant's name ("Layla, what's running?" -> "what's running?"). Runs only
 * while the page is open and the user has switched it on. Restarts itself when
 * the browser ends a recognition session.
 */
export function createWakeListener(
  name: string,
  locale: string,
  processLocally: boolean,
  onWake: (rest: string) => void,
  onError: (message: string) => void,
): WakeListener | null {
  const Constructor = recognitionConstructor();
  if (!Constructor) return null;
  const escaped = name.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const wake = new RegExp(`(^|\\s)(hey\\s+|hi\\s+|ok\\s+|okay\\s+|يا\\s+)?${escaped}([\\s,.!?،]|$)`, 'i');
  let running = false;
  let paused = false;
  let recognition: SpeechRecognitionLike | null = null;

  const begin = () => {
    if (!running || paused) return;
    recognition = new Constructor();
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.lang = locale;
    if (processLocally) recognition.processLocally = true;
    recognition.onresult = (event) => {
      const latest = event.results[event.results.length - 1];
      if (!latest?.isFinal) return;
      const heard = latest[0].transcript.trim();
      const match = wake.exec(heard);
      if (match) onWake(heard.slice(match.index + match[0].length).trim());
    };
    recognition.onerror = (event: Event) => {
      const code = (event as Event & { error?: string }).error;
      if (code === 'not-allowed' || code === 'service-not-allowed') {
        running = false;
        onError('Microphone permission is blocked. Allow it in the browser to use the wake name.');
      }
    };
    recognition.onend = () => { if (running && !paused) window.setTimeout(begin, 250); };
    try { recognition.start(); } catch { /* already started */ }
  };

  return {
    start: () => { running = true; paused = false; begin(); },
    stop: () => { running = false; recognition?.abort(); recognition = null; },
    pause: () => { paused = true; recognition?.abort(); recognition = null; },
    resume: () => { if (running && paused) { paused = false; begin(); } },
  };
}

async function voiceRequest<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      ...(await authHeaders()),
      'content-type': 'application/json',
      ...init.headers,
    },
  });
  // An ApiError keeps the status and code, so a 402 model budget refusal reads as a budget message.
  if (!response.ok) throw await apiErrorFromResponse(response, 'ANUM voice request failed');
  return response.json() as Promise<T>;
}
