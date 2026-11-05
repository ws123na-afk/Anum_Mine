import assert from 'node:assert/strict';
import { test } from 'node:test';
import { HqCameraBridge } from '../src/hq-nav/cameraBridge.ts';
import { campusCamera } from '../src/lib/hqCamera.ts';
import type { CameraState } from '../src/lib/headquartersSpatial.ts';

// The campus and building views cancel a flight before their own camera controls (Reset view, zoom, north) move the camera:
// a flight still under way would otherwise set its next frame over the control, so the view never settles where it was asked to.
test('a cancelled flight sets no more frames, so a camera control that follows it holds', () => {
  const frames = new Map<number, FrameRequestCallback>();
  let nextId = 1, clock = 0;
  const g = globalThis as unknown as Record<string, unknown>;
  const saved = { raf: g.requestAnimationFrame, caf: g.cancelAnimationFrame, document: g.document };
  g.requestAnimationFrame = (cb: FrameRequestCallback) => { const id = nextId++; frames.set(id, cb); return id; };
  g.cancelAnimationFrame = (id: number) => { frames.delete(id); };
  g.document = { querySelector: () => null };
  const tick = (ms: number) => { clock += ms; const due = [...frames.values()]; frames.clear(); for (const cb of due) cb(performance.now() + clock); };
  try {
    const home = campusCamera(80);
    let camera: CameraState = home;
    const sets: CameraState[] = [];
    const bridge = new HqCameraBridge();
    bridge.attach({ get: () => camera, set: (c) => { camera = c; sets.push(c); }, obstacles: () => [], frame: () => null, home: () => home });
    bridge.flyTo({ ...home, target: { x: 60, y: 0, z: -40 }, distance: 70 });
    tick(100);
    assert.ok(bridge.isFlying && sets.length > 0, 'the flight is under way');
    bridge.cancel();
    camera = home; // what Reset view does next
    const before = sets.length;
    for (let i = 0; i < 40; i++) tick(100);
    assert.equal(bridge.isFlying, false);
    assert.equal(sets.length, before, 'no flight frame after the cancel');
    assert.equal(camera, home);
  } finally {
    g.requestAnimationFrame = saved.raf; g.cancelAnimationFrame = saved.caf; g.document = saved.document;
  }
});

// A flight that stops short says so once (`cancelled`), never "arrived"; a newer flight replacing it says nothing of its own.
function withFakeFrames(run: (tick: (ms: number) => void, stage: { fire: (type: string) => void }, restartClock: () => void) => void) {
  const frames = new Map<number, FrameRequestCallback>();
  let nextId = 1, clock = 0;
  const g = globalThis as unknown as Record<string, unknown>;
  const saved = { raf: g.requestAnimationFrame, caf: g.cancelAnimationFrame, document: g.document };
  const handlers = new Map<string, Set<() => void>>();
  const el = {
    addEventListener: (type: string, fn: () => void) => { if (!handlers.has(type)) handlers.set(type, new Set()); handlers.get(type)!.add(fn); },
    removeEventListener: (type: string, fn: () => void) => { handlers.get(type)?.delete(fn); },
  };
  g.requestAnimationFrame = (cb: FrameRequestCallback) => { const id = nextId++; frames.set(id, cb); return id; };
  g.cancelAnimationFrame = (id: number) => { frames.delete(id); };
  g.document = { querySelector: () => el };
  const tick = (ms: number) => { clock += ms; const due = [...frames.values()]; frames.clear(); for (const cb of due) cb(performance.now() + clock); };
  try { run(tick, { fire: (type) => { for (const fn of [...(handlers.get(type) ?? [])]) fn(); } }, () => { clock = 0; }); }
  finally { g.requestAnimationFrame = saved.raf; g.cancelAnimationFrame = saved.caf; g.document = saved.document; }
}

test('a cancelled flight emits one cancelled event and no arrival, whether a control or the person stops it', () => {
  withFakeFrames((tick, stage, restartClock) => {
    const home = campusCamera(80);
    let camera: CameraState = home;
    const far: CameraState = { ...home, target: { x: 60, y: 0, z: -40 }, distance: 70 };
    const bridge = new HqCameraBridge();
    const events: string[] = [];
    bridge.subscribe((e) => events.push(`${e.phase}:${e.frame}`));
    bridge.attach({ get: () => camera, set: (c) => { camera = c; }, obstacles: () => [], frame: (id) => (id === 'b1' ? far : null), home: () => home });

    // A camera control (Reset view, zoom) cancels: one event, then silence; a second cancel with nothing flying says nothing.
    bridge.flyToFrame('b1');
    tick(100);
    bridge.cancel();
    bridge.cancel();
    for (let i = 0; i < 30; i++) tick(100);
    assert.deepEqual(events, ['start:b1', 'cancelled:b1']);

    // A wheel on the stage stops the flight where it is: cancelled, never "end" (that would say "Now showing").
    events.length = 0;
    camera = home;
    restartClock();
    bridge.flyToFrame('b1');
    tick(100);
    stage.fire('wheel');
    for (let i = 0; i < 30; i++) tick(100);
    assert.deepEqual(events, ['start:b1', 'cancelled:b1']);

    // A newer flight takes over without a cancel note; it arrives and says so.
    events.length = 0;
    camera = home;
    restartClock();
    bridge.flyToFrame('b1');
    tick(100);
    bridge.flyHome();
    for (let i = 0; i < 30; i++) tick(100);
    assert.deepEqual(events.filter((e) => e.startsWith('cancelled')), []);
    assert.match(events.at(-1) ?? '', /^(end|instant):__home__$/);
  });
});

test('a view that goes away mid flight cancels it once', () => {
  withFakeFrames((tick) => {
    const home = campusCamera(80);
    let camera: CameraState = home;
    const bridge = new HqCameraBridge();
    const events: string[] = [];
    bridge.subscribe((e) => events.push(e.phase));
    const detach = bridge.attach({ get: () => camera, set: (c) => { camera = c; }, obstacles: () => [], frame: () => ({ ...home, target: { x: 60, y: 0, z: -40 }, distance: 70 }), home: () => home });
    bridge.flyToFrame('b1');
    tick(100);
    detach();
    for (let i = 0; i < 30; i++) tick(100);
    assert.deepEqual(events, ['start', 'cancelled']);
  });
});
