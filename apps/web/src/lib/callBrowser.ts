// Browser side of a voice call (docs/voice.md#call-mode): the microphone with its level meter
// and per-turn recorder, Web Speech recognition that can forget what it heard, the reply
// voice (device, the in-browser natural voice, or the server voice), and the camera frame.
import type { AudioRecorder, Frame, MicHandle, Recognizer, Speaker } from './callEngine';
import { frameSize } from './call';
import { serverSpeech } from './callApi';
import { releaseOutputAudio, speakAndWait, stopSpeaking, trackOutputAudio, type ReplyVoice } from './voice';

/** Opens the microphone with echo cancellation, so the reply's sound is not heard as the caller. */
export async function openMicrophone(): Promise<MicHandle> {
  if (!navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error('No microphone API'), { name: 'NotFoundError' });
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  let context: AudioContext | null = null;
  let analyser: AnalyserNode | null = null;
  try {
    context = new AudioContext();
    analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaStreamSource(stream).connect(analyser);
    void context.resume();
  } catch {
    analyser = null; // Voice activity then comes from the recognizer alone.
  }
  const samples = new Float32Array(analyser?.fftSize ?? 0);
  return {
    level() {
      if (!analyser) return -1;
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (const value of samples) sum += value * value;
      return Math.min(1, Math.sqrt(sum / samples.length) * 5);
    },
    setEnabled(on) { stream.getAudioTracks().forEach((track) => { track.enabled = on; }); },
    close() {
      stream.getTracks().forEach((track) => track.stop());
      void context?.close().catch(() => undefined);
    },
    recorder(): AudioRecorder | null {
      if (typeof MediaRecorder === 'undefined') return null;
      const mimeType = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find((type) => MediaRecorder.isTypeSupported?.(type)) ?? '';
      let recorder: MediaRecorder | null = null;
      let chunks: Blob[] = [];
      let startedAt = 0;
      return {
        start() {
          chunks = [];
          recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
          recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
          startedAt = performance.now();
          recorder.start();
        },
        stop() {
          return new Promise((resolve) => {
            const active = recorder;
            recorder = null;
            if (!active || active.state === 'inactive') { resolve(null); return; }
            active.onstop = () => resolve(chunks.length ? { blob: new Blob(chunks, { type: active.mimeType || 'audio/webm' }), durationMs: performance.now() - startedAt } : null);
            active.stop();
          });
        },
      };
    },
  };
}

type RecognitionResultList = ArrayLike<{ 0: { transcript: string }; isFinal: boolean }>;
type RecognitionLike = EventTarget & {
  continuous: boolean; interimResults: boolean; lang: string; processLocally?: boolean;
  start(): void; stop(): void; abort(): void;
  onresult: ((event: Event & { results: RecognitionResultList }) => void) | null;
  onerror: ((event: Event & { error?: string }) => void) | null;
  onend: (() => void) | null;
};

/**
 * Continuous device recognition for a call. `mark()` forgets the results so far, so each turn
 * reports only its own words; the browser's session restarts by itself when it times out.
 */
export function createCallRecognizer(
  locale: string,
  processLocally: boolean,
  onText: (text: string, isFinal: boolean) => void,
  onBlocked: () => void,
): Recognizer | null {
  const scope = window as typeof window & { SpeechRecognition?: new () => RecognitionLike; webkitSpeechRecognition?: new () => RecognitionLike };
  const Constructor = scope.SpeechRecognition ?? scope.webkitSpeechRecognition;
  if (!Constructor) return null;
  let running = false;
  let current: RecognitionLike | null = null;
  let seen = 0;
  let markAt = 0;

  const begin = () => {
    if (!running) return;
    const recognition = new Constructor();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = locale;
    if (processLocally) recognition.processLocally = true;
    seen = 0;
    markAt = 0;
    recognition.onresult = (event) => {
      seen = event.results.length;
      let text = '';
      let final = true;
      for (let index = markAt; index < event.results.length; index += 1) {
        text += `${event.results[index][0].transcript} `;
        final &&= event.results[index].isFinal;
      }
      const clean = text.replace(/\s+/g, ' ').trim();
      if (clean) onText(clean, final);
    };
    recognition.onerror = (event) => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') { running = false; onBlocked(); }
    };
    recognition.onend = () => { if (running && current === recognition) window.setTimeout(begin, 150); };
    current = recognition;
    try { recognition.start(); } catch { /* already running */ }
  };

  return {
    start() { if (running) return; running = true; begin(); },
    stop() { running = false; const recognition = current; current = null; recognition?.abort(); },
    mark() { markAt = seen; },
  };
}

export type ReplyEngine = 'device' | 'natural' | 'server';

/**
 * Speaks one sentence at a time with the chosen engine. `stop()` silences it at once and
 * settles the sentence in progress, which is what barge-in needs. A failing server voice
 * falls back to the device voice for that sentence, and `onFallback` says so.
 */
export function createCallSpeaker(settings: () => { engine: ReplyEngine; locale: string; sessionId: string | null; voice: ReplyVoice }, onFallback: (reason: string) => void): Speaker {
  let settle: (() => void) | null = null;
  let audio: HTMLAudioElement | null = null;
  let fetching: AbortController | null = null;

  const deviceSpeak = (text: string, onStart: () => void, locale: string, voice: ReplyVoice) =>
    speakAndWait(text, { locale, voice, onStart, onFallback: (reason) => onFallback(reason) });

  return {
    speak(text, onStart) {
      return new Promise<void>((resolve) => {
        let done = false;
        const finish = () => { if (done) return; done = true; settle = null; resolve(); };
        settle = finish;
        const { engine, locale, sessionId, voice } = settings();
        if (engine === 'server' && sessionId) {
          fetching = new AbortController();
          void serverSpeech(sessionId, text, fetching.signal).then((blob) => {
            if (done) return;
            const url = URL.createObjectURL(blob);
            const playing = new Audio(url);
            audio = playing;
            // The orb's speaking bars follow this audio (docs/voice.md#the-space-orb).
            trackOutputAudio(playing);
            playing.onplay = onStart;
            playing.onended = () => { URL.revokeObjectURL(url); releaseOutputAudio(playing); finish(); };
            playing.onerror = () => { URL.revokeObjectURL(url); releaseOutputAudio(playing); finish(); };
            void playing.play().catch(() => finish());
          }).catch((error: unknown) => {
            if (done) return;
            onFallback(error instanceof Error ? error.message : 'Server voice unavailable');
            void deviceSpeak(text, onStart, locale, '').then(finish);
          });
          return;
        }
        void deviceSpeak(text, onStart, locale, engine === 'natural' ? 'natural' : voice).then(finish);
      });
    },
    stop() {
      fetching?.abort();
      fetching = null;
      audio?.pause();
      releaseOutputAudio(audio);
      audio = null;
      stopSpeaking();
      settle?.();
    },
  };
}

export type Facing = 'user' | 'environment';

export async function openCamera(facing: Facing): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error('No camera API'), { name: 'NotFoundError' });
  return navigator.mediaDevices.getUserMedia({ video: { facingMode: facing, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
}

/** One JPEG frame of the preview, scaled down and under the API's size limit; null before the video plays. */
export function captureFrame(video: HTMLVideoElement, maxBytes: number): Frame | null {
  if (!video.videoWidth || !video.videoHeight) return null;
  for (const [side, quality] of [[1024, 0.82], [768, 0.7], [512, 0.6]] as const) {
    const size = frameSize(video.videoWidth, video.videoHeight, side);
    const canvas = document.createElement('canvas');
    canvas.width = size.width;
    canvas.height = size.height;
    canvas.getContext('2d')?.drawImage(video, 0, 0, size.width, size.height);
    const url = canvas.toDataURL('image/jpeg', quality);
    const base64 = url.slice(url.indexOf(',') + 1);
    if (base64.length * 0.75 <= maxBytes) return { base64, mediaType: 'image/jpeg' };
  }
  return null;
}
