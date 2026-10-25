// The progress line's thresholds (src/lib/voiceProgress.ts, docs/voice.md#progress).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { ELAPSED_AFTER_MS, HARD_TIMEOUT_MS, SLOW_AFTER_MS, asProgress, thinkingView } from '../src/lib/voiceProgress.ts';

describe('thinkingView', () => {
  test('shows the plain message first, the seconds after 2 s, slow after 8 s, timed out after 30 s', () => {
    assert.deepEqual(thinkingView(0, null), { key: 'voice.state.thinking', seconds: null, slow: false, timedOut: false });
    assert.equal(thinkingView(ELAPSED_AFTER_MS - 1, null).seconds, null);
    assert.equal(thinkingView(ELAPSED_AFTER_MS, null).seconds, 2);
    assert.equal(thinkingView(SLOW_AFTER_MS - 1, null).slow, false);
    assert.deepEqual([thinkingView(SLOW_AFTER_MS, null).slow, thinkingView(SLOW_AFTER_MS, null).seconds], [true, 8]);
    assert.equal(thinkingView(HARD_TIMEOUT_MS - 1, null).timedOut, false);
    assert.equal(thinkingView(HARD_TIMEOUT_MS, null).timedOut, true);
  });

  test('names what is being checked', () => {
    assert.equal(thinkingView(0, { phase: 'checking', topic: 'tyre' }).key, 'voice.progress.tyre');
    assert.equal(thinkingView(0, { phase: 'checking', topic: 'tasks' }).key, 'voice.progress.tasks');
    assert.equal(thinkingView(0, { phase: 'checking', topic: 'knowledge' }).key, 'voice.progress.data');
    assert.equal(thinkingView(0, { phase: 'thinking', topic: '' }).key, 'voice.state.thinking');
  });

  test('ignores a malformed progress event', () => {
    assert.equal(asProgress({ phase: 'later' }), null);
    assert.equal(asProgress(null), null);
    assert.deepEqual(asProgress({ phase: 'checking', topic: 'tyre' }), { phase: 'checking', topic: 'tyre' });
  });
});
