import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  PHOTO, PROMOTE_FRAMES, PROMOTE_MS, SHADOW_SIZE, TIER_NAMES, canPromote, classifyRenderer, initialTier, parsePin, shouldPromote, slowFrameMs, type PromoteInput,
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

test('a pin wins; software starts at tier 0 and a very large canvas at tier 2', () => {
  assert.equal(parsePin('4'), 4);
  assert.equal(parsePin('0'), 0);
  assert.equal(parsePin('5'), null);
  assert.equal(parsePin('high'), null);
  assert.equal(parsePin(null), null);
  assert.equal(parsePin(' '), null);
  assert.equal(initialTier(4, 'software', 1e6), 4);
  assert.equal(initialTier(null, 'software', 1e6), 0);
  assert.equal(initialTier(null, 'discrete', 5_000_000), 2);
  assert.equal(initialTier(null, 'integrated', 1_000_000), 3);
  assert.equal(TIER_NAMES[PHOTO], 'photo');
});

const base: PromoteInput = { tier: 3, gpu: 'discrete', pixels: 1_500_000, pinned: false, demoted: false, reducedMotion: false, probeMs: Array.from({ length: PROMOTE_FRAMES }, () => 4) };

test('promotion needs a discrete or Apple GPU, tier 3, no pin, no earlier step down and a fast probe', () => {
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

test('the photographic tier asks for more and steps down sooner', () => {
  assert.ok(slowFrameMs(4) < slowFrameMs(3));
  assert.ok(SHADOW_SIZE[4] > SHADOW_SIZE[3] && SHADOW_SIZE[3] > SHADOW_SIZE[2] && SHADOW_SIZE[0] === 0);
});
