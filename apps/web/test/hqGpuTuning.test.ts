// GPU tuning from the first real graphics report (docs/ANUM_MINE_HQ_GPU_CHECK.md, "Real devices"): an integrated Intel UHD Graphics 620
// on Windows and Chrome. The report holds per tier summaries (not raw frames), so the frame streams below are rebuilt from them and checked
// to reproduce every figure of the report before the governor is fed with them.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { PerformanceObserver } from 'node:perf_hooks';
import { test } from 'node:test';
import { summarize, RollingFrames } from '../src/lib/hqFrameStats.ts';
import { DEFAULT_GOVERNOR, FrameGovernor } from '../src/lib/hqGovernor.ts';
import { buildReport, rememberTierFor, reportLooksAnonymous, verdictFor, type GpuProbe, type TierResult } from '../src/lib/hqGpuReport.ts';
import { HQ_TIERS, ladderFor, programVariants, tierById } from '../src/lib/hqQualityTiers.ts';
import { classifyRenderer, startTierFor } from '../src/lib/hqQualityTier.ts';
import { HITCH_MS, WARM, runBenchmark, warmedUp } from '../src/hq-gpu/gpuBench.ts';

type Report = { gpu: GpuProbe; tiers: TierResult[]; verdict: { kind: string; tier: number; tierKey: string } };
const REAL = JSON.parse(readFileSync(new URL('./fixtures/gpu-report-2026-11-02-uhd620.json', import.meta.url), 'utf8')) as Report;
const OWNER_GPU = REAL.gpu.renderer;

// ------------------------------------------------------------ the automatic choice

test('the first real report: an Intel UHD Graphics 620 starts at High (it measured 60 fps typical at High, not at Ultra)', () => {
  assert.equal(OWNER_GPU, 'ANGLE (Intel, Intel(R) UHD Graphics 620 (0x00005917) Direct3D11 vs_5_0 ps_5_0, D3D11)');
  assert.equal(classifyRenderer(OWNER_GPU), 'integrated');
  const pixels = REAL.gpu.viewport.width * REAL.gpu.viewport.height * REAL.gpu.dpr ** 2;
  assert.equal(startTierFor(OWNER_GPU, pixels), 2);
  assert.equal(tierById(startTierFor(OWNER_GPU, pixels)).key, REAL.verdict.tierKey, 'the start tier is the tier the benchmark recommended');
});

test('start tiers by renderer string: discrete Ultra, integrated High, older integrated and phones Medium, software Low', () => {
  const cases: Array<[string, number]> = [
    ['ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)', 3],
    ['ANGLE (AMD, AMD Radeon RX 7800 XT Direct3D11, D3D11)', 3],
    ['ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)', 3],
    ['ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)', 2],
    ['ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11, D3D11)', 2],
    ['ANGLE (Intel, Intel(R) HD Graphics 620 Direct3D11, D3D11)', 2],
    ['ANGLE (Intel, Intel(R) Arc(TM) Graphics Direct3D11, D3D11)', 2],
    ['ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11, D3D11)', 2],
    ['Apple GPU', 2],
    ['', 2],
    ['ANGLE (Intel, Intel(R) HD Graphics 4000 Direct3D11, D3D11)', 1],
    ['ANGLE (Intel, Intel(R) HD Graphics Direct3D11, D3D11)', 1],
    ['Mesa Intel(R) HD Graphics 5500 (BDW GT2)', 1],
    ['ANGLE (Intel, Intel(R) UHD Graphics 600 Direct3D11, D3D11)', 1],
    ['ANGLE (Intel, Intel(R) HD Graphics 510 Direct3D11, D3D11)', 1],
    ['ANGLE (AMD, AMD Radeon(TM) R5 Graphics Direct3D11, D3D11)', 1],
    ['Adreno (TM) 740', 1],
    ['Mali-G715', 1],
    ['ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)', 0],
  ];
  for (const [name, want] of cases) assert.equal(startTierFor(name, 1_500_000), want, name);
  // A very large canvas never starts above High.
  assert.equal(startTierFor('ANGLE (NVIDIA, NVIDIA GeForce RTX 4070, D3D11)', 8_300_000), 2);
  assert.equal(startTierFor(OWNER_GPU, 8_300_000), 2);
});

// ------------------------------------------------------------ the real frame distributions

/** A frame stream with `fill` frames spread between the `hitches` (evenly, so every window sees its share), starting with `first`. */
function stream(fill: Array<[number, number]>, hitches: number[], first: number[] = []): number[] {
  const base = fill.flatMap(([ms, n]) => Array<number>(n).fill(ms));
  const total = first.length + base.length + hitches.length;
  const out: number[] = [...first];
  let b = 0, h = 0;
  for (let i = first.length; i < total; i++) {
    const due = Math.floor(((h + 0.5) * (total - first.length)) / hitches.length) + first.length;
    if (h < hitches.length && i >= due) out.push(hitches[h++]); else out.push(base[b++]);
  }
  return out;
}

/** Rebuilt from the report's summaries (count, median, p95, worst, mean, dropped): the test below proves each one reproduces them. */
const REBUILT: Record<string, number[]> = {
  low: stream([[16.7, 43]], [600, 516.5, ...Array<number>(9).fill(88.56)], [3381.5]),
  medium: stream([[16.7, 220], [16.8, 38]], [39.21, 566.3, 39.21, 39.21]),
  high: stream([[16.7, 200], [16.8, 35]], [226.9, 616.2, 226.9]),
  ultra: stream([[16.7, 187]], [33.3, 206.09, 50, 66.7, 206.09, 66.7, 516.1, 66.7, 206.09, 66.7, 50, 66.7, 206.09, 66.7]),
};

test('the rebuilt frame streams reproduce every figure of the real report', () => {
  for (const tier of REAL.tiers) {
    const s = summarize(REBUILT[tier.key]);
    const r = tier.frames;
    assert.equal(s.count, r.count, `${tier.key} count`);
    assert.equal(s.medianMs, r.medianMs, `${tier.key} median`);
    assert.equal(s.p95Ms, r.p95Ms, `${tier.key} p95`);
    assert.equal(s.worstMs, r.worstMs, `${tier.key} worst`);
    assert.equal(s.droppedFrames, r.droppedFrames, `${tier.key} dropped`);
    assert.equal(Number(s.meanMs.toFixed(2)), r.meanMs, `${tier.key} mean`);
  }
});

test('the verdict on the real report is High at 60 frames a second, and a full benchmark remembers it', () => {
  const v = verdictFor(REAL.gpu, REAL.tiers);
  assert.deepEqual(v, { kind: 'reduced', tier: 2, fps: 60 });
  assert.equal(rememberTierFor(REAL.gpu, REAL.tiers), 2);
  // Not remembered: a partial run, a software rasteriser, a masked name (software unknown).
  assert.equal(rememberTierFor(REAL.gpu, REAL.tiers.slice(1)), null);
  assert.equal(rememberTierFor({ ...REAL.gpu, software: true }, REAL.tiers), null);
  assert.equal(rememberTierFor({ ...REAL.gpu, software: null }, REAL.tiers), null);
});

/** Feeds `frames` (looped) for `seconds` of simulated time, the stream chosen by the rung (`at(step)`), applying decisions. */
function feed(g: FrameGovernor, at: (step: number) => number[], seconds: number, clock: { now: number }): Array<['down' | 'up', number]> {
  const taken: Array<['down' | 'up', number]> = [];
  const end = clock.now + seconds * 1000;
  let i = 0;
  while (clock.now < end) {
    const frames = at(g.step);
    const ms = frames[i++ % frames.length];
    clock.now += ms;
    const d = g.sample(ms, clock.now);
    if (d) { g.apply(d, clock.now); taken.push([d, clock.now]); i = 0; }
  }
  return taken;
}

test('governor on the real data: Ultra (16.7 ms median, 66.7 ms p95) steps down; High and Medium never do', () => {
  const ultra = new FrameGovernor(ladderFor(tierById(3)).length);
  const taken = feed(ultra, () => REBUILT.ultra, 6, { now: 1000 });
  assert.ok(taken.length >= 1 && taken[0][0] === 'down', 'hitches alone step Ultra down although its median is 16.7 ms');
  assert.ok(taken[0][1] - 1000 < 5000, 'within a few seconds');
  for (const key of ['high', 'medium'] as const) {
    const g = new FrameGovernor(ladderFor(tierById(key === 'high' ? 2 : 1)).length);
    assert.deepEqual(feed(g, () => REBUILT[key], 120, { now: 1000 }), [], key);
  }
  // The median rule alone would never have moved: the old governor saw nothing wrong with Ultra here.
  const old = new FrameGovernor(6, { ...DEFAULT_GOVERNOR, hitchP95Ms: Infinity, healthyP95Ms: Infinity });
  assert.deepEqual(feed(old, () => REBUILT.ultra, 30, { now: 1000 }), []);
});

test('governor on the real data does not flip-flop: Ultra hitches above, High frames below, the wait to climb back doubles', () => {
  const g = new FrameGovernor(ladderFor(tierById(3)).length);
  const taken = feed(g, (step) => (step === 0 ? REBUILT.ultra : REBUILT.high), 300, { now: 1000 });
  const ups = taken.filter((t) => t[0] === 'up').map((t) => t[1]);
  assert.ok(taken.length <= 10, `few changes in five minutes (${taken.length})`);
  for (let i = 2; i < ups.length; i++) assert.ok(ups[i] - ups[i - 1] >= ups[i - 1] - ups[i - 2] - 1, 'each climb back waits at least as long as the last');
  assert.ok(g.step <= 1, 'it settles one rung below Ultra or at Ultra, never further down');
});

test('governor: a burst of a few long frames among healthy ones still moves nothing (p95 needs more than 1 frame in 20)', () => {
  const g = new FrameGovernor(6);
  const frames = stream([[16.7, 195]], [60, 60, 60, 60, 60]);
  assert.deepEqual(feed(g, () => frames, 60, { now: 1000 }), []);
});

// ------------------------------------------------------------ no garbage per frame

async function collections(run: () => void): Promise<number> {
  let n = 0;
  const obs = new PerformanceObserver((list) => { n += list.getEntries().length; });
  obs.observe({ entryTypes: ['gc'] });
  run();
  await new Promise((resolve) => setTimeout(resolve, 50));
  obs.disconnect();
  return n;
}

test('the rolling windows allocate nothing per frame, and the governor next to nothing (no collector pauses from the frame loop)', async () => {
  const w = new RollingFrames(120);
  for (let i = 0; i < 2000; i++) { w.push(16.7 + (i % 7)); w.quantile(0.95); }
  assert.ok(await collections(() => { for (let i = 0; i < 300_000; i++) { w.push(16.7 + (i % 7)); w.median(); w.quantile(0.95); } }) <= 1, 'windows: no garbage');
  const g = new FrameGovernor(6);
  const clock = { now: 1000 };
  for (let i = 0; i < 2000; i++) { clock.now += 16.7; g.sample(16.7 + (i % 7), clock.now); }
  const governed = await collections(() => { for (let i = 0; i < 300_000; i++) { clock.now += 16.7; g.sample(16.7 + (i % 7), clock.now); } });
  // The old windows copied and sorted an array on every frame: about 300 young collections over the same 300,000 frames (83 minutes at 60 Hz).
  const copying: number[] = [];
  const old = await collections(() => { for (let i = 0; i < 300_000; i++) { copying.push(16.7 + (i % 7)); if (copying.length > 40) copying.shift(); [...copying].sort((a, b) => a - b); } });
  assert.ok(governed * 10 < old, `governor ${governed} collections, copy and sort ${old}`);
  assert.ok(governed <= 20, `governor collections over 300,000 frames: ${governed}`);
});

test('rolling windows: median and p95 by the same rank rule as the report', () => {
  const w = new RollingFrames(120);
  const frames = REBUILT.ultra.slice(0, 120);
  for (const f of frames) w.push(f);
  const sorted = [...frames].sort((a, b) => a - b);
  assert.equal(w.median(), sorted[59]);
  assert.equal(w.quantile(0.95), sorted[113]);
  for (let i = 0; i < 200; i++) w.push(16.7);
  assert.equal(w.length, 120);
  assert.equal(w.quantile(0.95), 16.7);
  w.clear();
  assert.equal(w.median(), 0);
});

// ------------------------------------------------------------ shader variants

test('the warm-up compiles every shader variant the tiers reach: shadows on and off, and the glow target', () => {
  assert.deepEqual(programVariants(ladderFor(tierById(3))), [
    { shadows: true, glow: false }, { shadows: true, glow: true }, { shadows: false, glow: false }, { shadows: false, glow: true },
  ]);
  assert.deepEqual(programVariants(ladderFor(tierById(1))), [{ shadows: true, glow: false }, { shadows: false, glow: false }]);
  assert.deepEqual(programVariants(ladderFor(tierById(0))), [{ shadows: false, glow: false }]);
  // A daytime benchmark of every tier: the top rung of each, no glow target.
  assert.deepEqual(programVariants(HQ_TIERS.map((t) => ladderFor(t)[0]), false), [{ shadows: false, glow: false }, { shadows: true, glow: false }]);
});

// ------------------------------------------------------------ the benchmark warms a tier before timing it

test('warm-up ends only once frames settle, never before the minimum, always by the maximum', () => {
  const steady = Array<number>(20).fill(16.7);
  assert.equal(warmedUp(WARM.minMs - 1, steady), false);
  assert.equal(warmedUp(WARM.minMs, steady.slice(0, WARM.minFrames - 1)), false);
  assert.equal(warmedUp(WARM.minMs, steady), true);
  assert.equal(warmedUp(2000, [...steady, 600]), false, 'a stall in the last frames keeps it warming');
  assert.equal(warmedUp(WARM.maxMs, [...steady, 600]), true);
  // A slow but even device (software drawing) settles too.
  assert.equal(warmedUp(3000, Array<number>(20).fill(180)), true);
});

test('the benchmark compiles first, then warms each tier, and times none of the switch stalls (the real Low run timed a 3.4 s frame)', async () => {
  const calls: string[] = [];
  let clock = 0;
  let sincePin = 0;
  // After each switch: the real report's warm-up stalls (3.4 s, then 600 and 516 ms), then steady 60 Hz frames.
  const script = [3381.5, 600, 516.5];
  const fake = {
    stats: { drawn: 74, triangles: 141361, dpr: 1.13, shadowSize: 0, bloom: false },
    renderer: { domElement: { width: 100, height: 50 }, info: { programs: [], memory: { geometries: 0, textures: 0 } }, getContext: () => null },
    meshes: [], textures: [], composer: null, cameraState: { yaw: 0, pitch: 0.6, distance: 80, fov: 40, target: { x: 0, y: 0, z: 0 } },
    liftValue: new Map<string, number>(), startLifts() {}, requestFrame() {},
    async prewarm(ids: readonly number[]) { calls.push(`prewarm:${ids.join(',')}`); },
    setPin(id: number | null, run: boolean) { calls.push(`pin:${id}:${run}`); sincePin = 0; },
    setCamera() {},
  };
  const realPerf = globalThis.performance;
  const realRaf = (globalThis as { requestAnimationFrame?: unknown }).requestAnimationFrame;
  Object.defineProperty(globalThis, 'performance', { value: { now: () => clock, mark() {}, measure() {} }, configurable: true, writable: true });
  (globalThis as { requestAnimationFrame?: unknown }).requestAnimationFrame = (cb: (t: number) => void) => {
    clock += sincePin < script.length ? script[sincePin] : 16.7;
    sincePin += 1;
    setImmediate(() => cb(clock));
    return 1;
  };
  try {
    const ids = HQ_TIERS.map((t) => t.id);
    const out = await runBenchmark(fake as never, ids, 2000, new AbortController().signal, () => {});
    assert.equal(calls[0], `prewarm:${ids.join(',')}`, 'every variant is compiled before the first tier');
    assert.equal(calls[1], 'pin:0:true');
    assert.equal(out.length, ids.length);
    for (const r of out) {
      assert.ok(r.frames.worstMs < HITCH_MS, `${r.key}: worst ${r.frames.worstMs} ms, no switch stall timed`);
      assert.ok(Math.abs(r.frames.medianMs - 16.7) < 0.01);
      assert.ok((r.warmupMs ?? 0) >= 3381.5 + 600 + 516.5, `${r.key}: the stalls are in the warm-up (${r.warmupMs} ms)`);
      assert.deepEqual(r.hitches, { count: 0, compile: 0, buffers: 0 });
      assert.equal(r.gpu, null, 'no timer query here');
    }
    assert.equal(calls[calls.length - 1], 'pin:null:true', 'the tier in force is restored');
  } finally {
    Object.defineProperty(globalThis, 'performance', { value: realPerf, configurable: true, writable: true });
    (globalThis as { requestAnimationFrame?: unknown }).requestAnimationFrame = realRaf;
  }
});

// ------------------------------------------------------------ the report

test('the report carries warm-up, GPU time and hitch causes per tier, as numbers only, and stays anonymous', () => {
  const tiers: TierResult[] = REAL.tiers.map((t, i) => ({ ...t, warmupMs: 1234.4 + i, gpu: i === 0 ? null : { medianMs: 9.123, p95Ms: 12.5, samples: 200 }, hitches: { count: 2, compile: 1, buffers: 0 } }));
  const report = buildReport({ now: new Date('2026-11-02T00:00:00Z'), probe: REAL.gpu, quality: null, pinned: null, results: tiers });
  assert.equal(report.tiers[0].warmupMs, 1234);
  assert.equal(report.tiers[0].gpu, null);
  assert.deepEqual(report.tiers[1].gpu, { medianMs: 9.12, p95Ms: 12.5, samples: 200 });
  assert.deepEqual(report.tiers[1].hitches, { count: 2, compile: 1, buffers: 0 });
  assert.equal(reportLooksAnonymous(JSON.stringify(report)), true);
  assert.equal(reportLooksAnonymous(readFileSync(new URL('./fixtures/gpu-report-2026-11-02-uhd620.json', import.meta.url), 'utf8')), true);
});
