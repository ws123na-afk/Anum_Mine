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

test('an unknown or missing style is the standard campus sign: a dark board with the name, the owner\'s colour as its bar', () => {
  for (const look of [signLook(null, null), signLook(undefined, '#1f6f4a'), signLook('neon' as never, '#1f6f4a')]) {
    assert.equal(look.style, 'standard');
    assert.ok(contrast(look.text, look.face) >= 7, 'light lettering on a dark board');
  }
  assert.equal(signLook(null, null).accent, null);
  assert.equal(signLook(null, '#ffee99').readable, true, 'a pale bar is easy to see on the dark board');
  assert.equal(signLook(null, '#142c3d').readable, false, 'a bar the colour of the board is not');
  assert.equal(signLook(null, '#1f6f4a').part, 'bar');
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

// ---- the campus side: which logos load, what the sign text carries
import { loadLogos, logoKey, pickLogoTargets, signFor, signStats, MAX_LOGOS, type Logo } from '../src/hq-logo-signs/campusSigns.ts';

const target = (id: string, x: number, z: number, sha = 'a'.repeat(64)) => ({ id, buildingId: id, sha, x, z });

test('the nearest buildings load their logos first and no more than the cap', () => {
  const many = Array.from({ length: 40 }, (_, i) => target(`b${String(i).padStart(2, '0')}`, i * 10, 0));
  const picked = pickLogoTargets(many, { x: 0, z: 0 });
  assert.equal(picked.length, MAX_LOGOS);
  assert.deepEqual(picked.slice(0, 3).map((t) => t.id), ['b00', 'b01', 'b02']);
  assert.deepEqual(pickLogoTargets(many, { x: 390, z: 0 }, 2).map((t) => t.id), ['b39', 'b38']);
  assert.deepEqual(pickLogoTargets([target('x', 0, 0, '')], { x: 0, z: 0 }), [], 'a building without a logo is not a target');
  assert.deepEqual(pickLogoTargets(many, { x: 0, z: 0 }, 0), []);
});

test('a logo that cannot be fetched or decoded leaves the building on the lettered sign and is never an error', async () => {
  const controller = new AbortController();
  const targets = [target('a', 0, 0), target('b', 5, 0), target('c', 9, 0)];
  const seen: string[] = [];
  const result = await loadLogos(targets, new Map(), controller.signal, async (id) => {
    seen.push(id);
    if (id === 'a') return null;
    if (id === 'b') throw new Error('offline');
    return new Blob([new Uint8Array([1, 2, 3])]);
  });
  assert.deepEqual(seen.sort(), ['a', 'b', 'c']);
  assert.equal(result.size, 0, 'no bitmap support in the test runner and the other two failed: nothing is kept');
});

test('logos already decoded are reused and unwanted ones are closed', async () => {
  const closed: string[] = [];
  const keep: Logo = { image: { close: () => closed.push('keep'), width: 10, height: 10 } as never, width: 10, height: 10 };
  const drop: Logo = { image: { close: () => closed.push('drop'), width: 10, height: 10 } as never, width: 10, height: 10 };
  const a = target('a', 0, 0), gone = target('gone', 1, 0);
  const previous = new Map([[logoKey(a), keep], [logoKey(gone), drop]]);
  let fetched = 0;
  const result = await loadLogos([a], previous, new AbortController().signal, async () => { fetched += 1; return null; });
  assert.equal(fetched, 0, 'same building and hash: no new request');
  assert.equal(result.get(logoKey(a)), keep);
  assert.deepEqual(closed, ['drop']);
  const aborted = new AbortController();
  aborted.abort();
  const again = await loadLogos([a, target('z', 3, 0)], new Map(), aborted.signal, async () => new Blob([new Uint8Array([1])]));
  assert.equal(again.size, 0);
});

test('the sign text key moves with style, colour and logo, and not with anything else', () => {
  const sign = { buildingId: 'b1', style: 'plaque', color: '#1f6f4a', logoSha256: 'f'.repeat(64) };
  const loaded = new Map([[logoKey({ buildingId: 'b1', sha: 'f'.repeat(64) }), { image: {} as never, width: 20, height: 10 }]]);
  const withLogo = signFor(sign, 'Tyre Pulse', false, loaded);
  const noLogoYet = signFor(sign, 'Tyre Pulse', false, new Map());
  assert.notEqual(withLogo.brand, noLogoYet.brand, 'the sign is drawn again when the logo arrives');
  assert.equal(signFor({ ...sign, color: '#fff' }, 'x', false, new Map()).brand, 'plaque||', 'a colour that is not six digits is no colour, and there is no logo yet');
  assert.equal(signFor({ ...sign, style: 'neon' }, 'x', false, new Map()).brand.startsWith('|'), true, 'an unknown style is no style');
  assert.equal(typeof withLogo.paint, 'function');
  assert.deepEqual(signStats([{ sign }, { sign: undefined }], loaded), { branded: 1, logos: 1 });
  assert.deepEqual(signStats([{ sign }], new Map()), { branded: 1, logos: 0 });
});

// ---- the sign travels from the registry record to the campus building

test('the registry building record carries its sign into the scene and the campus building', async () => {
  const { projectHeadquarters } = await import('../src/lib/headquartersProjection.ts');
  const { expandCampus } = await import('../src/lib/campusRegistry.ts');
  const scope = { tenantId: 'tenant-a', workspaceId: 'w1' };
  const stamp = { timezone: 'Asia/Dubai', revision: 1 };
  const registry = {
    buildings: [
      { id: 'b1', name: 'Alpha', ...stamp, sign_style: 'banner', brand_color: '#1f6f4a', logo_sha256: 'c'.repeat(64) },
      { id: 'b2', name: 'Beta', ...stamp },
      { id: 'b3', name: 'Gamma', ...stamp, brand_color: '#51d9cc' },
    ],
    floors: ['b1', 'b2', 'b3'].map((id) => ({ id: `f-${id}`, name: 'Ground', building_id: id, level: 0, revision: 1 })),
    rooms: [], desks: [],
  };
  const scene = projectHeadquarters(scope, registry as never, [], new Date('2026-10-27T08:00:00Z')).buildings;
  assert.deepEqual(scene[0].sign, { buildingId: 'b1', style: 'banner', color: '#1f6f4a', logoSha256: 'c'.repeat(64) });
  assert.equal(scene[1].sign, undefined, 'no sign set: the record stays as it was');
  assert.deepEqual(scene[2].sign, { buildingId: 'b3', style: null, color: '#51d9cc', logoSha256: null });

  const home = { workspace_id: 'w1', tenant_id: 'tenant-a', name: 'Home', kind: 'business', icon: null, floors: null, accessible: true, totals: null, presence_verified_at: null } as never;
  const campus = { buildings: [home], totals: null, generated_at: '' } as never;
  const many = expandCampus(campus, 'w1', scene, null);
  assert.deepEqual(many.buildings.map((b) => b.sign?.buildingId ?? null), ['b1', null, 'b3']);
  // One registry building: the workspace's own building takes its sign and stays one building.
  const one = expandCampus(campus, 'w1', scene.slice(0, 1), null);
  assert.equal(one.buildings.length, 1);
  assert.equal(one.buildings[0].sign?.buildingId, 'b1');
  assert.equal(one.buildings[0].registryBuildingId, undefined);
  // No sign: the campus object itself comes back.
  assert.equal(expandCampus(campus, 'w1', scene.slice(1, 2), null), campus);
});

test('another workspace\'s logo is fetched in that workspace (its id goes to the fetcher); the open workspace\'s without one', async () => {
  const asked: Array<[string, string | undefined]> = [];
  await loadLogos([{ id: 'ws_b', buildingId: 'b1', sha: 'cd'.repeat(32), x: 0, z: 0, workspaceId: 'ws_b' }, { id: 'own', buildingId: 'b2', sha: 'ef'.repeat(32), x: 1, z: 0 }],
    new Map(), new AbortController().signal, async (id, _signal, workspaceId) => { asked.push([id, workspaceId]); return null; });
  assert.deepEqual(asked.sort(), [['b1', 'ws_b'], ['b2', undefined]]);
});
