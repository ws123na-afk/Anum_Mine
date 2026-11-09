// Unit tests for the Team page helpers (src/lib/teamPage.ts, docs/team-work.md#team-page).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { TeamPerson, Workload } from '@anum/contracts';
import { accessMatrix, departmentRows, filterPeople, initialsOf, NO_DIRECTORY_FILTER } from '../src/lib/teamPage.ts';

const person = (over: Partial<TeamPerson>): TeamPerson => ({ user_id: 'u', role: 'member', display_name: 'Someone', has_profile: true, email: null, department: null, job_title: null, capacity_hours: null, timezone: 'UTC', assignable: true, ...over });
const people = [
  person({ user_id: 'a', display_name: 'Aisha Saleh', department: 'sales', job_title: 'Sales lead', email: 'aisha@example.com' }),
  person({ user_id: 'b', display_name: 'Omar', role: 'owner', department: 'finance' }),
  person({ user_id: 'c', display_name: 'Zainab Ali', role: 'viewer', department: 'sales' }),
  person({ user_id: 'd', display_name: 'Élodie', role: 'viewer' }),
];

describe('access matrix', () => {
  test('levels compare each role with the owner per area', () => {
    const { areas, levels } = accessMatrix([
      { role: 'owner', permissions: ['task:read', 'task:create', 'task:manage', 'membership:manage', 'call:use'] },
      { role: 'member', permissions: ['task:read', 'task:create', 'call:use'] },
      { role: 'viewer', permissions: ['task:read', 'call:use'] },
    ]);
    assert.deepEqual(areas, ['call', 'membership', 'task']);
    assert.deepEqual(levels.member, { call: 'full', membership: 'none', task: 'edit' });
    assert.deepEqual(levels.viewer, { call: 'full', membership: 'none', task: 'read' });
    assert.equal(levels.owner.task, 'full');
  });
});

describe('directory and departments', () => {
  test('filters by search (accents folded), department, none and role, sorted by name', () => {
    assert.deepEqual(filterPeople(people, NO_DIRECTORY_FILTER).map((p) => p.user_id), ['a', 'd', 'b', 'c']);
    assert.deepEqual(filterPeople(people, { ...NO_DIRECTORY_FILTER, q: 'elodie' }).map((p) => p.user_id), ['d']);
    assert.deepEqual(filterPeople(people, { ...NO_DIRECTORY_FILTER, q: 'sales lead' }).map((p) => p.user_id), ['a']);
    assert.deepEqual(filterPeople(people, { ...NO_DIRECTORY_FILTER, department: 'sales', role: 'viewer' }).map((p) => p.user_id), ['c']);
    assert.deepEqual(filterPeople(people, { ...NO_DIRECTORY_FILTER, department: 'none' }).map((p) => p.user_id), ['d']);
  });
  test("departments sum their people's work; people without one come last", () => {
    const workload: Workload = { generated_at: '', unassigned_open: 0, rows: [
      { assignee_type: 'user', assignee_id: 'a', name: 'Aisha', open: 3, in_progress: 1, blocked: 0, overdue: 1, due_this_week: 2, estimate_hours: 0, capacity_hours: null, load_percent: null },
      { assignee_type: 'user', assignee_id: 'c', name: 'Zainab', open: 2, in_progress: 0, blocked: 0, overdue: 0, due_this_week: 1, estimate_hours: 0, capacity_hours: null, load_percent: null },
      { assignee_type: 'anum', assignee_id: 'anum', name: 'ANUM', open: 9, in_progress: 0, blocked: 0, overdue: 0, due_this_week: 0, estimate_hours: 0, capacity_hours: null, load_percent: null },
    ] };
    const rows = departmentRows(people, workload);
    assert.deepEqual(rows.map((r) => r.department), ['sales', 'finance', null]);
    assert.deepEqual([rows[0].people.length, rows[0].open, rows[0].overdue, rows[0].dueThisWeek], [2, 5, 1, 3]);
    assert.equal(departmentRows(people, null)[0].open, 0);
  });
  test('initials', () => {
    assert.equal(initialsOf('Aisha Al Saleh'), 'AS');
    assert.equal(initialsOf('omar'), 'OM');
    assert.equal(initialsOf(' '), '?');
  });
});
