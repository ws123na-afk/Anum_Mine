import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { OfficeLayoutDiff, OfficeLayoutDiffSummary, OfficeLayoutRevision } from '@anum/contracts';
import { apiErrorFrom } from '../src/lib/errors.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { createHistoryApi } from '../src/hqBuilder/historyApi.ts';
import { changeCount, historyProblem, miniView, revisionTitle, summaryText } from '../src/hqBuilder/historyModel.ts';

const zero: OfficeLayoutDiffSummary = { rooms_added: 0, rooms_removed: 0, rooms_moved: 0, rooms_changed: 0, desks_added: 0, desks_removed: 0, desks_moved: 0, floor_resized: false, rooms: 3, desks: 5 };

function recorder() {
  const calls: { path: string; init: RequestInit }[] = [];
  const request = async <T,>(path: string, init: RequestInit): Promise<T> => { calls.push({ path, init }); return {} as T; };
  const remove = async (path: string, init: RequestInit): Promise<void> => { calls.push({ path, init }); };
  return { calls, api: createHistoryApi(request, remove) };
}

test('history calls use the documented paths, and restore always says it was a click', async () => {
  const { calls, api } = recorder();
  await api.revisions('floor 1');
  await api.revisions('f', 12, 10);
  await api.revision('f', 4, 'current');
  await api.revision('f', 4, 'previous');
  await api.restore('f', 4, { payload_hash: 'a'.repeat(64), expected_head: 9, confirm_unassign: ['d1'] });
  await api.templates();
  await api.saveFloorTemplate({ id: 't', floor_id: 'f', name: 'Standard' });
  await api.saveBuildingTemplate({ id: 't2', building_id: 'b', name: 'Campus' });
  await api.renameTemplate('t', 'New', 2);
  await api.deleteTemplate('t', 3);
  await api.floorFromTemplate({ template_id: 't', id: 'f2', building_id: 'b', level: 1 });
  await api.buildingFromTemplate({ template_id: 't2', id: 'b2', name: 'Branch', timezone: 'Asia/Dubai' });
  assert.deepEqual(calls.map((c) => `${c.init.method} ${c.path}`), [
    'GET /api/v1/office-layout/floors/floor%201/revisions?limit=30',
    'GET /api/v1/office-layout/floors/f/revisions?limit=10&before=12',
    'GET /api/v1/office-layout/floors/f/revisions/4?against=current',
    'GET /api/v1/office-layout/floors/f/revisions/4?against=previous',
    'POST /api/v1/office-layout/floors/f/revisions/4/restore',
    'GET /api/v1/office-layout/templates',
    'POST /api/v1/office-layout/templates/from-floor',
    'POST /api/v1/office-layout/templates/from-building',
    'PATCH /api/v1/office-layout/templates/t',
    'DELETE /api/v1/office-layout/templates/t?expected_revision=3',
    'POST /api/v1/office-layout/floors/from-template',
    'POST /api/v1/office-layout/buildings/from-template',
  ]);
  const body = JSON.parse(String(calls[4].init.body));
  assert.equal(body.input_mode, 'click');
  assert.equal(body.expected_head, 9);
  assert.deepEqual(body.confirm_unassign, ['d1']);
});

test('summary sentences are plural aware in English and Arabic and never empty', () => {
  setLocale('en');
  assert.equal(summaryText(zero), 'no change to rooms or desks');
  assert.equal(summaryText({ ...zero, rooms_moved: 1, desks_added: 3 }), '1 room moved and 3 desks added');
  assert.equal(changeCount({ ...zero, rooms_moved: 1, floor_resized: true }), 2);
  setLocale('ar');
  assert.ok(summaryText({ ...zero, rooms_moved: 2 }).includes('غرفتان'));
  setLocale('en');
});

test('titles say what a revision is', () => {
  setLocale('en');
  const base: OfficeLayoutRevision = { revision_no: 3, action: 'office.floor.layout', actor: 'u', created_at: '2026-10-25T08:00:00Z', summary: zero, snapshot_hash: 'h', restored_from: null, is_current: false };
  assert.equal(revisionTitle(base), 'Layout saved');
  assert.equal(revisionTitle({ ...base, restored_from: 2, action: 'office.floor.restore' }), 'Restored from version 2');
  assert.equal(revisionTitle({ ...base, action: 'baseline', summary: { ...zero, baseline: true } }), 'Starting point before tracked changes');
  assert.equal(revisionTitle({ ...base, action: 'office.unknown' }), 'Layout changed');
});

test('the mini plan frame holds the floor and every room, also a removed one', () => {
  const diff: OfficeLayoutDiff = {
    rooms: [
      { id: 'a', name: 'A', kind: 'office', color: null, status: 'same', box: { x: 2, z: 2, width: 6, depth: 5, rotation: 0 }, from_box: null, from_name: null },
      { id: 'b', name: 'B', kind: 'office', color: null, status: 'removed', box: { x: 70, z: 45, width: 10, depth: 8, rotation: 0 }, from_box: null, from_name: null },
    ], desks: [], summary: zero,
  };
  const view = miniView(diff, { width_m: 60, depth_m: 40 });
  assert.deepEqual(view, { minX: 0, minZ: 0, width: 80, depth: 53 });
  assert.equal(miniView({ rooms: [], desks: [], summary: zero }, { width_m: null, depth_m: null }).width, 1);
});

test('every failure has a plain sentence and the stale and unassign cases are told apart', () => {
  setLocale('en');
  const envelope = (status: number, type: string) => apiErrorFrom(status, { error: { code: 'conflict', message: 'server text', details: [{ message: 'm', type }] } });
  assert.equal(historyProblem(envelope(409, 'STALE_LAYOUT')).kind, 'stale');
  assert.equal(historyProblem(envelope(409, 'UNASSIGN_NOT_CONFIRMED')).kind, 'unassign');
  assert.equal(historyProblem(envelope(409, 'TEMPLATE_NAME_TAKEN')).text, 'A template of this kind already has that name. Choose another name.');
  assert.equal(historyProblem(envelope(403, 'x')).kind, 'denied');
  assert.equal(historyProblem(envelope(503, 'x')).kind, 'unavailable');
  assert.equal(historyProblem(envelope(404, 'REVISION_NOT_FOUND')).kind, 'gone');
  assert.equal(historyProblem(new Error('boom')).kind, 'failed');
  assert.ok(!historyProblem(envelope(500, 'x')).text.includes('server text'));
});
