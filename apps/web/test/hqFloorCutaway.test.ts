// Floor transitions: the cut-away scene, which floors are lifted in or out, and the easing.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { buildBuildingScene } from '../src/lib/buildingScene.ts';
import { floorLifts } from '../src/lib/hqFloorLift.ts';
import { LIFT_MS, easeLift } from '../src/lib/hqRenderTypes.ts';
import type { SceneFloor } from '../src/lib/headquartersProjection.ts';

const floor = (level: number, rooms = 2): SceneFloor => ({
  id: 'f' + level, name: 'Floor ' + level, level, bounds: { width: 40, depth: 20 },
  rooms: Array.from({ length: rooms }, (_, i) => ({ id: `r${level}-${i}`, floorId: 'f' + level, buildingId: 'b', label: 'Room ' + i, kind: 'office' as const, x: i * 12, z: 0, width: 10, depth: 8, height: 3, capacity: 4, revision: 1, deskIds: [], evidence: 'registry' as const })),
  desks: [],
});

describe('cut-away scene', () => {
  const floors = [floor(0), floor(1), floor(2), floor(3)];
  test('without cut-away nothing changes: floors above the active one are not built and there are no ghosts', () => {
    const s = buildBuildingScene(floors, { activeIndex: 1 });
    assert.equal(s.ghosts.length, 0);
  });
  test('with cut-away every floor but the active one is a ghost of its own, and the base holds only the active floor', () => {
    const s = buildBuildingScene(floors, { activeIndex: 1, cutaway: true });
    assert.deepEqual(s.ghosts.map((g) => g.floorIndex), [0, 2, 3]);
    assert.ok(s.ghosts.every((g) => g.opaque.triangles > 0 && g.y === g.floorIndex * 4.2));
    assert.ok(s.rooms.every((r) => r.floorIndex === 1), 'only the active floor has pickable rooms');
    const plain = buildBuildingScene(floors, { activeIndex: 1 });
    assert.ok(s.opaque.triangles < plain.opaque.triangles + s.ghosts[0].opaque.triangles, 'the base no longer carries the floor below');
  });
  test('ghost floors are built once and reused while their floor does not change', () => {
    const a = buildBuildingScene(floors, { activeIndex: 1, cutaway: true });
    const b = buildBuildingScene(floors, { activeIndex: 1, cutaway: true, selectedRoomId: 'r1-0' });
    assert.equal(a.ghosts[0], b.ghosts[0], 'selection on the active floor does not rebuild the others');
    const c = buildBuildingScene(floors, { activeIndex: 2, cutaway: true });
    assert.equal(c.ghosts.find((g) => g.floorIndex === 0), a.ghosts.find((g) => g.floorIndex === 0), 'moving to another floor reuses the floors that stay ghosts');
    const d = buildBuildingScene([floor(0), floor(1), floor(2), floor(3)], { activeIndex: 1, cutaway: true });
    assert.notEqual(d.ghosts[0], a.ghosts[0], 'a new floor record is built again');
  });
  test('the triangle count counts only the floors on screen', () => {
    const shown = buildBuildingScene(floors, { activeIndex: 1, cutaway: true });
    const all = buildBuildingScene(floors, { activeIndex: 1, cutaway: true, showAll: true });
    assert.ok(all.triangles > shown.triangles);
    const cut = buildBuildingScene(floors, { activeIndex: 3, cutaway: true });
    const top = buildBuildingScene(floors, { activeIndex: 0, cutaway: true });
    assert.ok(cut.triangles > top.triangles, 'the top floor shows three ghosts below it, the bottom floor none');
  });
});

describe('floor lifts', () => {
  test('floors below stay, floors above are cut away, the open floor is drawn by the building', () => {
    assert.deepEqual(floorLifts([0, 1, 2, 3], 1, false, null), { 'floor:0': { to: 1 }, 'floor:1': { to: 0, from: 0 }, 'floor:2': { to: 0 }, 'floor:3': { to: 0 } });
  });
  test('the floor just left eases from 1 whichever way the person went', () => {
    assert.deepEqual(floorLifts([0, 1, 2], 0, false, 2)['floor:2'], { to: 0, from: 1 });
    assert.deepEqual(floorLifts([0, 1, 2], 2, false, 0)['floor:0'], { to: 1, from: 1 });
  });
  test('all floors shows every floor but the open one', () => {
    const l = floorLifts([0, 1, 2], 1, true, null);
    assert.deepEqual([l['floor:0'].to, l['floor:1'].to, l['floor:2'].to], [1, 0, 1]);
  });
});

describe('easing', () => {
  test('starts at 0, ends at 1, never overshoots, is symmetric and quick enough to feel direct', () => {
    assert.equal(easeLift(0), 0);
    assert.equal(easeLift(1), 1);
    assert.equal(easeLift(0.5), 0.5);
    assert.equal(easeLift(-3), 0);
    assert.equal(easeLift(7), 1);
    let prev = 0;
    for (let i = 1; i <= 100; i++) { const v = easeLift(i / 100); assert.ok(v >= prev && v <= 1); prev = v; }
    assert.ok(LIFT_MS >= 200 && LIFT_MS <= 500);
  });
});
