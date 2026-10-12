import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { AssistantProfile, TeamPerson } from '@anum/contracts';
import {
  assigneeOptions, assigneeValue, boardColumn, draftBody, dueState, emptyDraft, healthTone, isoToLocalInput, loadTone,
  localInputToIso, organisationInvitationLink, parseAssignee, shouldNotifyDesktop, slugify, workQuery,
} from '../src/lib/team.ts';
import { parseInvitationInput } from '../src/lib/admin.ts';

const person = (user_id: string, display_name: string, role = 'member'): TeamPerson => ({
  user_id, display_name, role, has_profile: true, email: null, department: null, job_title: null, capacity_hours: null, timezone: 'UTC', assignable: role !== 'viewer',
});
const assistant = (id: string, name: string, enabled = true): AssistantProfile => ({
  id, name, enabled, department: 'sales', instructions: '', allowed_tools: [], routing_keywords: [], autonomy: 'suggest', created_by: 'u', created_at: '', updated_at: '',
});

describe('team helpers', () => {
  test('assignee options list assignable people, ANUM, then enabled assistants', () => {
    const options = assigneeOptions([person('u1', 'Ahmed'), person('u2', 'Vera', 'viewer')], [assistant('a1', 'Sales'), assistant('a2', 'Off', false)], 'ANUM');
    assert.deepEqual(options.map((o) => o.value), ['user:u1', 'anum:anum', 'assistant:a1']);
    assert.deepEqual(parseAssignee('assistant:a1'), { type: 'assistant', id: 'a1' });
    assert.equal(parseAssignee('robot:x'), null);
    assert.equal(parseAssignee(''), null);
    assert.equal(assigneeValue('user', 'u1'), 'user:u1');
    assert.equal(assigneeValue(null, null), '');
  });

  test('datetime-local values round-trip with the browser offset', () => {
    const iso = localInputToIso('2026-10-15T17:00');
    assert.ok(iso && /^2026-10-15T17:00:00[+-]\d{2}:\d{2}$/.test(iso));
    assert.equal(isoToLocalInput(iso), '2026-10-15T17:00');
    assert.equal(localInputToIso('2026-02-30T10:00'), null);
    assert.equal(localInputToIso('tomorrow'), null);
    assert.equal(isoToLocalInput(null), '');
  });

  test('due state', () => {
    const now = new Date('2026-10-11T09:00:00');
    assert.equal(dueState({ due_at: null, status: 'todo' }, now), 'none');
    assert.equal(dueState({ due_at: '2026-10-10T09:00:00', status: 'todo' }, now), 'overdue');
    assert.equal(dueState({ due_at: '2026-10-10T09:00:00', status: 'done' }, now), 'closed');
    assert.equal(dueState({ due_at: new Date('2026-10-11T17:00:00').toISOString(), status: 'blocked' }, now), 'today');
    assert.equal(dueState({ due_at: new Date('2026-10-13T17:00:00').toISOString(), status: 'todo' }, now), 'soon');
    assert.equal(dueState({ due_at: new Date('2026-11-13T17:00:00').toISOString(), status: 'todo' }, now), 'later');
  });

  test('new work drafts are validated like the API', () => {
    assert.deepEqual(draftBody(emptyDraft()).errors, { title: 'required' });
    const bad = draftBody({ ...emptyDraft(), title: 'x', estimate: '2000', due: 'nope' });
    assert.deepEqual(bad.errors, { estimate: 'range', due: 'invalid' });
    const ok = draftBody({ ...emptyDraft(), title: '  Call   the supplier ', assignee: 'user:u1', estimate: '2.5', checklist: 'Quote\n\n Confirm \n' });
    assert.equal(ok.body?.title, 'Call the supplier');
    assert.deepEqual(ok.body?.assignee, { type: 'user', id: 'u1' });
    assert.equal(ok.body?.estimate_hours, 2.5);
    assert.deepEqual(ok.body?.checklist, ['Quote', 'Confirm']);
  });

  test('work queries name only what is set', () => {
    assert.equal(workQuery({ scope: 'mine', status: 'open' }), '/api/v1/work-items?scope=mine&status=open&limit=50');
    assert.equal(workQuery({ q: ' tyre ', offset: 50, limit: 25, assigneeType: 'user', assigneeId: 'u1' }), '/api/v1/work-items?q=tyre&assignee_type=user&assignee_id=u1&limit=25&offset=50');
  });

  test('tones, slugs, links and board columns', () => {
    assert.equal(loadTone(null), 'unknown');
    assert.equal(loadTone(120), 'over');
    assert.equal(loadTone(85), 'high');
    assert.equal(loadTone(10), 'ok');
    assert.equal(healthTone('attention'), 'dangerTone');
    assert.equal(healthTone('active'), 'success');
    assert.equal(slugify('Acme Tyres & Co.'), 'acme_tyres_co');
    assert.equal(slugify('Zé'), 'ze_org');
    assert.equal(slugify('شركة'), '');
    assert.equal(boardColumn('cancelled'), 'done');
    const link = organisationInvitationLink('https://anum.example/app#team', 'anum_inv_example', 'acme_main', 'acme');
    assert.equal(link, 'https://anum.example/app#invitation=anum_inv_example&workspace=acme_main&tenant=acme');
    assert.deepEqual(parseInvitationInput(link), { token: 'anum_inv_example', workspaceId: 'acme_main', tenantId: 'acme' });
    assert.deepEqual(parseInvitationInput('https://x#invitation=anum_inv_example&workspace=w_1'), { token: 'anum_inv_example', workspaceId: 'w_1' });
  });

  test('desktop alerts only for me, in the background, when allowed', () => {
    const base = { recipient: 'u1', me: 'u1', hidden: true, desktopEnabled: true, permission: 'granted' };
    assert.equal(shouldNotifyDesktop(base), true);
    assert.equal(shouldNotifyDesktop({ ...base, recipient: 'u2' }), false);
    assert.equal(shouldNotifyDesktop({ ...base, hidden: false }), false);
    assert.equal(shouldNotifyDesktop({ ...base, desktopEnabled: false }), false);
    assert.equal(shouldNotifyDesktop({ ...base, permission: 'default' }), false);
  });
});
