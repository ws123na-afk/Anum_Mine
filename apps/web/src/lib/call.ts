// Pure helpers for voice call mode (docs/voice.md#call-mode): the call timer, voice
// activity detection, the call's languages, frame sizing and the latency verdict.
// No browser or Vite APIs, so `pnpm test:unit` exercises them directly.

/** What the API reports a call can use (`GET /api/v1/voice/capabilities`). */
export interface EngineStatus {
  id: 'device' | 'server';
  kind: 'device' | 'server';
  available: boolean;
  label: string;
  model: string | null;
  usd_per_minute: number | null;
  usd_per_million_chars: number | null;
  reason: string | null;
}

export interface VoiceCapabilities {
  stt: EngineStatus[];
  tts: EngineStatus[];
  vision: { available: boolean; model: string | null; reason: string | null; max_frame_bytes: number; media_types: string[] };
  latency_target_ms: { first_audio: number; barge_in: number; end_of_turn: number };
  locales: string[];
  max_audio_bytes: number;
  max_audio_ms: number;
  max_speech_chars: number;
}

/** Used until the API answers, and when it cannot: device engines only, no camera answers. */
export const DEVICE_ONLY: VoiceCapabilities = {
  stt: [{ id: 'device', kind: 'device', available: true, label: 'Device', model: null, usd_per_minute: null, usd_per_million_chars: null, reason: null }],
  tts: [{ id: 'device', kind: 'device', available: true, label: 'Device', model: null, usd_per_minute: null, usd_per_million_chars: null, reason: null }],
  vision: { available: false, model: null, reason: null, max_frame_bytes: 1_500_000, media_types: ['image/jpeg'] },
  latency_target_ms: { first_audio: 1500, barge_in: 300, end_of_turn: 700 },
  locales: ['en-US', 'en-GB', 'ar-SA', 'ar-AE'],
  max_audio_bytes: 4 * 1024 * 1024,
  max_audio_ms: 120_000,
  max_speech_chars: 600,
};

export function serverEngine(list: EngineStatus[]): EngineStatus | undefined {
  return list.find((engine) => engine.id === 'server' && engine.available);
}

/** "0:07", "12:34", "1:02:03": the call timer. */
export function formatCallTime(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = String(seconds % 60).padStart(2, '0');
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${rest}` : `${minutes}:${rest}`;
}

/** The speech language for a call: the interface language's main voice unless one was chosen. */
export function callLocaleFor(uiLocale: string, saved?: string | null): string {
  const supported = DEVICE_ONLY.locales;
  if (saved && supported.includes(saved)) return saved;
  return uiLocale.toLowerCase().startsWith('ar') ? 'ar-SA' : 'en-US';
}

export function isArabicLocale(locale: string): boolean {
  return locale.toLowerCase().startsWith('ar');
}

export interface VadOptions {
  /** Silence that ends a turn. */
  endOfTurnMs: number;
  /** Sustained sound that starts speech while nothing is playing. */
  attackMs: number;
  /** Sustained sound that counts as talking over a reply (barge-in). */
  bargeInMs: number;
  /** The quietest level (0..1) that can count as speech. */
  minThreshold: number;
  /** Speech must be this many times louder than the room's noise floor. */
  noiseRatio: number;
  /** While a reply plays, the threshold rises by this factor so its echo is not "speech". */
  echoFactor: number;
}

export const DEFAULT_VAD: VadOptions = { endOfTurnMs: 700, attackMs: 120, bargeInMs: 250, minThreshold: 0.06, noiseRatio: 2.5, echoFactor: 1.8 };

export type VadEvent = 'start' | 'end' | null;

/**
 * Energy-based voice activity detection over microphone levels (0..1), sampled every few
 * tens of milliseconds. It tracks the room's noise floor while nobody speaks, needs a
 * sustained rise above it to report `start` (longer, and louder, while a reply is playing,
 * so the reply's own echo does not interrupt it) and a sustained quiet to report `end`.
 */
export function createVad(options: Partial<VadOptions> = {}) {
  const config: VadOptions = { ...DEFAULT_VAD };
  for (const [key, value] of Object.entries(options) as [keyof VadOptions, number | undefined][]) {
    if (typeof value === 'number' && Number.isFinite(value)) config[key] = value;
  }
  let noise = 0.02;
  let speaking = false;
  let aboveSince: number | null = null;
  let belowSince: number | null = null;

  const threshold = (playing: boolean) => Math.max(config.minThreshold, noise * config.noiseRatio) * (playing ? config.echoFactor : 1);
  // The noise floor drops at once to a quieter level and rises slowly: quickly between
  // turns, very slowly during speech (its pauses pull it back down), never from the
  // reply's echo. A steady fan or street noise therefore stops counting as speech.
  const learn = (level: number, rate: number) => {
    const value = Math.min(level, 0.5);
    noise = value < noise ? (noise + value) / 2 : noise + (value - noise) * rate;
  };

  return {
    get speaking() { return speaking; },
    threshold,
    push(level: number, now: number, playing = false): VadEvent {
      const limit = threshold(playing);
      if (!speaking) {
        if (level > limit) {
          aboveSince ??= now;
          if (now - aboveSince >= (playing ? config.bargeInMs : config.attackMs)) {
            speaking = true;
            aboveSince = null;
            belowSince = null;
            return 'start';
          }
        } else {
          aboveSince = null;
          if (!playing) learn(level, 0.05);
        }
        return null;
      }
      if (!playing) learn(level, 0.005);
      if (level < limit * 0.7) {
        belowSince ??= now;
        if (now - belowSince >= config.endOfTurnMs) {
          speaking = false;
          belowSince = null;
          return 'end';
        }
      } else {
        belowSince = null;
      }
      return null;
    },
    reset() { speaking = false; aboveSince = null; belowSince = null; },
  };
}

export type Vad = ReturnType<typeof createVad>;

/** Scale a camera frame so its longer side is at most `maxSide` (keeps the upload small). */
export function frameSize(width: number, height: number, maxSide = 1024): { width: number; height: number } {
  if (width <= 0 || height <= 0) return { width: 0, height: 0 };
  const scale = Math.min(1, maxSide / Math.max(width, height));
  return { width: Math.round(width * scale), height: Math.round(height * scale) };
}

/** Bytes of a base64 payload without decoding it. */
export function base64Bytes(base64: string): number {
  const padding = base64.endsWith('==') ? 2 : base64.endsWith('=') ? 1 : 0;
  return Math.floor((base64.length * 3) / 4) - padding;
}

export type LatencyVerdict = 'fast' | 'ok' | 'slow';

/** How a measured first-audio latency compares with the target. */
export function latencyVerdict(ms: number, targetMs: number): LatencyVerdict {
  if (ms <= targetMs * 0.75) return 'fast';
  return ms <= targetMs ? 'ok' : 'slow';
}

/** A short price note for an engine, or null when it costs nothing extra. */
export function engineCost(engine: EngineStatus): { perMinute?: number; perMillionChars?: number } | null {
  if (engine.usd_per_minute) return { perMinute: engine.usd_per_minute };
  if (engine.usd_per_million_chars) return { perMillionChars: engine.usd_per_million_chars };
  return null;
}
