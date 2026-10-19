// Unit tests for change orders, the usage ledger and profitability helpers (src/lib/businessChanges.ts, docs/business.md#change-orders).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  aiImportBody, changeBody, changeDraftOf, changeStatusLabel, changeTone, emptyChangeDraft, emptyUsageDraft, lastFinishedMonth, moneyOrDash, pctOrDash,
  signedMoney, timelineText, usageAmount, usageBody, usageKindLabel,
} from '../src/lib/businessChanges.ts';
import { setLocale } from '../src/lib/i18n.ts';

describe('business change orders', () => {
  test('a change order body needs a title and at least one change; lines are checked', () => {
    setLocale('en');
    const empty = emptyChangeDraft();
    assert.deepEqual(changeBody(empty), { ok: false, error: 'Say what changes (at least 2 characters).' });
    assert.equal(changeBody({ ...empty, title: 'Extra' }).ok, false);  // the blank line is skipped, so nothing changes
    const body = changeBody({ ...empty, title: '  Second   dashboard ', requestedBy: ' Sara ', timelineDays: '10', costDelta: '-200.5',
      lines: [{ action: 'add', description: 'Fuel dashboard', kind: 'service', quantity: '1', unit_price: '800' }, { action: 'remove', description: 'Old', kind: 'other', quantity: '2', unit_price: '50.25' }] });
    assert.deepEqual(body, { ok: true, value: {
      title: 'Second dashboard', request: '', requested_by: 'Sara', timeline_days: 10, internal_cost_delta: -200.5,
      lines: [{ action: 'add', description: 'Fuel dashboard', kind: 'service', quantity: 1, unit_price: 800 }, { action: 'remove', description: 'Old', kind: 'other', quantity: 2, unit_price: 50.25 }],
    } });
    assert.equal(changeBody({ ...empty, title: 'Extra', lines: [{ action: 'add', description: 'X', kind: 'service', quantity: '0', unit_price: '5' }] }).ok, false);
    assert.equal(changeBody({ ...empty, title: 'Extra', lines: [{ action: 'add', description: 'X', kind: 'service', quantity: '1', unit_price: '5.555' }] }).ok, false);
    assert.equal(changeBody({ ...empty, title: 'Extra', timelineDays: '1.5' }).ok, false);
    assert.equal(changeBody({ ...empty, title: 'Extra', timelineDays: '-3' }).ok, true);
    assert.equal(changeBody({ ...empty, title: 'Extra', costDelta: 'abc' }).ok, false);
  });

  test('an existing change order round-trips into the form', () => {
    const draft = changeDraftOf({ title: 'T', request: 'R', requested_by: 'S', timeline_days: -2, internal_cost_delta: 5, lines: [{ action: 'remove', description: 'A', kind: 'labor', quantity: 1.5, unit_price: 20 }] });
    assert.deepEqual(draft.lines[0], { action: 'remove', description: 'A', kind: 'labor', quantity: '1.5', unit_price: '20' });
    assert.equal(draft.timelineDays, '-2');
  });

  test('labels, tones, deltas and empty values are "-"', () => {
    setLocale('en');
    assert.equal(changeStatusLabel('pending_approval'), 'Waiting for an owner');
    assert.equal(changeTone('approved'), 'success');
    assert.equal(changeTone('withdrawn'), 'dangerTone');
    assert.equal(moneyOrDash(null, 'SAR'), '-');
    assert.equal(pctOrDash(null), '-');
    assert.equal(signedMoney(undefined, 'SAR'), '-');
    assert.match(signedMoney(800, 'SAR'), /^\+SAR\s?800\.00$/);
    assert.match(signedMoney(-344.99, 'SAR'), /^-SAR\s?344\.99$/);
    assert.equal(timelineText(0), 'no change');
    assert.equal(timelineText(10), '10 days later');
    assert.equal(timelineText(-3), '3 days sooner');
    assert.equal(usageKindLabel('ai_usage'), 'AI usage');
    setLocale('ar');
    assert.equal(changeStatusLabel('approved'), 'معتمد');
    setLocale('en');
  });

  test('ledger entries: quantity times cost rounded half up like the API; checks name what to fix', () => {
    assert.equal(usageAmount(7.5, 80.33), 602.48);
    assert.equal(usageAmount(8, 80.33), 642.64);
    assert.equal(usageAmount(3, 333.33), 999.99);
    const draft = emptyUsageDraft('SAR', '2026-11-03');
    assert.equal(usageBody('p1', draft).ok, false);
    assert.deepEqual(usageBody('p1', { ...draft, description: 'Build', quantity: '7.5', unitCost: '80.33', billable: true }), { ok: true, value: {
      project_id: 'p1', kind: 'labour', description: 'Build', occurred_on: '2026-11-03', quantity: 7.5, unit_cost: 80.33, currency: 'SAR', billable: true,
    } });
    const hours = usageBody('p1', { ...draft, description: 'Build', quantity: '0', unitCost: '1' });
    assert.equal(hours.ok, false);
    assert.equal(!hours.ok && hours.error, 'Enter the hours as a number above zero with at most two decimals.');
    assert.equal(usageBody('p1', { ...draft, description: 'Build', occurredOn: '' , unitCost: '1' }).ok, false);
  });

  test('AI usage imports only a finished month and a share above 0 up to 100', () => {
    const now = new Date(Date.UTC(2026, 9, 18));
    assert.equal(lastFinishedMonth(now), '2026-09');
    assert.equal(lastFinishedMonth(new Date(Date.UTC(2027, 0, 5))), '2026-12');
    assert.deepEqual(aiImportBody('p1', '2026-09', '50', false, now), { ok: true, value: { project_id: 'p1', month: '2026-09', share_pct: 50, billable: false } });
    assert.equal(aiImportBody('p1', '2026-10', '50', false, now).ok, false);
    assert.equal(aiImportBody('p1', '2026-09', '0', false, now).ok, false);
    assert.equal(aiImportBody('p1', '2026-09', '101', false, now).ok, false);
  });
});
