// The character kit (src/hqAssets/characters/): people drawn with a face, hair, hands, clothes and a seated or standing pose.
// Pure: no three.js. The truth rules are tested here: the build is only the owner's chosen style, nothing is read from a name,
// the same id always looks the same, and every figure stays inside its triangle budget.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { test } from 'node:test';
import { figureLook, resolveAvatar } from '../src/lib/hqAvatar.ts';
import { figureShape, isFigureKey, placementsFromSlots, type ModelPlacement } from '../src/lib/hqModels.ts';
import { resolvePlacements } from '../src/lib/hqModelCache.ts';
import { CHARACTER_LITE_TRIANGLE_CAP, CHARACTER_TRIANGLE_CAP, buildCharacterMesh } from '../src/hqAssets/characters/figure.ts';
import { parseShape, shapeCode, variationOf, type CharacterSpec } from '../src/hqAssets/characters/spec.ts';
import { ROLE } from '../src/hqAssets/characters/mesh.ts';
import { MeshBuilder } from '../src/lib/hqGeometry.ts';
import { seatedFigure, standingFigure } from '../src/hqAssets/interior/figures.ts';

const spec = (over: Partial<CharacterSpec> = {}): CharacterSpec => ({ build: 'n', hair: 'parted', standing: false, pose: 'typing', outfit: 's', hivis: false, headset: false, badge: false, variant: 12345, lod: 0, ...over });
const SLOT = { model: 'person', x: 0, y: 0, z: 0, rot: 0, width: 0.5 } as const;

test('the shape code round trips, and the entry chunk writes the format the kit reads', () => {
  const s = spec({ build: 'f', hair: 'long', pose: 'phone', outfit: 'u', hivis: true, headset: true, badge: true, variant: 987654, lod: 1 });
  assert.deepEqual(parseShape(shapeCode(s)), s);
  const look = figureLook('agent_x', { avatar_style: 'masculine', hair_style: 'parted', outfit: 'formal' }, 'office');
  const parsed = parseShape(figureShape(look, 'agent_x', 'reading', false))!;
  assert.equal(parsed.build, 'm'); assert.equal(parsed.hair, 'parted'); assert.equal(parsed.pose, 'reading'); assert.equal(parsed.outfit, 'f'); assert.equal(parsed.standing, false);
  assert.equal(parseShape(figureShape(look, 'agent_x', 'walking', true))!.standing, true);
  // An unknown pose falls back to the plain one, an unknown code is refused.
  assert.equal(parseShape(figureShape(look, 'agent_x', 'dancing', false))!.pose, 'typing');
  assert.equal(parseShape('fig:x:parted:s.typing:s:1'), null); assert.equal(parseShape('person_1'), null); assert.equal(parseShape('fig:m:parted:s.walking:s:1'), null);
});

test('the build is exactly the owner chosen style; the default is drawn from the id alone', () => {
  for (const [style, letter] of [['masculine', 'm'], ['feminine', 'f'], ['neutral', 'n']] as const) {
    const look = figureLook('agent_pick', { avatar_style: style }, 'office');
    assert.equal(parseShape(figureShape(look, 'agent_pick', 'typing', false))!.build, letter);
  }
  // No choice: the same id gives the same build every time, and it is the avatar default (never anything else).
  const a = figureLook('agent_auto', null, 'office'), b = figureLook('agent_auto', null, 'office');
  assert.equal(figureShape(a, 'agent_auto', 'typing', false), figureShape(b, 'agent_auto', 'typing', false));
  assert.equal(parseShape(figureShape(a, 'agent_auto', 'typing', false))!.build, resolveAvatar('agent_auto', null).avatar_style[0]);
});

test('property: changing only the name never changes the look (no function of the figure takes a name)', () => {
  const names = ['Mai', 'Raed', 'Hazrat', 'Badea', 'Ahmed Elbendari', 'Fatima', 'John', 'Mohammed', 'Sara', 'Omar', 'Layla', 'Sam', 'Alex', 'مي', 'رائد', 'بديعة', 'احمد', 'Zoe', 'Wei', 'Yuki'];
  const records = names.map((name) => ({ id: 'agent_same', name, avatar: null as null }));
  const codes = new Set(records.map((r) => figureShape(figureLook(r.id, r.avatar, 'office'), r.id, 'typing', false)));
  assert.equal(codes.size, 1, 'the same id and choice always give the same figure, whatever the name');
  // The same holds for a chosen style: the name is not an input anywhere.
  const chosen = new Set(records.map((r) => figureShape(figureLook(r.id, { avatar_style: 'feminine' }, 'office'), r.id, 'typing', false)));
  assert.equal(chosen.size, 1);
  // And the choice does change the figure.
  const other = figureShape(figureLook('agent_same', { avatar_style: 'masculine' }, 'office'), 'agent_same', 'typing', false);
  assert.notEqual([...chosen][0], other);
  // Static guard: no source of the kit has a parameter or a field called name, gender, sex, pronoun or ethnicity.
  const dir = new URL('../src/hqAssets/characters/', import.meta.url);
  for (const file of readdirSync(dir)) {
    const code = readFileSync(new URL(file, dir), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
    assert.doesNotMatch(code, /\b(name|names|gender|sex|pronoun|ethnicity|race)\b\s*[:,)=]/i, file);
  }
});

test('determinism: the same spec builds the same mesh, another variant builds another', () => {
  const a = buildCharacterMesh(spec()), b = buildCharacterMesh(spec()), c = buildCharacterMesh(spec({ variant: 54321, build: 'f', hair: 'long' }));
  assert.deepEqual(a.pos, b.pos); assert.deepEqual(a.col, b.col); assert.deepEqual(a.role, b.role);
  assert.notDeepEqual(a.pos, c.pos);
});

test('triangle budget: every build, hair, pose, outfit and flag stays within the caps; the distant version is much lighter', () => {
  let max = 0, lite = 0, worst = '';
  for (const build of ['m', 'f', 'n'] as const) for (const hair of ['spiky', 'parted', 'bun', 'long', 'swept'] as const) {
    for (const [standing, pose] of [[false, 'typing'], [false, 'reading'], [false, 'phone'], [false, 'talking'], [false, 'leaning'], [true, 'neutral'], [true, 'presenting'], [true, 'arms-crossed'], [true, 'phone'], [true, 'walking']] as const) {
      for (const [outfit, hivis, headset, badge] of [['s', false, false, false], ['f', false, true, true], ['u', true, true, false]] as const) {
        for (const variant of [3, 4096, 99999]) {
          const base = spec({ build, hair, standing, pose, outfit, hivis, headset, badge, variant });
          const n = buildCharacterMesh(base).triangles;
          if (n > max) { max = n; worst = shapeCode(base); }
          lite = Math.max(lite, buildCharacterMesh({ ...base, lod: 1 }).triangles);
        }
      }
    }
  }
  assert.ok(max <= CHARACTER_TRIANGLE_CAP, `${max} triangles in ${worst}`);
  assert.ok(lite <= CHARACTER_LITE_TRIANGLE_CAP, `lite ${lite}`);
  assert.ok(max >= 600, 'a close figure is detailed, not a box');
});

test('the three builds are three different bodies: shoulders are widest for masculine, narrowest for feminine', () => {
  const shoulder = (build: 'm' | 'f' | 'n'): number => {
    const m = buildCharacterMesh(spec({ build, standing: true, pose: 'neutral', variant: 1 }));
    let w = 0;
    // The widest the body is at the shoulder line (the head is above it, the arms hang from it).
    for (let i = 0; i < m.role.length; i++) if (m.pos[i * 3 + 1] > 1.38 && m.pos[i * 3 + 1] < 1.46) w = Math.max(w, Math.abs(m.pos[i * 3]));
    return w;
  };
  assert.ok(shoulder('m') > shoulder('n') && shoulder('n') > shoulder('f'), `${shoulder('m')} ${shoulder('n')} ${shoulder('f')}`);
});

test('a person has a face: eyes keep their own colour, the skin and the hair are tinted roles, shoes and a vest keep theirs', () => {
  const m = buildCharacterMesh(spec({ build: 'm', hair: 'parted', variant: 5, hivis: true }));
  const roles = new Set(m.role);
  for (const r of [ROLE.skin, ROLE.hair, ROLE.top, ROLE.bottom, ROLE.keep]) assert.ok(roles.has(r), `role ${r}`);
  // Eyes sit on the front of the head (+z) at head height, in a role that keeps its colour.
  let eyes = 0;
  for (let i = 0; i < m.role.length; i += 3) if (m.role[i] === ROLE.keep && m.pos[i * 3 + 1] > 1.15 && m.pos[i * 3 + 1] < 1.3 && m.pos[i * 3 + 2] > 0.05) eyes++;
  assert.ok(eyes >= 8, `${eyes} eye triangles`);
  // Normals are unit length and every triangle has a colour and a role.
  for (let i = 0; i < m.nor.length; i += 3) assert.ok(Math.abs(Math.hypot(m.nor[i], m.nor[i + 1], m.nor[i + 2]) - 1) < 1e-6);
  assert.equal(m.col.length, m.pos.length); assert.equal(m.role.length * 3, m.pos.length);
});

test('variety is cosmetic and bounded: head coverings and beards only where the rules allow, a hi-vis vest only for field rooms', () => {
  let scarf = 0, beard = 0, cap = 0;
  for (let v = 1; v < 3000; v += 7) {
    for (const build of ['m', 'f', 'n'] as const) {
      const x = variationOf(spec({ build, variant: v }));
      if (x.headwear === 'scarf') { scarf++; assert.notEqual(build, 'm'); }
      if (x.headwear === 'cap') cap++;
      if (x.beard) { beard++; assert.equal(build, 'm'); }
      if (x.garment === 'skirt' || x.garment === 'dress') assert.equal(build, 'f');
      assert.ok(x.width >= 0.94 && x.width <= 1.06);
    }
  }
  assert.ok(scarf > 0 && beard > 0 && cap > 0, 'the variation sets are all reachable');
  // Hi-vis: only the field role (a workshop or a warehouse room) gets the vest flag.
  const field = figureLook('agent_w', { outfit: 'uniform' }, 'field'), office = figureLook('agent_w', { outfit: 'uniform' }, 'office');
  assert.ok(parseShape(figureShape(field, 'agent_w', 'typing', false))!.hivis);
  for (const role of ['office', 'reception', 'security', 'operations', 'care', 'executive', 'finance'] as const) assert.ok(!parseShape(figureShape(figureLook('agent_w', { outfit: 'uniform' }, role), 'agent_w', 'typing', false))!.hivis, role);
  assert.ok(!parseShape(figureShape(office, 'agent_w', 'typing', false))!.hivis);
});

test('people are placed only for a seed: the kit shape is on the placement, nothing for a slot without one', () => {
  const none = placementsFromSlots([{ ...SLOT }, { ...SLOT, seed: '  ' }]);
  assert.equal(none.length, 0);
  const [p] = placementsFromSlots([{ ...SLOT, seed: 'agent_k', pose: 'phone', headset: true }]);
  assert.ok(p.shape && isFigureKey(p.shape));
  const s = parseShape(p.shape)!;
  assert.equal(s.pose, 'phone'); assert.equal(s.headset, true); assert.equal(s.standing, false);
  const [t] = placementsFromSlots([{ ...SLOT, seed: 'agent_k', pose: 'walking', standing: true }]);
  assert.equal(parseShape(t.shape!)!.standing, true);
});

test('the interior builders note a slot for a standing person too, and only with a seed', () => {
  const b = new MeshBuilder();
  seatedFigure(b, 1, 0, 1, { seed: 'agent_s', pose: 'reading' });
  standingFigure(b, 3, 0, 1, { seed: 'agent_t', pose: 'presenting' });
  assert.equal(b.slots.length, 2);
  assert.equal(b.slots[1].standing, true);
  assert.ok(b.triangleCount > 0);
});

test('kit figures are all or nothing: a room never mixes the kit with stand-ins', () => {
  const asked: ModelPlacement[] = ['fig:n:parted:s.typing:s:a', 'fig:f:long:s.typing:s:b'].map((shape, i) => ({
    id: `f${i}`, group: 'figure', key: shape as ModelPlacement['key'], x: 0, y: 0, z: 0, yaw: 0, scale: 1, footprint: [0.6, 0.6], shape: shape as ModelPlacement['shape'],
  }));
  const some = resolvePlacements(asked, new Set([asked[0].key]));
  assert.deepEqual(some.fallbackGroups, ['figure']); assert.equal(some.placements.length, 0);
  const all = resolvePlacements(asked, new Set(asked.map((p) => p.key)));
  assert.ok(all.readyGroups.has('figure')); assert.equal(all.placements.length, 2);
});
