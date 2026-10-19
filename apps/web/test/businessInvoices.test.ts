// Unit tests for the Business invoice helpers (src/lib/invoices.ts, docs/business.md#invoices).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { Billable, InvoiceSummary } from '@anum/contracts';
import {
  createBody, emptyFields, emptyManualLine, emptyNewInvoice, emptyPayment, invoiceFiltersActive, invoiceHash, invoiceQuery, invoiceStatusLabel, invoiceTitle, invoiceTone,
  lineTotals, listDate, manualLines, overdueText, paymentBody, profileBody, profileDraft, summaryTiles, today, updateBody, defaultInvoiceFilters,
} from '../src/lib/invoices.ts';
import { tabFromHash, TABS } from '../src/lib/business.ts';
import { setLocale } from '../src/lib/i18n.ts';

const billable: Billable = {
  account_id: 'bacc_1', project_id: 'bprj_1', currency: 'SAR', tax_rate_pct: 15,
  milestones: [
    { id: 'm1', position: 1, title: 'Design', amount: 1000, due_on: null, status: 'accepted', change_order_number: null, billable: true, reason: '' },
    { id: 'm2', position: 2, title: 'Extra pages', amount: 500, due_on: null, status: 'accepted', change_order_number: 1, billable: true, reason: '' },
    { id: 'm3', position: 3, title: 'Build', amount: 2000, due_on: null, status: 'planned', change_order_number: null, billable: false, reason: 'Already on invoice INV-0001.' },
  ],
  contract: null,
  usage: [{ id: 'u1', description: 'Hosting', kind: 'external_cost', occurred_on: '2026-10-01', quantity: 1, unit_cost: 80, amount: 80, currency: 'SAR', project_id: 'bprj_1', billable: true, reason: '' }],
  offers: [{ id: 'o1', label: 'Support v1', currency: 'SAR', price_type: 'hourly', price: null }, { id: 'o2', label: 'Setup v2', currency: 'SAR', price_type: 'fixed', price: 300 }],
};

describe('business invoices', () => {
  test('the invoices tab and an invoice open from the hash', () => {
    assert.ok(TABS.includes('invoices'));
    assert.equal(tabFromHash('#business-invoices'), 'invoices');
    assert.equal(tabFromHash('#business-invoices/binv_1'), 'invoices');
    assert.equal(invoiceHash('binv 1'), '#business-invoices/binv%201');
    assert.equal(invoiceHash(null), '#business-invoices');
  });

  test('status labels, tones and the overdue text', () => {
    setLocale('en');
    assert.equal(invoiceStatusLabel('pending_approval'), 'Waiting for approval');
    assert.equal(invoiceTone('paid'), 'success');
    assert.equal(invoiceTone('void'), 'dangerTone');
    assert.equal(invoiceTone('sent', true), 'dangerTone');
    assert.equal(invoiceTone('sent'), 'neutral');
    assert.equal(overdueText({ overdue: true, days_overdue: 1 }), 'Overdue by 1 day');
    assert.equal(overdueText({ overdue: true, days_overdue: 5 }), 'Overdue by 5 days');
    assert.equal(overdueText({ overdue: false, days_overdue: 0 }), '');
    assert.equal(invoiceTitle({ number: null }), 'Draft invoice');
    assert.equal(invoiceTitle({ number: 'INV-0007' }), 'INV-0007');
    assert.equal(listDate({ status: 'sent', due_date: '2026-11-01', issue_date: '2026-10-01' }), '2026-11-01');
    assert.equal(listDate({ status: 'paid', due_date: '2026-11-01', issue_date: '2026-10-01' }), '2026-10-01');
  });

  test('the Arabic labels follow the locale', () => {
    setLocale('ar');
    assert.equal(invoiceStatusLabel('paid'), 'مدفوعة');
    assert.match(overdueText({ overdue: true, days_overdue: 2 }), /يومين/);
    setLocale('en');
  });

  test('summary tiles are one block per currency and never added together', () => {
    setLocale('en');
    const summary: InvoiceSummary = {
      counts: { draft: 2, pending_approval: 0, sent: 3, paid: 1, void: 0 },
      currencies: [
        { currency: 'SAR', outstanding: 1150, overdue: 575, overdue_count: 1, invoiced_this_month: 2300, collected_this_month: 1150, drafts: 100 },
        { currency: 'USD', outstanding: 0, overdue: 0, overdue_count: 0, invoiced_this_month: 0, collected_this_month: 0, drafts: 0 },
      ],
      truncated: false, notes: [],
    };
    const blocks = summaryTiles(summary);
    assert.deepEqual(blocks.map((block) => block.currency), ['SAR', 'USD']);
    assert.equal(blocks[0].tiles.length, 5);
    assert.match(blocks[0].tiles[0].value, /SAR/);
    assert.match(blocks[0].tiles[0].value, /1,150\.00/);
    assert.equal(blocks[0].tiles[1].tone, 'danger');
    assert.equal(blocks[1].tiles[1].tone, '');
    assert.match(blocks[1].tiles[0].value, /\$0\.00|USD/);
    assert.equal(blocks[0].tiles[4].hint, '2 drafts');
  });

  test('list filters become the query the API reads', () => {
    const filters = defaultInvoiceFilters();
    assert.equal(invoiceFiltersActive(filters), false);
    assert.deepEqual(invoiceQuery(filters, 0), { limit: 25, offset: 0 });
    assert.deepEqual(invoiceQuery({ ...filters, status: 'sent', q: ' acme ' }, 25), { status: ['sent'], q: 'acme', limit: 25, offset: 25 });
    assert.deepEqual(invoiceQuery({ ...filters, overdue: true, status: 'sent' }, 0), { overdue: true, limit: 25, offset: 0 });
    assert.equal(invoiceFiltersActive({ ...filters, overdue: true }), true);
  });

  test('line totals round the way the server does', () => {
    assert.deepEqual(lineTotals(3, 33.33, 15), { net: 99.99, tax: 15, total: 114.99 });
    assert.deepEqual(lineTotals(1, 1000, 0), { net: 1000, tax: 0, total: 1000 });
  });

  test('a new invoice from milestones sends only the billable ones and the tax rate', () => {
    setLocale('en');
    const draft = { ...emptyNewInvoice(), milestoneIds: ['m1', 'm2', 'm3'], taxRate: '15', dueDays: '30', reference: 'PO 7' };
    const body = createBody({ accountId: 'bacc_1', projectId: 'bprj_1' }, draft, billable);
    assert.ok(body.ok);
    assert.deepEqual(body.value, { due_in_days: 30, customer_reference: 'PO 7', tax_rate_pct: 15, project_id: 'bprj_1', milestone_ids: ['m1', 'm2'] });
  });

  test('costs carry the markup; an offer without a fixed price needs one; nothing chosen is refused', () => {
    setLocale('en');
    const costs = createBody({ accountId: 'bacc_1', projectId: null }, { ...emptyNewInvoice(), usageIds: ['u1'], markup: '10', currency: 'USD' }, billable);
    assert.ok(costs.ok);
    assert.equal(costs.value.usage_markup_pct, 10);
    assert.equal(costs.value.account_id, 'bacc_1');
    assert.equal(costs.value.currency, 'USD');
    assert.equal(costs.value.project_id, undefined);
    const hourly = createBody({ accountId: 'bacc_1', projectId: null }, { ...emptyNewInvoice(), offerId: 'o1', offerQty: '4' }, billable);
    assert.equal(hourly.ok, false);
    const priced = createBody({ accountId: 'bacc_1', projectId: null }, { ...emptyNewInvoice(), offerId: 'o1', offerQty: '4', offerPrice: '250' }, billable);
    assert.ok(priced.ok);
    assert.deepEqual([priced.value.offer_id, priced.value.offer_quantity, priced.value.offer_unit_price], ['o1', 4, 250]);
    const fixed = createBody({ accountId: 'bacc_1', projectId: null }, { ...emptyNewInvoice(), offerId: 'o2' }, billable);
    assert.ok(fixed.ok);
    assert.equal(fixed.value.offer_unit_price, undefined);
    const nothing = createBody({ accountId: 'bacc_1', projectId: 'bprj_1' }, emptyNewInvoice(), billable);
    assert.equal(nothing.ok, false);
    assert.match(nothing.ok ? '' : nothing.error, /Choose something to invoice/);
    const bad = createBody({ accountId: 'bacc_1', projectId: 'bprj_1' }, { ...emptyNewInvoice(), milestoneIds: ['m1'], taxRate: '120' }, billable);
    assert.equal(bad.ok, false);
  });

  test('the unbilled contract share is limited to what is left', () => {
    setLocale('en');
    const open: Billable = { ...billable, milestones: [], contract: { contract_total: 10000, invoiced: 4000, remaining: 6000 } };
    const ok = createBody({ accountId: 'bacc_1', projectId: 'bprj_1' }, { ...emptyNewInvoice(), contractAmount: '2,500.50' }, open);
    assert.ok(ok.ok);
    assert.equal(ok.value.contract_amount, 2500.5);
    const tooMuch = createBody({ accountId: 'bacc_1', projectId: 'bprj_1' }, { ...emptyNewInvoice(), contractAmount: '6000.01' }, open);
    assert.equal(tooMuch.ok, false);
    assert.match(tooMuch.ok ? '' : tooMuch.error, /Only .*6,000\.00 of the contract is left/);
  });

  test('manual lines are checked and blank rows are ignored', () => {
    setLocale('en');
    const good = manualLines([{ ...emptyManualLine(), description: 'Travel', unitPrice: '120', quantity: '2' }, emptyManualLine()]);
    assert.ok(good.ok);
    assert.deepEqual(good.value, [{ description: 'Travel', kind: 'service', quantity: 2, unit_price: 120 }]);
    assert.equal(manualLines([{ ...emptyManualLine(), description: 'Travel', unitPrice: 'abc' }]).ok, false);
    assert.equal(manualLines([{ ...emptyManualLine(), unitPrice: '5' }]).ok, false);
    assert.equal(manualLines([{ ...emptyManualLine(), description: 'x', unitPrice: '5', quantity: '0' }]).ok, false);
    const taxed = manualLines([{ ...emptyManualLine(), description: 'x', unitPrice: '5', taxPct: '0' }]);
    assert.ok(taxed.ok);
    assert.equal(taxed.value[0].tax_rate_pct, 0);
  });

  test('editing sends every text field, replaces manual lines and removes sourced ones by position', () => {
    setLocale('en');
    const body = updateBody({ ...emptyFields('14'), notes: '', billName: 'Acme Ltd' }, [], [3, 1], true);
    assert.ok(body.ok);
    assert.equal(body.value.notes, '');
    assert.equal(body.value.bill_to_name, 'Acme Ltd');
    assert.deepEqual(body.value.lines, []);
    assert.deepEqual(body.value.remove_lines, [1, 3]);
    assert.equal(body.value.due_in_days, 14);
    const untouched = updateBody(emptyFields('14'), [], [], false);
    assert.ok(untouched.ok);
    assert.equal(untouched.value.lines, undefined);
    assert.equal(untouched.value.remove_lines, undefined);
  });

  test('a payment cannot exceed the balance and needs a date', () => {
    setLocale('en');
    const draft = emptyPayment(1150, '2026-10-19');
    assert.equal(draft.amount, '1150.00');
    const ok = paymentBody({ ...draft, amount: '500', reference: ' TT-9 ' }, 1150, 'SAR');
    assert.ok(ok.ok);
    assert.deepEqual(ok.value, { amount: 500, paid_on: '2026-10-19', method: 'bank_transfer', reference: 'TT-9', note: '' });
    const over = paymentBody({ ...draft, amount: '1150.01' }, 1150, 'SAR');
    assert.equal(over.ok, false);
    assert.match(over.ok ? '' : over.error, /more than the balance/);
    assert.equal(paymentBody({ ...draft, amount: '0' }, 1150, 'SAR').ok, false);
    assert.equal(paymentBody({ ...draft, paidOn: '' }, 1150, 'SAR').ok, false);
  });

  test('the billing profile is checked like the API does', () => {
    setLocale('en');
    const profile = {
      legal_name: ' Acme ', address: '', tax_label: 'VAT no.', tax_number: '300', email: '', phone: '', bank_details: 'IBAN SA00', footer_note: '', invoice_prefix: 'INV',
      default_due_days: 14, default_tax_rate_pct: 15,
    };
    const draft = profileDraft(profile);
    assert.equal(draft.default_due_days, '14');
    const ok = profileBody(draft);
    assert.ok(ok.ok);
    assert.equal(ok.value.legal_name, 'Acme');
    assert.equal(ok.value.default_tax_rate_pct, 15);
    assert.equal(profileBody({ ...draft, invoice_prefix: 'INV-2026-LONG' }).ok, false);
    assert.equal(profileBody({ ...draft, default_due_days: '400' }).ok, false);
    assert.equal(profileBody({ ...draft, default_tax_rate_pct: '101' }).ok, false);
  });

  test('today is a calendar date in the browser zone', () => {
    assert.equal(today(new Date(2026, 9, 5, 23, 30)), '2026-10-05');
  });
});
