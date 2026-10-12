// Unit tests for the Automations page helpers (src/lib/automation.ts).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  automationAlert, automationError, canEdit, canManage, checkDraft, createdTaskId, cronShapeError, draftFromWorkflow, emptyDraft, formatTime,
  intervalCron, moveStep, newStep, nextRunLabel, outcomeLabel, overlapLabel, pageLabel, relativeTime, removeStep, runDuration, runProgress,
  statusLabel, statusTone, timeZones, type StepDraft, type WorkflowDraft,
} from '../src/lib/automation.ts';

const step = (patch: Partial<StepDraft> = {}): StepDraft => ({ ...newStep([]), ...patch });
const draft = (patch: Partial<WorkflowDraft> = {}): WorkflowDraft => ({ name: 'Digest', description: '', overlapPolicy: 'allow', steps: [step({ action: 'notify' })], ...patch });

describe('step drafts', () => {
  test('newStep picks an unused id and a unique key', () => {
    const first = newStep([]);
    assert.equal(first.id, 'step-1');
    const second = newStep([first, { ...first, id: 'step-2' }]);
    assert.equal(second.id, 'step-3');
    assert.notEqual(first.key, second.key);
  });
  test('moveStep swaps and ignores out-of-range moves; removeStep keeps the last step', () => {
    assert.deepEqual(moveStep(['a', 'b', 'c'], 0, 1), ['b', 'a', 'c']);
    assert.deepEqual(moveStep(['a', 'b'], 0, -1), ['a', 'b']);
    assert.deepEqual(removeStep(['a', 'b'], 0), ['b']);
    assert.deepEqual(removeStep(['a'], 0), ['a']);
  });
  test('an empty draft starts with one task step', () => {
    const value = emptyDraft();
    assert.equal(value.steps.length, 1);
    assert.equal(value.steps[0].action, 'task.create');
    assert.equal(value.overlapPolicy, 'allow');
  });
  test('draftFromWorkflow round-trips and keeps input keys the form does not edit', () => {
    const steps = [
      { id: 'make', name: 'Make', action: 'task.create', input: { title: 'Report', prompt: 'Summarise', priority: 'high' }, max_attempts: 2 },
      { id: 'tell', name: 'Tell', action: 'notify', input: { message: 'Done' }, max_attempts: 3 },
    ];
    const result = checkDraft(draftFromWorkflow({ name: 'W', description: 'd', overlap_policy: 'skip', steps }));
    assert.ok(result.ok);
    const expected = [
      { ...steps[0], retry_delay_seconds: 60 },
      { ...steps[1], input: { message: 'Done', channels: ['in_app'] }, retry_delay_seconds: 60 },
    ];
    if (result.ok) assert.deepEqual(result.body, { name: 'W', description: 'd', overlap_policy: 'skip', steps: expected });
  });
});

describe('checkDraft', () => {
  test('builds the API body from per-action fields', () => {
    const result = checkDraft(draft({ name: '  Weekly  ', steps: [step({ id: 'a', name: 'A', action: 'task.create', title: ' T ', prompt: ' P ' }), step({ id: 'b', name: 'B', action: 'pause', message: 'ignored' })] }));
    assert.ok(result.ok);
    if (!result.ok) return;
    assert.equal(result.body.name, 'Weekly');
    assert.deepEqual(result.body.steps.map((s) => s.input), [{ title: 'T', prompt: 'P' }, {}]);
  });
  test('reports every problem against its field, with how to fix it', () => {
    const a = step({ id: 'x', name: '', action: 'task.create' }), b = step({ id: 'x', name: 'B', action: 'task.execute' });
    const result = checkDraft(draft({ name: ' ', steps: [a, b] }));
    assert.ok(!result.ok);
    if (result.ok) return;
    assert.equal(result.errors.name, 'Give the automation a name.');
    assert.match(result.errors[`${a.key}.name`], /Step 1: give the step a name/);
    assert.match(result.errors[`${a.key}.title`], /needs a title/);
    assert.match(result.errors[`${a.key}.prompt`], /prompt/);
    assert.match(result.errors[`${b.key}.id`], /already used/);
    assert.match(result.errors[`${b.key}.action`], /no longer supported/);
    assert.match(result.summary, /^6 fields need attention/);
  });
  test('length limits mirror the API', () => {
    const long = step({ action: 'notify', message: 'x'.repeat(501) });
    const result = checkDraft(draft({ steps: [long] }));
    assert.ok(!result.ok);
    if (!result.ok) assert.match(result.errors[`${long.key}.message`], /500 characters/);
  });
});

describe('schedules', () => {
  test('intervalCron expands intervals and explains the ones cron cannot express', () => {
    assert.equal(intervalCron(15), '*/15 * * * *');
    assert.equal(intervalCron(60), '0 * * * *');
    assert.equal(intervalCron(360), '0 */6 * * *');
    assert.equal(intervalCron(1440), '0 0 * * *');
    assert.throws(() => intervalCron(0), /at least one minute/);
    assert.throws(() => intervalCron(7), /divide an hour/);
    assert.throws(() => intervalCron(420), /divide a day/);
  });
  test('cronShapeError checks the five fields', () => {
    assert.equal(cronShapeError('0 9 * * 1-5'), null);
    assert.match(cronShapeError('0 9 * *') ?? '', /five fields .* has 4/);
  });
  test('outcome and overlap labels are plain words', () => {
    assert.equal(outcomeLabel('skipped_overlap'), 'Skipped: a run was still active');
    assert.equal(outcomeLabel(null), null);
    assert.equal(overlapLabel('cancel_previous'), 'Replace the active run');
  });
  test('time zones include UTC', () => { assert.ok(timeZones().includes('UTC')); });
});

describe('times', () => {
  const now = new Date('2026-10-11T08:00:00Z');
  test('relativeTime reads both ways', () => {
    assert.equal(relativeTime('2026-10-11T08:20:00Z', now), 'in 20 min');
    assert.equal(relativeTime('2026-10-11T05:00:00Z', now), '3 h ago');
    assert.equal(relativeTime('2026-10-15T08:00:00Z', now), 'in 4 days');
    assert.equal(relativeTime(null, now), '');
  });
  test('formatTime and nextRunLabel use the schedule time zone', () => {
    assert.equal(formatTime('2026-10-12T06:00:00Z', 'Asia/Riyadh', 'en-GB'), 'Mon 12 Oct, 09:00');
    assert.equal(nextRunLabel('2026-10-11T09:00:00Z', false, now), 'Paused');
    assert.equal(nextRunLabel(null, true, now), 'Not scheduled');
    assert.match(nextRunLabel('2026-10-11T07:00:00Z', true, now), /^Due now/);
    assert.match(nextRunLabel('2026-10-11T09:00:00Z', true, now, 'UTC'), /\(in 1 h\)$/);
  });
  test('runDuration only for finished runs', () => {
    assert.equal(runDuration({ status: 'completed', created_at: '2026-10-11T08:00:00Z', updated_at: '2026-10-11T08:00:42Z' }), '42 s');
    assert.equal(runDuration({ status: 'paused', created_at: '2026-10-11T08:00:00Z', updated_at: '2026-10-11T09:00:00Z' }), '');
  });
});

describe('status, permissions, errors', () => {
  test('status words and tones', () => {
    assert.equal(statusLabel('disabled'), 'Paused');
    assert.equal(statusLabel('cancelled'), 'Cancelled');
    assert.equal(statusTone('failed'), 'dangerTone');
    assert.equal(statusTone('paused'), 'warning');
    assert.equal(runProgress({ status: 'failed', current_step: 1, steps: [{ status: 'completed' }, { status: 'failed' }] as never }), '1 of 2 steps');
  });
  test('owners change anything, members their own, viewers nothing', () => {
    assert.equal(canManage({ userId: 'v', role: 'viewer' }), false);
    assert.equal(canEdit({ userId: 'm', role: 'member' }, 'm'), true);
    assert.equal(canEdit({ userId: 'm', role: 'member' }, 'someone'), false);
    assert.equal(canEdit({ userId: 'o', role: 'owner' }, 'someone'), true);
    assert.equal(canEdit({ userId: 'v', role: 'viewer' }, 'v'), false);
  });
  test('automationError prefers field messages and the API sentence', () => {
    assert.equal(automationError({ status: 422, detail: 'Request validation failed', details: [{ location: 'body.cron', message: 'Value error, Hour field: 25 is outside 0-23.' }] }), 'Hour field: 25 is outside 0-23.');
    assert.equal(automationError({ status: 403, detail: 'Only the automation\'s creator (a) or a workspace owner can change this automation.' }), 'Only the automation\'s creator (a) or a workspace owner can change this automation.');
    assert.equal(automationError(new TypeError('Failed to fetch')), 'Could not reach the ANUM API. Check your connection and try again.');
  });
  test('pageLabel and createdTaskId', () => {
    assert.equal(pageLabel(20, 20, 45), '21–40 of 45');
    assert.equal(pageLabel(0, 0, 0), 'No results');
    assert.equal(createdTaskId({ action: 'task.create', output: { task_id: 'task_1' } }), 'task_1');
    assert.equal(createdTaskId({ action: 'notify', output: { task_id: 'task_1' } }), null);
  });
});

describe('automationAlert', () => {
  const failed = { id: 'e1', type: 'automation_run.failed', payload: { workflow_id: 'w', workflow_name: 'Digest', run_id: 'r', error: 'Disk full', failed_step: 'Stop', notify_user_ids: ['me'] } };
  test('alerts only the people the event names', () => {
    assert.deepEqual(automationAlert(failed, 'me'), { id: 'e1', kind: 'failed', title: 'Digest failed', message: 'Stop: Disk full', workflowId: 'w', runId: 'r' });
    assert.equal(automationAlert(failed, 'someone'), null);
    assert.equal(automationAlert(failed, null), null);
    assert.equal(automationAlert({ ...failed, type: 'automation_run.completed' }, 'me'), null);
  });
  test('notify steps become notices', () => {
    const alert = automationAlert({ id: 'e2', type: 'automation.notification', payload: { workflow_id: 'w', run_id: 'r', message: 'Backups verified', notify_user_ids: ['me'] } }, 'me');
    assert.equal(alert?.kind, 'notice');
    assert.equal(alert?.message, 'Backups verified');
  });
});
