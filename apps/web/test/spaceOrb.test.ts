// Unit tests for the voice assistant's space orb (docs/voice.md#the-space-orb): the state mapping
// from the real voice lifecycle (src/lib/spaceMode.ts), the scene's pure geometry, and the
// renderer's cleanup (src/lib/spaceScene.ts) against a stubbed browser. Run with
// `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, test } from 'node:test';
import {
  alphaBounds, ATLAS_REGIONS, callSpaceMode, nextMeteorDelay, particleBudget, satelliteInFront, SPACE_COLORS, SPACE_MODES, spaceDpr,
  spaceLevel, spherePoints, voiceSpaceMode,
} from '../src/lib/spaceMode.ts';
import { createSpaceScene } from '../src/lib/spaceScene.ts';

describe('space orb state mapping', () => {
  test('the Voice view: microphone stopped is standby, active is listening, then thinking and speaking', () => {
    assert.equal(voiceSpaceMode('idle', false), 'standby');
    assert.equal(voiceSpaceMode('listening', false), 'listening');
    assert.equal(voiceSpaceMode('thinking', false), 'thinking');
    assert.equal(voiceSpaceMode('speaking', false), 'speaking');
  });

  test('a waiting approval shows amber only while nothing else happens', () => {
    assert.equal(voiceSpaceMode('idle', true), 'alert');
    assert.equal(voiceSpaceMode('listening', true), 'listening');
    assert.equal(voiceSpaceMode('speaking', true), 'speaking');
  });

  test('a call: hearing the caller is listening; connecting, ending and ended are standby', () => {
    assert.equal(callSpaceMode('listening'), 'listening');
    assert.equal(callSpaceMode('hearing'), 'listening');
    assert.equal(callSpaceMode('thinking'), 'thinking');
    assert.equal(callSpaceMode('speaking'), 'speaking');
    for (const phase of ['idle', 'connecting', 'ending', 'ended', null, undefined]) assert.equal(callSpaceMode(phase), 'standby');
    assert.equal(callSpaceMode('ended', true), 'alert');
    assert.equal(callSpaceMode('speaking', true), 'speaking');
  });

  test('every mode has its own colour, amber for alert', () => {
    assert.equal(new Set(SPACE_MODES.map((mode) => SPACE_COLORS[mode].join())).size, SPACE_MODES.length);
    assert.deepEqual(SPACE_COLORS.alert, [255, 181, 71]);
  });

  test('the bars follow measured audio only while listening or speaking, clamped', () => {
    assert.equal(spaceLevel('listening', 2), 1);
    assert.equal(spaceLevel('speaking', 0.4), 0.4);
    assert.equal(spaceLevel('speaking', -1), 0);
    assert.equal(spaceLevel('listening', Number.NaN), 0);
    assert.equal(spaceLevel('listening', undefined), 0);
    assert.equal(spaceLevel('standby', 0.9), 0);
    assert.equal(spaceLevel('alert', 0.9), 0);
    assert.equal(spaceLevel('thinking', 0), 0.5);
  });
});

describe('space scene geometry', () => {
  test('shooting stars come every 2.5 to 4 seconds, so at least 3 in 12 s', () => {
    assert.equal(nextMeteorDelay(0), 2.5);
    assert.equal(nextMeteorDelay(1), 4);
    assert.equal(nextMeteorDelay(7), 4);
    // The first star at 0.2 s, then the slowest cadence: 0.2, 4.2, 8.2 are all inside 12 s.
    assert.ok(0.2 + 2 * nextMeteorDelay(1) < 12);
  });

  test('the orbiting satellite is in front of Earth on the near half only', () => {
    assert.equal(satelliteInFront(Math.PI / 2), true);
    assert.equal(satelliteInFront(-Math.PI / 2), false);
    assert.equal(satelliteInFront(3 * Math.PI / 2), false);
  });

  test('the device pixel ratio is capped at 2', () => {
    assert.equal(spaceDpr(3), 2);
    assert.equal(spaceDpr(1.5), 1.5);
    assert.equal(spaceDpr(0), 1);
    assert.equal(spaceDpr(Number.NaN), 1);
  });

  test('sprites are cropped to their opaque pixels, so no transparent square is drawn', () => {
    const width = 40, height = 10, pixels = new Uint8ClampedArray(width * height * 4);
    const opaque = (x: number, y: number) => { pixels[(y * width + x) * 4 + 3] = 255; };
    opaque(3, 2); opaque(6, 7);  // astronaut region 0..11
    opaque(20, 4);               // Earth region 11..25
    assert.deepEqual(alphaBounds(pixels, width, height, 0), { x: 3, y: 2, w: 4, h: 6 });
    assert.deepEqual(alphaBounds(pixels, width, height, 1), { x: 20, y: 4, w: 1, h: 1 });
    // No opaque pixel: the whole region.
    assert.deepEqual(alphaBounds(pixels, width, height, 2), { x: Math.floor(ATLAS_REGIONS[2] * width), y: 0, w: width - Math.floor(ATLAS_REGIONS[2] * width), h: height });
  });

  test('particles sit on the unit sphere and are fewer on small canvases', () => {
    for (const [x, y, z] of spherePoints(50)) assert.ok(Math.abs(Math.hypot(x, y, z) - 1) < 1e-9);
    assert.equal(particleBudget(100, 100), 320);
    assert.equal(particleBudget(2000, 2000), 850);
  });
});

// ---- the renderer against a stubbed browser ----

type Listener = (event: unknown) => void;
class Target {
  listeners = new Map<string, Set<Listener>>();
  addEventListener(type: string, fn: Listener) { (this.listeners.get(type) ?? this.listeners.set(type, new Set()).get(type)!).add(fn); }
  removeEventListener(type: string, fn: Listener) { this.listeners.get(type)?.delete(fn); }
  count() { let n = 0; for (const set of this.listeners.values()) n += set.size; return n; }
  dispatch(type: string, event: unknown) { for (const fn of this.listeners.get(type) ?? []) fn(event); }
}

const context2d = () => new Proxy({}, {
  get: (_target, key) => key === 'createRadialGradient' || key === 'createLinearGradient' ? () => ({ addColorStop() {} }) : () => undefined,
  set: () => true,
});

class Canvas extends Target {
  width = 0; height = 0; clientWidth = 400; clientHeight = 300;
  getContext() { return context2d(); }
  getBoundingClientRect() { return { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight }; }
}

let frames = new Map<number, (now: number) => void>();
let nextId = 1;
let observers = 0;
let doc: Target & { hidden: boolean };
const saved: Record<string, unknown> = {};
const globals = ['requestAnimationFrame', 'cancelAnimationFrame', 'document', 'window', 'ResizeObserver', 'Image', 'matchMedia'];

beforeEach(() => {
  for (const key of globals) saved[key] = (globalThis as Record<string, unknown>)[key];
  frames = new Map(); nextId = 1; observers = 0;
  doc = Object.assign(new Target(), { hidden: false, createElement: () => new Canvas() });
  Object.assign(globalThis, {
    requestAnimationFrame: (fn: (now: number) => void) => { const id = nextId++; frames.set(id, fn); return id; },
    cancelAnimationFrame: (id: number) => { frames.delete(id); },
    document: doc,
    window: { devicePixelRatio: 3 },
    ResizeObserver: class { constructor() { observers += 1; } observe() {} disconnect() { observers -= 1; } },
    Image: class { onload: unknown = null; onerror: unknown = null; src = ''; removeAttribute() { this.src = ''; } },
    matchMedia: () => ({ matches: false }),
  });
});
afterEach(() => { for (const key of globals) (globalThis as Record<string, unknown>)[key] = saved[key]; });

/** Runs the pending animation frames `count` times, `step` ms apart. */
function run(count: number, step = 16, start = { now: 0 }) {
  for (let i = 0; i < count; i += 1) {
    start.now += step;
    const due = [...frames.entries()];
    frames.clear();
    for (const [, fn] of due) fn(start.now);
  }
}

describe('space scene renderer', () => {
  test('one animation loop per scene, and destroy releases the loop, observer and listeners', () => {
    const canvas = new Canvas(), stage = new Target();
    const scene = createSpaceScene(canvas as unknown as HTMLCanvasElement, { atlasUrl: 'a.webp', variant: 'full', pointerTarget: stage as unknown as HTMLElement })!;
    assert.ok(scene);
    run(5);
    assert.equal(frames.size, 1, 'exactly one frame scheduled');
    assert.equal(observers, 1);
    assert.equal(stage.count(), 3);
    assert.equal(doc.count(), 1);
    scene.destroy();
    assert.equal(frames.size, 0);
    assert.equal(observers, 0);
    assert.equal(stage.count(), 0);
    assert.equal(doc.count(), 0);
    assert.equal(canvas.width, 0);
    scene.destroy();
  });

  test('ten mounts and unmounts leave nothing running', () => {
    const canvas = new Canvas();
    for (let i = 0; i < 10; i += 1) {
      const scene = createSpaceScene(canvas as unknown as HTMLCanvasElement, { atlasUrl: 'a.webp', variant: i % 2 ? 'full' : 'compact' })!;
      run(3);
      scene.destroy();
    }
    assert.equal(frames.size, 0);
    assert.equal(observers, 0);
    assert.equal(canvas.count(), 0);
    assert.equal(doc.count(), 0);
  });

  test('time stops while paused and resumes without a jump; shooting stars need motion', () => {
    const clock = { now: 0 };
    const scene = createSpaceScene(new Canvas() as unknown as HTMLCanvasElement, { atlasUrl: 'a.webp', variant: 'full' })!;
    run(30, 16, clock);
    const before = scene.snapshot().t;
    assert.ok(before > 0.4);
    scene.setPaused(true);
    assert.equal(scene.shootingStar(), false);
    run(10, 1000, clock);
    assert.equal(scene.snapshot().t, before, 'paused: no time passes');
    scene.setPaused(false);
    run(2, 16, clock);
    assert.ok(scene.snapshot().t - before < 0.05, 'resumed without a time jump');
    assert.equal(scene.shootingStar(), true);
    scene.destroy();
  });

  test('a hidden tab suspends time', () => {
    const clock = { now: 0 };
    const scene = createSpaceScene(new Canvas() as unknown as HTMLCanvasElement, { atlasUrl: 'a.webp', variant: 'compact' })!;
    run(10, 16, clock);
    const before = scene.snapshot().t;
    doc.hidden = true;
    run(10, 1000, clock);
    doc.hidden = false;
    run(2, 16, clock);
    assert.ok(scene.snapshot().t - before < 0.05);
    scene.destroy();
  });

  test('reduced motion keeps the scene still and sends no shooting stars', () => {
    const scene = createSpaceScene(new Canvas() as unknown as HTMLCanvasElement, { atlasUrl: 'a.webp', variant: 'full', reducedMotion: { matches: true } })!;
    run(60);
    const snap = scene.snapshot();
    assert.equal(snap.t, 0);
    assert.equal(snap.meteorsShown, 0);
    assert.equal(scene.shootingStar(), false);
    scene.destroy();
  });

  test('at least three shooting stars in twelve seconds, and the mode follows setMode', () => {
    const scene = createSpaceScene(new Canvas() as unknown as HTMLCanvasElement, { atlasUrl: 'a.webp', variant: 'full', getLevel: () => 0.7 })!;
    run(12 * 30, 1000 / 30);
    assert.ok(scene.snapshot().meteorsShown >= 3, `meteors: ${scene.snapshot().meteorsShown}`);
    for (const mode of SPACE_MODES) { scene.setMode(mode); assert.equal(scene.snapshot().mode, mode); }
    scene.destroy();
  });

  test('the full scene draws the orb under its anchor and keeps the sprites above the panel', () => {
    for (const [width, height, talk] of [[1190, 830, { left: 400, top: 100, width: 400, height: 400 }], [390, 610, { left: 85, top: 90, width: 220, height: 220 }]] as const) {
      const canvas = new Canvas();
      canvas.clientWidth = width; canvas.clientHeight = height;
      const anchor = { getBoundingClientRect: () => talk };
      const panel = { getBoundingClientRect: () => ({ left: 0, top: height * 0.6, width, height: height * 0.4 }) };
      const scene = createSpaceScene(canvas as unknown as HTMLCanvasElement, {
        atlasUrl: 'a.webp', variant: 'full', orbAnchor: anchor as unknown as HTMLElement, floorAnchor: panel as unknown as HTMLElement,
      })!;
      run(10);
      const snap = scene.snapshot();
      const [cx, cy, r] = snap.orb;
      assert.ok(Math.abs(cx - (talk.left + talk.width / 2)) < 13 && Math.abs(cy - (talk.top + talk.height / 2)) < 11, `orb at ${cx}, ${cy}`);
      assert.equal(r, talk.width / 2);
      assert.equal(snap.floor, height * 0.6);
      assert.ok(snap.earth[1] < snap.floor && snap.astronaut[1] < snap.floor, 'sprites above the panel');
      for (const [x] of [snap.earth, snap.astronaut]) assert.ok(x > 0 && x < width, 'sprites on the stage');
      if (width < 600) {
        // Phones: Earth sits off the orb's lower right instead of behind it.
        assert.ok(Math.hypot(snap.earth[0] - cx, snap.earth[1] - cy) > r + Math.min(width * 0.24, 165) * 0.45, 'Earth clear of the orb');
      }
      scene.destroy();
    }
  });

  test('pointer movement tilts the orb', () => {
    const canvas = new Canvas();
    const scene = createSpaceScene(canvas as unknown as HTMLCanvasElement, { atlasUrl: 'a.webp', variant: 'compact' })!;
    canvas.dispatch('pointermove', { clientX: 400, clientY: 0 });
    run(60);
    const [x, y] = scene.snapshot().tilt;
    assert.ok(x > 0.9 && y < -0.9, `tilt ${x}, ${y}`);
    canvas.dispatch('pointerleave', {});
    run(120);
    assert.ok(Math.abs(scene.snapshot().tilt[0]) < 0.01);
    scene.destroy();
  });
});
