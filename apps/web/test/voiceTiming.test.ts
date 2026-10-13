// Turn timings and the natural voice's fast fallback (src/lib/voiceTiming.ts, docs/voice.md#latency).
// Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  NATURAL_WAIT_MS,
  clientTiming,
  createNaturalSpeaker,
  formatServerTiming,
  formatTurnTiming,
  seconds,
  switchSpeechLanguage,
} from '../src/lib/voiceTiming.ts';

const labels = { heard: 'heard', answer: 'answer', voice: 'voice' };

describe('turn timing', () => {
  test('durations come from the marks, in order', () => {
    const timing = clientTiming({ lastWords: 1000, sent: 1300, firstText: 2500, firstSound: 3300 });
    assert.deepEqual(timing, { heard: 300, answer: 1200, voice: 800 });
    assert.equal(formatTurnTiming(timing, labels), 'heard 0.3 s, answer 1.2 s, voice 0.8 s');
  });

  test('a typed question has no heard part and a muted reply no voice part', () => {
    const timing = clientTiming({ sent: 10, firstText: 610 });
    assert.deepEqual(timing, { heard: null, answer: 600, voice: null });
    assert.equal(formatTurnTiming(timing, labels), 'answer 0.6 s');
    assert.equal(formatTurnTiming(clientTiming({}), labels), '');
  });

  test('marks out of order are not shown as negative times', () => {
    assert.equal(clientTiming({ sent: 500, firstText: 400 }).answer, null);
    assert.equal(seconds(-20), '0.0');
  });

  test('Arabic labels, unit and separator', () => {
    const text = formatTurnTiming({ heard: 300, answer: 1200, voice: null }, { heard: 'الاستماع', answer: 'الإجابة', voice: 'الصوت' }, 'ث', '، ');
    assert.equal(text, 'الاستماع 0.3 ث، الإجابة 1.2 ث');
  });

  test('the server side names the model, the lookups and the total', () => {
    const server = formatServerTiming(
      { first_token_ms: 900, tool_ms: 210, total_ms: 1500, tool_rounds: 1, tool_calls: 2, grounding: 'tools', sources: ['workspace_tasks', 'tyre_alerts'] },
      { model: 'model', lookups: 'lookups', total: 'total' },
    );
    assert.equal(server, 'model 0.9 s, lookups 0.2 s (workspace_tasks, tyre_alerts), total 1.5 s');
    assert.equal(formatServerTiming({ first_token_ms: null, tool_ms: 0, total_ms: 40, tool_rounds: 0, tool_calls: 0, grounding: 'none', sources: [] }, { model: 'm', lookups: 'l', total: 't' }), 't 0.0 s');
    assert.equal(formatServerTiming(null, { model: 'm', lookups: 'l', total: 't' }), '');
  });

  test('the EN / AR switch keeps a chosen region', () => {
    assert.equal(switchSpeechLanguage('en-GB', 'en'), 'en-GB');
    assert.equal(switchSpeechLanguage('en-GB', 'ar'), 'ar-SA');
    assert.equal(switchSpeechLanguage('ar-AE', 'ar'), 'ar-AE');
    assert.equal(switchSpeechLanguage('ar-AE', 'en'), 'en-US');
  });
});

describe('natural voice with a deadline', () => {
  const never = () => new Promise<never>(() => undefined);
  const instant = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms === NATURAL_WAIT_MS ? 5 : ms));

  test('the automatic voice speaks a sentence with the system voice when the engine is not ready in time', async () => {
    const speaker = createNaturalSpeaker(never, async () => 'audio', instant);
    assert.deepEqual(await speaker.speak('Hello there.', NATURAL_WAIT_MS), { kind: 'fallback', reason: 'slow' });
    assert.equal(speaker.slow, false, 'still loading: the next sentence may make it');
  });

  test('a warm engine speaks the sentence itself', async () => {
    let loads = 0;
    const speaker = createNaturalSpeaker(async () => { loads += 1; return 'engine'; }, async (engine, text) => `${engine}:${text}`, instant);
    await speaker.warm();
    assert.equal(speaker.ready, true);
    assert.deepEqual(await speaker.speak('Hi.', NATURAL_WAIT_MS), { kind: 'audio', audio: 'engine:Hi.' });
    assert.equal(loads, 1, 'pre-warmed once, not loaded again');
  });

  test('a loaded engine that is too slow for a sentence is skipped from then on', async () => {
    let generated = 0;
    const speaker = createNaturalSpeaker(async () => 'engine', async () => { generated += 1; return never(); }, instant);
    await speaker.warm();
    assert.equal((await speaker.speak('One.', NATURAL_WAIT_MS)).kind, 'fallback');
    assert.equal(speaker.slow, true);
    assert.deepEqual(await speaker.speak('Two.', NATURAL_WAIT_MS), { kind: 'fallback', reason: 'slow' });
    assert.equal(generated, 1, 'no second sentence handed to the slow engine');
  });

  test('chosen explicitly, the natural voice waits and falls back only on failure', async () => {
    let release: (value: string) => void = () => undefined;
    const speaker = createNaturalSpeaker(async () => 'engine', () => new Promise<string>((resolve) => { release = resolve; }), instant);
    const waiting = speaker.speak('Wait for me.', null);
    await new Promise((resolve) => setTimeout(resolve, 20));
    release('late audio');
    assert.deepEqual(await waiting, { kind: 'audio', audio: 'late audio' });
    const failing = createNaturalSpeaker(async () => { throw new Error('no network'); }, async () => 'x', instant);
    assert.deepEqual(await failing.speak('Hi.', null), { kind: 'fallback', reason: 'failed' });
    assert.deepEqual(await failing.speak('Hi.', NATURAL_WAIT_MS), { kind: 'fallback', reason: 'failed' });
    await failing.warm();
    assert.equal(failing.ready, false);
  });
});
