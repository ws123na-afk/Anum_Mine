// Campus look (docs/ANUM_MINE_HQ_CENTER_PLAZA.md "Buildings face the plaza", docs/ANUM_MINE_HQ_GLASS_PLAN.md, docs/ANUM_MINE_HQ_BUILDING_LOOK.md):
// the facing maths, the turned glass lookup, the cross-dissolve weight, seeded planting and the bevel geometry.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';
import type { Registry } from '../src/lib/headquartersProjection.ts';
import { projectHeadquarters } from '../src/lib/headquartersProjection.ts';
import { facingAngle, facingTurn, footprintRect, frontOf, leanOf, nearestYaw, turnAngle, turnRect, turnXZ, unturnXZ, worldSize, type QuarterTurn } from '../src/lib/campusFacing.ts';
import { layoutCampus, lotBlocksCentre, lotOf, marginsOf, type CampusBuilding, type Placement } from '../src/lib/campusModel.ts';
import { buildCampusScene } from '../src/lib/campusScene.ts';
import { drawExterior } from '../src/lib/campusExteriors.ts';
import { archetypeOf } from '../src/lib/campusBuildings.ts';
import { MAT, MeshBuilder, VERTEX_FLOATS } from '../src/lib/hqGeometry.ts';
import { planFrame, planInputOf, PLINTH, STOREY_PITCH } from '../src/lib/hqPlanFrame.ts';
import { KIND_FLOOR, sampleBehindPane, storeyAt } from '../src/lib/hqPlanAtlas.ts';
import { plannedDrawer, planAtlas } from '../src/lib/hqPlanKit.ts';
import { paneRowOf } from '../src/hqAssets/exterior/planShell.ts';
import { PLAN_COMMON, PLAN_FADE_MS, canDissolve, planFade, planKeyOf } from '../src/lib/hqGlassShader.ts';
import { PLANT_TINTS, PLANT_WEIGHTS, plantAt, type PlantKind } from '../src/hqAssets/environment/vegetation.ts';
import { stamp, makeTemplate } from '../src/hqAssets/environment/common.ts';
import { bevelBand, chamferedRing, cornerBevels } from '../src/hqAssets/exterior/bevel.ts';

const b = (id: string, over: Partial<CampusBuilding> = {}): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name: id, kind: 'business', icon: null, floors: 2, accessible: true, totals: null, presence_verified_at: null, ...over });
const campus = (n: number): CampusBuilding[] => [b('exec', { kind: 'executive', name: 'Executive' }), ...Array.from({ length: n }, (_, i) => b(`ws-${i}`, { name: `Building ${i}`, kind: (['business', 'department', 'personal', 'career'] as const)[i % 4] }))];
const dot = (a: readonly number[], c: readonly number[]) => a[0] * c[0] + a[1] * c[1];

describe('buildings face the plaza', () => {
  test('the quarter turn whose front points most directly at the centre', () => {
    assert.equal(facingTurn(0, -50), 0, 'north of the plaza faces +z');
    assert.equal(facingTurn(0, 50), 2, 'south faces -z');
    assert.equal(facingTurn(50, 0), 1, 'east faces -x');
    assert.equal(facingTurn(-50, 0), 3, 'west faces +x');
    assert.equal(facingTurn(0, 0), 0);
    // For any position the chosen front is the best of the four and within 45 degrees of the exact direction.
    for (let k = 0; k < 360; k += 7) {
      const a = (k * Math.PI) / 180, x = Math.cos(a) * 60, z = Math.sin(a) * 60;
      const t = facingTurn(x, z), to = [-x / 60, -z / 60];
      const best = Math.max(...([0, 1, 2, 3] as const).map((q) => dot(frontOf(q), to)));
      assert.ok(Math.abs(dot(frontOf(t), to) - best) < 1e-9, `angle ${k}`);
      const off = Math.abs(((facingAngle(x, z) - turnAngle(t) + Math.PI * 3) % (Math.PI * 2)) - Math.PI);
      assert.ok(off <= Math.PI / 4 + 1e-9, `angle ${k} is ${off} off`);
    }
  });

  test('turning is a rotation, its inverse undoes it, and rectangles and sizes follow', () => {
    for (const t of [0, 1, 2, 3]) {
      const [x, z] = turnXZ(3, -7, t);
      assert.deepEqual(unturnXZ(x, z, t).map((v) => Math.round(v * 1e9) / 1e9), [3, -7]);
      assert.ok(Math.abs(Math.hypot(x, z) - Math.hypot(3, -7)) < 1e-9);
      // The local front (+z) turns to frontOf(t).
      assert.deepEqual(turnXZ(0, 1, t).map((v) => Math.round(v)), [...frontOf(t as QuarterTurn)]);
    }
    assert.deepEqual(turnRect({ x0: -10, z0: -4, x1: 10, z1: 4 }, 1, 100, 0), { x0: 96, z0: -10, x1: 104, z1: 10 });
    assert.deepEqual(worldSize({ width: 20, depth: 8, turn: 3 }), { w: 8, d: 20 });
    assert.deepEqual(footprintRect({ x: 0, z: 0, width: 20, depth: 8, turn: 2 }), { x0: -10, z0: -4, x1: 10, z1: 4 });
    // The front garden of a lot lies on the side that faces the plaza.
    const east = lotOf({ x: 80, z: 0, width: 17, depth: 14, turn: 1 });
    assert.equal(80 - east.x0, 7 + 16, 'front garden toward the centre (-x)');
    assert.equal(east.x1 - 80, 7 + 6, 'back margin away from it');
  });

  test('a laid out campus: every door, sign and front looks at the fountain, lots never overlap', () => {
    for (const n of [3, 8, 17]) {
      const places = layoutCampus(campus(n));
      const lots = places.map((p) => lotOf(p));
      places.forEach((p, i) => {
        assert.equal(p.turn, facingTurn(p.x, p.z), p.workspaceId);
        const shape = drawExterior(p, archetypeOf(p), i, places.length);
        // The door stands between the building and the plaza.
        assert.ok(dot([shape.door.x - p.x, shape.door.z - p.z], [-p.x, -p.z]) > 0, `${p.workspaceId} door faces the plaza`);
        assert.ok(Math.hypot(shape.door.x, shape.door.z) < Math.hypot(p.x, p.z), `${p.workspaceId} door is nearer the centre`);
        // The pick box of the campus scene covers the turned footprint.
        const f = footprintRect(p);
        assert.ok(shape.box.min.x <= f.x0 + 0.01 && shape.box.max.x >= f.x1 - 0.01 && shape.box.min.z <= f.z0 + 0.01 && shape.box.max.z >= f.z1 - 0.01);
        for (let j = 0; j < i; j++) {
          const a = lots[i], c = lots[j];
          assert.ok(!(a.x0 < c.x1 && a.x1 > c.x0 && a.z0 < c.z1 && a.z1 > c.z0), `${p.workspaceId} overlaps ${places[j].workspaceId}`);
        }
      });
      assert.equal(places[0].turn, 0, 'the executive building keeps the north slot and looks south at the fountain');
    }
  });

  test('the campus scene is deterministic and its pick boxes and walkways follow the turn', () => {
    const places = layoutCampus(campus(8));
    const one = buildCampusScene(places), two = buildCampusScene(places);
    assert.equal(one.triangles, two.triangles);
    assert.deepEqual([...one.scenery.vertices.slice(0, 4000)], [...two.scenery.vertices.slice(0, 4000)]);
    one.buildings.forEach((bd, i) => {
      const f = footprintRect(places[i]);
      assert.ok(Math.abs(bd.box.min.x - (f.x0 - 1.2)) < 1e-9 && Math.abs(bd.box.max.z - (f.z1 + 1.2)) < 1e-9, bd.workspaceId);
      const spoke = one.plaza.spokes.find((s) => s.id === bd.workspaceId);
      assert.ok(spoke, `${bd.workspaceId} has a walkway`);
    });
  });

  test('with its lean every front, door and sign points straight at the fountain (within 10 degrees) for 2, 4, 8 and 16 buildings', () => {
    const TOLERANCE = (10 * Math.PI) / 180;
    const off = (a: number, c: number) => Math.abs(Math.atan2(Math.sin(a - c), Math.cos(a - c)));
    // The lean itself: on a diagonal it makes up the 45 degrees the quarter turn leaves, and it is never more than that.
    for (let k = 0; k < 360; k += 5) {
      const a = (k * Math.PI) / 180, x = Math.cos(a) * 70, z = Math.sin(a) * 70, t = facingTurn(x, z), yaw = leanOf(x, z, t);
      assert.ok(Math.abs(yaw) <= Math.PI / 4 + 1e-9, `angle ${k}: lean ${yaw}`);
      assert.ok(off(turnAngle(t) + yaw, facingAngle(x, z)) < 1e-9, `angle ${k}: quarter turn plus lean is the exact heading`);
      // A turned and leaned point and its inverse.
      const [wx, wz] = turnXZ(3, -7, t, yaw);
      assert.deepEqual(unturnXZ(wx, wz, t, yaw).map((v) => Math.round(v * 1e9) / 1e9), [3, -7]);
    }
    for (const n of [2, 4, 8, 16]) {
      const places = layoutCampus(campus(n - 1));
      assert.equal(places.length, n);
      const scene = buildCampusScene(places);
      const lots = places.map((p) => lotOf(p, marginsOf(p.kind, false)));
      places.forEach((p, i) => {
        // The building's front (its entrance side: door, sign, forecourt) points at the plaza centre: the front's outward direction is
        // the direction from the building's middle to the fountain, within 10 degrees (the quarter turn alone allowed 45).
        const front = turnAngle(p.turn) + (p.yaw ?? 0);
        assert.ok(off(front, facingAngle(p.x, p.z)) <= TOLERANCE, `${n}: ${p.workspaceId} front is ${off(front, facingAngle(p.x, p.z))} rad off`);
        // Standing in the doorway and looking straight out, the fountain is ahead too. A centred door sees it within 10 degrees; a side
        // entrance (the warehouse's office annex, about 9 m off the middle) within 12 on the smallest campus, where it stands nearest.
        const door = scene.buildings[i].door;
        const [doorSide] = unturnXZ(door.x - p.x, door.z - p.z, p.turn, p.yaw ?? 0);
        const doorLimit = Math.abs(doorSide) < 1 ? TOLERANCE : (12 * Math.PI) / 180;
        assert.ok(off(front, facingAngle(door.x, door.z)) <= doorLimit, `${n}: ${p.workspaceId} door looks ${off(front, facingAngle(door.x, door.z))} rad off the fountain`);
        assert.ok(dot([door.x - p.x, door.z - p.z], [Math.sin(front), Math.cos(front)]) > 0, `${n}: ${p.workspaceId} door is on the front`);
        const shape = drawExterior(p, archetypeOf(p), i, places.length);
        assert.ok(Math.hypot(shape.door.x, shape.door.z) < Math.hypot(p.x, p.z), `${n}: ${p.workspaceId} door is nearer the centre`);
        // The label stays over the building.
        const f = footprintRect(p);
        assert.ok(shape.anchor.x >= f.x0 - 0.5 && shape.anchor.x <= f.x1 + 0.5 && shape.anchor.z >= f.z0 - 0.5 && shape.anchor.z <= f.z1 + 0.5, `${n}: ${p.workspaceId} label`);
        // Nothing stands on the plaza, the roundabout or the avenues, and no two lots (the boxes round the leaned lots) overlap.
        assert.ok(!lotBlocksCentre(lots[i]), `${n}: ${p.workspaceId} is on the plaza or an avenue`);
        for (let j = 0; j < i; j++) {
          const a = lots[i], c = lots[j];
          assert.ok(!(a.x0 < c.x1 && a.x1 > c.x0 && a.z0 < c.z1 && a.z1 > c.z0), `${n}: ${p.workspaceId} overlaps ${places[j].workspaceId}`);
        }
        // A walkway reaches its door and crosses no other building.
        const spoke = scene.plaza.spokes.find((sp) => sp.id === p.workspaceId);
        assert.ok(spoke && spoke.points.length >= 2, `${n}: ${p.workspaceId} has a walkway`);
        for (const [x, z] of spoke!.points) for (const other of places) {
          if (other === p) continue;
          const [lx, lz] = unturnXZ(x - other.x, z - other.z, other.turn, other.yaw ?? 0);
          assert.ok(!(Math.abs(lx) < other.width / 2 && Math.abs(lz) < other.depth / 2), `${n}: the walkway of ${p.workspaceId} crosses ${other.workspaceId}`);
        }
      });
      assert.ok(places.some((p) => Math.abs(p.yaw ?? 0) > 0.05) || n < 4, `${n}: a diagonal building is leaned`);
    }
  });

  test('the focus camera turns the short way round to the front', () => {
    assert.equal(nearestYaw(350, 10), 370);
    assert.equal(nearestYaw(10, 350), -10);
    assert.equal(Math.abs(nearestYaw(0, 180)), 180);
    assert.equal(nearestYaw(725, 0), 720);
  });
});

type Fixture = { buildings: { id: string; name: string }[]; floors: unknown[]; rooms: Record<string, unknown>[]; desks: Record<string, unknown>[] };
const fx = JSON.parse(readFileSync(new URL('./fixtures/starterCampus.json', import.meta.url), 'utf8')) as Fixture;
const registry = {
  buildings: fx.buildings.map((x) => ({ ...x, revision: 1, timezone: 'Asia/Dubai' })), floors: (fx.floors as object[]).map((x) => ({ ...x, revision: 1 })),
  rooms: fx.rooms.map((r) => ({ ...r, revision: 1 })), desks: fx.desks.map((d) => ({ ...d, revision: 1 })),
} as unknown as Registry;
const hq = projectHeadquarters({ tenantId: 't', workspaceId: 'w' }, registry, []).buildings.find((x) => x.id === 'campus-01-hq')!;

describe('the glass turns with the building', () => {
  const frame = planFrame(hq.floors)!;
  const plans = new Map([['hq', planInputOf(hq, null)!]]);
  const rows = new Map([['hq', 0]]);
  for (const [turn, yaw] of [[0, 0], [1, 0], [2, 0], [3, 0], [1, 0.6], [2, -0.4]] as const) {
    test(`quarter turn ${turn}${yaw ? `, leaned ${yaw} rad` : ''}: every pane looks into the room that sits behind it on the plan`, () => {
      const p: Placement = { workspaceId: 'hq', x: 140, z: 60, width: frame.w, depth: frame.d, height: 0, levels: 1, kind: 'executive', icon: null, turn, ...(yaw ? { yaw } : {}) };
      const shape = plannedDrawer(rows, plans)(p, 'executive', 0, 1)!;
      const atlas = planAtlas([p], plans, rows)!;
      assert.equal(atlas.buildings[0].turn, turn);
      const v = shape.vertices;
      let checked = 0, matched = 0;
      for (let i = 0; i + VERTEX_FLOATS * 3 <= v.length; i += VERTEX_FLOATS * 3) {
        if (!(v[i + 9] >= 2 && v[i + 11] > 0 && paneRowOf(v[i + 11]) === 0 && v[i + 1] <= frame.roof)) continue;
        const ks = [0, 1, 2].map((k) => i + k * VERTEX_FLOATS);
        const cx = ks.reduce((s, k) => s + v[k], 0) / 3, cy = ks.reduce((s, k) => s + v[k + 1], 0) / 3, cz = ks.reduce((s, k) => s + v[k + 2], 0) / 3;
        const nx = v[ks[0] + 3], nz = v[ks[0] + 5];
        if (Math.abs(nx) + Math.abs(nz) < 0.9) continue;
        const storey = storeyAt(atlas, 0, cy);
        assert.equal(storey, Math.floor((cy - PLINTH) / STOREY_PITCH));
        const s = sampleBehindPane(atlas, 0, cx, cy, cz, nx, nz);
        // The same point in plan metres: back into the building's own frame, then onto the plan.
        const [lx, lz] = unturnXZ(cx - nx * 2.8 - p.x, cz - nz * 2.8 - p.z, turn, yaw);
        const px = lx + frame.cx, pz = lz + frame.cz;
        const floor = hq.floors[frame.storeys[storey].floorIndex];
        const room = floor.rooms.find((r) => px > r.x + 0.3 && px < r.x + r.width - 0.3 && pz > r.z + 0.3 && pz < r.z + r.depth - 0.3);
        checked += 1;
        if (!room || s.wall) continue;
        const near = floor.desks.some((d) => d.roomId === room.id && Math.abs(d.position.x - px) < 1.4 && Math.abs(d.position.z - pz) < 1.5);
        const table = room.kind === 'meeting' && px > room.x + room.width * 0.2 && px < room.x + room.width * 0.8 && pz > room.z + room.depth * 0.28 && pz < room.z + room.depth * 0.72;
        if (near || table) continue;
        assert.deepEqual([...s.rgb], [...(KIND_FLOOR[room.kind] ?? KIND_FLOOR.office)]);
        matched += 1;
      }
      assert.ok(checked > 40, `checked ${checked}`);
      assert.ok(matched > 8, `matched ${matched}`);
    });
  }

  test('the shader undoes the turn and dissolves from the previous atlas', () => {
    for (const name of ['uPlanPrev', 'uPlanMix', 'uPlanInfo[bi].w']) assert.ok(PLAN_COMMON.includes(name), name);
  });

  test('the shader undoes the quarter turns and the lean with one angle, the same way the plan sampler does', () => {
    assert.ok(PLAN_COMMON.includes('vec2 l = vec2(d.x * hc - d.y * hs, d.x * hs + d.y * hc);'));
    for (const turn of [0, 1, 2, 3]) for (const yaw of [0, 0.5, -0.7]) {
      const ha = -(turn * Math.PI) / 2 + yaw, hc = Math.cos(ha), hs = Math.sin(ha);
      for (const [dx, dz] of [[4, -2], [-3, 7], [0.5, 0.25]]) {
        const [ex, ez] = unturnXZ(dx, dz, turn, yaw);
        assert.ok(Math.abs(dx * hc - dz * hs - ex) < 1e-9 && Math.abs(dx * hs + dz * hc - ez) < 1e-9, `turn ${turn} lean ${yaw}`);
      }
    }
  });

  test('cross-dissolve: eased from 0 to 1, at once under reduced motion or with nothing to dissolve from', () => {
    assert.equal(planFade(0, false, true), 0);
    assert.equal(planFade(PLAN_FADE_MS / 2, false, true), 0.5);
    assert.equal(planFade(PLAN_FADE_MS, false, true), 1);
    assert.equal(planFade(PLAN_FADE_MS * 3, false, true), 1);
    assert.equal(planFade(0, true, true), 1, 'reduced motion: no fade');
    assert.equal(planFade(0, false, false), 1, 'first atlas: no fade');
    let last = -1;
    for (let t = 0; t <= PLAN_FADE_MS; t += 15) { const k = planFade(t, false, true); assert.ok(k >= last); last = k; }
    assert.ok(PLAN_FADE_MS > 0 && PLAN_FADE_MS <= 600, 'a short fade');
  });

  test('only an occupancy change dissolves; a new layout or a moved building swaps at once', () => {
    const base = { layoutRevision: 'a', width: 256, height: 128, buildings: [{ origin: [1, 2], turn: 1, size: [20, 10], storeys: 2, baseY: 0.9, pitch: 4.2 }], cols: 2, rows: 1 };
    const k = planKeyOf(base);
    assert.ok(canDissolve(k, planKeyOf({ ...base })));
    assert.ok(!canDissolve(null, k));
    assert.ok(!canDissolve(k, planKeyOf({ ...base, layoutRevision: 'b' })));
    assert.ok(!canDissolve(k, planKeyOf({ ...base, buildings: [{ ...base.buildings[0], turn: 2 }] })));
    assert.ok(!canDissolve(k, planKeyOf({ ...base, width: 512 })));
  });
});

describe('planting variety', () => {
  test('a pick depends only on the seed and the index', () => {
    for (let i = 0; i < 50; i++) assert.deepEqual(plantAt('s', i), plantAt('s', i));
    assert.notDeepEqual(Array.from({ length: 20 }, (_, i) => plantAt('s', i).kind), Array.from({ length: 20 }, (_, i) => plantAt('t', i).kind));
  });

  test('every kind, several species, sizes and tints show up in roughly their weights', () => {
    const picks = Array.from({ length: 800 }, (_, i) => plantAt('anum-mine-planting', i));
    const count = (k: PlantKind) => picks.filter((p) => p.kind === k).length / picks.length;
    for (const k of Object.keys(PLANT_WEIGHTS) as PlantKind[]) assert.ok(Math.abs(count(k) - PLANT_WEIGHTS[k]) < 0.06, `${k} ${count(k)}`);
    assert.ok(new Set(picks.filter((p) => p.kind === 'tree').map((p) => p.species)).size >= 6);
    assert.equal(new Set(picks.map((p) => p.tint)).size, PLANT_TINTS.length);
    const sizes = picks.map((p) => p.scale);
    assert.ok(Math.max(...sizes) - Math.min(...sizes) > 0.5);
    assert.ok(picks.every((p) => (p.kind === 'tree') === (p.species !== null)));
  });

  test('a tint changes the leaves only, never bark or other surfaces', () => {
    const t = makeTemplate('t', (m) => { m.box(0, 0, 0, 1, 1, 1, [0.5, 0.5, 0.5], { shade: false }); m.withMat(MAT.foliage, () => m.box(0, 1, 0, 1, 1, 1, [0.5, 0.5, 0.5], { shade: false })); });
    const out = new MeshBuilder();
    stamp(out, t, { x: 0, z: 0, tint: [1.2, 1, 0.5] });
    const v = out.toFloat32();
    for (let i = 0; i < v.length; i += VERTEX_FLOATS) {
      if (v[i + 10] === MAT.foliage) assert.deepEqual([v[i + 6], v[i + 7], v[i + 8]].map((c) => Math.round(c * 100) / 100), [0.6, 0.5, 0.25]);
      else assert.deepEqual([v[i + 6], v[i + 7], v[i + 8]].map((c) => Math.round(c * 100) / 100), [0.5, 0.5, 0.5]);
    }
  });
});

const n0 = (f: { c: number[]; n: number[] }, p: readonly [number, number]) => f.n[0] * (p[0] - f.c[0]) + f.n[2] * (p[1] - f.c[2]);

describe('bevels', () => {
  const normals = (m: MeshBuilder) => {
    const v = m.toFloat32(), out: Array<{ c: number[]; n: number[] }> = [];
    for (let i = 0; i < v.length; i += VERTEX_FLOATS * 3) {
      const c = [0, 1, 2].map((a) => (v[i + a] + v[i + VERTEX_FLOATS + a] + v[i + 2 * VERTEX_FLOATS + a]) / 3);
      out.push({ c, n: [v[i + 3], v[i + 4], v[i + 5]] });
    }
    return out;
  };

  test('a bevelled band: 38 triangles, every face looks out or up, the chamfers slope', () => {
    const m = new MeshBuilder();
    bevelBand(m, -5, -3, 10, 6, 12, 0.5, [0.8, 0.8, 0.8]);
    assert.equal(m.triangleCount, 38);
    const faces = normals(m);
    for (const { c, n } of faces) assert.ok(n[1] > 0.99 || n[0] * c[0] + n[2] * c[2] > 0, `face at ${c.map((x) => x.toFixed(2))} looks in`);
    assert.ok(faces.some(({ n }) => n[1] > 0.3 && n[1] < 0.95), 'a sloping chamfer');
    const bb = m.bounds();
    assert.deepEqual([bb.min.x, bb.max.x, bb.min.y, bb.max.y], [-5, 5, 12, 12.5]);
  });

  test('corner beads: 24 triangles that stand outside the block and cover each corner', () => {
    const m = new MeshBuilder();
    cornerBevels(m, 0, 0, 8, 4, 0, 6, [0.7, 0.7, 0.7]);
    assert.equal(m.triangleCount, 24);
    const faces = normals(m);
    assert.ok(faces.every(({ n }) => Math.abs(n[1]) < 1e-6), 'upright faces');
    // Each corner's 45 degree face looks out across the corner, away from the block's middle.
    const diagonal = faces.filter(({ n }) => Math.abs(Math.abs(n[0]) - Math.SQRT1_2) < 1e-6);
    assert.equal(diagonal.length, 8);
    for (const { c, n } of diagonal) assert.ok(n[0] * (c[0] - 4) > 0 && n[2] * (c[2] - 2) > 0);
    // And it passes outside the sharp corner (0, 0): the corner lies behind the face.
    const first = diagonal.find(({ c }) => c[0] < 1 && c[2] < 1)!;
    assert.ok(n0(first, [0, 0]) < 0);
    // The 45 degree face passes outside the sharp corner, so the corner never pokes through.
    const ring = chamferedRing(0, 0, 8, 4, 1);
    assert.equal(ring.length, 8);
    assert.deepEqual(ring[0], [1, 0]);
    const inner = chamferedRing(0, 0, 8, 4, 1, 0.2);
    // The cut edges of the inset ring stay parallel to the outer ones.
    const slope = (r: ReadonlyArray<readonly [number, number]>) => (r[2][1] - r[1][1]) / (r[2][0] - r[1][0]);
    assert.ok(Math.abs(slope(ring) - slope(inner)) < 1e-9);
  });
});
