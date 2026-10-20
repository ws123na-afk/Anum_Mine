import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { watchAgentStop } from '../src/lib/agentStopWatch.ts';

// The banner follows the organization's switch (docs/agent-controls.md): another owner can turn the
// stop on or off, so it is read again on a poll and when the window regains focus.
const fakeTimers = () => {
  const runs: (() => void)[] = [];
  return { runs, setTimer: (run: () => void) => { runs.push(run); return runs.length; }, clearTimer: () => { runs.length = 0; } };
};
const target = () => {
  const listeners = new Map<string, () => void>();
  return {
    listeners,
    addEventListener: (type: string, fn: () => void) => { listeners.set(type, fn); },
    removeEventListener: (type: string) => { listeners.delete(type); },
  };
};
const settle = () => new Promise((resolve) => setImmediate(resolve));

describe('the stop banner follows the organization switch', () => {
  test('another owner engaging or releasing the stop is picked up on the poll and on focus', async () => {
    const state = { engaged: false };
    const seen: boolean[] = [];
    const timers = fakeTimers();
    const win = target();
    const stop = watchAgentStop(async () => state.engaged, (engaged) => seen.push(engaged), {
      setTimer: timers.setTimer, clearTimer: timers.clearTimer, windowTarget: win, documentTarget: null,
    });
    await settle();
    assert.deepEqual(seen, [false]); // the first read on mount
    state.engaged = true; // another owner turns it on
    timers.runs[0]();
    await settle();
    timers.runs[0]();
    await settle();
    assert.deepEqual(seen, [false, true]); // reported once, not on every poll
    state.engaged = false; // and off again: the window regains focus
    win.listeners.get('focus')?.();
    await settle();
    assert.deepEqual(seen, [false, true, false]);
    stop();
    assert.equal(win.listeners.size, 0);
    assert.equal(timers.runs.length, 0);
  });

  test('a hidden page does not poll, a failed read keeps the last state and a stopped watch is quiet', async () => {
    const seen: boolean[] = [];
    const timers = fakeTimers();
    const doc = { ...target(), hidden: true };
    let fail = false;
    const stop = watchAgentStop(async () => { if (fail) throw new Error('offline'); return true; }, (engaged) => seen.push(engaged), {
      setTimer: timers.setTimer, clearTimer: timers.clearTimer, windowTarget: null, documentTarget: doc,
    });
    await settle();
    assert.deepEqual(seen, []); // hidden: nothing read
    doc.hidden = false;
    doc.listeners.get('visibilitychange')?.();
    await settle();
    assert.deepEqual(seen, [true]);
    fail = true;
    timers.runs[0]();
    await settle();
    assert.deepEqual(seen, [true]); // the banner is not cleared by a network error
    stop();
    fail = false;
    doc.listeners.get('visibilitychange')?.();
    await settle();
    assert.deepEqual(seen, [true]);
  });
});
