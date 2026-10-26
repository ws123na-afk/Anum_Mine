import assert from 'node:assert/strict';
import { test } from 'node:test';
import { HQ_TIERS, ladderFor, parsePin, tierById, isTierId } from '../src/lib/hqQualityTiers.ts';
import { summarize, percentile } from '../src/lib/hqFrameStats.ts';
import { DEFAULT_GOVERNOR, FrameGovernor } from '../src/lib/hqGovernor.ts';
import { buildReport, describeAgent, isSoftwareRenderer, reportLooksAnonymous, verdictFor, type GpuProbe, type TierResult } from '../src/lib/hqGpuReport.ts';
import { getGpuState, setGpuState, subscribeGpuState } from '../src/lib/hqGpuState.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

// ------------------------------------------------------------ the tier registry

test('the registry has ordered, unique tiers, each with a name in both languages', () => {
  assert.ok(HQ_TIERS.length >= 4);
  assert.deepEqual(HQ_TIERS.map((t) => t.id), [...HQ_TIERS.map((t) => t.id)].sort((a, b) => a - b));
  assert.equal(new Set(HQ_TIERS.map((t) => t.id)).size, HQ_TIERS.length);
  assert.equal(new Set(HQ_TIERS.map((t) => t.key)).size, HQ_TIERS.length);
  for (const tier of HQ_TIERS) {
    const key = `hqGpu.tier.${tier.key}`;
    assert.ok(key in en, `English name for tier ${tier.key}`);
    assert.ok(key in ar, `Arabic name for tier ${tier.key}`);
  }
  for (let i = 1; i < HQ_TIERS.length; i++) assert.ok(HQ_TIERS[i].shadowSize >= HQ_TIERS[i - 1].shadowSize, 'a heavier tier never has smaller shadows');
});

test('a stored pin is read through the registry only', () => {
  assert.equal(parsePin('2'), 2);
  assert.equal(parsePin('0'), 0);
  assert.equal(parsePin(null), null);
  assert.equal(parsePin(''), null);
  assert.equal(parsePin('9'), null);
  assert.equal(parsePin('1.5'), null);
  assert.equal(parsePin('high'), null);
  assert.equal(isTierId(3), true);
  assert.equal(tierById(99).id, HQ_TIERS[HQ_TIERS.length - 1].id);
});

test('the ladder lowers pixel ratio first, then shadows, then glow, then glass effects, and skips rungs a tier does not have', () => {
  const ultra = ladderFor(tierById(3));
  assert.deepEqual(ultra.map((s) => [s.dprScale, s.shadowSize, s.bloom, s.lean]), [
    [1, 2048, true, false], [0.85, 2048, true, false], [0.7, 2048, true, false], [0.7, 1024, true, false], [0.7, 0, true, false], [0.7, 0, false, false], [0.7, 0, false, true],
  ]);
  const low = ladderFor(tierById(0));
  assert.deepEqual(low.map((s) => [s.dprScale, s.shadowSize, s.bloom, s.lean]), [[1, 0, false, false], [0.85, 0, false, false], [0.7, 0, false, false], [0.7, 0, false, true]]);
  for (const tier of HQ_TIERS) {
    const rungs = ladderFor(tier);
    for (let i = 1; i < rungs.length; i++) {
      const a = rungs[i - 1], b = rungs[i];
      assert.ok(b.dprScale <= a.dprScale && b.shadowSize <= a.shadowSize && (!b.bloom || a.bloom) && (b.lean || !a.lean), 'every rung is cheaper or equal');
      assert.notDeepEqual(a, b);
    }
    assert.equal(rungs[rungs.length - 1].lean, true);
  }
});

// ------------------------------------------------------------ frame statistics

test('frame summary gives median, p95, worst and dropped frames', () => {
  const frames = [...Array(95).fill(16.7), ...Array(5).fill(60)];
  const s = summarize(frames);
  assert.equal(s.count, 100);
  assert.equal(s.medianMs, 16.7);
  assert.equal(s.worstMs, 60);
  assert.equal(s.droppedFrames, 5);
  assert.ok(s.p95Ms >= 16.7 && s.p95Ms <= 60);
  assert.ok(s.fps > 50 && s.fps < 60);
  assert.equal(summarize([]).count, 0);
  assert.equal(summarize([NaN, -1, 0]).count, 0);
  assert.equal(percentile([], 0.5), 0);
});

// ------------------------------------------------------------ the frame governor with simulated frame times

/** Feed frames of `ms` each for `seconds` of simulated time, applying decisions. Returns the decisions taken with their time. */
function run(g: FrameGovernor, ms: number, seconds: number, clock: { now: number }): Array<['down' | 'up', number]> {
  const taken: Array<['down' | 'up', number]> = [];
  const end = clock.now + seconds * 1000;
  while (clock.now < end) {
    clock.now += ms;
    const d = g.sample(ms, clock.now);
    if (d) { g.apply(d, clock.now); taken.push([d, clock.now]); }
  }
  return taken;
}

test('governor: healthy frames change nothing', () => {
  const g = new FrameGovernor(6);
  assert.deepEqual(run(g, 16.7, 60, { now: 1000 }), []);
  assert.equal(g.step, 0);
});

test('governor: slow frames step down one rung at a time with a cool-down, and stop at the last rung', () => {
  const g = new FrameGovernor(4);
  const clock = { now: 1000 };
  const taken = run(g, 45, 30, clock);
  assert.deepEqual(taken.map((t) => t[0]), ['down', 'down', 'down']);
  assert.equal(g.step, 3);
  for (let i = 1; i < taken.length; i++) assert.ok(taken[i][1] - taken[i - 1][1] >= DEFAULT_GOVERNOR.cooldownMs, 'cool-down between steps');
});

test('governor: a single hitch or a burst of compile stalls never steps down', () => {
  const g = new FrameGovernor(6);
  const clock = { now: 1000 };
  run(g, 16.7, 2, clock);
  for (let i = 0; i < 6; i++) { clock.now += 400; assert.equal(g.sample(400, clock.now), null); }
  for (let i = 0; i < 5; i++) { clock.now += 80; assert.equal(g.sample(80, clock.now), null); }
  assert.deepEqual(run(g, 16.7, 5, clock), []);
  assert.equal(g.step, 0);
});

test('governor: it climbs back only after a long healthy run, and slowly', () => {
  const g = new FrameGovernor(6);
  const clock = { now: 1000 };
  run(g, 45, 8, clock);
  const down = g.step;
  assert.ok(down >= 1);
  const early = run(g, 16.7, 8, clock);
  assert.deepEqual(early, [], 'eight healthy seconds are not enough');
  const later = run(g, 16.7, 8, clock);
  assert.ok(later.length >= 1 && later.every((t) => t[0] === 'up'));
  assert.equal(g.step, down - 1, 'one rung at a time');
});

test('governor: hysteresis, frames between the healthy and slow limits move nothing', () => {
  const g = new FrameGovernor(6);
  assert.deepEqual(run(g, 22, 60, { now: 1000 }), []);
  assert.equal(g.step, 0);
});

test('governor: a step down soon after a step up doubles the wait before the next step up (no flip-flop)', () => {
  const g = new FrameGovernor(6);
  const clock = { now: 1000 };
  run(g, 45, 2, clock);
  assert.equal(g.step, 1);
  const up1 = run(g, 16.7, 14, clock);
  assert.equal(up1.length, 1);
  assert.equal(g.step, 0);
  run(g, 45, 3.5, clock);
  assert.equal(g.step, 1);
  const wait1 = run(g, 16.7, 14, clock);
  assert.deepEqual(wait1, [], '14 healthy seconds are no longer enough');
  const wait2 = run(g, 16.7, 14, clock);
  assert.equal(wait2.length, 1, 'the doubled wait (24 s) passes');
});

test('governor: disabled means no decisions at all (pinned tier, software rasteriser, a run in progress)', () => {
  const g = new FrameGovernor(6);
  g.enabled = false;
  assert.deepEqual(run(g, 90, 30, { now: 1000 }), []);
  assert.equal(g.step, 0);
});

test('governor: reset forgets a slow spell (a hidden tab) and defer keeps the rung', () => {
  const g = new FrameGovernor(6);
  const clock = { now: 1000 };
  for (let i = 0; i < 20; i++) { clock.now += 45; g.sample(45, clock.now); }
  g.reset();
  assert.equal(g.medianMs, 0);
  const taken = run(g, 45, 3, clock);
  assert.ok(taken.length <= 1);
  g.defer(clock.now);
  assert.equal(g.step, taken.length);
});

// ------------------------------------------------------------ verdicts and the report

const probe = (over: Partial<GpuProbe> = {}): GpuProbe => ({
  webglVersion: 2, renderer: 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060, Direct3D11)', vendor: 'Google Inc. (NVIDIA)', unmasked: true, software: false,
  maxTextureSize: 16384, maxCubeMapSize: 16384, maxAnisotropy: 16, maxVertexUniformVectors: 4095, maxFragmentUniformVectors: 1024,
  extensions: ['EXT_color_buffer_float', 'WEBGL_debug_renderer_info'], used: { EXT_color_buffer_float: true }, dpr: 1.25, viewport: { width: 1440, height: 900 },
  cores: 12, deviceMemoryGb: 8, browser: 'Chrome 130', os: 'Windows', ...over,
});
const result = (tier: number, ms: number, p95 = ms * 1.2): TierResult => ({
  tier, key: tierById(tier).key, frames: { count: 300, medianMs: ms, p95Ms: p95, worstMs: p95 * 1.5, meanMs: ms, droppedFrames: 0, fps: 1000 / ms },
  drawCalls: 80, triangles: 90000, dpr: 1, shadowSize: tierById(tier).shadowSize, bloom: tierById(tier).bloom, memoryEstimateMb: 120, floorChange: true,
});

test('verdict: the heaviest tier that holds 60 frames a second', () => {
  const all = [0, 1, 2, 3].map((t) => result(t, 16.7));
  assert.deepEqual(verdictFor(probe(), all), { kind: 'fits', tier: 3, fps: 60 });
  const slowTop = [result(0, 16.7), result(1, 16.7), result(2, 16.7), result(3, 30, 40)];
  assert.deepEqual(verdictFor(probe(), slowTop), { kind: 'reduced', tier: 2, fps: 60 });
  const mid = [result(0, 16.7), result(1, 28, 35), result(2, 40, 50), result(3, 55, 70)];
  assert.deepEqual(verdictFor(probe(), mid), { kind: 'reduced', tier: 0, fps: 60 });
  const thirty = [result(0, 30, 40), result(1, 33, 45), result(2, 60, 80)];
  assert.deepEqual(verdictFor(probe(), thirty), { kind: 'reduced', tier: 1, fps: 30 });
  assert.equal(verdictFor(probe(), [result(0, 80, 120), result(1, 90, 130)]).kind, 'poor');
  assert.equal(verdictFor(probe(), []).kind, 'unmeasured');
});

test('verdict: a software rasteriser is reported honestly whatever the timings say', () => {
  const v = verdictFor(probe({ renderer: 'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)', software: true }), [0, 1, 2, 3].map((t) => result(t, 10)));
  assert.equal(v.kind, 'software');
  assert.equal(v.tier, 0);
  assert.equal(verdictFor(probe({ webglVersion: 0 }), []).kind, 'no-webgl');
  assert.equal(isSoftwareRenderer('ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)'), true);
  assert.equal(isSoftwareRenderer('llvmpipe (LLVM 15.0.7, 256 bits)'), true);
  assert.equal(isSoftwareRenderer('Microsoft Basic Render Driver'), true);
  assert.equal(isSoftwareRenderer('ANGLE (NVIDIA, NVIDIA GeForce RTX 3060, Direct3D11)'), false);
  assert.equal(isSoftwareRenderer('Mali-G78'), false);
});

test('the report has a fixed shape and no personal data, even when the input carries extra fields', () => {
  const dirty = { ...probe(), accountId: 'acct_1234', email: 'person@example.com', workspace_id: 'ws_1', url: 'https://app.example.com/office?tenant=t1', ip: '203.0.113.9' } as unknown as GpuProbe;
  const report = buildReport({ now: new Date('2026-10-26T10:00:00Z'), probe: dirty, quality: null, pinned: null, results: [result(2, 16.7)], notes: ['ok'] });
  const json = JSON.stringify(report);
  assert.equal(reportLooksAnonymous(json), true);
  assert.ok(!/acct_1234|person@example|ws_1|203\.0\.113|app\.example/.test(json));
  assert.deepEqual(Object.keys(report).sort(), ['generatedAt', 'gpu', 'notes', 'quality', 'schema', 'tiers', 'verdict']);
  assert.deepEqual(Object.keys(report.gpu).sort(), [
    'browser', 'cores', 'deviceMemoryGb', 'dpr', 'extensions', 'maxAnisotropy', 'maxCubeMapSize', 'maxFragmentUniformVectors', 'maxTextureSize', 'maxVertexUniformVectors', 'os', 'renderer', 'software', 'unmasked', 'used', 'vendor', 'viewport', 'webglVersion',
  ]);
  assert.equal(report.schema, 'anum.hq.gpu-report/1');
  assert.equal(report.verdict.tierKey, 'high');
  assert.equal(reportLooksAnonymous('{"x":"https://example.com"}'), false);
  assert.equal(reportLooksAnonymous('{"x":"a@b.co"}'), false);
});

test('the report clips strings and drops non printable text', () => {
  const report = buildReport({ now: new Date(0), probe: probe({ renderer: 'GPU\u0000‮' + 'x'.repeat(500) }), quality: null, pinned: 1, results: [] });
  assert.ok(report.gpu.renderer.length <= 160);
  assert.ok(!/[^\x20-\x7e]/.test(report.gpu.renderer));
});

test('only browser family and operating system are kept from a user agent', () => {
  assert.deepEqual(describeAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'), { browser: 'Chrome 130', os: 'Windows' });
  assert.deepEqual(describeAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'), { browser: 'Safari 17', os: 'iOS' });
  assert.deepEqual(describeAgent('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/129.0.0.0 Mobile Safari/537.36'), { browser: 'Chrome 129', os: 'Android' });
});

test('the quality store notifies subscribers once per change', () => {
  let calls = 0;
  const off = subscribeGpuState(() => { calls += 1; });
  const state = { tier: 2, reason: 'automatic' as const, step: 0, steps: 6, settings: ladderFor(tierById(2))[0], software: false, governed: true };
  setGpuState(state);
  setGpuState(state);
  assert.equal(calls, 1);
  assert.equal(getGpuState(), state);
  off();
  setGpuState(null);
  assert.equal(calls, 1);
});
