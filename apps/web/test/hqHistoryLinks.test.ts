// "Show history" from a task, meeting or approval (matrix row 10.4) and the export's server filter and JSON file
// (row 10.9): src/lib/hqHistoryLink.ts, src/lib/hqHistory.ts, src/lib/hqHistoryExport.ts.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { OfficeHistoryEvidence, OfficeHistoryPage } from '@anum/contracts';
import { EMPTY_FILTERS, MAX_EXPORT_MENTIONS, RECORD_PAGES, exportFilters, exportMentions, historyQuery, recordRequests, resolveRecord, type HistoryScope } from '../src/lib/hqHistory.ts';
import { narrowedExportQuery, narrowedFilename, rowsToJson, type ExportRow } from '../src/lib/hqHistoryExport.ts';
import { HISTORY_LINK_EVENT, parseHistoryRef, showRecordHistory } from '../src/lib/hqHistoryLink.ts';

let n = 0;
function rec(target: string, minutes: number, correlation: string, action = 'task.updated'): OfficeHistoryEvidence {
  n += 1;
  return {
    id: `audit_${n}`, timestamp: new Date(Date.parse('2026-10-20T10:00:00Z') + minutes * 60_000).toISOString(), actor: 'owner_1', action, target,
    category: 'task', outcome: 'success', correlation_id: correlation, metadata: {}, source: 'workspace_audit', verified: true,
  };
}
const page = (items: OfficeHistoryEvidence[], next: string | null = null): OfficeHistoryPage => ({
  items, next_cursor: next, workspace_id: 'w', category: 'all', evidence_notice: 'x', historical_coverage_complete: false,
});
const task: Extract<HistoryScope, { kind: 'record' }> = { kind: 'record', recordKind: 'task', id: 'task_1', label: 'Plan rotation' };

describe('show history link', () => {
  test('only a task, meeting or approval with a plain id becomes an event', () => {
    const sent: Event[] = [];
    const target = { dispatchEvent: (e: Event) => { sent.push(e); return true; } };
    assert.equal(showRecordHistory({ recordKind: 'meeting', id: 'meeting_1', label: '  Weekly sync ' }, target), true);
    assert.equal(sent[0].type, HISTORY_LINK_EVENT);
    assert.deepEqual((sent[0] as CustomEvent).detail, { recordKind: 'meeting', id: 'meeting_1', label: 'Weekly sync' });
    assert.equal(showRecordHistory({ recordKind: 'room' as 'task', id: 'r1' }, target), false);
    assert.equal(showRecordHistory({ recordKind: 'task', id: '../x' }, target), false);
    assert.equal(sent.length, 1);
    assert.equal(parseHistoryRef(null), null);
    assert.deepEqual(parseHistoryRef({ recordKind: 'approval', id: 'ap_1', label: '' }), { recordKind: 'approval', id: 'ap_1' });
  });

  test('the item is looked up by its exact target, never by a text search', () => {
    const q = historyQuery({ ...EMPTY_FILTERS, q: 'ignored' }, task, 'c1', 200);
    assert.ok(q.ok);
    const url = new URL(`http://x${q.path}`);
    assert.equal(url.searchParams.get('target'), 'task_1');
    assert.equal(url.searchParams.get('q'), null);
    assert.equal(url.searchParams.get('cursor'), 'c1');
    assert.equal(exportFilters(EMPTY_FILTERS, task), null);
  });

  test('requests are distinct correlation ids, oldest first; records without an id are counted apart', () => {
    const items = [rec('task_1', 30, 'corr_b', 'task.completed'), rec('task_1', 20, ''), rec('task_1', 10, 'corr_a', 'task.started'), rec('task_1', 0, 'corr_b', 'task.created')];
    const { requests, withoutId } = recordRequests(items);
    assert.equal(withoutId, 1);
    assert.deepEqual(requests.map((r) => [r.correlationId, r.action, r.records]), [['corr_b', 'task.created', 2], ['corr_a', 'task.started', 1]]);
  });

  test('resolving opens the first request and keeps the others to pick from', async () => {
    const paths: string[] = [];
    const found = await resolveRecord(async (path) => { paths.push(path); return paths.length === 1 ? page([rec('task_1', 5, 'corr_late')], 'next') : page([rec('task_1', 1, 'corr_first'), rec('task_10', 0, 'corr_other')]); }, task);
    assert.ok(found && found.kind === 'request');
    assert.equal(found.scope.correlationId, 'corr_first');
    assert.deepEqual(found.scope.from?.requests.map((r) => r.correlationId), ['corr_first', 'corr_late']);
    assert.equal(found.scope.from?.partial, false);
    assert.equal(found.scope.from?.label, 'Plan rotation');
    assert.equal(paths.length, 2);
  });

  test('an item without records, or without a correlation id, resolves to none; a long history stops and says so', async () => {
    assert.deepEqual(await resolveRecord(async () => page([]), task), { kind: 'none', withoutId: 0, partial: false });
    assert.deepEqual(await resolveRecord(async () => page([rec('task_1', 0, ' ')]), task), { kind: 'none', withoutId: 1, partial: false });
    let calls = 0;
    const long = await resolveRecord(async () => { calls += 1; return page([], 'more'); }, task);
    assert.deepEqual(long, { kind: 'none', withoutId: 0, partial: true });
    assert.equal(calls, RECORD_PAGES);
    let current = true;
    assert.equal(await resolveRecord(async () => { current = false; return page([rec('task_1', 0, 'c')]); }, task, () => current), null);
  });
});

describe('export: server mention filter and JSON', () => {
  const room: HistoryScope = { kind: 'room', roomId: 'room_1', label: 'R', deskIds: ['desk_1', 'desk_2'] };
  test('room and agent views send their ids; other views and oversized rooms send none', () => {
    assert.deepEqual(exportMentions(room), ['room_1', 'desk_1', 'desk_2']);
    assert.deepEqual(exportMentions({ kind: 'agent', agentId: 'agent_1', name: 'A' }), ['agent_1']);
    assert.equal(exportMentions({ kind: 'office' }), null);
    assert.equal(exportMentions({ kind: 'correlation', correlationId: 'c' }), null);
    const desks = Array.from({ length: MAX_EXPORT_MENTIONS }, (_, i) => `desk_${i}`);
    assert.equal(exportMentions({ ...room, deskIds: desks } as HistoryScope), null);
  });
  test('the query carries the filters, JSON and one mention per id', () => {
    const url = new URLSearchParams(narrowedExportQuery({ action: 'office.*', q: 'x' }, ['room_1', 'desk_1']));
    assert.equal(url.get('format'), 'json');
    assert.equal(url.get('action'), 'office.*');
    assert.deepEqual(url.getAll('mention'), ['room_1', 'desk_1']);
    assert.deepEqual(new URLSearchParams(narrowedExportQuery({}, null)).getAll('mention'), []);
  });
  test('JSON keeps the export fields and drops secret or conversation keys', () => {
    const row: ExportRow = { id: 'a1', created_at: 't', actor: 'u', action: 'office.room.update', target: 'room_1', outcome: 'success', correlation_id: 'c', tenant_id: 't', workspace_id: 'w', metadata: { note: 'kept', prompt: 'private', nested: { api_key: 'x', ok: 1 } } };
    const parsed = JSON.parse(rowsToJson([row]));
    assert.deepEqual(parsed[0].metadata, { note: 'kept', nested: { ok: 1 } });
    assert.equal(parsed[0].correlation_id, 'c');
    assert.equal(narrowedFilename(room, '2026-10-29', 'json'), 'history-room-2026-10-29.json');
    assert.equal(narrowedFilename(room, '2026-10-29'), 'history-room-2026-10-29.csv');
  });
});
