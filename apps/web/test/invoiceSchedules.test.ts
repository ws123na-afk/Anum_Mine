// Recurring invoices on the web (src/lib/invoiceSchedules.ts, docs/business.md#recurring-invoices): the form's body and the labels.
// Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { InvoiceSchedule } from '@anum/contracts';
import { draftOf, emptySchedule, frequencyText, hasInterval, nextText, scheduleBody, statusTone, summaryText } from '../src/lib/invoiceSchedules.ts';
import { placeholdersOf, setLocale } from '../src/lib/i18n.ts';
import { invoiceSchedulesAr } from '../src/lib/messages/invoiceSchedules.ar.ts';
import { invoiceSchedulesEn } from '../src/lib/messages/invoiceSchedules.en.ts';

afterEach(() => setLocale('en'));

const filled = () => ({ ...emptySchedule('2026-12-01', '15'), accountId: 'bacc_1', lines: [{ description: 'Support retainer', kind: 'service' as const, quantity: '1', unitPrice: '1000', taxPct: '15' }] });

describe('the schedule form', () => {
  test('a filled form becomes the request body with the lines priced by the server', () => {
    const body = scheduleBody(filled());
    assert.ok(body.ok);
    if (!body.ok) return;
    assert.equal(body.value.account_id, 'bacc_1');
    assert.equal(body.value.frequency, 'monthly');
    assert.equal(body.value.interval, undefined);
    assert.deepEqual(body.value.lines, [{ description: 'Support retainer', kind: 'service', quantity: 1, unit_price: 1000, tax_rate_pct: 15, tax_code: null }]);
    assert.equal(body.value.end_date, null);
    assert.equal(body.value.max_count, null);
  });

  test('each problem is said in words', () => {
    assert.equal(scheduleBody({ ...filled(), accountId: '' }).ok, false);
    assert.equal(scheduleBody({ ...filled(), startDate: 'soon' }).ok, false);
    assert.equal(scheduleBody({ ...filled(), lines: [] }).ok, false);
    assert.equal(scheduleBody({ ...filled(), frequency: 'weeks', interval: '60' }).ok, false);
    assert.equal(scheduleBody({ ...filled(), end: 'date', endDate: '2026-01-01' }).ok, false);
    assert.equal(scheduleBody({ ...filled(), end: 'count', maxCount: '0' }).ok, false);
    assert.equal(scheduleBody({ ...filled(), dueDays: '400' }).ok, false);
    const weeks = scheduleBody({ ...filled(), frequency: 'weeks', interval: '2', end: 'count', maxCount: '6' });
    assert.ok(weeks.ok && weeks.value.interval === 2 && weeks.value.max_count === 6);
  });

  test('a saved schedule fills the edit form', () => {
    const saved = { title: 'Retainer', account_id: 'bacc_1', currency: 'SAR', frequency: 'weeks', interval: 2, start_date: '2026-12-01', end_date: null, max_count: 4,
      due_in_days: 14, issue_mode: 'request_approval', customer_reference: 'PO-1', notes: '', payment_terms: '',
      lines: [{ description: 'Support', kind: 'service', quantity: 1, unit_price: 500, tax_rate_pct: 15, tax_code: null }] } as unknown as InvoiceSchedule;
    const draft = draftOf(saved);
    assert.equal(draft.end, 'count');
    assert.equal(draft.maxCount, '4');
    assert.equal(draft.lines[0].unitPrice, '500');
    assert.equal(hasInterval('weeks'), true);
    assert.equal(hasInterval('quarterly'), false);
  });
});

describe('labels', () => {
  test('frequency, next and the last run read in English and Arabic', () => {
    assert.equal(frequencyText({ frequency: 'monthly', interval: 1 }), 'Every month');
    assert.equal(frequencyText({ frequency: 'weeks', interval: 2 }), 'Every 2 weeks');
    assert.equal(nextText({ status: 'active', next_date: '2026-12-01', end_reason: '' }), 'Next draft on 2026-12-01');
    assert.equal(nextText({ status: 'ended', next_date: null, end_reason: 'count' }), 'Ended after its last invoice');
    assert.equal(statusTone('paused'), 'warning');
    assert.equal(summaryText(null), null);
    assert.match(summaryText({ reminders_planned: 2, emails_planned: 1, skipped_do_not_contact: 1, failed: 0, note: '' }) ?? '', /2 reminders.*1 skipped/);
    setLocale('ar');
    assert.equal(frequencyText({ frequency: 'weeks', interval: 2 }), 'كل أسبوعين');
  });

  test('every key exists in both languages with the same placeholders', () => {
    for (const key of Object.keys(invoiceSchedulesEn) as (keyof typeof invoiceSchedulesEn)[]) {
      const en = invoiceSchedulesEn[key], ar = invoiceSchedulesAr[key];
      assert.ok(ar, key);
      const words = (value: unknown) => (typeof value === 'string' ? value : Object.values(value as Record<string, string>).join(' '));
      for (const name of placeholdersOf(words(en))) {
        if (name === 'count') continue;
        assert.ok(words(ar).includes(`{${name}}`), `${key} {${name}}`);
      }
    }
  });
});
