import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  ACCENT_HEX, contrastRatio, ACCENTS, AVATAR_STYLES, HAIR_BY_STYLE, HAIR_COLORS, HAIR_STYLES, MODEL_OF_HAIR, OUTFITS, SKIN_TONES,
  chipColours, defaultAvatar, figureLook, initialsOf, inkOn, luminance, resolveAvatar, roleFamily,
} from '../src/lib/hqAvatar.ts';
import { ROLE, SWATCH, cellOf, roleOf } from '../src/lib/hqFigureRoles.ts';
import { placementsFromSlots } from '../src/lib/hqModels.ts';

const ids = Array.from({ length: 600 }, (_, i) => `agent_${(i * 7919).toString(36)}${i}`);

test('the default style is deterministic, always the neutral build, and spreads the rest over the palette', () => {
  assert.deepEqual(defaultAvatar('agent_x'), defaultAvatar('agent_x'));
  const seen = { build: new Set<string>(), skin: new Set<string>(), hair: new Set<string>(), color: new Set<string>(), outfit: new Set<string>(), accent: new Set<string>() };
  const builds: Record<string, number> = {};
  for (const id of ids) {
    const a = defaultAvatar(id);
    seen.build.add(a.avatar_style); seen.skin.add(a.skin_tone); seen.hair.add(a.hair_style); seen.color.add(a.hair_color); seen.outfit.add(a.outfit); seen.accent.add(a.accent);
    builds[a.avatar_style] = (builds[a.avatar_style] ?? 0) + 1;
    assert.ok(HAIR_BY_STYLE[a.avatar_style].includes(a.hair_style), 'the hair shape exists for the build');
  }
  assert.deepEqual([...seen.build], ['neutral']);
  assert.equal(seen.skin.size, SKIN_TONES.length);
  assert.equal(seen.hair.size, HAIR_BY_STYLE.neutral.length);
  assert.equal(seen.color.size, HAIR_COLORS.length);
  assert.equal(seen.outfit.size, OUTFITS.length);
  assert.equal(seen.accent.size, ACCENTS.length);
  // A build is drawn only when the owner chose one: every record without a choice is neutral.
  assert.deepEqual(builds, { neutral: ids.length });
});

test('a room of six agents mostly looks different from each other', () => {
  let distinct = 0, trials = 0;
  for (let start = 0; start < 300; start += 6) {
    const codes = new Set(ids.slice(start, start + 6).map((id) => `${figureLook(id, null).code}/${defaultAvatar(id).hair_color}/${defaultAvatar(id).accent}`));
    distinct += codes.size; trials += 6;
  }
  assert.ok(distinct / trials > 0.97, `${distinct}/${trials}`);
});

test('the default never reads a name: the functions take an id and a role, nothing else', () => {
  const source = readFileSync(new URL('../src/lib/hqAvatar.ts', import.meta.url), 'utf8');
  for (const fn of ['defaultAvatar', 'resolveAvatar', 'figureLook', 'roleFamily', 'chipColours']) {
    const signature = source.match(new RegExp(`export function ${fn}\\(([^)]*)\\)`))?.[1] ?? '';
    assert.ok(!/\bname\b/i.test(signature), `${fn}(${signature})`);
  }
  assert.ok(!/gender|ethnic|\bage\b/i.test(source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')), 'no code path about gender, ethnicity or age');
  // Same id, any role-free call: independent of everything else, including a changed "name" the caller might hold.
  assert.equal(figureLook('agent_7', null).code, figureLook('agent_7', undefined).code);
});

test('a chosen style wins field by field and keeps the rest of the default', () => {
  const base = defaultAvatar('agent_a');
  const r = resolveAvatar('agent_a', { skin_tone: 'tone_6', outfit: 'uniform' });
  assert.equal(r.skin_tone, 'tone_6'); assert.equal(r.outfit, 'uniform');
  assert.equal(r.avatar_style, base.avatar_style); assert.equal(r.hair_color, base.hair_color);
  const forced = resolveAvatar('agent_a', { avatar_style: 'masculine' });
  assert.equal(forced.avatar_style, 'masculine'); assert.ok(HAIR_BY_STYLE.masculine.includes(forced.hair_style));
  // A hair shape with no build picks a build that offers it.
  assert.ok(HAIR_BY_STYLE[resolveAvatar('agent_a', { hair_style: 'long' }).avatar_style].includes('long'));
  assert.equal(resolveAvatar('agent_a', { hair_style: 'spiky' as never, avatar_style: 'feminine' }).avatar_style, 'feminine');
  // Unknown values (an older palette) fall back to the default instead of failing.
  assert.deepEqual(resolveAvatar('agent_a', { skin_tone: 'tone_99' as never, accent: '#fff' as never }), base);
  assert.deepEqual(resolveAvatar('agent_a', null), base);
});

test('the look carries the model of its hair shape, the build width and the role cues', () => {
  const look = figureLook('agent_b', { avatar_style: 'neutral', hair_style: 'bun' }, 'reception');
  assert.equal(look.modelKey, MODEL_OF_HAIR.bun);
  assert.ok(look.scale[0] < 1 && look.scale[2] < 1, 'the neutral build is slimmer');
  assert.ok(look.headset && !look.badge, 'reception wears a headset');
  assert.ok(figureLook('agent_b', null, 'security').badge);
  assert.ok(figureLook('agent_b', null, 'operations').headset);
  assert.equal(figureLook('agent_b', null, 'executive').avatar.outfit, 'formal');
  assert.equal(figureLook('agent_b', { outfit: 'uniform' }, 'executive').avatar.outfit, 'uniform', 'the owner choice beats the role default');
  for (const o of OUTFITS) for (const a of ACCENTS) {
    const l = figureLook('agent_b', { outfit: o, accent: a });
    for (const c of [l.top, l.bottom, l.trim, l.accent, l.skin, l.hair]) assert.match(c, /^#[0-9a-f]{6}$/);
  }
});

test('role families come from the department text or the kind of room, never a name', () => {
  assert.equal(roleFamily({ roomKind: 'office', department: 'Security' }), 'security');
  assert.equal(roleFamily({ roomKind: 'reception' }), 'reception');
  assert.equal(roleFamily({ roomKind: 'customer-care' }), 'care');
  assert.equal(roleFamily({ roomKind: 'office', department: 'Finance' }), 'finance');
  assert.equal(roleFamily({ roomKind: 'warehouse' }), 'field');
  assert.equal(roleFamily({ roomKind: 'office', department: null }), 'office');
});

test('chips: initials, contrast and the chosen colours', () => {
  assert.equal(initialsOf('Ada Lovelace'), 'AL'); assert.equal(initialsOf('  mono '), 'M'); assert.equal(initialsOf(''), '?');
  const chip = chipColours('agent_c', { accent: 'amber', skin_tone: 'tone_2' });
  assert.equal(chip.background, '#c98a1f');
  assert.ok(luminance(chip.background) >= 0 && ['#111418', '#ffffff'].includes(chip.ink));
  assert.equal(inkOn('#ffffff'), '#111418'); assert.equal(inkOn('#000000'), '#ffffff');
});

test('the placed figure uses the chosen style, else the default drawn from the seed, and never a figure without a seed', () => {
  const chosen = figureLook('agent_d', { avatar_style: 'feminine', hair_style: 'long' });
  const [p] = placementsFromSlots([{ model: 'person', x: 0, y: 0, z: 0, rot: 0, width: 0.5, seed: 'agent_d', look: chosen }]);
  assert.equal(p.key, 'person_3'); assert.equal(p.look?.code, chosen.code);
  const [d] = placementsFromSlots([{ model: 'person', x: 0, y: 0, z: 0, rot: 0, width: 0.5, seed: 'agent_d' }]);
  assert.equal(d.look?.code, figureLook('agent_d', null).code);
  assert.equal(placementsFromSlots([{ model: 'person', x: 0, y: 0, z: 0, rot: 0, width: 0.5, look: chosen }]).length, 0);
});

test('model roles: skin column, hair, shirt, shoes and glasses are told apart', () => {
  const at = (mesh: 'head' | 'body', u: number, v: number, restY: number, extra: Partial<Parameters<typeof roleOf>[0]> = {}) => {
    const { k, r } = cellOf(u, v);
    return roleOf({ mesh, k, r, restY, face: false, model: 'person_0', ...extra });
  };
  const cell = (cx: number, cy: number) => [(cx + 0.5) / 16, (cy + 0.5) / 16] as const;
  assert.equal(at('head', ...cell(15, 12), 0.8), ROLE.skin);
  assert.equal(at('head', ...cell(11, 13), 0.9), ROLE.hair);
  assert.equal(at('head', ...cell(1, 13), 0.8, { face: true }), ROLE.keep);
  assert.equal(at('head', ...cell(7, 13), 0.8), ROLE.keep);
  assert.equal(at('body', ...cell(9, 12), 0.45), ROLE.trim);
  assert.equal(at('body', ...cell(1, 14), 0.4), ROLE.top);
  assert.equal(at('body', ...cell(1, 14), 0.15), ROLE.bottom);
  assert.equal(at('body', ...cell(1, 14), 0.02), ROLE.keep);
  assert.equal(at('body', ...cell(5, 9), 0.45), ROLE.accent);
  assert.equal(at('body', ...cell(11, 8), 0.4, { model: 'person_3' }), ROLE.skin);
  assert.equal(SWATCH.length, 8); assert.ok(SWATCH.every((row) => row.length === 8));
});

test('chip text has WCAG AA contrast on every accent colour', () => {
  for (const accent of ACCENTS) {
    const chip = chipColours('agent_x', { accent });
    assert.ok(contrastRatio(chip.background, chip.ink) >= 4.5, `${accent} ${contrastRatio(chip.background, chip.ink).toFixed(2)}`);
    assert.equal(chip.background, ACCENT_HEX[accent]);
  }
});
