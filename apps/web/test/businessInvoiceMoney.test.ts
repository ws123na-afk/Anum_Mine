// Unit tests for credit notes, customers' payment reports, the PDF download and the Accounts tab
// (docs/business.md#credit-notes, docs/business-portal.md#reporting-a-payment).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { InvoiceListItem } from '@anum/contracts';
import { creditBody, creditNoteTone, emptyCredit, noticeStatusLabel, noticeTone, noticesWaiting, paymentStateLabel } from '../src/lib/invoices.ts';
import { REPORT_METHODS, emptyReport, invoiceStatusText, invoiceStatusTone, localToday, reportBody, reportStatusLabel, reportTone } from '../src/lib/portal.ts';
import { fileNameFromDisposition } from '../src/lib/fileSave.ts';
import { accountPlace } from '../src/lib/accounts.ts';
import { TABS, tabFromHash } from '../src/lib/business.ts';
import { setLocale } from '../src/lib/i18n.ts';

describe('credit notes', () => {
  test('the form starts at the balance and checks the amount and the reason', () => {
    setLocale('en');
    assert.deepEqual(emptyCredit(1035), { amount: '1035.00', reason: '' });
    assert.deepEqual(emptyCredit(0), { amount: '', reason: '' });
    assert.deepEqual(creditBody({ amount: '100', reason: '  Price   correction ' }, 1035, 'SAR'), { ok: true, value: { amount: 100, reason: 'Price correction' } });
    assert.deepEqual(creditBody({ amount: '1,035.00', reason: 'Waived' }, 1035, 'SAR'), { ok: true, value: { amount: 1035, reason: 'Waived' } });
    for (const amount of ['', '0', 'abc', '10.005', '-5']) {
      const result = creditBody({ amount, reason: 'Waived' }, 1035, 'SAR');
      assert.equal(result.ok, false, amount);
    }
  });

  test('more than is owed is refused before the server is asked, and says how much is owed', () => {
    const result = creditBody({ amount: '1035.01', reason: 'Waived' }, 1035, 'SAR');
    assert.equal(result.ok, false);
    assert.match(result.ok ? '' : result.error, /at most the balance of/);
    assert.match(result.ok ? '' : result.error, /1,035\.00/);
  });

  test('a reason of at least three characters is required', () => {
    const result = creditBody({ amount: '10', reason: ' ab ' }, 100, 'SAR');
    assert.equal(result.ok, false);
    assert.match(result.ok ? '' : result.error, /three characters/);
  });

  test('labels and tones', () => {
    setLocale('en');
    assert.equal(paymentStateLabel('credited'), 'Settled by credit');
    assert.equal(noticeStatusLabel('pending'), 'Waiting');
    assert.equal(noticeTone('confirmed'), 'success');
    assert.equal(noticeTone('rejected'), 'dangerTone');
    assert.equal(noticeTone('pending'), 'warning');
    assert.equal(creditNoteTone('void'), 'dangerTone');
    assert.equal(creditNoteTone('issued'), 'neutral');
  });

  test('reports waiting add up over a page', () => {
    const rows = [{ notices_pending: 2 }, { notices_pending: 0 }, { notices_pending: 1 }] as Pick<InvoiceListItem, 'notices_pending'>[];
    assert.equal(noticesWaiting(rows), 3);
    assert.equal(noticesWaiting([]), 0);
  });
});

describe('a customer reports a payment', () => {
  const today = '2026-10-20';

  test('the form starts at the balance on today with a bank transfer', () => {
    assert.deepEqual(emptyReport(750, today, 'key-1'), { key: 'key-1', name: '', amount: '750.00', paidOn: today, method: 'bank_transfer', reference: '', note: '' });
    assert.notEqual(emptyReport(750, today).key, emptyReport(750, today).key);
    assert.ok(REPORT_METHODS.includes('bank_transfer') && REPORT_METHODS.includes('cash'));
  });

  test('a good report becomes the request body with a cleaned name', () => {
    const result = reportBody({ ...emptyReport(750, today, 'key-1'), name: '  Sara   Khan ', amount: '400', reference: ' TRX-9 ', note: 'by wire' }, 750, 'SAR', today);
    assert.deepEqual(result, { ok: true, value: { idempotency_key: 'key-1', payer_name: 'Sara Khan', amount: 400, paid_on: today, method: 'bank_transfer', reference: 'TRX-9', note: 'by wire' } });
  });

  test('each rule has its own sentence', () => {
    setLocale('en');
    const base = { ...emptyReport(750, today), name: 'Sara Khan' };
    const error = (draft: typeof base, balance = 750) => {
      const result = reportBody(draft, balance, 'SAR', today);
      return result.ok ? '' : result.error;
    };
    assert.match(error({ ...base, name: 'S' }), /full name/);
    assert.match(error({ ...base, name: '12 34' }), /full name/);
    assert.match(error({ ...base, amount: '0' }), /above zero/);
    assert.match(error({ ...base, amount: '750.01' }), /more than the balance of SAR/);
    assert.match(error({ ...base, paidOn: '2026-10-21' }), /date/);
    assert.match(error({ ...base, paidOn: '20/10/2026' }), /date/);
    assert.equal(error({ ...base, amount: '750' }), '');
  });

  test('a report is a request, never a status: the labels say it waits for the business', () => {
    setLocale('en');
    assert.equal(reportStatusLabel('pending'), 'Waiting for the business');
    assert.equal(reportStatusLabel('rejected'), 'Not accepted');
    assert.equal(reportTone('confirmed'), 'success');
    assert.equal(reportTone('pending'), 'warning');
  });

  test('an invoice settled by credit reads as settled, not open', () => {
    setLocale('en');
    const credited = { status: 'paid', overdue: false, payment_state: 'credited' };
    assert.equal(invoiceStatusText(credited), 'Settled by credit');
    assert.equal(invoiceStatusTone(credited), 'success');
    assert.equal(invoiceStatusText({ status: 'sent', overdue: false, payment_state: 'unpaid' }), 'Open');
  });

  test('today is the browser calendar day in YYYY-MM-DD', () => {
    assert.equal(localToday(new Date(2026, 9, 5, 23, 59)), '2026-10-05');
    assert.match(localToday(), /^\d{4}-\d{2}-\d{2}$/);
  });
});

describe('the PDF download', () => {
  test('the server file name is kept when it is safe, else the fallback is used', () => {
    assert.equal(fileNameFromDisposition('attachment; filename="INV-2026-0001.pdf"', 'invoice.pdf'), 'INV-2026-0001.pdf');
    assert.equal(fileNameFromDisposition('attachment; filename=draft-binv_1.pdf', 'invoice.pdf'), 'draft-binv_1.pdf');
    assert.equal(fileNameFromDisposition('attachment; filename="../../etc/passwd"', 'invoice.pdf'), 'invoice.pdf');
    assert.equal(fileNameFromDisposition(null, 'invoice.pdf'), 'invoice.pdf');
    assert.equal(fileNameFromDisposition('inline', 'invoice.pdf'), 'invoice.pdf');
  });
});

describe('the Accounts tab', () => {
  test('it is a Business tab that opens from the hash', () => {
    assert.ok(TABS.includes('accounts'));
    assert.equal(tabFromHash('#business-accounts'), 'accounts');
    assert.equal(tabFromHash('#business-accounts/bacc_1'), 'accounts');
    assert.ok(TABS.indexOf('accounts') > TABS.indexOf('leads'));
  });

  test('the place joins the city and the country that exist', () => {
    assert.equal(accountPlace({ city: 'Riyadh', country: 'SA' }), 'Riyadh, SA');
    assert.equal(accountPlace({ city: ' ', country: 'SA' }), 'SA');
    assert.equal(accountPlace({ city: '', country: '' }), '');
  });
});
