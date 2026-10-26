import assert from 'node:assert/strict';
import { test } from 'node:test';
import { FULLSCREEN_VERT, GRADE_FRAG, PBR_COMMON, PBR_LEAF, PBR_NORMAL, PBR_ROUGH, SHAFT_FRAG } from '../src/lib/hqPhotoShaders.ts';

/** Every `{`/`(` has its match: a cheap guard against a truncated or mistyped chunk (the real compile happens on a GPU). */
function balanced(src: string): boolean {
  let depth = 0, paren = 0;
  for (const ch of src) {
    if (ch === '{') depth += 1; else if (ch === '}') depth -= 1; else if (ch === '(') paren += 1; else if (ch === ')') paren -= 1;
    if (depth < 0 || paren < 0) return false;
  }
  return depth === 0 && paren === 0;
}

test('every shader chunk is balanced and has the entry points the renderer relies on', () => {
  for (const [name, src] of Object.entries({ PBR_COMMON, PBR_ROUGH, PBR_NORMAL, PBR_LEAF, FULLSCREEN_VERT, SHAFT_FRAG, GRADE_FRAG })) assert.ok(balanced(src), name);
  assert.match(SHAFT_FRAG, /void main\(\)/);
  assert.match(GRADE_FRAG, /void main\(\)/);
  assert.match(FULLSCREEN_VERT, /gl_Position/);
});

test('the PBR chunks use the names three.js declares and share their own locals in the right order', () => {
  assert.match(PBR_ROUGH, /roughnessFactor/);
  assert.match(PBR_ROUGH, /diffuseColor/);
  assert.match(PBR_NORMAL, /\bnormal\b/);
  assert.match(PBR_NORMAL, /viewMatrix/);
  // hqPm and hqFade are declared by the roughness chunk (which three.js places first) and read by the normal and leaf chunks.
  assert.match(PBR_ROUGH, /vec4 hqPm/);
  assert.match(PBR_ROUGH, /float hqFade/);
  for (const chunk of [PBR_NORMAL, PBR_LEAF]) assert.match(chunk, /hqPm/);
  assert.ok(!/vec4 hqPm/.test(PBR_NORMAL) && !/vec4 hqPm/.test(PBR_LEAF));
  assert.match(PBR_COMMON, /uniform sampler2D uPbrMap/);
  assert.match(PBR_COMMON, /uniform vec4 uPbr/);
  assert.match(PBR_LEAF, /uSunDir/);
  assert.match(PBR_LEAF, /uNight/);
});

test('the detail fades with distance, the shafts can be switched off and the grade keeps colours in range', () => {
  assert.match(PBR_ROUGH, /smoothstep\(uDetail\.x, uDetail\.y/);
  assert.match(SHAFT_FRAG, /uShaft <= 0\.001/);
  assert.match(GRADE_FRAG, /clamp\(c\.rgb, 0\.0, 1\.0\)/);
  assert.match(GRADE_FRAG, /uContrast/);
  assert.ok(!/[–—]/.test(PBR_COMMON + PBR_ROUGH + PBR_NORMAL + PBR_LEAF + SHAFT_FRAG + GRADE_FRAG), 'no long dashes');
});
