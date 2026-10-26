import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { EMPTY_FILTERS, OFFICE_SCOPE } from '../src/lib/hqHistory.ts';
import { exportNarrowing, narrowedFilename, needsNarrowing, parseExportRows, rowsToCsv, safeMetadata, type ExportRow } from '../src/lib/hqHistoryExport.ts';

const row = (over: Partial<ExportRow>): ExportRow => ({
  id: 'a1', created_at: '2026-10-20T09:00:00+00:00', actor: 'user_1', action: 'office.room.update', target: 'room_1', outcome: 'success',
  correlation_id: 'c1', tenant_id: 't', workspace_id: 'w', metadata: {}, ...over,
});
const room = { kind: 'room', roomId: 'room_1', label: 'R', deskIds: ['desk_1'] } as const;
const agent = { kind: 'agent', agentId: 'agent_1', name: 'A' } as const;

describe('history export narrowing', () => {
  test('whole office views and named types need no narrowing; room, agent and Other do', () => {
    assert.equal(needsNarrowing(EMPTY_FILTERS, OFFICE_SCOPE), false);
    assert.equal(needsNarrowing({ ...EMPTY_FILTERS, category: 'office' }, OFFICE_SCOPE), false);
    assert.equal(needsNarrowing(EMPTY_FILTERS, { kind: 'correlation', correlationId: 'c' }), false);
    assert.equal(needsNarrowing(EMPTY_FILTERS, room), true);
    assert.equal(needsNarrowing(EMPTY_FILTERS, agent), true);
    assert.equal(needsNarrowing({ ...EMPTY_FILTERS, category: 'other' }, OFFICE_SCOPE), true);
    assert.equal(exportNarrowing(EMPTY_FILTERS, OFFICE_SCOPE), null);
  });
  test('the room view keeps rows that name the room or a desk anywhere, and nothing else', () => {
    const keep = exportNarrowing(EMPTY_FILTERS, room)!;
    assert.equal(keep(row({ target: 'room_1' })), true);
    assert.equal(keep(row({ target: 'desk_1' })), true);
    assert.equal(keep(row({ target: 'floor_1', metadata: { after: { rooms: [{ id: 'room_1' }] } } })), true);
    assert.equal(keep(row({ target: 'room_2', metadata: { after: { rooms: [{ id: 'room_3' }] } } })), false);
  });
  test('the agent view keeps rows by actor, target or recorded value', () => {
    const keep = exportNarrowing(EMPTY_FILTERS, agent)!;
    assert.equal(keep(row({ actor: 'agent_1', target: 'task_1' })), true);
    assert.equal(keep(row({ metadata: { agent_id: 'agent_1' } })), true);
    assert.equal(keep(row({ actor: 'user_1', target: 'task_1' })), false);
  });
  test('Other drops every named type and keeps the rest', () => {
    const keep = exportNarrowing({ ...EMPTY_FILTERS, category: 'other' }, OFFICE_SCOPE)!;
    for (const action of ['task.create', 'agent_run.start', 'approval.decide', 'agent_meeting.start', 'office.room.update', 'work_item.create', 'workspace.rename', 'workspace_member.add', 'automation.run', 'autonomy.set']) {
      assert.equal(keep(row({ action })), false, action);
    }
    assert.equal(keep(row({ action: 'audit.export' })), true);
    assert.equal(keep(row({ action: 'auth.login' })), true);
  });
  test('a named type plus a room view still narrows by the room', () => {
    const keep = exportNarrowing({ ...EMPTY_FILTERS, category: 'office' }, room)!;
    assert.equal(keep(row({ target: 'room_9' })), false);
    assert.equal(keep(row({ target: 'room_1' })), true);
  });
});

describe('history export file', () => {
  test('secret and conversation keys are dropped at every depth, other values stay', () => {
    const clean = safeMetadata({ before: { name: 'A', api_key: 'x' }, items: [{ prompt: 'p', ok: 1 }], transcript: 't', Authorization: 'a', token_count: 3, turns: [1] }) as Record<string, unknown>;
    assert.deepEqual(clean, { before: { name: 'A' }, items: [{ ok: 1 }] });
  });
  test('the CSV has the audit columns, quotes cells and defuses spreadsheet formulas', () => {
    const csv = rowsToCsv([row({ id: 'a1', actor: '=cmd', target: 'room "1", x', metadata: { b: 1, a: { prompt: 'secret words' } } })]);
    const [head, line] = csv.split('\r\n');
    assert.equal(head, 'id,created_at,actor,action,target,outcome,correlation_id,tenant_id,workspace_id,metadata');
    assert.ok(line.includes("'=cmd"));
    assert.ok(line.includes('"room ""1"", x"'));
    assert.ok(!csv.includes('secret words'));
    assert.ok(line.includes('""a"": {}'));
  });
  test('an export without rows is just the header; bad answers are refused', () => {
    assert.equal(rowsToCsv([]), 'id,created_at,actor,action,target,outcome,correlation_id,tenant_id,workspace_id,metadata\r\n');
    assert.throws(() => parseExportRows({ detail: 'no' }));
    assert.deepEqual(parseExportRows([{ id: 'x', action: 'a' }, null, { id: 3 }]).map((r) => r.id), ['x']);
  });
  test('file names are plain', () => {
    assert.equal(narrowedFilename(room, '2026-10-26'), 'history-room-2026-10-26.csv');
    assert.equal(narrowedFilename(agent, '2026/10;26'), 'history-agent-20261026.csv');
    assert.equal(narrowedFilename(OFFICE_SCOPE, '2026-10-26'), 'history-other-2026-10-26.csv');
  });
});
