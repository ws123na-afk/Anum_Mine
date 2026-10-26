// The glass shows the real floors (docs/ANUM_MINE_HQ_GLASS_PLAN.md): the plan atlas is deterministic, a pane maps to the room that sits
// behind it on that floor, the shell follows the floor plans, and a desk is lit only when presence says its agent is at work.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';
import type { Registry } from '../src/lib/headquartersProjection.ts';
import { projectHeadquarters } from '../src/lib/headquartersProjection.ts';
import { FLOOR_PITCH } from '../src/lib/buildingScene.ts';
import { layoutCampus, type CampusBuilding } from '../src/lib/campusModel.ts';
import { registryKey, registryPlans } from '../src/lib/campusRegistry.ts';
import { buildCampusScene } from '../src/lib/campusScene.ts';
import { VERTEX_FLOATS } from '../src/lib/hqGeometry.ts';
import { indexPresence, type HqAgentPresence } from '../src/lib/hqPresence.ts';
import { hasPlan, layoutKey, planFrame, planInputOf, planSize, PLINTH, STOREY_PITCH, type PlanInput } from '../src/lib/hqPlanFrame.ts';
import { CORRIDOR, KIND_FLOOR, LIGHT, buildGlassPlan, decodeAlpha, encodeAlpha, sampleBehindPane, samplePlan, storeyAt, type PlanEntry } from '../src/lib/hqPlanAtlas.ts';
import { plannedDrawer, plannedRows, planAtlas } from '../src/lib/hqPlanKit.ts';
import { buildPlanShell, paneRowOf, tagPaneUv } from '../src/hqAssets/exterior/planShell.ts';
import { PLAN_COLOR, PLAN_COMMON } from '../src/lib/hqGlassShader.ts';
import { STOREY } from '../src/hqAssets/exterior/parts.ts';

type Fixture = { buildings: { id: string; name: string }[]; floors: { id: string; building_id: string; level: number; name: string }[]; rooms: Record<string, unknown>[]; desks: Record<string, unknown>[] };
const fx = JSON.parse(readFileSync(new URL('./fixtures/starterCampus.json', import.meta.url), 'utf8')) as Fixture;
const rev = { revision: 1 };
const registryOf = (patch?: (f: Fixture) => Fixture): Registry => {
  const f = patch ? patch(structuredClone(fx)) : fx;
  return {
    buildings: f.buildings.map((b) => ({ ...b, ...rev, timezone: 'Asia/Dubai' })), floors: f.floors.map((x) => ({ ...x, ...rev })),
    rooms: f.rooms.map((r) => ({ ...r, ...rev })), desks: f.desks.map((d) => ({ ...d, ...rev })),
  } as unknown as Registry;
};
const project = (r: Registry) => projectHeadquarters({ tenantId: 't', workspaceId: 'w' }, r, []);
const hq = project(registryOf()).buildings.find((b) => b.id === 'campus-01-hq')!;
const presenceOf = (list: Array<Partial<HqAgentPresence> & { agentId: string; status: HqAgentPresence['status'] }>) =>
  indexPresence(list.map((p) => ({ name: '', role: null, taskTitles: [], observedAt: new Date().toISOString(), ...p })));

const entryOf = (input: PlanInput, x = 0, z = 0): PlanEntry => ({ origin: { x, z }, frame: planFrame(input.floors)!, input });
const input = (): PlanInput => planInputOf(hq, null)!;

describe('floor plans of a registry building', () => {
  test('the shell frame follows each floor: basement skipped, setbacks kept, one storey pitch', () => {
    const frame = planFrame(hq.floors)!;
    assert.equal(STOREY_PITCH, FLOOR_PITCH);
    assert.equal(STOREY, FLOOR_PITCH);
    assert.equal(hq.floors.length, 5);
    assert.equal(frame.storeys.length, 4, 'level -1 is below ground');
    assert.ok(frame.storeys.every((s, i) => s.n === i));
    const depths = frame.storeys.map((s) => s.rect.z1 - s.rect.z0);
    assert.ok(new Set(depths).size > 1, 'floors have their own footprints');
    // The union holds every storey, and the middle is the placement origin.
    for (const s of frame.storeys) { assert.ok(s.rect.x0 >= frame.rect.x0 && s.rect.x1 <= frame.rect.x1 && s.rect.z0 >= frame.rect.z0 && s.rect.z1 <= frame.rect.z1); }
    assert.equal(frame.roof, PLINTH + 4 * FLOOR_PITCH);
    assert.deepEqual(planSize(hq.floors), { w: frame.w, d: frame.d, levels: 4, height: frame.roof });
  });
  test('a building without rooms has no plan and keeps the generic look', () => {
    const empty = project(registryOf((f) => ({ ...f, rooms: f.rooms.filter((r) => !String(r.floor_id).startsWith('campus-07')) }))).buildings.find((b) => b.id === 'campus-07-personal')!;
    assert.equal(hasPlan(empty.floors), false);
    assert.equal(planFrame(empty.floors), null);
    assert.equal(planInputOf(empty, null), null);
    const campus = { buildings: [] as CampusBuilding[], totals: null, generated_at: '' };
    assert.equal(registryPlans(campus, 'w', [empty], null).size, 0);
  });
  test('without plans the campus layout is exactly what it was', () => {
    const b = (id: string, kind: CampusBuilding['kind']): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name: id, kind, icon: null, floors: 3, accessible: true, totals: null, presence_verified_at: null });
    const list = [b('a', 'executive'), b('b', 'business'), b('c', 'department')];
    assert.deepEqual(layoutCampus(list), layoutCampus(list, new Map()));
    assert.equal(layoutCampus(list)[0].width, 15);
  });
  test('real footprints widen the rings so planned buildings never overlap', () => {
    const b = (id: string, kind: CampusBuilding['kind']): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name: id, kind, icon: null, floors: 3, accessible: true, totals: null, presence_verified_at: null });
    const list = [b('hq', 'executive'), ...['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((i) => b(i, 'business'))];
    const sizes = new Map(list.map((x) => [x.workspace_id, { w: 40, d: 30, levels: 4, height: 18 }]));
    const p = layoutCampus(list, sizes);
    for (let i = 0; i < p.length; i++) for (let j = i + 1; j < p.length; j++) {
      const gapX = Math.abs(p[i].x - p[j].x) - (p[i].width + p[j].width) / 2, gapZ = Math.abs(p[i].z - p[j].z) - (p[i].depth + p[j].depth) / 2;
      assert.ok(gapX > 0 || gapZ > 0, `${p[i].workspaceId} and ${p[j].workspaceId} overlap`);
    }
  });
});

describe('the plan atlas', () => {
  test('is deterministic and its revision moves with layout and with presence, separately', () => {
    const a = buildGlassPlan([entryOf(input())]), b = buildGlassPlan([entryOf(input())]);
    assert.equal(a.revision, b.revision);
    assert.deepEqual(a.data, b.data);
    assert.equal(a.width, a.cols * a.tile);
    assert.equal(a.height, a.rows * a.tile);
    // Presence changes the revision but not the layout revision.
    const deskWithAgent = hq.floors.find((f) => f.level === 2)!.desks[0];
    const presence = presenceOf([{ agentId: 'agent_x', status: 'working', deskId: deskWithAgent.id }]);
    const registry = registryOf((f) => ({ ...f, desks: f.desks.map((d) => (d.id === deskWithAgent.id ? { ...d, agent_id: 'agent_x' } : d)) }));
    const withAgent = planInputOf(project(registry).buildings.find((x) => x.id === 'campus-01-hq')!, presence)!;
    const c = buildGlassPlan([entryOf(withAgent)]);
    assert.notEqual(c.revision, a.revision);
    assert.equal(c.layoutRevision, a.layoutRevision);
    // A moved room is a new layout.
    const moved = project(registryOf((f) => ({ ...f, rooms: f.rooms.map((r) => (r.id === 'campus-01-hq-facilities' ? { ...r, scene_x: 4 } : r)) }))).buildings.find((x) => x.id === 'campus-01-hq')!;
    const d = buildGlassPlan([entryOf(planInputOf(moved, null)!)]);
    assert.notEqual(d.layoutRevision, a.layoutRevision);
    assert.notEqual(layoutKey(moved.floors), layoutKey(hq.floors));
  });
  test('alpha codes: outside, wall bit and light', () => {
    assert.deepEqual(decodeAlpha(0), { outside: true, wall: false, light: 0 });
    for (const light of [0, 0.26, 0.8, 1]) for (const wall of [false, true]) {
      const d = decodeAlpha(encodeAlpha(light, wall));
      assert.equal(d.wall, wall);
      assert.equal(d.outside, false);
      assert.ok(Math.abs(d.light - light) < 0.01);
    }
  });
  test('a room draws as its kind and the corridor as the corridor', () => {
    const plan = buildGlassPlan([entryOf(input())]);
    const frame = planFrame(hq.floors)!;
    // The reception on floor 1 (level 1 is storey 0 of the shell): sample its middle, away from desks and walls.
    const floor = hq.floors.find((f) => f.level === 1)!;
    const storey = frame.storeys.find((s) => hq.floors[s.floorIndex].id === floor.id)!;
    const room = floor.rooms.find((r) => r.kind === 'reception')!;
    const taken = floor.desks.filter((d) => d.roomId === room.id);
    let seen = 0;
    for (let k = 0; k < 40 && seen === 0; k++) {
      const x = room.x + 0.8 + ((room.width - 1.6) * (k % 8)) / 7, z = room.z + 0.8 + ((room.depth - 1.6) * Math.floor(k / 8)) / 4;
      if (taken.some((d) => Math.abs(d.position.x - x) < 1 && Math.abs(d.position.z - z) < 1.2)) continue;
      const s = samplePlan(plan, 0, storey.n, x - frame.cx, z - frame.cz);
      assert.deepEqual([...s.rgb], [...KIND_FLOOR.reception]);
      assert.ok(Math.abs(s.light - LIGHT.vacant) < 0.01, 'a vacant room is dim');
      seen += 1;
    }
    assert.equal(seen, 1);
    const outside = samplePlan(plan, 0, storey.n, 500, 500);
    assert.equal(outside.outside, true);
    // Between rooms and the slab edge there is corridor.
    const corridor = samplePlan(plan, 0, storey.n, storey.rect.x0 + 0.5 - frame.cx, storey.rect.z0 + 0.5 - frame.cz);
    assert.deepEqual([...corridor.rgb], [...CORRIDOR]);
  });
});

describe('occupancy truth', () => {
  const frame = planFrame(hq.floors)!;
  const floorIdx = hq.floors.findIndex((f) => f.level === 2);
  const storey = frame.storeys.find((s) => s.floorIndex === floorIdx)!;
  const floor = hq.floors[floorIdx];
  const desk = floor.desks[0], other = floor.desks[1];
  const registry = registryOf((f) => ({ ...f, desks: f.desks.map((d) => (d.id === desk.id ? { ...d, agent_id: 'agent_a' } : d.id === other.id ? { ...d, agent_id: 'agent_b' } : d)) }));
  const building = project(registry).buildings.find((b) => b.id === 'campus-01-hq')!;
  const screenAt = (plan: ReturnType<typeof buildGlassPlan>, d: typeof desk) =>
    samplePlan(plan, 0, storey.n, d.position.x - 0.0 - frame.cx, d.position.z - 0.18 - frame.cz);

  test('a desk is lit only when presence says working or meeting', () => {
    const presence = presenceOf([{ agentId: 'agent_a', status: 'working' }, { agentId: 'agent_b', status: 'idle' }]);
    const plan = buildGlassPlan([entryOf(planInputOf(building, presence)!)]);
    const lit = screenAt(plan, desk), dim = screenAt(plan, other);
    assert.deepEqual([...lit.rgb], [112, 190, 255]);
    assert.equal(lit.light, 1);
    assert.deepEqual([...dim.rgb], [30, 36, 44]);
    assert.ok(dim.light < 1, "an idle desk in a lit room is a dark screen, not a lit one");
  });
  test('unknown presence and stale evidence stay dim, a meeting lights', () => {
    const none = buildGlassPlan([entryOf(planInputOf(building, null)!)]);
    assert.deepEqual([...screenAt(none, desk).rgb], [30, 36, 44]);
    const stale = buildGlassPlan([entryOf(planInputOf(building, presenceOf([{ agentId: 'agent_a', status: 'working', stale: true }]))!)]);
    assert.deepEqual([...screenAt(stale, desk).rgb], [30, 36, 44]);
    const meeting = buildGlassPlan([entryOf(planInputOf(building, presenceOf([{ agentId: 'agent_a', status: 'meeting' }]))!)]);
    assert.deepEqual([...screenAt(meeting, desk).rgb], [112, 190, 255]);
  });
  test('a figure is drawn only at a desk with an assigned agent', () => {
    const plan = buildGlassPlan([entryOf(planInputOf(building, null)!)]);
    const figureAt = (d: typeof desk) => samplePlan(plan, 0, storey.n, d.position.x - frame.cx, d.position.z + 0.7 - frame.cz);
    const seated = figureAt(desk);
    const vacant = figureAt(floor.desks[2]);
    assert.notDeepEqual([...seated.rgb], [...vacant.rgb]);
    assert.ok(!floor.desks[2].agentId);
  });
  test('a room is lit only while one of its desks is', () => {
    const lit = planInputOf(building, presenceOf([{ agentId: 'agent_a', status: 'working' }]))!;
    const room = floor.rooms.find((r) => r.id === desk.roomId)!;
    const spot = (plan: ReturnType<typeof buildGlassPlan>) => samplePlan(plan, 0, storey.n, room.x + room.width - 0.4 - frame.cx, room.z + room.depth - 0.4 - frame.cz);
    assert.ok(spot(buildGlassPlan([entryOf(lit)])).light > 0.7);
    assert.ok(spot(buildGlassPlan([entryOf(planInputOf(building, null)!)])).light < 0.3);
  });
});

describe('a facade pane maps to the room behind it', () => {
  const rows = new Map([['hq', 0]]);
  const placement = { workspaceId: 'hq', x: 200, z: -120, width: 0, depth: 0, height: 0, levels: 1, kind: 'executive' as const, icon: null };
  const plans = new Map([['hq', input()]]);
  const frame = planFrame(hq.floors)!;
  const shape = plannedDrawer(rows, plans)({ ...placement, width: frame.w, depth: frame.d }, 'executive', 0, 1)!;
  const atlas = planAtlas([{ ...placement, width: frame.w, depth: frame.d }], plans, rows)!;

  test('the shell is generated from the plans: footprint, storey pitch, tagged panes', () => {
    assert.ok(shape.triangles > 500);
    // Bounds cover the union footprint, centred on the placement.
    assert.ok(shape.bounds.min.x <= placement.x - frame.w / 2 + 0.01 && shape.bounds.max.x >= placement.x + frame.w / 2 - 0.01);
    const v = shape.vertices;
    const tagged = new Set<number>();
    for (let i = 0; i < v.length; i += VERTEX_FLOATS) if (v[i + 9] >= 2 && v[i + 1] <= frame.roof && v[i + 11] > 0) tagged.add(paneRowOf(v[i + 11]));
    assert.deepEqual([...tagged], [0]);
  });
  test('for every sampled pane the atlas texel is the room (or corridor) of that floor at that position', () => {
    const v = shape.vertices;
    let checked = 0, inRoom = 0;
    for (let i = 0; i + VERTEX_FLOATS * 3 <= v.length; i += VERTEX_FLOATS * 3) {
      if (!(v[i + 9] >= 2 && v[i + 11] > 0 && paneRowOf(v[i + 11]) === 0 && v[i + 1] <= frame.roof)) continue;
      const verts = [0, 1, 2].map((k) => i + k * VERTEX_FLOATS);
      const cx = verts.reduce((s, k) => s + v[k], 0) / 3, cy = verts.reduce((s, k) => s + v[k + 1], 0) / 3, cz = verts.reduce((s, k) => s + v[k + 2], 0) / 3;
      const nx = v[verts[0] + 3], nz = v[verts[0] + 5];
      if (Math.abs(nx) + Math.abs(nz) < 0.9) continue;
      const storey = storeyAt(atlas, 0, cy);
      assert.equal(storey, Math.floor((cy - PLINTH) / STOREY_PITCH), 'the storey of the pane is the floor at that height');
      const s = sampleBehindPane(atlas, 0, cx, cy, cz, nx, nz);
      const floor = hq.floors[frame.storeys[storey].floorIndex];
      // The same point in plan metres, looked up in the registry itself.
      const px = cx - nx * 2.8 - placement.x + frame.cx, pz = cz - nz * 2.8 - placement.z + frame.cz;
      const room = floor.rooms.find((r) => px > r.x + 0.3 && px < r.x + r.width - 0.3 && pz > r.z + 0.3 && pz < r.z + r.depth - 0.3);
      checked += 1;
      if (!room || s.wall) continue;
      inRoom += 1;
      const near = floor.desks.some((d) => d.roomId === room.id && Math.abs(d.position.x - px) < 1.4 && Math.abs(d.position.z - pz) < 1.5);
      const table = room.kind === 'meeting' && px > room.x + room.width * 0.2 && px < room.x + room.width * 0.8 && pz > room.z + room.depth * 0.28 && pz < room.z + room.depth * 0.72;
      if (!near && !table) assert.deepEqual([...s.rgb], [...(KIND_FLOOR[room.kind] ?? KIND_FLOOR.office)], `pane at ${cx.toFixed(1)},${cy.toFixed(1)},${cz.toFixed(1)} should show a ${room.kind}`);
    }
    assert.ok(checked > 40, `checked ${checked}`);
    assert.ok(inRoom > 8, `${inRoom} panes look into a room`);
  });
  test('tagging round-trips and leaves untouched panes alone', () => {
    const a = new Float32Array(VERTEX_FLOATS * 3);
    for (let k = 0; k < 3; k++) { a[k * VERTEX_FLOATS + 9] = 2.5; a[k * VERTEX_FLOATS + 11] = 0.001 + k * 0.4; a[k * VERTEX_FLOATS + 12] = 0.5; }
    const before = [...a];
    tagPaneUv(a, 5);
    for (let k = 0; k < 3; k++) assert.equal(paneRowOf(a[k * VERTEX_FLOATS + 11]), 5);
    assert.equal(paneRowOf(before[11]), -1);
    const high = new Float32Array(a.length).fill(0);
    for (let k = 0; k < 3; k++) { high[k * VERTEX_FLOATS + 1] = 90; high[k * VERTEX_FLOATS + 9] = 2.5; high[k * VERTEX_FLOATS + 11] = 0.2; }
    tagPaneUv(high, 1, 40);
    assert.equal(paneRowOf(high[11]), -1, 'a crown pane above the roof keeps the generic room');
  });
  test('each building kind is drawn on the real footprint and its heights follow the stack', () => {
    for (const kind of ['executiveHq', 'officeTower', 'adminBlock', 'retailBlock', 'tyreWorkshop', 'warehouse', 'personalVilla'] as const) {
      const a = buildPlanShell(kind, frame, { seed: 'k', row: 0 });
      assert.ok(a.height >= frame.roof, kind);
      assert.ok(a.triangles > 300 && a.triangles < 9000, `${kind} ${a.triangles}`);
      assert.deepEqual(a.footprint, { width: frame.w, depth: frame.d });
    }
  });
  test('the campus scene uses the plan shell only for planned buildings', () => {
    const b = (id: string, kind: 'executive' | 'business'): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name: id, kind, icon: null, floors: 3, accessible: true, totals: null, presence_verified_at: null });
    const list = [b('hq', 'executive'), b('plain', 'business')];
    const p = layoutCampus(list, new Map([['hq', planSize(hq.floors)!]]));
    const generic = buildCampusScene(p);
    const planned = buildCampusScene(p, { draw: plannedDrawer(plannedRows(p, plans), plans) });
    assert.notEqual(generic.buildings[0].mesh.triangles, planned.buildings[0].mesh.triangles);
    assert.equal(generic.buildings[1].mesh.triangles, planned.buildings[1].mesh.triangles, 'a building with no plan is unchanged');
    assert.equal(registryKey('w', 'b'), 'w~b');
  });
});

describe('shader source', () => {
  test('the plan chunk declares what the renderer feeds it and uses no long dashes', () => {
    for (const name of ['uPlanTex', 'uPlanRect', 'uPlanInfo', 'uPlanGrid', 'uPlanCount', 'hq_planAt']) assert.ok(PLAN_COMMON.includes(name), name);
    assert.ok(PLAN_COLOR.includes('hqEmit') && PLAN_COLOR.includes('hqPlan'));
    assert.ok(!/[–—]/.test(PLAN_COMMON + PLAN_COLOR));
  });
});
