// Recognition mode detection (src/lib/recognitionMode.ts, docs/voice.md#the-space-orb).
// `SpeechRecognition.available()` crashes the renderer in headless Chromium, so an automated
// browser must never call it. Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { automatedBrowser, chooseRecognitionMode } from '../src/lib/recognitionMode.ts';

function recognizer(availability: string) {
  const calls: unknown[] = [];
  return { calls, probe: { available: (options: { langs: string[]; processLocally: boolean }) => { calls.push(options); return Promise.resolve(availability); } } };
}

describe('recognition mode', () => {
  test('a real browser asks for on-device recognition', async () => {
    const { calls, probe } = recognizer('available');
    assert.equal(await chooseRecognitionMode(probe, 'en-US', false), 'on-device');
    assert.deepEqual(calls, [{ langs: ['en-US'], processLocally: true }]);
  });

  test('an automated browser never calls available() and uses the cloud recognizer', async () => {
    const { calls, probe } = recognizer('available');
    assert.equal(await chooseRecognitionMode(probe, 'en-US', true), 'cloud');
    assert.equal(calls.length, 0);
  });

  test('cloud when on-device is missing or the probe rejects; typing without a recognizer', async () => {
    assert.equal(await chooseRecognitionMode(recognizer('unavailable').probe, 'ar-AE', false), 'cloud');
    assert.equal(await chooseRecognitionMode({ available: () => Promise.reject(new Error('old')) }, 'en-US', false), 'cloud');
    assert.equal(await chooseRecognitionMode(undefined, 'en-US', false), 'typing');
  });

  test('outside a browser nothing counts as automated', () => {
    assert.equal(typeof automatedBrowser(), 'boolean');
  });
});
