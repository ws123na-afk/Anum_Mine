// Listening quickly and reliably, focus mode and leaving the tab (docs/voice.md): the recognition
// gate (src/lib/recognitionGate.ts) with fake recognizers, and the full-screen helpers
// (src/lib/focusMode.ts) with fake elements. Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { createRecognitionGate, heardOwnReply, recognitionErrorKind, wakeRestartDelay } from '../src/lib/recognitionGate.ts';
import { enterFocus, exitFocus, focusElement } from '../src/lib/focusMode.ts';

/** A page that allows one recognition session at a time, like the browser. */
function browser() {
  let active: object | null = null;
  const log: string[] = [];
  class Recognizer {
    readonly name: string;
    constructor(name: string) { this.name = name; }
    start() {
      if (active) { log.push(`refused ${this.name}`); throw Object.assign(new Error('recognition has already started'), { name: 'InvalidStateError' }); }
      active = this;
      log.push(`start ${this.name}`);
    }
    end() { if (active === this) active = null; log.push(`end ${this.name}`); }
  }
  return { Recognizer, log, get active() { return active; } };
}
const instant = () => Promise.resolve();

describe('recognition gate', () => {
  test('the next recognizer starts only after the previous one has ended (wake name hands over)', async () => {
    const page = browser();
    const gate = createRecognitionGate((ms) => new Promise((resolve) => setTimeout(resolve, Math.min(ms, 5))));
    const wake = new page.Recognizer('wake');
    assert.equal(await gate.start(wake), true);
    const talk = new page.Recognizer('talk');
    const started = gate.start(talk);
    // The wake listener ends a moment later, as the browser reports it.
    setTimeout(() => { wake.end(); gate.ended(wake); }, 1);
    assert.equal(await started, true);
    assert.deepEqual(page.log, ['start wake', 'end wake', 'start talk']);
    assert.equal(gate.busy, true);
  });

  test('a refused start is retried instead of leaving the page listening to nothing', async () => {
    const page = browser();
    const gate = createRecognitionGate(instant);
    const stray = new page.Recognizer('stray');
    stray.start(); // something the gate does not know about is still running
    const talk = new page.Recognizer('talk');
    let attempts = 0;
    const retrying = { start: () => { attempts += 1; if (attempts === 2) stray.end(); talk.start(); } };
    assert.equal(await gate.start(retrying), true);
    assert.equal(attempts, 2);
    assert.equal(page.active, talk);
  });

  test('a start that keeps failing reports false, and the gate is free again', async () => {
    const gate = createRecognitionGate(instant);
    const broken = { start: () => { throw Object.assign(new Error('busy'), { name: 'InvalidStateError' }); } };
    assert.equal(await gate.start(broken), false);
    assert.equal(gate.busy, false);
    const other = { start: () => { throw new Error('not allowed'); } };
    assert.equal(await gate.start(other), false);
  });

  test('waiting for an idle page never hangs on a recognizer that never reports its end', async () => {
    const gate = createRecognitionGate((ms) => new Promise((resolve) => setTimeout(resolve, Math.min(ms, 5))));
    await gate.start({ start: () => undefined });
    const began = Date.now();
    await gate.whenIdle(600);
    assert.ok(Date.now() - began < 200);
  });

  test('the wake listener listens again at once after a normal end and backs off when sessions keep failing', () => {
    assert.equal(wakeRestartDelay(8000, 0), 0);
    assert.equal(wakeRestartDelay(1000, 3), 0);
    assert.equal(wakeRestartDelay(50, 0), 150);
    assert.equal(wakeRestartDelay(50, 2), 600);
    assert.equal(wakeRestartDelay(50, 10), 2000);
  });

  test('recognition errors mean the right thing to the person talking', () => {
    assert.equal(recognitionErrorKind('aborted'), 'silent');
    assert.equal(recognitionErrorKind('no-speech'), 'not-heard');
    assert.equal(recognitionErrorKind('not-allowed'), 'blocked');
    assert.equal(recognitionErrorKind('service-not-allowed'), 'blocked');
    assert.equal(recognitionErrorKind('audio-capture'), 'no-mic');
    assert.equal(recognitionErrorKind('network'), 'network');
    assert.equal(recognitionErrorKind(undefined), 'other');
  });

  test('the reply coming back through the speakers is not the person saying the name', () => {
    assert.equal(heardOwnReply('Layla', "I'm Layla, your assistant."), true);
    assert.equal(heardOwnReply('your assistant', "I'm Layla, your assistant."), true);
    assert.equal(heardOwnReply('Layla stop', "I'm Layla, your assistant."), false);
    assert.equal(heardOwnReply('ليلى', 'أنا ليلى، مساعدتك.'), true);
    assert.equal(heardOwnReply('', 'anything'), false);
  });
});

describe('focus mode', () => {
  const fakeDocument = () => {
    const doc = { fullscreenElement: null as unknown, exits: 0, exitFullscreen: async () => { doc.exits += 1; doc.fullscreenElement = null; } };
    return doc;
  };

  test('uses the browser full screen when the element supports it', async () => {
    const doc = fakeDocument();
    const element = { requestFullscreen: async () => { doc.fullscreenElement = element; } };
    assert.equal(await enterFocus(element as unknown as HTMLElement), 'native');
    assert.equal(focusElement(doc as unknown as Document), element);
    await exitFocus(doc as unknown as Document);
    assert.equal(doc.exits, 1);
    assert.equal(focusElement(doc as unknown as Document), null);
    await exitFocus(doc as unknown as Document);
    assert.equal(doc.exits, 1, 'nothing to leave');
  });

  test('falls back to the webkit prefix, then to the overlay (iPhone Safari, or a refused request)', async () => {
    let webkit = 0;
    assert.equal(await enterFocus({ webkitRequestFullscreen: () => { webkit += 1; } } as unknown as HTMLElement), 'native');
    assert.equal(webkit, 1);
    assert.equal(await enterFocus({} as unknown as HTMLElement), 'overlay');
    assert.equal(await enterFocus({ requestFullscreen: async () => { throw new TypeError('not allowed'); } } as unknown as HTMLElement), 'overlay');
    const webkitDoc = { webkitFullscreenElement: {}, webkitExitFullscreen: () => { webkitDoc.webkitFullscreenElement = null as unknown as object; } };
    await exitFocus(webkitDoc as unknown as Document);
    assert.equal(webkitDoc.webkitFullscreenElement, null);
  });
});
