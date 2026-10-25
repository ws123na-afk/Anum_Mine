import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  clampDesk, clampInto, findFreeSpot, freeDeskSpot, moveRect, nextDoorSide, nextRotation, overlaps, planView, pointInRoom,
  resizeByKey, resizeRect, rotateDeskPoint, rotateRect90, snap, withinBounds, type Rect,
} from '../src/hqBuilder/geometry.ts';

const room = (id: string, x: number, z: number, width: number, depth: number): Rect => ({ id, x, z, width, depth });

test('snap rounds to the grid step and leaves values alone when snapping is off', () => {
  assert.equal(snap(3.4, 1), 3);
  assert.equal(snap(3.6, 1), 4);
  assert.equal(snap(1.26, 0.5), 1.5);
  assert.equal(snap(1.2345, 0), 1.235);
  assert.equal(snap(-0.4, 1), -0);
});

test('rooms that only share an edge do not overlap, rooms that intersect do', () => {
  assert.equal(overlaps(room('a', 0, 0, 5, 5), room('b', 5, 0, 5, 5)), false);
  assert.equal(overlaps(room('a', 0, 0, 5, 5), room('b', 4.9, 0, 5, 5)), true);
  assert.equal(overlaps(room('a', 0, 0, 5, 5), room('b', 0, 5, 5, 5)), false);
  assert.equal(overlaps(room('a', 0, 0, 10, 10), room('b', 2, 2, 1, 1)), true);
});

test('bounds: inside, touching the edge and outside', () => {
  const floor = { width: 60, depth: 40 };
  assert.equal(withinBounds(room('a', 0, 0, 60, 40), floor), true);
  assert.equal(withinBounds(room('a', 55, 0, 6, 5), floor), false);
  assert.equal(withinBounds(room('a', -0.5, 0, 6, 5), floor), false);
  assert.equal(withinBounds(room('a', 500, 500, 6, 5), null), true);
});

test('moving snaps the new corner to the grid', () => {
  assert.deepEqual(moveRect(room('a', 2, 2, 6, 5), 3.4, -0.6, 1), room('a', 5, 1, 6, 5));
  assert.deepEqual(moveRect(room('a', 2, 2, 6, 5), 0.5, 0, 0), room('a', 2.5, 2, 6, 5));
});

test('resizing from a handle keeps the opposite edge and a one metre minimum', () => {
  const start = room('a', 10, 10, 6, 5);
  assert.deepEqual(resizeRect(start, 'e', 3.2, 0, 1), room('a', 10, 10, 9, 5));
  assert.deepEqual(resizeRect(start, 'w', -2, 0, 1), room('a', 8, 10, 8, 5));
  assert.deepEqual(resizeRect(start, 'se', 1, 1, 1), room('a', 10, 10, 7, 6));
  assert.deepEqual(resizeRect(start, 'nw', 1, 1, 1), room('a', 11, 11, 5, 4));
  const squeezed = resizeRect(start, 'e', -20, 0, 1);
  assert.equal(squeezed.width, 1);
  assert.equal(squeezed.x, 10);
  const flipped = resizeRect(start, 'n', 0, 50, 1);
  assert.equal(flipped.depth, 1);
  assert.equal(flipped.z + flipped.depth, 15);
});

test('resizing by key grows right and down, shrinks left and up, never below one metre', () => {
  const start = room('a', 0, 0, 6, 5);
  assert.equal(resizeByKey(start, 'ArrowRight', 1).width, 7);
  assert.equal(resizeByKey(start, 'ArrowLeft', 1).width, 5);
  assert.equal(resizeByKey(start, 'ArrowDown', 0.5).depth, 5.5);
  assert.equal(resizeByKey(room('a', 0, 0, 1, 1), 'ArrowLeft', 1).width, 1);
});

test('a quarter turn swaps width and depth around the centre and stays inside the floor', () => {
  const turned = rotateRect90(room('a', 10, 10, 8, 4), null);
  assert.equal(turned.width, 4);
  assert.equal(turned.depth, 8);
  assert.equal(turned.x + turned.width / 2, 14);
  assert.equal(turned.z + turned.depth / 2, 12);
  const atEdge = rotateRect90(room('a', 0, 0, 8, 2), { width: 60, depth: 40 });
  assert.equal(atEdge.x >= 0 && atEdge.z >= 0, true);
  assert.equal(withinBounds(atEdge, { width: 60, depth: 40 }), true);
  assert.equal(nextRotation(270), 0);
  assert.equal(nextDoorSide('west'), 'north');
});

test('desks turn with their room: the corner moves to the next corner clockwise', () => {
  // Room 8 wide, 4 deep. The north west corner (0, 0) becomes the north east corner of the 4 wide, 8 deep room.
  assert.deepEqual(rotateDeskPoint(0, 0, 4), { x: 4, z: 0 });
  assert.deepEqual(rotateDeskPoint(8, 0, 4), { x: 4, z: 8 });
  assert.deepEqual(rotateDeskPoint(2, 1, 4), { x: 3, z: 2 });
  const turned = rotateRect90(room('a', 0, 0, 8, 4), null);
  const moved = rotateDeskPoint(7.5, 3.5, 4);
  assert.equal(pointInRoom(turned, moved.x, moved.z), true);
});

test('desk helpers clamp into the room and find free grid spots', () => {
  assert.deepEqual(clampDesk({ width: 6, depth: 4 }, 9, -1), { x: 6, z: 0 });
  assert.deepEqual(clampDesk({ width: 6, depth: 4 }, 1.13, 1.12, 0.25), { x: 1.25, z: 1 });
  assert.equal(pointInRoom({ width: 6, depth: 4 }, 6, 4), true);
  assert.equal(pointInRoom({ width: 6, depth: 4 }, 6.1, 4), false);
  const first = freeDeskSpot({ width: 6, depth: 4 }, []);
  assert.deepEqual(first, { x: 0.75, z: 0.75 });
  assert.deepEqual(freeDeskSpot({ width: 6, depth: 4 }, [first as { x: number; z: number }]), { x: 2.25, z: 0.75 });
  assert.equal(freeDeskSpot({ width: 1, depth: 1 }, [{ x: 0.5, z: 0.5 }]), null);
});

test('free spot: beside the source, then below, then a scan, and null when the floor is full', () => {
  const floor = { width: 30, depth: 20 };
  const source = room('s', 0, 0, 6, 5);
  assert.deepEqual(findFreeSpot([source], 6, 5, floor, source), { x: 6, z: 0 });
  const blocked = [source, room('t', 6, 0, 6, 5)];
  assert.deepEqual(findFreeSpot(blocked, 6, 5, floor, source), { x: 0, z: 5 });
  assert.equal(findFreeSpot([room('big', 0, 0, 30, 20)], 6, 5, floor), null);
  assert.deepEqual(findFreeSpot([], 6, 5, null), { x: 0, z: 0 });
});

test('clampInto and planView', () => {
  assert.deepEqual(clampInto(room('a', 58, 38, 6, 5), { width: 60, depth: 40 }), room('a', 54, 35, 6, 5));
  assert.deepEqual(planView([], { width: 60, depth: 40 }), { width: 60, depth: 40 });
  assert.deepEqual(planView([room('a', 30, 10, 10, 10)], null), { width: 46, depth: 26 });
});
