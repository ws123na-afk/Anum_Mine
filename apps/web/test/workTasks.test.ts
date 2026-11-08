// Unit tests for My tasks and Team tasks grouping (src/lib/workTasks.ts, docs/tasks.md#my-tasks-and-team-tasks).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { WorkItem } from '@anum/contracts';
import { dueIn, groupWork, weekStart, workGroup } from '../src/lib/workTasks.ts';

const now = new Date(2026, 10, 5, 10, 0); // Thursday 5 November 2026, 10:00
let n = 0;
const item = (over: Partial<WorkItem> = {}): WorkItem => ({
  id: `w${++n}`, tenant_id: 't', workspace_id: 'w', title: `Item ${n}`, description: '', status: 'todo', priority: 'normal', assignee_type: null, assignee_id: null, assignee_name: null,
  created_by: 'u', created_by_name: null, due_at: null, follow_up_at: null, completed_at: null, reminder_sent_at: null, estimate_hours: null, source: 'manual', source_detail: {},
  agent_task_id: null, version: 1, created_at: '', updated_at: new Date(2026, 10, 5, 9).toISOString(), overdue: false, checklist_total: 0, checklist_done: 0, comment_count: 0, ...over,
} as WorkItem);

describe('work groups', () => {
  test('the week starts on Monday', () => {
    assert.equal(weekStart(now).getDate(), 2);
    assert.equal(weekStart(new Date(2026, 10, 2, 0, 5)).getDate(), 2);
  });
  test('overdue and due today win over the open status; done counts only this week; cancelled is left out', () => {
    assert.equal(workGroup(item({ status: 'in_progress', due_at: new Date(2026, 10, 4).toISOString() }), now), 'overdue');
    assert.equal(workGroup(item({ status: 'blocked', due_at: new Date(2026, 10, 5, 17).toISOString() }), now), 'today');
    assert.equal(workGroup(item({ status: 'in_progress', due_at: new Date(2026, 10, 9).toISOString() }), now), 'in_progress');
    assert.equal(workGroup(item({ status: 'done', completed_at: new Date(2026, 10, 3).toISOString() }), now), 'done_week');
    assert.equal(workGroup(item({ status: 'done', completed_at: new Date(2026, 9, 30).toISOString() }), now), null);
    assert.equal(workGroup(item({ status: 'cancelled' }), now), null);
  });
  test('groups sort by urgency, filter by priority and drop duplicates', () => {
    const late = item({ due_at: new Date(2026, 10, 4).toISOString() });
    const later = item({ due_at: new Date(2026, 10, 3).toISOString() });
    const urgent = item({ priority: 'urgent', due_at: new Date(2026, 10, 20).toISOString() });
    const low = item({ priority: 'low' });
    const groups = groupWork([late, later, urgent, low, late], now);
    assert.deepEqual(groups.overdue.map((i) => i.id), [later.id, late.id]);
    assert.deepEqual(groups.todo.map((i) => i.id), [urgent.id, low.id]);
    assert.deepEqual(groupWork([late, urgent, low], now, 'low').todo.map((i) => i.id), [low.id]);
    assert.equal(groupWork([late, urgent, low], now, 'low').overdue.length, 0);
  });
  test('time left reads in minutes, hours or days, and says when it is late', () => {
    assert.deepEqual(dueIn(new Date(2026, 10, 5, 10, 30).toISOString(), now), { unit: 'minutes', count: 30, late: false });
    assert.deepEqual(dueIn(new Date(2026, 10, 5, 18).toISOString(), now), { unit: 'hours', count: 8, late: false });
    assert.deepEqual(dueIn(new Date(2026, 10, 2, 10).toISOString(), now), { unit: 'days', count: 3, late: true });
  });
});

describe('completed this week paging', () => {
  test('stops at the last page or once the page reaches last week', async () => {
    const { doneWeekComplete } = await import('../src/lib/workTasks.ts');
    const at = (d: number) => new Date(2026, 10, d, 12).toISOString();
    assert.equal(doneWeekComplete({ items: [{ updated_at: at(4) }], next_offset: null }, now), true);
    assert.equal(doneWeekComplete({ items: [{ updated_at: at(4) }, { updated_at: at(3) }], next_offset: 200 }, now), false);
    assert.equal(doneWeekComplete({ items: [{ updated_at: at(4) }, { updated_at: at(1) }], next_offset: 200 }, now), true);
    assert.equal(doneWeekComplete({ items: [], next_offset: 200 }, now), true);
  });
});
