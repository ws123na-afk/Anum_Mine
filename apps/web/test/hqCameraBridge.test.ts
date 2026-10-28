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
