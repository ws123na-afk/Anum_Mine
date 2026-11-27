// Unit tests for retrying a read that failed for a passing reason (src/lib/retry.ts).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { isTransient, retryTransient } from '../src/lib/retry.ts';

const failure = (status: number) => Object.assign(new Error(`HTTP ${status}`), { status });
const noWait = async () => undefined;

describe('retryTransient', () => {
  test('a rate limit or gateway hiccup is tried again, a permanent answer is not', () => {
    for (const status of [429, 502, 503, 504]) assert.equal(isTransient(failure(status)), true);
    for (const status of [400, 401, 403, 404, 409, 422, 500]) assert.equal(isTransient(failure(status)), false);
    assert.equal(isTransient(new TypeError('Failed to fetch')), true);
    assert.equal(isTransient(null), false);
  });

  test('returns the first success after passing failures, waiting the given delays', async () => {
    const waits: number[] = [];
    let calls = 0;
    const value = await retryTransient(async () => { calls += 1; if (calls < 3) throw failure(429); return 'owner'; }, [10, 20], async (ms) => { waits.push(ms); });
    assert.equal(value, 'owner');
    assert.equal(calls, 3);
    assert.deepEqual(waits, [10, 20]);
  });

  test('gives up after the last delay with the last error', async () => {
    let calls = 0;
    await assert.rejects(retryTransient(async () => { calls += 1; throw failure(503); }, [1, 1], noWait), /HTTP 503/);
    assert.equal(calls, 3);
  });

  test('a 404 or 403 fails at once', async () => {
    let calls = 0;
    await assert.rejects(retryTransient(async () => { calls += 1; throw failure(404); }, [1, 1], noWait), /HTTP 404/);
    assert.equal(calls, 1);
  });
});
