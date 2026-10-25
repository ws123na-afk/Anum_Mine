import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';
import { test } from 'node:test';
import { MeshBuilder } from '../src/lib/hqGeometry.ts';
import { lamp } from '../src/lib/hqProps.ts';
import { buildBuildingScene } from '../src/lib/buildingScene.ts';
import type { SceneFloor } from '../src/lib/headquartersProjection.ts';
import { ModelCache, catalogBytes, keysNeeded, resolvePlacements } from '../src/lib/hqModelCache.ts';
import { MODEL_SPECS, PERSON_KEYS, placementsFromSlots, seedHash, type ModelKey } from '../src/lib/hqModels.ts';
import { buildInteriorRoom, seatedFigure, workstation, SCHEMES } from '../src/hqAssets/interior/index.ts';

const modelsDir = new URL('../public/hq/models/', import.meta.url);

test('every catalogued model file exists at its recorded size and is a binary glTF', () => {
  for (const spec of Object.values(MODEL_SPECS)) {
    const url = new URL(spec.file, modelsDir);
    assert.equal(statSync(url).size, spec.bytes, spec.key);
    assert.equal(readFileSync(url).subarray(0, 4).toString('latin1'), 'glTF', spec.key);
  }
});

test('the shipped models stay inside the byte budget and every one has a recorded permissive licence and the NOTICE says so', () => {
  const total = catalogBytes(Object.keys(MODEL_SPECS) as ModelKey[]);
  assert.ok(total <= 800_000, `models total ${total} bytes`);
  const notice = readFileSync(new URL('NOTICE.txt', modelsDir), 'utf8');
  for (const spec of Object.values(MODEL_SPECS)) {
    assert.ok(['CC0-1.0', 'CC-BY-4.0'].includes(spec.license));
    assert.ok(notice.includes(spec.file), `${spec.file} in NOTICE`);
    assert.ok(notice.includes(spec.author.split(' (')[0]), `${spec.author} in NOTICE`);
  }
  assert.ok(!/[\u2013\u2014]/.test(notice), 'no long dashes');
});

test('truth rule: a person placement exists only for a slot with a seed, and the look comes from the seed alone', () => {
  const none = placementsFromSlots([
    { model: 'person', x: 1, y: 0, z: 1, rot: 0, width: 0.5 },
    { model: 'person', x: 2, y: 0, z: 2, rot: 0, width: 0.5, seed: '   ' },
  ]);
  assert.equal(none.length, 0);
  const a = placementsFromSlots([{ model: 'person', x: 1, y: 0, z: 1, rot: 0, width: 0.5, seed: 'agent_one' }]);
  const b = placementsFromSlots([{ model: 'person', x: 9, y: 0, z: 9, rot: 2, width: 0.5, seed: 'agent_one' }]);
  assert.equal(a.length, 1);
  assert.equal(a[0].key, b[0].key, 'same seed, same look, wherever the desk is');
  assert.equal(a[0].key, PERSON_KEYS[seedHash('agent_one') % PERSON_KEYS.length]);
  assert.equal(placementsFromSlots([{ model: 'unknown', x: 0, y: 0, z: 0, rot: 0, width: 1 }]).length, 0);
});

test('truth rule: the interior builders note a person slot only for a station that carries an occupant seed', () => {
  const frame = { x: 0, y: 0, z: 0, width: 10, depth: 7 };
  const stations = [
    { id: 'd1', x: 2, z: 3, state: 'working' as const, occupantSeed: 'agent_a' },
    { id: 'd2', x: 4, z: 3, state: 'vacant' as const, occupantSeed: null },
    { id: 'd3', x: 6, z: 3 },
  ];
  const b = new MeshBuilder();
  workstation(b, 0, stations[0], SCHEMES.walnut);
  workstation(b, 0, stations[1], SCHEMES.walnut);
  workstation(b, 0, stations[2], SCHEMES.walnut);
  const people = b.slots.filter((s) => s.model === 'person');
  assert.equal(people.length, 1);
  assert.equal(people[0].seed, 'agent_a');
  // Whole rooms: the stock rooms invent no person, even when they are given desks without an occupant.
  for (const kind of ['reception', 'office', 'customer-care', 'operations', 'executive', 'lounge', 'meeting'] as const) {
    const room = new MeshBuilder();
    buildInteriorRoom(room, kind, frame, { seed: 'x', stations: stations.map((s) => ({ ...s, occupantSeed: null })) });
    assert.equal(room.slots.filter((s) => s.model === 'person').length, 0, kind);
  }
});

function floorWith(deskAgents: Array<string | null>): SceneFloor {
  return {
    id: 'f0', name: 'Floor 0', level: 0,
    rooms: [{ id: 'r0', floorId: 'f0', buildingId: 'b', label: 'Customer Care', kind: 'office', x: 0, z: 0, width: 10, depth: 7, height: 3.2, capacity: 4, revision: 1, deskIds: [], evidence: 'registry' as const },
      { id: 'r1', floorId: 'f0', buildingId: 'b', label: 'Reception', kind: 'reception', x: 12, z: 0, width: 8, depth: 6, height: 3.2, capacity: 4, revision: 1, deskIds: [], evidence: 'registry' as const }],
    desks: deskAgents.map((agentId, i) => ({ id: `d${i}`, roomId: 'r0', label: `Desk ${i}`, position: { x: 2 + i * 2, y: 0.78, z: 3 }, agentId, agent: null, assignmentVisible: true })),
    bounds: { width: 30, depth: 10 },
  };
}

test('truth rule at scene level: no registry assignment means no person placement and no figure stand-in, a sofa is furniture', () => {
  const empty = buildBuildingScene([floorWith([null, null, null])], { activeIndex: 0, people: new Map() });
  assert.equal(empty.models.filter((m) => m.group === 'figure').length, 0);
  assert.equal(empty.fallbacks.figure.vertices.length, 0);
  assert.ok(empty.models.some((m) => m.group === 'sofa'), 'the reception waiting area has a sofa');
  const one = buildBuildingScene([floorWith(['agent_a', null, null])], { activeIndex: 0, people: new Map([['d0', 'agent_a']]) });
  assert.equal(one.models.filter((m) => m.group === 'figure').length, 1);
  assert.ok(one.fallbacks.figure.vertices.length > 0, 'the procedural stand-in is kept for a failed load');
  // A person from the model layer needs the people map: assigned desks without a seed draw no one.
  const unseeded = buildBuildingScene([floorWith(['agent_a'])], { activeIndex: 0, people: new Map() });
  assert.equal(unseeded.models.filter((m) => m.group === 'figure').length, 0);
});

test('splitting keeps every triangle exactly once: rest plus the tagged parts equal the whole', () => {
  const b = new MeshBuilder();
  b.box(0, 0, 0, 1, 1, 1, [1, 1, 1]);
  b.tagged('sofa', () => b.box(2, 0, 0, 1, 1, 1, [1, 0, 0]));
  b.tagged('figure', () => b.box(4, 0, 0, 1, 1, 1, [0, 1, 0]));
  const split = b.toFloat32Split(['sofa', 'figure']);
  assert.equal(split.rest.length + split.byTag.get('sofa')!.length + split.byTag.get('figure')!.length, b.toFloat32().length);
  assert.ok(split.byTag.get('sofa')!.length > 0 && split.byTag.get('figure')!.length > 0);
});

test('lamps note a halo for the night glow, and a figure alone does not', () => {
  const mesh = new MeshBuilder();
  seatedFigure(mesh, 1, 0, 1, { seed: 'a' });
  assert.equal(mesh.halos.length, 0);
  lamp(mesh, 3, 3);
  assert.equal(mesh.halos.length, 8);
  assert.equal(mesh.halos[1], 2.7);
});

// ---------------------------------------------------------------- the cache and the fallback rules

const placement = (key: ModelKey, group: 'sofa' | 'figure') => ({ id: key, group, key, x: 0, y: 0, z: 0, yaw: 0, scale: 1, footprint: [1, 1] as const });

test('the cache loads each model once, remembers failures and never rejects', async () => {
  let calls = 0;
  const cache = new ModelCache<string>(async (key) => { calls += 1; if (key === 'armchair') throw new Error('404'); return { model: key, bytes: 10 }; });
  const [a, b, bad] = await Promise.all([cache.get('sofa'), cache.get('sofa'), cache.get('armchair')]);
  assert.equal(a?.model, 'sofa');
  assert.equal(b, a);
  assert.equal(bad, null);
  assert.equal(calls, 2);
  assert.equal(cache.hasFailed('armchair'), true);
  assert.equal(cache.peek('sofa')?.bytes, 10);
  assert.equal(cache.peek('armchair'), null);
  assert.equal(cache.bytes, 10);
});

test('the cache gives up on a model that never answers', async () => {
  const cache = new ModelCache<string>(() => new Promise(() => undefined), 20);
  assert.equal(await cache.get('sofa'), null);
  assert.equal(cache.hasFailed('sofa'), true);
});

test('fallback: when no model loads every group keeps its procedural stand-in and nothing is drawn', () => {
  const asked = [placement('sofa', 'sofa'), placement('person_0', 'figure')];
  const r = resolvePlacements(asked, new Set());
  assert.deepEqual([...r.fallbackGroups].sort(), ['figure', 'sofa']);
  assert.equal(r.placements.length, 0);
  assert.equal(r.readyGroups.size, 0);
});

test('fallback: a furniture group is ready only when all its models loaded, so styles never mix', () => {
  const asked = [placement('sofa', 'sofa'), placement('armchair', 'sofa')];
  const half = resolvePlacements(asked, new Set<ModelKey>(['sofa']));
  assert.deepEqual(half.fallbackGroups, ['sofa']);
  assert.equal(half.placements.length, 0);
  const full = resolvePlacements(asked, new Set<ModelKey>(['sofa', 'armchair']));
  assert.equal(full.placements.length, 2);
  assert.deepEqual(full.fallbackGroups, []);
});

test('fallback: a person whose own look failed to load takes another loaded look, and a person is never added', () => {
  const asked = [placement('person_2', 'figure'), placement('person_4', 'figure')];
  const r = resolvePlacements(asked, new Set<ModelKey>(['person_0']));
  assert.equal(r.placements.length, asked.length, 'one placement per asked person');
  assert.ok(r.placements.every((p) => p.key === 'person_0'));
  assert.deepEqual(keysNeeded(asked).sort(), ['person_2', 'person_4']);
});
