// Turn timings for the Voice page (docs/voice.md#latency) and the natural voice's fast fallback.
// Pure: no DOM, no i18n, so `test/voiceTiming.test.ts` runs it under node.
import type { VoiceTurnTiming } from '@anum/contracts';

export type { VoiceTurnTiming };

/** What the browser measured for one turn, in milliseconds (null when it did not happen). */
export interface ClientTurnTiming {
  /** The last recognized words to the question being sent (null for a typed question). */
  heard: number | null;
  /** The question sent to the first words of the reply arriving. */
  answer: number | null;
  /** The first words arriving to the reply starting to sound (null when muted or silent). */
  voice: number | null;
}

/** Marks taken during a turn; `clientTiming` turns them into durations. */
export interface TurnMarks {
  lastWords?: number;
  sent?: number;
  firstText?: number;
  firstSound?: number;
}

const span = (from: number | undefined, to: number | undefined): number | null =>
  from === undefined || to === undefined || to < from ? null : Math.round(to - from);

export function clientTiming(marks: TurnMarks): ClientTurnTiming {
  return {
    heard: span(marks.lastWords, marks.sent),
    answer: span(marks.sent, marks.firstText),
    voice: span(marks.firstText, marks.firstSound),
  };
}

/** Seconds with one decimal, never "-0.0". */
export function seconds(ms: number): string {
  return (Math.max(0, ms) / 1000).toFixed(1);
}

export interface TimingLabels {
  heard: string;
  answer: string;
  voice: string;
}

/** "heard 0.3 s, answer 1.2 s, voice 0.8 s": the parts that were measured, in that order. */
export function formatTurnTiming(timing: ClientTurnTiming, labels: TimingLabels, unit = 's', separator = ', '): string {
  return (['heard', 'answer', 'voice'] as const)
    .filter((key) => timing[key] !== null)
    .map((key) => `${labels[key]} ${seconds(timing[key] as number)} ${unit}`)
    .join(separator);
}

export interface ServerTimingLabels {
  model: string;
  lookups: string;
  total: string;
}

/** The server's side of the turn, for the tooltip: first model words, lookups and total. */
export function formatServerTiming(timing: VoiceTurnTiming | null | undefined, labels: ServerTimingLabels, unit = 's'): string {
  if (!timing) return '';
  const parts: string[] = [];
  if (timing.first_token_ms !== null) parts.push(`${labels.model} ${seconds(timing.first_token_ms)} ${unit}`);
  if (timing.tool_calls > 0 || timing.tool_ms > 0) parts.push(`${labels.lookups} ${seconds(timing.tool_ms)} ${unit} (${timing.sources.join(', ') || timing.tool_calls})`);
  parts.push(`${labels.total} ${seconds(timing.total_ms)} ${unit}`);
  return parts.join(', ');
}

/** The default reply voice: natural when it is ready in time, the system voice otherwise. */
export const NATURAL_WAIT_MS = 1500;

export type NaturalOutcome<T> = { kind: 'audio'; audio: T } | { kind: 'fallback'; reason: 'slow' | 'failed' };

/**
 * The natural (Kokoro) voice with a deadline. `load` returns the engine (downloaded once),
 * `generate` one sentence's audio. With a deadline, a sentence that is not ready in time is
 * spoken by the system voice instead, and after one late sentence from a loaded engine (a slow
 * machine) the engine is skipped for the rest of the page's life so it does not keep the CPU busy.
 * Without a deadline (the person chose the natural voice), it waits, and falls back only on failure.
 */
export function createNaturalSpeaker<E, T>(
  load: () => Promise<E>,
  generate: (engine: E, text: string) => Promise<T>,
  wait: (ms: number) => Promise<void> = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
) {
  let engine: E | null = null;
  let slow = false;
  const warm = (): Promise<void> => load().then((loaded) => { engine = loaded; }).catch(() => undefined);
  return {
    /** Start loading in the background (the Voice page opens). */
    warm,
    get ready(): boolean { return engine !== null; },
    get slow(): boolean { return slow; },
    async speak(text: string, deadlineMs: number | null): Promise<NaturalOutcome<T>> {
      if (deadlineMs !== null && slow) return { kind: 'fallback', reason: 'slow' };
      const loadedBefore = engine !== null;
      const work = (async () => {
        const loaded = engine ?? await load();
        engine = loaded;
        return generate(loaded, text);
      })();
      if (deadlineMs === null) {
        try { return { kind: 'audio', audio: await work }; } catch { return { kind: 'fallback', reason: 'failed' }; }
      }
      const late = Symbol('late');
      const result = await Promise.race([work.catch(() => null), wait(deadlineMs).then(() => late)]);
      if (result === late) {
        // Still loading: the next sentence may make it. Loaded but too slow to speak: skip it from now on.
        if (loadedBefore) slow = true;
        void work.catch(() => undefined);
        return { kind: 'fallback', reason: 'slow' };
      }
      return result === null ? { kind: 'fallback', reason: 'failed' } : { kind: 'audio', audio: result as T };
    },
  };
}

/** The speech language for the quick EN / AR switch, keeping a chosen region when it matches. */
export function switchSpeechLanguage(current: string, language: 'en' | 'ar'): string {
  if (current.toLowerCase().startsWith(language)) return current;
  return language === 'ar' ? 'ar-SA' : 'en-US';
}
