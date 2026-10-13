// Starting and restarting speech recognition reliably (docs/voice.md#listening-quickly-and-reliably).
// DOM-free, so `pnpm test:unit` covers it with fake recognizers.
//
// A page gets one recognition session at a time: starting a new recognizer while the previous one
// is still ending (the wake listener handing over to tap-to-talk, a follow-up turn) is refused or
// silently dropped, which left the Voice page "listening" to nothing. The gate starts the next
// recognizer only once the previous one has ended (or after a short limit), and retries a refused
// start, so a turn always begins.

export interface Startable { start(): void }

export interface RecognitionGate {
  /** Starts `recognizer` once the previous one has ended; false when the browser keeps refusing. */
  start(recognizer: Startable): Promise<boolean>;
  /** Call from the recognizer's `onend`. */
  ended(recognizer: Startable): void;
  /** Resolves when no recognizer is running (or after `maxWaitMs`). */
  whenIdle(maxWaitMs?: number): Promise<void>;
  readonly busy: boolean;
}

const isRefusal = (error: unknown) => error instanceof Error && (error.name === 'InvalidStateError' || /already started/i.test(error.message));

export function createRecognitionGate(
  wait: (ms: number) => Promise<void> = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  { maxWaitMs = 600, retryMs = 120, attempts = 3 } = {},
): RecognitionGate {
  let current: Startable | null = null;
  let release: () => void = () => undefined;
  let done: Promise<void> = Promise.resolve();
  const whenIdle = (limit = maxWaitMs) => (current ? Promise.race([done, wait(limit)]) : Promise.resolve());
  return {
    async start(recognizer) {
      if (current && current !== recognizer) await whenIdle();
      for (let attempt = 1; attempt <= attempts; attempt += 1) {
        current = recognizer;
        done = new Promise((resolve) => { release = resolve; });
        try {
          recognizer.start();
          return true;
        } catch (error) {
          current = null;
          release();
          if (!isRefusal(error) || attempt === attempts) return false;
          await wait(retryMs);
        }
      }
      return false;
    },
    ended(recognizer) {
      if (recognizer !== current) return;
      current = null;
      release();
    },
    whenIdle,
    get busy() { return current !== null; },
  };
}

/**
 * How long the wake listener waits before listening again after the browser ended its session.
 * Normal ends (silence, the browser's session limit) restart at once; sessions that end almost
 * immediately (no microphone, network trouble) back off so the page never spins.
 */
export function wakeRestartDelay(sessionMs: number, quickEnds: number): number {
  if (sessionMs >= 1000) return 0;
  return Math.min(2000, 150 * 2 ** Math.max(0, quickEnds));
}

export type RecognitionErrorKind = 'silent' | 'not-heard' | 'blocked' | 'no-mic' | 'network' | 'other';

/** What a SpeechRecognition error code means for the person talking. */
export function recognitionErrorKind(code: string | undefined): RecognitionErrorKind {
  switch (code) {
    case 'aborted': return 'silent';
    case 'no-speech': return 'not-heard';
    case 'not-allowed': case 'service-not-allowed': return 'blocked';
    case 'audio-capture': return 'no-mic';
    case 'network': return 'network';
    default: return 'other';
  }
}

const normalise = (text: string) => text.toLowerCase().normalize('NFKC').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();

/**
 * Whether what the wake listener heard is the assistant's own reply coming back through the
 * speakers (so saying the name over a reply interrupts it, but the reply itself never does).
 */
export function heardOwnReply(heard: string, spoken: string): boolean {
  const said = normalise(heard), reply = normalise(spoken);
  return said.length > 0 && reply.length > 0 && ` ${reply} `.includes(` ${said} `);
}
