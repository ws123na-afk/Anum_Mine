// Unit tests for the trigger, email-step and waiting-step helpers (src/lib/automation.ts).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  canTakeOver, checkDraft, checkTriggerDraft, conditionText, createdTaskId, cronWords, draftFromWorkflow, emptyTriggerDraft, eventLabel, newCondition, newStep,
  runStatusLabel, signingExample, splitRecipients, triggerDraftFrom, triggerOutcomeLabel, waitingApprovalId, waitingLabel, webhookUrl,
  type AutomationTrigger, type StepDraft, type WorkflowDraft,
} from '../src/lib/automation.ts';
import { setLocale } from '../src/lib/i18n.ts';

const step = (patch: Partial<StepDraft> = {}): StepDraft => ({ ...newStep([]), ...patch });
const draft = (steps: StepDraft[]): WorkflowDraft => ({ name: 'Flow', description: '', overlapPolicy: 'allow', steps });
const events = ['email.received', 'file.uploaded'];

describe('steps that do real work', () => {
  test('a task step runs by default and can be create-only', () => {
    const ran = checkDraft(draft([step({ action: 'task.create', title: 'T', prompt: 'P' })]));
    const created = checkDraft(draft([step({ action: 'task.create', title: 'T', prompt: 'P', runTask: false })]));
    assert.ok(ran.ok && created.ok);
    if (ran.ok && created.ok) {
      assert.deepEqual(ran.body.steps[0].input, { title: 'T', prompt: 'P' });
      assert.deepEqual(created.body.steps[0].input, { title: 'T', prompt: 'P', run: false });
    }
  });
  test('an email step needs a mailbox, recipients and a body', () => {
    const bad = checkDraft(draft([step({ action: 'email.send' })]));
    assert.ok(!bad.ok);
    if (!bad.ok) assert.deepEqual(Object.keys(bad.errors).map((key) => key.split('.').pop()).sort(), ['accountId', 'body', 'to']);
    const good = checkDraft(draft([step({ action: 'email.send', accountId: 'acct', to: 'a@example.com; {{trigger.from}}\nb@example.com', subject: ' Hi ', body: ' Shipped ', maxAttempts: 2, retryDelay: 30 })]));
    assert.ok(good.ok);
    if (good.ok) {
      assert.deepEqual(good.body.steps[0].input, { account_id: 'acct', to: ['a@example.com', '{{trigger.from}}', 'b@example.com'], subject: 'Hi', body: 'Shipped' });
      assert.equal(good.body.steps[0].max_attempts, 2);
      assert.equal(good.body.steps[0].retry_delay_seconds, 30);
    }
  });
  test('notify can push and round-trips its channels', () => {
    const result = checkDraft(draft([step({ action: 'notify', message: 'Low stock', push: true })]));
    assert.ok(result.ok);
    if (!result.ok) return;
    assert.deepEqual(result.body.steps[0].input, { message: 'Low stock', channels: ['in_app', 'push'] });
    const back = draftFromWorkflow({ ...result.body, steps: result.body.steps });
    assert.equal(back.steps[0].push, true);
  });
  test('the retry delay is checked', () => {
    const result = checkDraft(draft([step({ action: 'notify', retryDelay: 2 })]));
    assert.ok(!result.ok);
  });
  test('splitRecipients removes blanks and duplicates', () => {
    assert.deepEqual(splitRecipients(' a@x.com, ,a@x.com;b@y.com '), ['a@x.com', 'b@y.com']);
  });
});

describe('waiting steps', () => {
  test('say what a running step waits for', () => {
    setLocale('en');
    assert.match(waitingLabel({ status: 'running', waiting_for: 'approval', attempt: 1, max_attempts: 1, next_attempt_at: null }) ?? '', /approval/i);
    assert.match(waitingLabel({ status: 'running', waiting_for: 'retry', attempt: 1, max_attempts: 3, next_attempt_at: '2026-10-12T09:00:00Z' }) ?? '', /attempt 2 of 3/);
    assert.equal(waitingLabel({ status: 'completed', waiting_for: null, attempt: 1, max_attempts: 1, next_attempt_at: null }), null);
    assert.equal(runStatusLabel({ status: 'running', waiting_for: 'approval' }), 'Waiting for approval');
    assert.equal(waitingApprovalId({ waiting_for: 'approval', output: { approval_id: 'ap_1' } }), 'ap_1');
    assert.equal(createdTaskId({ action: 'email.send', output: { task_id: 'task_9' } }), 'task_9');
  });
  test('Arabic run states and schedule words', () => {
    setLocale('ar');
    assert.equal(runStatusLabel({ status: 'running', waiting_for: 'approval' }), 'في انتظار الموافقة');
    assert.equal(cronWords('At 09:00, every day', 'في الساعة 09:00، كل يوم'), 'في الساعة 09:00، كل يوم');
    assert.equal(eventLabel('email.received'), 'وصل بريد إلكتروني');
    setLocale('en');
    assert.equal(cronWords('At 09:00, every day', 'في الساعة 09:00، كل يوم'), 'At 09:00, every day');
    assert.equal(cronWords('At 09:00, every day', undefined), 'At 09:00, every day');
  });
});

describe('triggers', () => {
  test('a valid event trigger becomes the API body', () => {
    setLocale('en');
    const value = { ...emptyTriggerDraft('event'), name: ' Noon mail ', conditions: [{ ...newCondition('from'), operator: 'contains' as const, value: ' noon.com ' }, { ...newCondition('subject'), operator: 'exists' as const }] };
    const checked = checkTriggerDraft(value, events);
    assert.ok(checked.ok);
    if (checked.ok) assert.deepEqual(checked.body, {
      name: 'Noon mail', kind: 'event', event_type: 'email.received', rate_limit_per_minute: 30, replay_window_seconds: 300,
      conditions: [{ field: 'from', operator: 'contains', value: 'noon.com' }, { field: 'subject', operator: 'exists', value: null }],
    });
  });
  test('problems point at their fields', () => {
    const bad = { ...emptyTriggerDraft('webhook'), replayWindow: 5, rateLimit: 0, conditions: [{ ...newCondition('bad field!'), value: '' }] };
    const checked = checkTriggerDraft(bad, events);
    assert.ok(!checked.ok);
    if (!checked.ok) {
      assert.ok(checked.errors.name && checked.errors.rateLimit && checked.errors.replayWindow);
      assert.equal(Object.keys(checked.errors).filter((key) => key.endsWith('.field') || key.endsWith('.value')).length, 2);
    }
    assert.ok(!checkTriggerDraft({ ...emptyTriggerDraft('event'), name: 'x', eventType: 'automation_run.completed' }, events).ok);
  });
  test('labels, links and the signing example', () => {
    setLocale('en');
    assert.equal(conditionText({ field: 'from', operator: 'contains', value: 'noon.com' }), 'from contains "noon.com"');
    assert.equal(triggerOutcomeLabel('rate_limited'), 'refused, over the rate limit');
    assert.equal(webhookUrl('https://api.example.com/', '/api/v1/automation/hooks/t/w/x'), 'https://api.example.com/api/v1/automation/hooks/t/w/x');
    assert.match(signingExample('https://api.example.com/h'), /X-Anum-Signature: t=\$TS,v1=\$SIG/);
    const trigger = { name: 'Hook', kind: 'webhook', event_type: null, rate_limit_per_minute: 5, replay_window_seconds: 120, conditions: [{ field: 'body.status', operator: 'equals', value: 'paid' }] } as unknown as AutomationTrigger;
    const back = triggerDraftFrom(trigger);
    assert.equal(back.kind, 'webhook');
    assert.equal(back.conditions[0].value, 'paid');
  });
  test('only owners take over, and not their own', () => {
    assert.equal(canTakeOver({ userId: 'o', role: 'owner' }, 'm'), true);
    assert.equal(canTakeOver({ userId: 'o', role: 'owner' }, 'o'), false);
    assert.equal(canTakeOver({ userId: 'm', role: 'member' }, 'x'), false);
  });
});
