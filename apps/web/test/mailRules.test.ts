// Unit tests for the Email Center phase 6 helpers (src/lib/mailRules.ts and the sidebar's poll in
// src/lib/mailUnread.ts, docs/email.md#mailbox-settings): retention options, the rule checks made
// before asking for approval, and the background unread poll.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { parseRetention, retentionOptions, ruleProblem, sameSettings, sortRules, splitAddresses, type MailRule } from '../src/lib/mailRules.ts';
import { mailUnread, pollMailUnread, publishMailUnread } from '../src/lib/mailUnread.ts';

const rule = (id: string, status: MailRule['status'], created_at: string) => ({ id, status, created_at } as MailRule);

describe('retention', () => {
  test('options are keep plus the server choices and the current value', () => {
    assert.deepEqual(retentionOptions([7, 30, 90], null), [null, 7, 30, 90]);
    assert.deepEqual(retentionOptions([7, 30], 45), [null, 7, 30, 45]);
    assert.deepEqual(retentionOptions([7, 30], 30), [null, 7, 30]);
  });

  test('select values become days or keep', () => {
    assert.equal(parseRetention(''), null);
    assert.equal(parseRetention('30'), 30);
    assert.equal(parseRetention('0'), null);
    assert.equal(parseRetention('5000'), null);
    assert.equal(parseRetention('7.5'), null);
  });

  test('a change is only a change when a value differs', () => {
    const base = { sent_retention_days: 30, draft_retention_days: null, label_retention_days: null, background_sync: true };
    assert.equal(sameSettings(base, { ...base }), true);
    assert.equal(sameSettings(base, { ...base, background_sync: false }), false);
    assert.equal(sameSettings(base, { ...base, label_retention_days: 7 }), false);
  });
});

describe('forwarding rules', () => {
  const ok = { name: 'Orders', from_contains: 'client.example', subject_contains: '', forward_to: ['partner@elsewhere.example'] };

  test('addresses split on commas, spaces and semicolons, each once', () => {
    assert.deepEqual(splitAddresses('a@x.example, b@x.example;A@x.example\nc@x.example'), ['a@x.example', 'b@x.example', 'c@x.example']);
    assert.deepEqual(splitAddresses('  '), []);
  });

  test('the checks before asking match the API', () => {
    assert.equal(ruleProblem(ok, 'owner@example.test'), null);
    assert.equal(ruleProblem({ ...ok, name: ' ' }, 'owner@example.test')?.problem, 'name');
    assert.equal(ruleProblem({ ...ok, from_contains: '' }, 'owner@example.test')?.problem, 'condition');
    assert.equal(ruleProblem({ ...ok, from_contains: '', subject_contains: 'Order' }, 'owner@example.test'), null);
    assert.equal(ruleProblem({ ...ok, forward_to: [] }, 'owner@example.test')?.problem, 'noAddress');
    assert.equal(ruleProblem({ ...ok, forward_to: ['a@x.example', 'b@x.example', 'c@x.example', 'd@x.example', 'e@x.example', 'f@x.example'] }, 'owner@example.test')?.problem, 'tooMany');
    assert.deepEqual(ruleProblem({ ...ok, forward_to: ['not-an-address'] }, 'owner@example.test'), { problem: 'invalid', address: 'not-an-address' });
    assert.equal(ruleProblem({ ...ok, forward_to: ['Owner@Example.test'] }, 'owner@example.test')?.problem, 'self');
  });

  test('waiting rules first, then forwarding, then paused', () => {
    const sorted = sortRules([rule('a', 'paused', '1'), rule('b', 'active', '2'), rule('c', 'pending_approval', '3'), rule('d', 'active', '1')]);
    assert.deepEqual(sorted.map((r) => r.id), ['c', 'd', 'b', 'a']);
  });
});

describe('the sidebar poll', () => {
  function fakeTimers() {
    const queue: { fn: () => void; ms: number }[] = [];
    return { queue, timers: { set: (fn: () => void, ms: number) => { queue.push({ fn, ms }); return queue.length; }, clear: () => { queue.length = 0; } } };
  }
  const flush = () => new Promise((resolve) => setImmediate(resolve));

  test('publishes the stored total and asks again in two minutes', async () => {
    publishMailUnread(0);
    const { queue, timers } = fakeTimers();
    const stop = pollMailUnread(async () => ({ enabled: true, sync_running: true, total: 7 }), timers, () => true);
    await flush();
    assert.equal(mailUnread(), 7);
    assert.equal(queue.at(-1)?.ms, 120_000);
    stop();
  });

  test('backs off after failures and stops when the server does not check', async () => {
    const { queue, timers } = fakeTimers();
    let fail = true;
    pollMailUnread(async () => { if (fail) throw new Error('offline'); return { enabled: true, sync_running: false, total: 3 }; }, timers, () => true);
    await flush();
    assert.equal(queue.at(-1)?.ms, 240_000);
    fail = false;
    const before = queue.length;
    queue.at(-1)!.fn();
    await flush();
    assert.equal(queue.length, before); // stopped: nothing scheduled after an answer without background checks
  });

  test('waits while the tab is hidden', async () => {
    const { queue, timers } = fakeTimers();
    let calls = 0;
    pollMailUnread(async () => { calls += 1; return { enabled: true, sync_running: true, total: 1 }; }, timers, () => false);
    await flush();
    assert.equal(calls, 0);
    assert.equal(queue.length, 1);
  });
});
