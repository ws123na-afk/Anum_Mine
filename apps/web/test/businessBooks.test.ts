// Unit tests for the business books helpers (src/lib/businessBooks.ts, docs/business-books.md#web).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { BooksReport } from '@anum/contracts';
import {
  BOOKS_TABS, billBody, billProblem, booksTabFromId, budgetProblem, emptyBill, intercompanyProblem, lineLabel, moneyList, normalizeCategory, presetPeriod,
  reportIsEmpty, sectionLabel, settingsBody, statementRows, validAmount, validRate,
} from '../src/lib/businessBooks.ts';
import { TABS, tabFromHash } from '../src/lib/business.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { businessBooksAr } from '../src/lib/messages/businessBooks.ar.ts';
import { businessBooksEn } from '../src/lib/messages/businessBooks.en.ts';

const group = (lines: [string, string][], total: string) => ({ lines: lines.map(([key, amount]) => ({ key, amount })), total });

function report(extra: Partial<BooksReport> = {}): BooksReport {
  return {
    period: { from: '2026-01-01', to: '2026-12-31', basis: 'accrual' }, base_currency: 'SAR',
    pnl: { currency: 'SAR', revenue: group([['service', '900.00'], ['credit_notes', '-100.00']], '800.00'), cost_of_sales: group([['fuel', '150.00']], '150.00'),
      gross_profit: '650.00', gross_margin_pct: '81.3', operating_expenses: group([['office_rent', '1000.00']], '1000.00'), operating_profit: '-350.00',
      other_income: group([], '0.00'), finance_costs: group([], '0.00'), fx_differences: '0.00', net_profit: '-350.00', net_margin_pct: null, not_converted: [], complete: true },
    tax: { label: 'VAT', country: 'SA', by_currency: [], allocations_without_tax_split: 0 },
    cash_flow: { lines: {}, net: [], paid_by_members: {} },
    balances: { as_of: '2026-12-31', aged_on: '2026-11-04', receivables: [], outstanding_invoices: [], outstanding_invoices_count: 0, payables: [], unpaid_bills: [],
      unpaid_bills_count: 0, owed_to_members: [], debt: [], intercompany: [] },
    equity: { contributions: [], drawings: [] }, trend: [], customers: [], projects: [], cost_centres: [], budgets: [], recurring: { items: [], per_month: [] },
    allocations_in_review: 0, conversions: [], notes: [], truncated: false, ...extra,
  };
}

describe('business books helpers', () => {
  test('the Books tab is a Business tab and its sections are deep-linkable', () => {
    assert.ok(TABS.includes('books'));
    assert.equal(tabFromHash('#business-books/bills'), 'books');
    assert.equal(booksTabFromId('bills'), 'bills');
    assert.equal(booksTabFromId(null), 'overview');
    assert.equal(booksTabFromId('nonsense'), 'overview');
    assert.equal(BOOKS_TABS[0], 'overview');
  });

  test('amounts, categories and rates are checked before sending', () => {
    assert.ok(validAmount('150'));
    assert.ok(validAmount('12.345'));
    assert.ok(!validAmount('0'));
    assert.ok(validAmount('0', true));
    assert.ok(!validAmount('-5'));
    assert.ok(!validAmount('1.2345'));
    assert.ok(!validAmount('1e3'));
    assert.equal(normalizeCategory('Office Rent'), 'office_rent');
    assert.equal(normalizeCategory('  fuel-cards '), 'fuel_cards');
    assert.ok(validRate('3.6725'));
    assert.ok(!validRate('0'));
    assert.ok(!validRate('abc'));
  });

  test('a bill form names its first problem and its body sends what the server expects', () => {
    const draft = emptyBill('2026-11-04', 'SAR');
    assert.equal(billProblem(draft), 'books.bill.needVendor');
    const filled = { ...draft, vendor_id: 'v1', description: 'Rent', subtotal: '1000', tax: '150', category: 'Office Rent' };
    assert.equal(billProblem(filled), null);
    assert.equal(billProblem({ ...filled, due_date: '2026-11-01' }), 'books.bill.dueBeforeDate');
    assert.equal(billProblem({ ...filled, subtotal: '0', tax: '0' }), 'books.amountInvalid');
    assert.equal(billProblem({ ...filled, useful_life_months: '36' }), 'books.bill.lifeNeedsCapital');
    assert.equal(billProblem({ ...filled, section: 'capital', useful_life_months: '36' }), null);
    const body = billBody({ ...filled, section: 'capital', useful_life_months: '36' });
    assert.deepEqual([body.category, body.useful_life_months, body.due_date, body.section], ['office_rent', 36, null, 'capital']);
    assert.equal(billBody({ ...filled, useful_life_months: '36' }).useful_life_months, null);
  });

  test('settings send category maps keyed by the ledger category and keep the version', () => {
    const body = settingsBody({ base_currency: 'AED', country: '', fiscal_year_start_month: 4, basis: 'cash', tax_label: ' VAT ', rate_max_age_days: '10',
      mappings: [{ category: 'Fuel', section: 'cost_of_sales' }, { category: '', section: 'excluded' }] }, null);
    assert.deepEqual(body, { base_currency: 'AED', country: null, fiscal_year_start_month: 4, basis: 'cash', tax_label: 'VAT',
      category_sections: { fuel: 'cost_of_sales' }, rate_max_age_days: 10, version: null });
  });

  test('budget and intercompany forms', () => {
    assert.equal(budgetProblem({ name: 'R', period_start: '2026-11-01', months: 1, amount: '1', section: null, category: null, project_id: null, cost_centre_id: null }), 'books.budget.needName');
    assert.equal(budgetProblem({ name: 'Rent', period_start: '2026-11-01', months: 1, amount: '1200', section: null, category: 'rent', project_id: null, cost_centre_id: null }), null);
    assert.equal(intercompanyProblem({ workspace_id: '', kind: 'loan', direction: 'payable', entry_date: '2026-11-01', currency: 'SAR', amount: '1', description: 'Loan' }), 'books.ic.needParty');
    assert.equal(intercompanyProblem({ workspace_id: 'w', kind: 'loan', direction: 'payable', entry_date: '2026-11-01', currency: 'SAR', amount: '1', description: 'x' }), 'books.needDescription3');
  });

  test('the statement lists lines, totals and results in order and marks losses', () => {
    setLocale('en');
    const rows = statementRows(report());
    assert.deepEqual(rows.map((row) => row.key), ['revenue.service', 'revenue.credit_notes', 'revenue.total', 'cost_of_sales.fuel', 'cost_of_sales.total', 'gross_profit',
      'operating_expenses.office_rent', 'operating_expenses.total', 'operating_profit', 'net_profit']);
    assert.equal(rows.find((row) => row.key === 'operating_expenses.office_rent')?.label, 'Office rent');
    assert.equal(rows.find((row) => row.key === 'revenue.credit_notes')?.negative, true);
    assert.equal(rows.at(-1)?.kind, 'result');
    assert.equal(lineLabel('credit_notes'), 'Credit notes');
    assert.equal(sectionLabel('excluded'), 'Not counted');
    const withExtras = statementRows(report({ pnl: { ...report().pnl, finance_costs: group([['loan_interest', '20.00']], '20.00'), fx_differences: '5.00' } }));
    assert.ok(withExtras.some((row) => row.key === 'finance_costs.total') && withExtras.some((row) => row.key === 'fx_differences'));
  });

  test('an empty period is recognised; anything owed or not converted is not empty', () => {
    const zero = report({ pnl: { ...report().pnl, revenue: group([], '0.00'), cost_of_sales: group([], '0.00'), operating_expenses: group([], '0.00') } });
    assert.ok(reportIsEmpty(zero));
    assert.ok(!reportIsEmpty({ ...zero, pnl: { ...zero.pnl, not_converted: [{ currency: 'USD', groups: { operating_expense: '10.00' } }] } }));
    assert.ok(!reportIsEmpty({ ...zero, balances: { ...zero.balances, payables: [{ currency: 'SAR', total: '10.00' }] } }));
    assert.ok(!reportIsEmpty(report()));
  });

  test('period presets follow the financial year', () => {
    const today = new Date(2026, 10, 4);
    assert.deepEqual(presetPeriod('this_month', today, 1), { from: '2026-11-01', to: '2026-11-30' });
    assert.deepEqual(presetPeriod('last_month', today, 1), { from: '2026-10-01', to: '2026-10-31' });
    assert.deepEqual(presetPeriod('this_year', today, 4), { from: '2026-04-01', to: '2027-03-31' });
    assert.deepEqual(presetPeriod('last_year', today, 4), { from: '2025-04-01', to: '2026-03-31' });
    assert.deepEqual(presetPeriod('this_quarter', today, 4), { from: '2026-10-01', to: '2026-12-31' });
    assert.deepEqual(presetPeriod('this_quarter', today, 2), { from: '2026-11-01', to: '2027-01-31' });
    assert.deepEqual(presetPeriod('this_year', new Date(2026, 1, 10), 4), { from: '2025-04-01', to: '2026-03-31' });
  });

  test('money lists never add currencies', () => {
    setLocale('en');
    assert.equal(moneyList([]), '-');
    assert.match(moneyList([{ currency: 'SAR', amount: '150.00' }, { currency: 'USD', amount: '10.00' }]), /SAR.*150\.00.*·.*USD.*10\.00/);
  });

  test('the Arabic catalog has every English key and no long dashes', () => {
    assert.deepEqual(Object.keys(businessBooksAr).sort(), Object.keys(businessBooksEn).sort());
    for (const value of [...Object.values(businessBooksEn), ...Object.values(businessBooksAr)]) {
      assert.doesNotMatch(JSON.stringify(value), /[–—]/);
    }
    setLocale('ar');
    assert.equal(sectionLabel('capital'), 'أصل رأسمالي');
    setLocale('en');
  });
});

describe('business books review fixes', () => {
  test('a zero exchange difference in a three-decimal currency is not shown', () => {
    setLocale('en');
    const base = report();
    const rows = statementRows({ ...base, base_currency: 'KWD', pnl: { ...base.pnl, currency: 'KWD', fx_differences: '0.000' } });
    assert.ok(!rows.some((row) => row.key === 'fx_differences'));
  });
});

describe('a quiet period still shows what is carried', () => {
  test('loans, money owed to members and intercompany balances keep the report from being empty', () => {
    const base = report();
    const zero = { ...base, pnl: { ...base.pnl, revenue: group([], '0.00'), cost_of_sales: group([], '0.00'), operating_expenses: group([], '0.00') } };
    assert.ok(reportIsEmpty(zero));
    assert.ok(!reportIsEmpty({ ...zero, balances: { ...zero.balances, debt: [{ currency: 'SAR', amount: '10000.00' }] } }));
    assert.ok(!reportIsEmpty({ ...zero, balances: { ...zero.balances, owed_to_members: [{ currency: 'SAR', amount: '80.00' }] } }));
    assert.ok(!reportIsEmpty({ ...zero, balances: { ...zero.balances, intercompany: [{ workspace_id: 'w', name: 'Other', currency: 'SAR', amount: '-50.00' }] } }));
    assert.ok(!reportIsEmpty({ ...zero, equity: { contributions: [], drawings: [{ currency: 'SAR', amount: '5.00' }] } }));
  });
});
