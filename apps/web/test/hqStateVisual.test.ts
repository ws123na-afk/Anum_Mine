import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MeshBuilder } from '../src/lib/hqGeometry.ts';
import { FIGURE_TRIANGLE_CAP, STATION_TRIANGLE_CAP, resolveScheme, workstation } from '../src/hqAssets/interior/index.ts';
import { STATE_LOOK, lookSignature, markerParts, visualOfStatus, type VisualState } from '../src/hqAssets/interior/stateVisual.ts';
import { buildBuildingScene } from '../src/lib/buildingScene.ts';

const SERVER_STATES = ['working', 'queued', 'meeting', 'waiting_approval', 'blocked', 'idle', 'paused', 'unknown'] as const;

test('each of the eight server states maps to its own desk look', () => {
  const visuals = SERVER_STATES.map((s) => visualOfStatus(s));
  assert.equal(new Set(visuals).size, 8);
  assert.equal(new Set(visuals.map(lookSignature)).size, 8);
});

test('states are told apart by shape and pose as well as by colour', () => {
  const markers = new Set(SERVER_STATES.map((s) => STATE_LOOK[visualOfStatus(s)].marker));
  // working and idle carry no marker; the other six each have their own.
  assert.equal(markers.size, 7);
  for (const a of SERVER_STATES) for (const b of SERVER_STATES) {
    if (a >= b) continue;
    const la = STATE_LOOK[visualOfStatus(a)], lb = STATE_LOOK[visualOfStatus(b)];
    const channels = [la.screen.join() !== lb.screen.join(), (la.light?.join() ?? '') !== (lb.light?.join() ?? ''), la.pose !== lb.pose, la.marker !== lb.marker].filter(Boolean).length;
    assert.ok(channels >= 2, `${a} and ${b} differ on ${channels} channel(s)`);
  }
});

test('a stale record is drawn as unknown, never as the state it once proved', () => {
  assert.equal(visualOfStatus('working', true), 'unknown');
  assert.equal(visualOfStatus('something-new'), 'unknown');
});

test('no pose invents movement: the desk poses are all seated', () => {
  for (const key of Object.keys(STATE_LOOK) as VisualState[]) assert.ok(['typing', 'reading', 'phone', 'talking', 'leaning'].includes(STATE_LOOK[key].pose), key);
});

test('a desk draws the same way for every state within its caps, and a marker adds a few boxes only', () => {
  const triangles = (state: VisualState, seed: string | null) => {
    const b = new MeshBuilder();
    workstation(b, 0, { x: 0, z: 0, state, occupantSeed: seed }, resolveScheme());
    return b.triangleCount;
  };
  const base = triangles('idle', null);
  for (const state of Object.keys(STATE_LOOK) as VisualState[]) {
    const n = triangles(state, 'agent-1');
    assert.ok(n <= STATION_TRIANGLE_CAP + FIGURE_TRIANGLE_CAP, `${state}: ${n}`);
    const marker = markerParts(STATE_LOOK[state].marker).length;
    assert.ok(triangles(state, null) - base <= marker * 12 + 60, `${state} marker is small`);
  }
});

test('the building scene draws a different desk for every state', () => {
  const room = { id: 'r', floorId: 'f', buildingId: 'b', label: 'Room', kind: 'office', x: 0, z: 0, width: 9, depth: 6, height: 3.2, capacity: 4, revision: 1, deskIds: ['d0'], evidence: 'registry' as const };
  const floor = { id: 'f', name: 'F', level: 0, rooms: [room], desks: [{ id: 'd0', roomId: 'r', label: 'Desk 1', position: { x: 4, y: 0.78, z: 3 }, agentId: 'a1', agent: null, assignmentVisible: true }], bounds: { width: 20, depth: 16 } };
  const seen = new Set<string>();
  for (const state of Object.keys(STATE_LOOK) as VisualState[]) {
    const scene = buildBuildingScene([floor as never], { activeIndex: 0, deskState: new Map([['d0', state]]), people: new Map([['d0', 'a']]) });
    seen.add(Array.from(scene.opaque.vertices).join(','));
  }
  assert.equal(seen.size, Object.keys(STATE_LOOK).length);
});
