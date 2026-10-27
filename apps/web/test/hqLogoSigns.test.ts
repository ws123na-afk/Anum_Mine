// Building sign model, logo file checks and layout (docs/ANUM_MINE_HQ_LOGO_SIGNS.md). Pure: no browser.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CELL_H, CELL_W, SIGN_STYLES, contrast, layoutSign, luminance, normalizeColor, signLook } from '../src/hq-logo-signs/signModel.ts';
import { MAX_LOGO_BYTES, checkLogoBytes, formatBytes, sniffImage } from '../src/hq-logo-signs/logoFile.ts';

const bytes = (...values: number[]) => new Uint8Array(values);
const text = (value: string) => new TextEncoder().encode(value);
const PNG = bytes(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0);
const JPEG = bytes(0xff, 0xd8, 0xff, 0xe0, 0, 0);
const WEBP = new Uint8Array([...text('RIFF'), 1, 2, 3, 4, ...text('WEBP')]);

test('contrast follows WCAG: black on white is 21 to 1, the same colour is 1 to 1', () => {
  assert.equal(Math.round(contrast('#000000', '#ffffff') * 10) / 10, 21);
  assert.equal(contrast('#336699', '#336699'), 1);
  assert.ok(luminance('#ffffff') > luminance('#777777'));
});

test('a colour is normalised to lower case six digits or refused', () => {
  assert.equal(normalizeColor(' #1F6F4A '), '#1f6f4a');
  for (const bad of ['', 'red', '#fff', '#12345g', '1f6f4a', '#1f6f4a0', null, undefined]) assert.equal(normalizeColor(bad), null);
});

test('every style has a board, readable lettering and says where the colour appears', () => {
  for (const style of SIGN_STYLES) {
    for (const color of [null, '#ffffff', '#000000', '#1f6f4a', '#ffd400', '#0a0a0a']) {
      const look = signLook(style, color);
      assert.equal(look.style, style);
      // The lettering is always readable on the board, whatever colour the owner chose (WCAG AA for text).
      assert.ok(contrast(look.text, look.face) >= 4.5, `${style} ${color}: ${contrast(look.text, look.face)}`);
      if (color) assert.equal(look.accent, color);
    }
  }
});

test('plain: a faint colour falls back to ink for the lettering and stays on the bar', () => {
  const pale = signLook('plain', '#ffee99');
  assert.equal(pale.readable, false);
  assert.equal(pale.letteringFallback, true);
  assert.equal(pale.accent, '#ffee99');
  assert.notEqual(pale.text, '#ffee99');
  const deep = signLook('plain', '#1f6f4a');
  assert.equal(deep.readable, true);
  assert.equal(deep.text, '#1f6f4a');
});

test('illuminated: a dark colour is not used for the glowing lettering; a light one is', () => {
  assert.equal(signLook('illuminated', '#102030').letteringFallback, true);
  assert.equal(signLook('illuminated', '#7fffd4').text, '#7fffd4');
});

test('banner: the board is the owner\'s colour and the lettering is the better of white and ink; never unreadable', () => {
  const light = signLook('banner', '#ffe08a');
  assert.equal(light.face, '#ffe08a');
  assert.ok(contrast(light.text, light.face) >= 7);
  assert.equal(light.readable, true);
  assert.equal(signLook('banner', null).accent, null);
});

test('plaque: the frame needs 3 to 1 against the dark board', () => {
  assert.equal(signLook('plaque', '#1b2025').readable, false);
  assert.equal(signLook('plaque', '#51d9cc').readable, true);
  assert.equal(signLook('plaque', '#51d9cc').part, 'frame');
});

test('an unknown or missing style is the plain lettered sign', () => {
  assert.equal(signLook(null, null).style, 'plain');
  assert.equal(signLook('neon' as never, '#1f6f4a').style, 'plain');
});

test('layout: no logo gives the name the whole cell; a logo sits at the start edge on a plate and keeps its proportions', () => {
  const lettered = layoutSign(null, false);
  assert.equal(lettered.plate, null);
  assert.ok(lettered.text.w > CELL_W - 80);
  const wide = layoutSign({ w: 400, h: 100 }, false);
  assert.ok(wide.plate && wide.logo);
  assert.ok(Math.abs(wide.logo.w / wide.logo.h - 4) < 0.1, 'proportions kept');
  assert.ok(wide.plate.x < wide.text.x, 'plate first in left to right');
  const arabic = layoutSign({ w: 400, h: 100 }, true);
  assert.ok(arabic.plate && arabic.plate.x > arabic.text.x, 'plate on the right in Arabic');
  for (const l of [wide, arabic]) {
    assert.ok(l.plate!.x >= 0 && l.plate!.x + l.plate!.w <= CELL_W && l.plate!.y + l.plate!.h <= CELL_H, 'plate inside the cell');
    assert.ok(l.logo!.x >= l.plate!.x && l.logo!.x + l.logo!.w <= l.plate!.x + l.plate!.w, 'logo inside its plate');
    assert.ok(l.text.x >= 0 && l.text.x + l.text.w <= CELL_W && l.text.w > 100, 'text inside the cell');
  }
  const tall = layoutSign({ w: 50, h: 500 }, false);
  assert.ok(tall.logo!.h <= tall.plate!.h);
  assert.equal(layoutSign({ w: 0, h: 0 }, false).plate, null);
});

test('files are judged by their first bytes, not their name or type', () => {
  assert.equal(sniffImage(PNG), 'png');
  assert.equal(sniffImage(JPEG), 'jpeg');
  assert.equal(sniffImage(WEBP), 'webp');
  assert.equal(sniffImage(text('GIF89a....')), 'gif');
  assert.equal(sniffImage(text('<?xml version="1.0"?><svg/>')), 'svg');
  assert.equal(sniffImage(text('  <svg xmlns="http://www.w3.org/2000/svg"/>')), 'svg');
  assert.equal(sniffImage(text('MZ not a picture')), null);
});

test('checkLogoBytes explains each refusal', () => {
  assert.deepEqual(checkLogoBytes(1000, PNG), { ok: true, kind: 'png' });
  assert.deepEqual(checkLogoBytes(0, PNG), { ok: false, problem: 'empty' });
  assert.deepEqual(checkLogoBytes(MAX_LOGO_BYTES + 1, PNG), { ok: false, problem: 'tooBig' });
  assert.deepEqual(checkLogoBytes(MAX_LOGO_BYTES, JPEG), { ok: true, kind: 'jpeg' });
  assert.deepEqual(checkLogoBytes(50, text('<svg/>')), { ok: false, problem: 'svg' });
  assert.deepEqual(checkLogoBytes(50, text('GIF87a')), { ok: false, problem: 'gif' });
  assert.deepEqual(checkLogoBytes(50, text('hello')), { ok: false, problem: 'notPicture' });
});

test('sizes read plainly', () => {
  assert.equal(formatBytes(900), '900 B');
  assert.equal(formatBytes(2048), '2.0 KB');
  assert.equal(formatBytes(200 * 1024), '200 KB');
  assert.equal(formatBytes(1048576), '1.0 MB');
});
