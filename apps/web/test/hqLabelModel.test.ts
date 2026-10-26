import assert from 'node:assert/strict';
import { test } from 'node:test';
import { floorExtent, floorNameParts, floorShowsLabels, floorTagSpecs, FLOOR_PITCH_M, roomAnchor, roomLabelSpecs, roomSubtitle, slabsAbove, SLAB_M, STACKED_LABEL_Y } from '../src/lib/hqLabelModel.ts';
import { isOccluded, layoutLabels, segmentHitsBox, type LabelInput } from '../src/lib/hqLabels.ts';
import { campusCamera } from '../src/lib/hqCamera.ts';
import { viewProjection } from '../src/lib/hqMath.ts';
import { cameraEye } from '../src/lib/headquartersSpatial.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { FLOOR_PITCH, WALL_HEIGHT } from '../src/lib/buildingScene.ts';

const room = (id: string, x: number, z: number, extra: Record<string, unknown> = {}) => ({ id, label: id, kind: 'office' as const, x, z, width: 8, depth: 6, teamDepartment: null, ...extra });
const floors = [
  { id: 'f0', rooms: [room('a', 0, 0), room('b', 10, 0, { teamDepartment: 'sales' })] },
  { id: 'f1', rooms: [room('c', 0, 0, { kind: 'meeting' })] },
  { id: 'f2', rooms: [room('d', 0, 0)] },
];

test('the model uses the building scene floor pitch', () => assert.equal(FLOOR_PITCH_M, FLOOR_PITCH));

test('the room subtitle states registry facts only: kind, desks, a known department', () => {
  setLocale('en');
  assert.equal(roomSubtitle({ kind: 'office', teamDepartment: 'sales' }, 6), 'Office · 6 desks · Sales');
  assert.equal(roomSubtitle({ kind: 'meeting', teamDepartment: null }, 0), 'Meeting room');
  assert.equal(roomSubtitle({ kind: 'office', teamDepartment: 'one_off_unknown' }, 1), 'Office · 1 desk', 'a department the catalogue does not know is left out, never guessed');
  setLocale('ar');
  assert.match(roomSubtitle({ kind: 'office', teamDepartment: null }, 3), /3/);
  setLocale('en');
});

test('single mode labels only the active floor, hung above the room at that floor height', () => {
  const specs = roomLabelSpecs({ floors, activeIndex: 1, mode: 'single', selectedRoomId: null, wallHeight: WALL_HEIGHT });
  assert.deepEqual(specs.map((s) => s.roomId), ['c']);
  assert.equal(specs[0].anchor.y, FLOOR_PITCH * 1 + WALL_HEIGHT + 0.5);
  assert.equal(specs[0].anchor.x, 4);
  assert.deepEqual(specs[0].occluders, [], 'a single floor has nothing above it');
  assert.equal(floorShowsLabels('single', 0, 1), false);
  assert.equal(floorShowsLabels('single', 1, 1), true);
  assert.equal(floorShowsLabels('stacked', 0, 1), true);
});

test('stacked mode labels every floor inside the floor, below the slab above, and lists the slabs that can hide it', () => {
  const specs = roomLabelSpecs({ floors, activeIndex: 0, mode: 'stacked', selectedRoomId: null, wallHeight: WALL_HEIGHT });
  assert.equal(specs.length, 4);
  for (const s of specs) {
    assert.equal(s.anchor.y, s.floor * FLOOR_PITCH + STACKED_LABEL_Y);
    assert.ok(s.anchor.y < (s.floor + 1) * FLOOR_PITCH - SLAB_M, 'the label hangs under the slab of the floor above');
    assert.equal(s.occluders.length, floors.length - 1 - s.floor, 'one slab per floor above');
  }
  assert.deepEqual(specs.filter((s) => s.floor === 2).map((s) => s.occluders.length), [0], 'the top floor has no slab over it');
});

test('a slab above hides a label behind it and leaves one in front of it', () => {
  const above = slabsAbove(floors)[0];
  const anchor = roomAnchor(floors[0].rooms[0], 0, 'stacked', WALL_HEIGHT);
  const high = { x: 4, y: 40, z: 3 };
  assert.equal(isOccluded(high, anchor, above), true, 'looking straight down through the floors above');
  const front = { x: 4, y: 3, z: -40 };
  assert.equal(isOccluded(front, anchor, above), false, 'looking in from the open front under the slab');
  assert.equal(isOccluded(null, anchor, above), false, 'no eye, no occlusion');
});

test('segmentHitsBox is a real slab test', () => {
  const box = { min: { x: 0, y: 0, z: 0 }, max: { x: 2, y: 2, z: 2 } };
  assert.equal(segmentHitsBox({ x: -1, y: 1, z: 1 }, { x: 3, y: 1, z: 1 }, box), true);
  assert.equal(segmentHitsBox({ x: -1, y: 5, z: 1 }, { x: 3, y: 5, z: 1 }, box), false);
  assert.equal(segmentHitsBox({ x: -1, y: 1, z: 1 }, { x: 1, y: 1, z: 1 }, box), true, 'ends inside');
  assert.equal(segmentHitsBox({ x: -3, y: 1, z: 1 }, { x: -1, y: 1, z: 1 }, box), false, 'stops short');
});

test('floor tags sit outside the floor on the side nearest the eye', () => {
  const tags = floorTagSpecs({ floors, activeIndex: 1 });
  assert.equal(tags.length, 3);
  assert.equal(tags[1].priority, 0);
  const e = floorExtent(floors[1])!;
  for (const a of [tags[1].anchor, ...tags[1].alternatives]) {
    const outside = a.x < e.x0 || a.x > e.x1 || a.z < e.z0 || a.z > e.z1;
    assert.ok(outside, 'no slab can cover a tag that hangs beyond the floor edge');
  }
  const input: LabelInput = { id: tags[0].id, anchor: tags[0].anchor, alternatives: tags[0].alternatives, tiers: ['full'] };
  const vp = viewProjection(campusCamera(60), 1.6);
  const eye = { x: 5, y: 10, z: 200 };
  const out = layoutLabels([input], vp, 1200, 700, () => ({ w: 100, h: 40 }), 0, { eye });
  assert.ok(out[0].id === tags[0].id);
});

test('floor names split at the first colon, nothing is invented', () => {
  assert.deepEqual(floorNameParts('Ground floor: E-commerce, Retail'), { short: 'Ground floor', rest: 'E-commerce, Retail' });
  assert.deepEqual(floorNameParts('Rooftop'), { short: 'Rooftop', rest: null });
  assert.deepEqual(floorNameParts('Basement:'), { short: 'Basement', rest: null });
});

const vp = viewProjection(campusCamera(60), 1.6);
const sizes: Record<string, { w: number; h: number }> = { full: { w: 180, h: 46 }, compact: { w: 110, h: 36 }, dot: { w: 26, h: 26 } };
const sizeOf = (_id: string, d: 'full' | 'compact' | 'dot') => sizes[d];

test('density: crowded room labels give up detail before they overlap, and 60 rooms never overlap', () => {
  const labels: LabelInput[] = Array.from({ length: 60 }, (_, i) => ({ id: 'r' + i, anchor: { x: (i % 10) * 3 - 14, y: 4, z: Math.floor(i / 10) * 3 }, priority: 1, tiers: ['full', 'compact', 'dot'] }));
  const out = layoutLabels(labels, vp, 1200, 700, sizeOf, 60);
  const shown = out.filter((p) => p.shown);
  assert.ok(shown.length > 20, 'most labels still show');
  const densities = new Set(shown.map((p) => p.density));
  assert.ok(densities.size >= 2, 'more than one density is in use: ' + [...densities].join(','));
  const boxes = shown.map((p) => ({ x0: p.x - sizes[p.density].w / 2, x1: p.x + sizes[p.density].w / 2, y0: p.y - sizes[p.density].h, y1: p.y }));
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j];
    assert.ok(!(a.x0 < b.x1 - 2 && a.x1 > b.x0 + 2 && a.y0 < b.y1 - 2 && a.y1 > b.y0 + 2), `labels ${i} and ${j} overlap`);
  }
});

test('density: a lone label is a full card, the selected one stays full and in place, a seated agent never moves off its desk', () => {
  const alone = layoutLabels([{ id: 'a', anchor: { x: 0, y: 4, z: 0 }, tiers: ['full', 'compact', 'dot'] }], vp, 1200, 700, sizeOf);
  assert.equal(alone[0].density, 'full');
  const at = { x: 0, y: 4, z: 0 };
  const out = layoutLabels([
    { id: 'room', anchor: at, priority: 1, tiers: ['full', 'compact', 'dot'] },
    { id: 'selected', anchor: at, priority: 0, tiers: ['full'] },
    { id: 'agent', anchor: at, priority: 2, tiers: ['full', 'compact', 'dot'] },
  ], vp, 1200, 700, sizeOf);
  const [room, selected, agent] = out;
  assert.equal(selected.shown && selected.density === 'full', true);
  assert.equal(selected.shifted, 0);
  assert.ok(!agent.shown || agent.shifted === 0, 'the agent either sits at its anchor or gives way');
  assert.ok(room.shown, 'a room card is a way into the scene and stays');
  assert.ok(agent.shown || agent.crowded === true, 'an agent card with no spot is crowded (kept for the keyboard), not removed');
});

test('occluded labels are hidden and flagged, labels without occluders are not', () => {
  const above = slabsAbove(floors)[0];
  const anchor = roomAnchor(floors[0].rooms[0], 0, 'stacked', WALL_HEIGHT);
  const eye = cameraEye({ ...campusCamera(60), target: { x: 4, y: 0, z: 3 }, distance: 60, pitch: 80, yaw: 0 });
  const out = layoutLabels([{ id: 'low', anchor, occluders: above, tiers: ['full'] }, { id: 'free', anchor, tiers: ['full'] }], viewProjection({ ...campusCamera(60), target: { x: 4, y: 0, z: 3 }, distance: 60, pitch: 80, yaw: 0 }, 1.6), 1200, 700, sizeOf, 0, { eye });
  assert.equal(out[0].shown, false);
  assert.equal(out[0].occluded, true);
  assert.equal(out[1].occluded, false);
});
