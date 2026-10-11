// Unit tests for voice call mode: src/lib/call.ts (timer, voice activity detection, sizing)
// and src/lib/callEngine.ts (the call loop with barge-in), driven by fakes.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { base64Bytes, callLocaleFor, createVad, engineCost, formatCallTime, frameSize, latencyVerdict, serverEngine, DEVICE_ONLY } from '../src/lib/call.ts';
import { CallEngine, classifyError, soundsLikeEcho, type CallApi, type CallDeps, type Frame, type MicHandle, type StreamHandlers } from '../src/lib/callEngine.ts';
import { ApiError } from '../src/lib/errors.ts';

describe('call helpers', () => {
  test('formats the call timer', () => {
    assert.equal(formatCallTime(0), '0:00');
    assert.equal(formatCallTime(7.9), '0:07');
    assert.equal(formatCallTime(754), '12:34');
    assert.equal(formatCallTime(3723), '1:02:03');
    assert.equal(formatCallTime(-5), '0:00');
  });

  test('picks the call language from the interface unless one was chosen', () => {
    assert.equal(callLocaleFor('ar'), 'ar-SA');
    assert.equal(callLocaleFor('en'), 'en-US');
    assert.equal(callLocaleFor('en', 'ar-AE'), 'ar-AE');
    assert.equal(callLocaleFor('ar', 'xx-YY'), 'ar-SA');
  });

  test('sizes frames and measures base64 payloads', () => {
    assert.deepEqual(frameSize(1920, 1080), { width: 1024, height: 576 });
    assert.deepEqual(frameSize(640, 480), { width: 640, height: 480 });
    assert.deepEqual(frameSize(0, 480), { width: 0, height: 0 });
    assert.equal(base64Bytes('aGVsbG8='), 5);
    assert.equal(base64Bytes('aGk='), 2);
  });

  test('judges latency against the target and reads engine prices', () => {
    assert.equal(latencyVerdict(900, 1500), 'fast');
    assert.equal(latencyVerdict(1400, 1500), 'ok');
    assert.equal(latencyVerdict(2100, 1500), 'slow');
    assert.equal(serverEngine(DEVICE_ONLY.stt), undefined);
    assert.equal(engineCost(DEVICE_ONLY.stt[0]), null);
    assert.deepEqual(engineCost({ ...DEVICE_ONLY.stt[0], id: 'server', usd_per_minute: 0.006 }), { perMinute: 0.006 });
  });
});

describe('voice activity detection', () => {
  const feed = (vad: ReturnType<typeof createVad>, level: number, from: number, to: number, playing = false) => {
    const events: string[] = [];
    for (let now = from; now <= to; now += 50) {
      const event = vad.push(level, now, playing);
      if (event) events.push(`${event}@${now}`);
    }
    return events;
  };

  test('starts on sustained speech and ends after the end-of-turn silence', () => {
    const vad = createVad({ endOfTurnMs: 700 });
    assert.deepEqual(feed(vad, 0.01, 0, 1000), []);
    assert.deepEqual(feed(vad, 0.4, 1050, 2000), ['start@1200']);
    assert.equal(vad.speaking, true);
    // A short pause inside a sentence does not end the turn.
    assert.deepEqual(feed(vad, 0.01, 2050, 2400), []);
    assert.deepEqual(feed(vad, 0.4, 2450, 2600), []);
    assert.deepEqual(feed(vad, 0.01, 2650, 3500), ['end@3350']);
  });

  test('a click is not speech, and the reply echo needs to be louder and longer to interrupt', () => {
    const vad = createVad();
    assert.deepEqual(feed(vad, 0.5, 0, 50), []);
    assert.deepEqual(feed(vad, 0.01, 100, 500), []);
    // While a reply plays, a level that would start speech is treated as its echo.
    assert.deepEqual(feed(vad, 0.1, 550, 1500, true), []);
    assert.deepEqual(feed(vad, 0.4, 1550, 2000, true), ['start@1800']);
  });

  test('adapts to a noisy room', () => {
    const vad = createVad();
    feed(vad, 0.08, 0, 12000);
    assert.equal(vad.speaking, false);
    assert.ok(vad.threshold(false) > 0.15);
  });
});

describe('echo and errors', () => {
  test('tells the reply echo from the caller talking over it', () => {
    assert.equal(soundsLikeEcho('the weekly report is ready', 'The weekly report is ready for review.'), true);
    assert.equal(soundsLikeEcho('stop wait a second please', 'The weekly report is ready for review.'), false);
  });

  test('classifies failures into what the call screen explains', () => {
    assert.deepEqual(classifyError(new ApiError(402, 'model_budget_exceeded', 'Budget used.'), true), { kind: 'budget', detail: 'Budget used.' });
    assert.equal(classifyError(new ApiError(429, 'rate_limited', null), true).kind, 'limit');
    assert.equal(classifyError(new ApiError(409, 'conflict', 'Voice session is closed'), true).kind, 'session');
    assert.equal(classifyError(new ApiError(503, 'service_unavailable', 'down'), true).kind, 'provider');
    assert.equal(classifyError(new TypeError('Failed to fetch'), false).kind, 'offline');
    assert.equal(classifyError(new Error('I could not answer.'), true).kind, 'provider');
  });
});

// ---- the call loop, with fakes ----

interface Harness {
  engine: CallEngine;
  api: CallApi & { asked: string[]; looked: Frame[]; replies: string[]; aborted: number; completed: number; summarized: { save: boolean; seconds: number }[] };
  speak: string[];
  stops: { count: number };
  level: { value: number };
  advance(ms: number): Promise<void>;
  say(text: string, final?: boolean): void;
  marks: { count: number };
  micEnabled: { value: boolean };
}

function harness(options: { reply?: string; recognizer?: boolean; micError?: string; online?: boolean; speakMs?: number; stt?: 'device' | 'server'; failAsk?: Error } = {}): Harness {
  let now = 0;
  const ticks = new Set<() => void>();
  const timers: { at: number; fn: () => void }[] = [];
  const speak: string[] = [];
  const stops = { count: 0 };
  const level = { value: 0.01 };
  const marks = { count: 0 };
  const micEnabled = { value: true };
  let onText: ((text: string, isFinal: boolean) => void) | null = null;
  let stopSpeaking: (() => void) | null = null;
  const reply = options.reply ?? 'Here is the first sentence of it. And here is the second sentence too.';

  const api = {
    asked: [] as string[], looked: [] as Frame[], replies: [] as string[], aborted: 0, completed: 0, summarized: [] as { save: boolean; seconds: number }[],
    async createSession() { return { id: 'voice_test' }; },
    async appendTranscript(_session: string, text: string) { api.asked.push(text); return { id: `seg_${api.asked.length}` }; },
    async ask(_session: string, _segment: string, signal: AbortSignal, handlers: StreamHandlers) {
      if (options.failAsk) throw options.failAsk;
      handlers.onMeta({ intent: 'question', risk_tier: 'read', proposed_task: null, workspace: { tasks_total: 1, running: 0, waiting_approval: 0, pending_approvals: 2 } });
      for (const word of reply.split(/(?<= )/)) {
        if (signal.aborted) { api.aborted += 1; return; }
        handlers.onText(word);
        await Promise.resolve();
      }
      api.replies.push(reply);
    },
    async look(_session: string, _segment: string, frame: Frame, signal: AbortSignal, handlers: StreamHandlers) {
      api.looked.push(frame);
      handlers.onMeta({ intent: 'question', risk_tier: 'read', proposed_task: null, workspace: { tasks_total: 0, running: 0, waiting_approval: 0, pending_approvals: 0 }, vision: true });
      if (!signal.aborted) handlers.onText('A mug on a desk.');
    },
    async transcribe() { return { id: 'seg_server', text: 'what is on my desk' }; },
    async createTask() { return { title: 'call the supplier' }; },
    async summarize(_session: string, seconds: number, save: boolean) { api.summarized.push({ save, seconds }); return { summary: 'You asked one question.', source: 'transcript', conversation_id: 'conv_1', turns_saved: 3, duration_seconds: seconds }; },
    async complete() { api.completed += 1; },
  };

  const mic: MicHandle = {
    level: () => level.value,
    setEnabled: (on) => { micEnabled.value = on; },
    close: () => undefined,
    recorder: () => options.stt === 'server' ? { start: () => undefined, stop: async () => ({ blob: new Blob(['speech']), durationMs: 1500 }) } : null,
  };

  const deps: CallDeps = {
    api,
    openMic: async () => {
      if (options.micError) throw Object.assign(new Error('blocked'), { name: options.micError });
      return mic;
    },
    createRecognizer: (_locale, text) => {
      if (options.recognizer === false) return null;
      onText = text;
      return { start: () => undefined, stop: () => undefined, mark: () => { marks.count += 1; } };
    },
    speaker: {
      speak: (text, onStart) => new Promise<void>((resolve) => {
        speak.push(text);
        onStart();
        const done = () => { stopSpeaking = null; resolve(); };
        stopSpeaking = done;
        timers.push({ at: now + (options.speakMs ?? 1000), fn: done });
      }),
      stop: () => { stops.count += 1; stopSpeaking?.(); },
    },
    now: () => now,
    every: (_ms, fn) => { ticks.add(fn); return () => ticks.delete(fn); },
    after: (ms, fn) => { const timer = { at: now + ms, fn }; timers.push(timer); return () => { const index = timers.indexOf(timer); if (index >= 0) timers.splice(index, 1); }; },
    isOnline: () => options.online ?? true,
  };
  const engine = new CallEngine(deps, { locale: 'en-US', name: 'Anum', retention: 'session', stt: options.stt ?? 'device', speakerOn: true, lookPrompt: 'What do you see?' });

  const flush = async () => { for (let index = 0; index < 20; index += 1) await Promise.resolve(); };
  return {
    engine, api, speak, stops, level, marks, micEnabled,
    async advance(ms: number) {
      const end = now + ms;
      while (now < end) {
        now += 50;
        for (const tick of [...ticks]) tick();
        for (const timer of timers.filter((item) => item.at <= now)) { timers.splice(timers.indexOf(timer), 1); timer.fn(); }
        await flush();
      }
    },
    say(text: string, final = true) { onText?.(text, final); },
  };
}

describe('call loop', () => {
  test('a spoken turn ends on silence, streams the reply and speaks it sentence by sentence', async () => {
    const h = harness();
    await h.engine.start();
    assert.equal(h.engine.snapshot.phase, 'listening');
    assert.equal(h.engine.snapshot.startedAt, 0);
    h.level.value = 0.5;
    await h.advance(300);
    assert.equal(h.engine.snapshot.phase, 'hearing');
    h.say('what is running', false);
    assert.equal(h.engine.snapshot.caption, 'what is running');
    h.level.value = 0.01;
    await h.advance(800);
    assert.deepEqual(h.api.asked, ['what is running']);
    assert.equal(h.engine.snapshot.phase, 'speaking');
    assert.deepEqual(h.speak, ['Here is the first sentence of it.']);
    assert.ok((h.engine.snapshot.lastLatencyMs ?? 99999) <= 100);
    await h.advance(2200);
    assert.deepEqual(h.speak, ['Here is the first sentence of it.', 'And here is the second sentence too.']);
    assert.equal(h.engine.snapshot.phase, 'listening');
    assert.equal(h.engine.snapshot.pendingApprovals, 2);
    assert.deepEqual(h.engine.snapshot.turns.map((turn) => turn.speaker), ['you', 'assistant']);
  });

  test('talking over the reply stops it at once (barge-in) and the new words are the next turn', async () => {
    const h = harness({ speakMs: 5000 });
    await h.engine.start();
    void h.engine.submit('tell me about the report');
    await h.advance(100);
    assert.equal(h.engine.snapshot.phase, 'speaking');
    const stopsBefore = h.stops.count;
    h.level.value = 0.6;
    await h.advance(300);
    assert.equal(h.stops.count, stopsBefore + 1);
    assert.equal(h.engine.snapshot.bargeIns, 1);
    assert.equal(h.engine.snapshot.phase, 'hearing');
    h.say('actually what about tasks');
    h.level.value = 0.01;
    await h.advance(800);
    assert.deepEqual(h.api.asked, ['tell me about the report', 'actually what about tasks']);
  });

  test('the recognizer hearing new words over the reply also interrupts, its echo does not', async () => {
    const h = harness({ speakMs: 5000 });
    await h.engine.start();
    void h.engine.submit('hello');
    await h.advance(100);
    h.say('here is the first sentence', false);
    assert.equal(h.engine.snapshot.phase, 'speaking');
    h.say('no stop that please', false);
    assert.equal(h.engine.snapshot.phase, 'listening');
    assert.equal(h.engine.snapshot.bargeIns, 1);
  });

  test('mute stops listening; the speaker switch silences replies but keeps the text', async () => {
    const h = harness();
    await h.engine.start();
    h.engine.setMuted(true);
    assert.equal(h.micEnabled.value, false);
    h.level.value = 0.6;
    await h.advance(400);
    assert.equal(h.engine.snapshot.phase, 'listening');
    h.engine.setMuted(false);
    assert.equal(h.micEnabled.value, true);
    h.level.value = 0.01;
    h.engine.setSpeaker(false);
    void h.engine.submit('status please');
    await h.advance(100);
    assert.deepEqual(h.speak, []);
    assert.match(h.engine.snapshot.turns[1].text, /second sentence/);
  });

  test('a frame goes with the question only when the camera is on and it was asked for', async () => {
    const h = harness();
    await h.engine.start();
    h.engine.sendFrame();
    assert.equal(h.api.looked.length, 0, 'no camera, no frame');
    h.engine.setCamera(async () => ({ base64: '/9j/frame', mediaType: 'image/jpeg' }));
    h.engine.sendFrame();
    await h.advance(100);
    assert.deepEqual(h.api.asked, ['What do you see?']);
    assert.equal(h.api.looked.length, 1);
    assert.equal(h.engine.snapshot.turns[0].withFrame, true);
    await h.advance(1200);
    void h.engine.submit('and now?');
    await h.advance(3000);
    assert.equal(h.api.looked.length, 1, 'later questions go without a frame');
    h.engine.setAutoFrames(true);
    void h.engine.submit('what about this?');
    await h.advance(1200);
    assert.equal(h.api.looked.length, 2);
    h.engine.setCamera(null);
    assert.equal(h.engine.snapshot.autoFrames, false);
  });

  test('server recognition records the turn and sends the transcribed segment', async () => {
    const h = harness({ stt: 'server' });
    await h.engine.start();
    assert.equal(h.engine.snapshot.stt, 'server');
    h.level.value = 0.5;
    await h.advance(300);
    h.level.value = 0.01;
    await h.advance(900);
    assert.deepEqual(h.api.asked, [], 'the server stored the segment itself');
    assert.equal(h.engine.snapshot.turns[0].text, 'what is on my desk');
  });

  test('every state is reported: blocked or missing microphone, offline, typing only, model down', async () => {
    const blocked = harness({ micError: 'NotAllowedError' });
    await blocked.engine.start();
    assert.deepEqual(blocked.engine.snapshot.problem, { kind: 'mic-denied' });
    assert.equal(blocked.engine.snapshot.phase, 'idle');
    const missing = harness({ micError: 'NotFoundError' });
    await missing.engine.start();
    assert.equal(missing.engine.snapshot.problem?.kind, 'mic-missing');
    const offline = harness({ online: false });
    await offline.engine.start();
    assert.equal(offline.engine.snapshot.problem?.kind, 'offline');
    const typing = harness({ recognizer: false });
    await typing.engine.start();
    assert.equal(typing.engine.snapshot.stt, 'typing');
    assert.equal(typing.engine.snapshot.problem?.kind, 'typing-only');
    const down = harness({ failAsk: new ApiError(503, 'service_unavailable', 'The model did not answer.') });
    await down.engine.start();
    await down.engine.submit('hello');
    assert.deepEqual(down.engine.snapshot.problem, { kind: 'provider', detail: 'The model did not answer.' });
    assert.equal(down.engine.snapshot.phase, 'listening');
    assert.deepEqual(down.engine.snapshot.turns.map((turn) => turn.speaker), ['you']);
  });

  test('silence after speech that recognized nothing says so instead of sending', async () => {
    const h = harness();
    await h.engine.start();
    h.level.value = 0.5;
    await h.advance(300);
    h.level.value = 0.01;
    await h.advance(2200);
    assert.equal(h.engine.snapshot.problem?.kind, 'heard-nothing');
    assert.deepEqual(h.api.asked, []);
  });

  test('hanging up stops everything, then saves the summary before closing the session', async () => {
    const h = harness();
    await h.engine.start();
    void h.engine.submit('hello');
    await h.advance(65_000);
    h.engine.hangUp();
    assert.equal(h.engine.snapshot.phase, 'ending');
    assert.equal(h.engine.durationSeconds, 65);
    assert.equal(await h.engine.finish(true), true);
    assert.deepEqual(h.api.summarized, [{ save: true, seconds: 65 }]);
    assert.equal(h.engine.snapshot.summary?.conversation_id, 'conv_1');
    assert.equal(h.api.completed, 1);
    assert.equal(h.engine.snapshot.phase, 'ended');
  });

  test('ending without saving closes the session and keeps nothing', async () => {
    const h = harness();
    await h.engine.start();
    void h.engine.submit('hello');
    await h.advance(100);
    h.engine.hangUp();
    await h.engine.finish(false);
    assert.deepEqual(h.api.summarized, []);
    assert.equal(h.api.completed, 1);
  });

  test('a proposed task is created only on confirmation', async () => {
    const h = harness();
    await h.engine.start();
    void h.engine.submit('hello');
    await h.advance(3000);
    const turn = h.engine.snapshot.turns[1];
    (turn.meta as { proposed_task: string | null }).proposed_task = 'call the supplier';
    assert.equal(await h.engine.confirmTask(turn.id), 'call the supplier');
    assert.equal(h.engine.snapshot.turns[1].resolved, 'created');
  });
});
