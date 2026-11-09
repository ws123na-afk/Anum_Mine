// Unit tests for the business journal helpers (src/lib/businessLedger.ts, docs/business-books.md#the-journal).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { LedgerBalanceSheet, LedgerPnl, LedgerTrialRow } from '@anum/contracts';
import {
  SUBTYPE_DIGIT, accountName, accountProblem, balanceRows, closingNet, issueText, lastMonthEnd, manualBody, manualProblem, manualTotals, pnlRows, sourceHash,
} from '../src/lib/businessLedger.ts';
import { BOOKS_TABS, booksTabFromId, settingsBody } from '../src/lib/businessBooks.ts';

const block = (total: string, rows: { code: string; name: string; amount: string; system_key?: string | null }[] = []) => ({
  total, rows: rows.map((row) => ({ account_id: `acc_${row.code}`, subtype: 'x', system_key: row.system_key ?? null, ...row })),
});

describe('business journal', () => {
  test('the new sections open from their hash and the adjustments keep theirs', () => {
    for (const tab of ['ledger', 'statements', 'accounts', 'close', 'journal'] as const) {
      assert.ok(BOOKS_TABS.includes(tab));
      assert.equal(booksTabFromId(tab), tab);
    }
  });

  test('account codes follow the kind of account', () => {
    assert.equal(SUBTYPE_DIGIT.bank, '1');
    assert.equal(SUBTYPE_DIGIT.operating_expense, '6');
    assert.equal(accountProblem({ code: '6200', name: 'Rent', subtype: 'operating_expense', category: 'office_rent' }), null);
    assert.equal(accountProblem({ code: '1200', name: 'Rent', subtype: 'operating_expense', category: '' }), 'ledger.account.codeRange');
    assert.equal(accountProblem({ code: '62', name: 'Rent', subtype: 'operating_expense', category: '' }), 'ledger.account.badCode');
    assert.equal(accountProblem({ code: '6200', name: 'R', subtype: 'operating_expense', category: '' }), 'ledger.account.needName');
    assert.equal(accountProblem({ code: '1050', name: 'Petty cash', subtype: 'bank', category: 'rent' }), 'ledger.account.categoryKind');
    assert.equal(accountProblem({ code: '6200', name: 'Rent', subtype: '', category: '' }), 'ledger.account.needSubtype');
  });

  test('a manual entry balances to the minor unit before it is sent', () => {
    const lines = [{ account_id: 'a', debit: '100.10', credit: '' }, { account_id: 'b', debit: '', credit: '100.1' }];
    assert.deepEqual(manualTotals(lines), { debit: 100100, credit: 100100 });
    const form = { entry_date: '2026-11-01', description: 'Opening balance', lines };
    assert.equal(manualProblem(form), null);
    assert.deepEqual(manualBody(form).lines, [{ account_id: 'a', debit: '100.10', credit: '0' }, { account_id: 'b', debit: '0', credit: '100.1' }]);
    assert.equal(manualProblem({ ...form, lines: [lines[0], { ...lines[1], credit: '100' }] }), 'ledger.manual.unbalanced');
    assert.equal(manualProblem({ ...form, lines: [lines[0]] }), 'ledger.manual.twoLines');
    assert.equal(manualProblem({ ...form, lines: [{ ...lines[0], credit: '1' }, lines[1]] }), 'ledger.manual.oneSide');
    assert.equal(manualProblem({ ...form, lines: [{ ...lines[0], account_id: '' }, lines[1]] }), 'ledger.manual.needAccount');
    assert.equal(manualProblem({ ...form, lines: [{ ...lines[0], debit: '1,000' }, lines[1]] }), 'ledger.manual.badAmount');
    assert.equal(manualProblem({ ...form, description: 'x' }), 'ledger.manual.needWords');
  });

  test('the month a close usually covers is the one before today', () => {
    assert.equal(lastMonthEnd('2026-11-09'), '2026-10-31');
    assert.equal(lastMonthEnd('2026-03-01'), '2026-02-28');
    assert.equal(lastMonthEnd('2026-01-15'), '2025-12-31');
  });

  test('statement rows read in order and system accounts are named in the reader\'s language', () => {
    const pnl: LedgerPnl = { currency: 'SAR', revenue: block('1000.00', [{ code: '4000', name: 'Revenue: services', amount: '1000.00', system_key: 'revenue_service' }]),
      cost_of_sales: block('0.00'), gross_profit: '1000.00', gross_margin_pct: '100.0', operating_expenses: block('400.00', [{ code: '6200', name: 'Office rent', amount: '400.00' }]),
      operating_profit: '600.00', other_income: block('0.00'), finance_costs: block('0.00'), fx_differences: '0.00', net_profit: '600.00', net_margin_pct: '60.0' };
    const rows = pnlRows(pnl);
    assert.deepEqual(rows.map((row) => row.key), ['revenue:acc_4000', 'revenue', 'cost_of_sales', 'gross_profit', 'operating_expenses:acc_6200', 'operating_expenses',
      'operating_profit', 'other_income', 'finance_costs', 'fx_differences', 'net_profit']);
    assert.equal(rows[0].label, '4000 Revenue: services');
    assert.equal(rows[4].label, '6200 Office rent');
    assert.equal(accountName({ system_key: 'bank', name: 'Main bank' }), 'Main bank');  // renamed by the owner: kept
    const sheet: LedgerBalanceSheet = { currency: 'SAR', as_of: '2026-11-09', assets: { current: block('1000.00'), non_current: block('0.00'), total: '1000.00' },
      liabilities: { current: block('400.00'), non_current: block('0.00'), total: '400.00' },
      equity: block('600.00', [{ code: '', name: 'Result not yet closed', amount: '600.00', system_key: 'unclosed_result' }]),
      liabilities_and_equity: '1000.00', balanced: true, difference: '0.00', complete: true };
    const bs = balanceRows(sheet);
    assert.equal(bs.at(-1)?.key, 'liabilities_and_equity');
    assert.ok(bs.some((row) => row.key === 'assets' && row.kind === 'result'));
  });

  test('trial balance rows give one signed balance and issues say why they wait', () => {
    const row = { closing_debit: '0.00', closing_credit: '250.50' } as LedgerTrialRow;
    assert.equal(closingNet(row), '-250.50');
    assert.match(issueText({ event_key: 'bill:1', source_kind: 'bill', source_id: '1', event_date: '2026-11-01', reason: 'missing_rate', currency: 'USD',
      rate_date: '2026-11-01', description: 'Bill' }), /USD/);
  });

  test('an entry opens its source record', () => {
    assert.equal(sourceHash('invoice', 'inv_1'), 'business-invoices/inv_1');
    assert.equal(sourceHash('bill_payment', 'bpay_1'), 'business-books/bills');
    assert.equal(sourceHash('manual', 'bjm_1'), null);
    assert.equal(sourceHash('bill', null), null);
  });

  test('settings carry the owner\'s policy note only when the form has one', () => {
    const form = { base_currency: 'SAR', country: '', fiscal_year_start_month: 1, basis: 'accrual' as const, tax_label: '', rate_max_age_days: '31', mappings: [] };
    assert.equal('policy_note' in settingsBody(form, null), false);
    assert.equal(settingsBody({ ...form, policy_note: ' Straight line. ' }, null).policy_note, 'Straight line.');
  });
});
