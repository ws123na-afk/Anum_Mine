import { automatedBrowser, chooseRecognitionMode, type AvailabilityProbe, type RecognitionMode } from './recognitionMode';
import type { Task } from '@anum/contracts';
import { apiBaseUrl, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { matchWakeWord } from './wake';

export { defaultAssistantName, greeting, matchWakeWord, sanitizeAssistantName } from './wake';
import { postSse } from './chatApi';
import { t } from './i18n';
import { createRecognitionGate, recognitionErrorKind, wakeRestartDelay } from './recognitionGate';


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

export type VoiceIntent = 'question' | 'status' | 'marketing_summary' | 'create_task' | 'visual_only' | 'identity' | 'greeting' | 'thanks';
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

export interface VoiceStreamHandlers {
  /** Intent, risk tier and proposal, before any reply text. */
  onMeta?: (meta: Omit<VoiceAskResult, 'reply' | 'assistant_segment'>) => void;
  /** Each piece of the reply as it streams. */
  onText?: (text: string) => void;
}

/**
 * The same answer as askVoiceAssistant, streamed (`/ask/stream`), so speech can start with
 * the first sentence. Resolves with the whole result once the reply is stored.
 */
export async function streamVoiceAssistant(
  sessionId: string,
  transcriptSegmentId: string,
  signal: AbortSignal,
  handlers: VoiceStreamHandlers = {},
): Promise<VoiceAskResult> {
  let meta: Omit<VoiceAskResult, 'reply' | 'assistant_segment'> | null = null;
  let reply = '';
  let segment: TranscriptSegment | null = null;
  let failure: string | null = null;
  await postSse(`/api/v1/voice/sessions/${sessionId}/ask/stream`, { transcript_segment_id: transcriptSegmentId }, signal, (event) => {
    const data = (event.data ?? {}) as Record<string, unknown>;
    if (event.event === 'meta') {
      meta = data as unknown as Omit<VoiceAskResult, 'reply' | 'assistant_segment'>;
      handlers.onMeta?.(meta);
    } else if (event.event === 'delta' && typeof data.text === 'string') {
      reply += data.text;
      handlers.onText?.(data.text);
    } else if (event.event === 'done') {
      segment = (data.assistant_segment as TranscriptSegment | null) ?? null;
    } else if (event.event === 'error') {
      failure = typeof data.message === 'string' ? data.message : t('voice.err.couldNotAnswer');
    }
  });
  if (failure) throw new Error(failure);
  if (!meta) throw new Error(t('voice.err.endedEarly'));
  const now = new Date().toISOString();
  return {
    ...(meta as Omit<VoiceAskResult, 'reply' | 'assistant_segment'>),
    reply: reply.trim(),
    assistant_segment: segment ?? { id: `assistant-${now}`, session_id: sessionId, role: 'assistant', text: reply.trim(), is_final: true, client_sequence: 0, created_at: now },
  };
}

/** Speak and resolve when the speech ends (or a length-based fallback fires: some browsers never report the end). */
export function speakAndWait(text: string, options: SpeakOptions): Promise<void> {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => { if (!done) { done = true; window.clearTimeout(fallback); options.onEnd?.(); resolve(); } };
    const fallback = window.setTimeout(finish, Math.min(30000, 4000 + text.length * 80));
    void speak(text, { ...options, onEnd: finish }).catch(finish);
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

export type { RecognitionMode };

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

/**
 * Needs no user gesture (only installing a language pack does), so the Voice page probes when it
 * opens and the first tap never waits for it. Automated (headless) browsers skip the on-device
 * probe, which crashes their renderer (lib/recognitionMode.ts).
 */
export async function detectRecognitionMode(locale: string, automated: boolean = automatedBrowser()): Promise<RecognitionMode> {
  return chooseRecognitionMode(recognitionConstructor() as AvailabilityProbe | undefined, locale, automated);
}

// One recognition session at a time for the whole page (lib/recognitionGate.ts): the next
// recognizer starts once the previous one has ended, and a refused start is retried.
const recognitionGate = createRecognitionGate();

/** Resolves once no recognizer is running (the wake listener has handed over), or after `maxWaitMs`. */
export function recognitionIdle(maxWaitMs?: number): Promise<void> {
  return recognitionGate.whenIdle(maxWaitMs);
}

const errorMessage = (code: string | undefined): string => {
  switch (recognitionErrorKind(code)) {
    case 'blocked': return t('voice.err.micBlocked');
    case 'no-mic': return t('voice.err.noMic');
    case 'network': return t('voice.err.network');
    case 'not-heard': return t('voice.notHeard');
    default: return t('voice.err.recognition');
  }
};

export function createPushToTalk(
  onTranscript: (text: string, isFinal: boolean) => void,
  onError: (message: string, code?: string) => void,
  locale = navigator.language || 'en-US',
  onEnd?: () => void,
  processLocally = false,
  continuous = true,
): { supported: boolean; start: () => Promise<boolean>; stop: () => void; cancel: () => void } {
  const Constructor = recognitionConstructor();
  if (!Constructor) {
    return {
      supported: false,
      start: async () => { onError(t('voice.err.unsupported')); return false; },
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
  // 'aborted' is our own cancel and 'no-speech' a silent turn (onEnd reports it): neither is an error.
  recognition.onerror = (event: Event) => {
    const code = (event as Event & { error?: string }).error;
    const kind = recognitionErrorKind(code);
    if (kind !== 'silent' && kind !== 'not-heard') onError(errorMessage(code), code);
  };
  let cancelled = false;
  recognition.onend = () => { recognitionGate.ended(recognition); if (!cancelled) onEnd?.(); };
  return {
    supported: true,
    start: async () => {
      const started = await recognitionGate.start(recognition);
      // Cancelled while waiting for the microphone: end it at once.
      if (started && cancelled) { try { recognition.abort(); } catch { /* ended */ } return false; }
      if (!started && !cancelled) onError(t('voice.err.recognition'));
      return started;
    },
    stop: () => { try { recognition.stop(); } catch { /* not started */ } },
    // A cancelled turn ends quietly: no "didn't catch that", no hand-over.
    cancel: () => { cancelled = true; try { recognition.abort(); } catch { /* not started */ } },
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

// The reply's loudness for the space orb (docs/voice.md#the-space-orb). Natural and server voices
// play through an <audio> element, which is routed through an AnalyserNode when an AudioContext is
// already running (a suspended one would silence the reply, so then it plays directly). System
// voices give no audio to measure: each word boundary is a pulse that fades, an approximation.
let outputContext: AudioContext | null = null;
let outputAnalyser: AnalyserNode | null = null;
let outputSource: MediaElementAudioSourceNode | null = null;
let measuredAudio: HTMLAudioElement | null = null;
let outputData: Uint8Array<ArrayBuffer> | null = null;
let systemSpeaking = false;
let lastBoundary = 0;

/**
 * Measure a reply's <audio> for the orb (this module's natural and server voices, and the call's
 * server voice). Call before play(); the element is released when it ends or the next one starts.
 */
export function trackOutputAudio(audio: HTMLAudioElement): void {
  releaseOutput();
  measuredAudio = audio;
  try {
    if (typeof AudioContext === 'undefined') return;
    outputContext ??= new AudioContext();
    if (outputContext.state !== 'running') { void outputContext.resume().catch(() => undefined); return; }
    if (!outputAnalyser) {
      outputAnalyser = outputContext.createAnalyser();
      outputAnalyser.fftSize = 256;
      outputAnalyser.connect(outputContext.destination);
      outputData = new Uint8Array(outputAnalyser.frequencyBinCount);
    }
    outputSource = outputContext.createMediaElementSource(audio);
    outputSource.connect(outputAnalyser);
  } catch {
    outputSource = null;
  }
}

function releaseOutput(): void {
  try { outputSource?.disconnect(); } catch { /* already disconnected */ }
  outputSource = null;
  measuredAudio = null;
}

/** Stop measuring `audio` if it is the one measured (it ended or was stopped). */
export function releaseOutputAudio(audio: HTMLAudioElement | null): void {
  if (audio && audio === measuredAudio) releaseOutput();
}

/** The reply audio's level, 0..1, while a reply plays; 0 when nothing plays. Read once per frame. */
export function outputAmplitude(now = performance.now()): number {
  if (measuredAudio && !measuredAudio.paused && !measuredAudio.ended) {
    if (!outputSource || !outputAnalyser || !outputData) return 0.35;
    outputAnalyser.getByteFrequencyData(outputData);
    let sum = 0;
    for (const value of outputData) sum += value;
    return Math.min(1, sum / outputData.length / 90);
  }
  if (systemSpeaking) return boundaryPulse(now - lastBoundary);
  return 0;
}

/** A system voice's level from the time since its last word boundary: a pulse that fades. */
export function boundaryPulse(sinceBoundaryMs: number): number {
  return 0.2 + 0.6 * Math.exp(-Math.max(0, sinceBoundaryMs) / 180);
}

export function stopSpeaking(): void {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  systemSpeaking = false;
  currentAudio?.pause();
  releaseOutputAudio(currentAudio);
  currentAudio = null;
}

function playBlob(blob: Blob, options: SpeakOptions): Promise<void> {
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  currentAudio = audio;
  trackOutputAudio(audio);
  audio.onplay = () => options.onStart?.();
  audio.onended = () => { URL.revokeObjectURL(url); releaseOutputAudio(audio); options.onEnd?.(); };
  audio.onerror = () => { URL.revokeObjectURL(url); releaseOutputAudio(audio); options.onEnd?.(); };
  return audio.play();
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
  utterance.onstart = () => { systemSpeaking = true; lastBoundary = performance.now(); options.onStart?.(); };
  utterance.onboundary = () => { lastBoundary = performance.now(); };
  utterance.onend = () => { systemSpeaking = false; options.onEnd?.(); };
  utterance.onerror = () => { systemSpeaking = false; options.onEnd?.(); };
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
      if (!response.ok) throw new Error(t('voice.err.server', { status: String(response.status) }));
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
    options.onFallback?.(error instanceof Error ? error.message : t('voice.err.engine'));
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

export type MicrophonePermission = 'granted' | 'denied' | 'prompt' | 'unknown';

/**
 * Reads the microphone permission without asking for it. Hands-free listening starts on its own
 * only when this is 'granted'; otherwise the user starts it with a tap, which is when the browser asks.
 */
export async function microphonePermission(): Promise<MicrophonePermission> {
  try {
    const status = await navigator.permissions?.query({ name: 'microphone' as PermissionName });
    return status?.state ?? 'unknown';
  } catch {
    return 'unknown';
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
  onWake: (rest: string, heard: string) => void,
  onError: (message: string) => void,
): WakeListener | null {
  const Constructor = recognitionConstructor();
  if (!Constructor) return null;
  let running = false;
  let paused = false;
  let recognition: SpeechRecognitionLike | null = null;
  let timer = 0;
  let quickEnds = 0;

  const begin = () => {
    window.clearTimeout(timer);
    if (!running || paused || recognition) return;
    const current = new Constructor();
    recognition = current;
    const startedAt = Date.now();
    current.continuous = true;
    current.interimResults = false;
    current.lang = locale;
    if (processLocally) current.processLocally = true;
    current.onresult = (event) => {
      const latest = event.results[event.results.length - 1];
      if (!latest?.isFinal) return;
      const heard = latest[0].transcript.trim();
      const rest = matchWakeWord(heard, name);
      if (rest !== null) onWake(rest, heard);
    };
    current.onerror = (event: Event) => {
      const code = (event as Event & { error?: string }).error;
      if (recognitionErrorKind(code) === 'blocked') {
        running = false;
        onError(t('voice.err.micBlocked'));
      }
    };
    // The browser ends a continuous session on silence or after about a minute: listen again at
    // once (it used to wait 250 ms, missing the start of the next sentence).
    current.onend = () => {
      recognitionGate.ended(current);
      if (recognition === current) recognition = null;
      if (!running || paused) return;
      const lasted = Date.now() - startedAt;
      quickEnds = lasted >= 1000 ? 0 : quickEnds + 1;
      timer = window.setTimeout(begin, wakeRestartDelay(lasted, quickEnds - 1));
    };
    void recognitionGate.start(current).then((started) => {
      // Stopped or paused while waiting for the microphone: end the late starter at once.
      if (started && recognition !== current) { try { current.abort(); } catch { /* ended */ } return; }
      if (!started && recognition === current) { recognition = null; if (running && !paused) timer = window.setTimeout(begin, 500); }
    });
  };

  const halt = () => {
    window.clearTimeout(timer);
    const current = recognition;
    recognition = null;
    try { current?.abort(); } catch { /* not started */ }
  };

  return {
    start: () => { running = true; paused = false; begin(); },
    stop: () => { running = false; halt(); },
    pause: () => { paused = true; halt(); },
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
