import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  FX_NAMES, PROMOTE_FRAMES, PROMOTE_MS, canPromote, classifyRenderer, parseFxOff, shouldPromote, type PromoteInput,
} from '../src/lib/hqQualityTier.ts';

test('renderer strings map to a GPU class, and software is never mistaken for hardware', () => {
  const cases: Array<[string, string]> = [
    ['ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)', 'software'],
    ['llvmpipe (LLVM 15.0.7, 256 bits)', 'software'],
    ['Microsoft Basic Render Driver', 'software'],
    ['ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)', 'discrete'],
    ['ANGLE (AMD, AMD Radeon RX 7800 XT Direct3D11, D3D11)', 'discrete'],
    ['ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)', 'apple'],
    ['ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11, D3D11)', 'integrated'],
    ['Adreno (TM) 740', 'mobile'],
    ['Mali-G715', 'mobile'],
    ['', 'unknown'],
  ];
  for (const [name, want] of cases) assert.equal(classifyRenderer(name), want, name);
});

const base: PromoteInput = { tier: 3, gpu: 'discrete', pixels: 1_500_000, pinned: false, demoted: false, reducedMotion: false, probeMs: Array.from({ length: PROMOTE_FRAMES }, () => 4) };

test('promotion needs a discrete or Apple GPU, the ultra tier (3), no pin, no earlier step down and a fast probe', () => {
  assert.equal(shouldPromote(base), true);
  assert.equal(shouldPromote({ ...base, gpu: 'apple' }), true);
  for (const gpu of ['software', 'integrated', 'mobile', 'unknown'] as const) assert.equal(shouldPromote({ ...base, gpu }), false, gpu);
  assert.equal(canPromote('software'), false);
  assert.equal(shouldPromote({ ...base, tier: 2 }), false);
  assert.equal(shouldPromote({ ...base, tier: 4 }), false);
  assert.equal(shouldPromote({ ...base, pinned: true }), false);
  assert.equal(shouldPromote({ ...base, demoted: true }), false);
  assert.equal(shouldPromote({ ...base, reducedMotion: true }), false);
  assert.equal(shouldPromote({ ...base, pixels: 6_000_000 }), false);
  assert.equal(shouldPromote({ ...base, probeMs: base.probeMs.slice(1) }), false);
  assert.equal(shouldPromote({ ...base, probeMs: base.probeMs.map(() => PROMOTE_MS + 1) }), false);
  // One hitch is tolerated, a long stall is not.
  assert.equal(shouldPromote({ ...base, probeMs: [...base.probeMs.slice(1), PROMOTE_MS * 2] }), true);
  assert.equal(shouldPromote({ ...base, probeMs: [...base.probeMs.slice(1), PROMOTE_MS * 4] }), false);
});

test('effects can be switched off by name, unknown names and case are handled', () => {
  assert.deepEqual([...parseFxOff(null)], []);
  assert.deepEqual([...parseFxOff('')], []);
  assert.deepEqual([...parseFxOff('gtao, DOF ,nonsense,,pbr')].sort(), ['dof', 'gtao', 'pbr']);
  assert.equal(parseFxOff(FX_NAMES.join(',')).size, FX_NAMES.length);
});
