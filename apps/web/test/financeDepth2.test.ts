// A repayment split across loans, foreign balances in the base currency and personal trips and projects: the pure helpers
// and the new catalog keys (docs/finance.md#lent-and-borrowed, #foreign-currency-balances, #trips-and-projects). SYNTHETIC.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { FinancePersonLoan } from '@anum/contracts';
import { checkSplit, checkTag, revaluationRows, revaluationTone, splitCandidates, suggestedPart, tagBody, tripNeedsDates } from '../src/lib/financeDepth2.ts';
import { acceptMode, variantOf } from '../src/lib/financeSuggestions.ts';
import { sectionFromHash } from '../src/lib/finance.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const loan = (id: string, extra: Partial<FinancePersonLoan> = {}): FinancePersonLoan => ({
  id, direction: 'lent', person: 'Synthetic Cousin', amount: '300.00', currency: 'AED', lent_on: '2026-09-01', due_on: '2026-11-01', notes: null, remind: true,
  remind_days_before: 3, closed_on: null, interest_rate: null, installments: null, first_due_on: null, repaid: '0.00', outstanding: '300.00', handed: '300.00',
  status: 'open', days_until_due: 5, interest: '0.00', total_due: '300.00', next_due_on: '2026-11-01', next_due_amount: '300.00', overdue_amount: '0.00', links: 1,
  ...extra,
} as FinancePersonLoan);

describe('a repayment split across loans', () => {
  const a = loan('a', { next_due_on: '2026-11-20' });
  const b = loan('b', { person: ' synthetic cousin ', next_due_on: '2026-11-02', outstanding: '200.00', next_due_amount: '200.00' });
  const other = loan('c', { person: 'Synthetic Uncle' });
  const usd = loan('d', { currency: 'USD' });
  const repaid = loan('e', { status: 'repaid', outstanding: '0.00' });

  it('offers the same person, direction and currency, open, earliest due first', () => {
    assert.deepEqual(splitCandidates([a, b, other, usd, repaid, loan('f', { direction: 'borrowed' })], a).map((l) => l.id), ['b', 'a']);
  });

  it('starts each line at what is due now, never more than what is left', () => {
    assert.equal(suggestedPart(loan('x', { overdue_amount: '120.00' })), '120.00');
    assert.equal(suggestedPart(loan('x', { next_due_amount: '500.00', outstanding: '250.00' })), '250.00');
    assert.equal(suggestedPart(loan('x', { next_due_amount: '0.00' })), '');
  });

  it('says what the API would refuse: too few parts, a bad amount, more than a loan or the payment', () => {
    const loans = [a, b];
    assert.equal(checkSplit([{ loanId: 'a', amount: '100' }, { loanId: 'b', amount: '' }], loans, '500', 'AED').problem, 'too_few');
    assert.equal(checkSplit([{ loanId: 'a', amount: '1,00' }, { loanId: 'b', amount: '5' }], loans, '500', 'AED').problem, 'invalid_amount');
    assert.equal(checkSplit([{ loanId: 'a', amount: '100' }, { loanId: 'b', amount: '250' }], loans, '500', 'AED').problem, 'over_loan');
    assert.equal(checkSplit([{ loanId: 'a', amount: '300' }, { loanId: 'b', amount: '200' }], loans, '450', 'AED').problem, 'over_payment');
    const fine = checkSplit([{ loanId: 'a', amount: '300' }, { loanId: 'b', amount: '150.5' }], loans, '450.50', 'AED');
    assert.deepEqual([fine.problem, fine.total, fine.parts.length], [null, '450.50', 2]);
  });

  it('reads a split suggestion as one and accepts it as a link', () => {
    const part = { canonical_id: 'c1', dimension: 'person_loan' as const, target_type: 'finance_person_loan', target_id: 'a', amount: '300.00', currency: 'AED',
      role: 'deposit' as const, treatment: null, category: null, project_id: null };
    const action = { kind: 'link' as const, transaction_ids: [], category: null, subcategory_id: null, transaction_type: null, link: null, parts: [part, { ...part, target_id: 'b' }] };
    assert.equal(variantOf({ kind: 'loan_repayment', params: { direction: 'lent', split: '2' }, action }), 'lent_split');
    assert.equal(variantOf({ kind: 'loan_repayment', params: { direction: 'borrowed', split: '2' }, action }), 'borrowed_split');
    assert.equal(acceptMode({ action }), 'link');
  });
});

describe('foreign balances in the base currency', () => {
  const row = { account_id: 'x', name: 'Dollars', currency: 'USD', base_currency: 'AED', balance: '10.00', value: '37.00', rate: '3.7', rate_as_of: '2026-11-01',
    rate_source: 'manual', cost: '36.00', missing_rate_dates: [] };
  it('shows a gain, a loss and a missing rate apart, and leaves empty balances out', () => {
    assert.equal(revaluationTone({ status: 'ok', unrealised: '1.00' }), 'ok');
    assert.equal(revaluationTone({ status: 'ok', unrealised: '-1.00' }), 'bad');
    assert.equal(revaluationTone({ status: 'rate_not_available', unrealised: null }), 'warn');
    assert.equal(revaluationTone({ status: 'cost_rate_not_available', unrealised: null }), 'warn');
    assert.deepEqual(revaluationRows([{ ...row, status: 'ok', unrealised: '1.00' }, { ...row, account_id: 'y', status: 'nothing_to_revalue', unrealised: null }])
      .map((r) => r.account_id), ['x']);
  });
});

describe('trips and projects', () => {
  const form = { kind: 'trip' as const, name: ' Synthetic Muscat ', starts_on: '2026-05-01', ends_on: '2026-05-04', keywords: ' Muscat ', notes: '', archived: false };
  it('checks the form as the API does and sends clean values', () => {
    assert.equal(checkTag(form), null);
    assert.equal(checkTag({ ...form, name: ' ' }), 'name');
    assert.equal(checkTag({ ...form, ends_on: '2026-04-30' }), 'dates');
    assert.deepEqual(tagBody(form), { kind: 'trip', name: 'Synthetic Muscat', starts_on: '2026-05-01', ends_on: '2026-05-04', keywords: 'Muscat', notes: null, archived: false });
    assert.equal(tripNeedsDates({ ...form, ends_on: '' }), true);
    assert.equal(tripNeedsDates({ ...form, kind: 'project', ends_on: '' }), false);
  });
  it('opens as a section of Goals', () => {
    assert.deepEqual(sectionFromHash('#finance-goals-trips'), { tab: 'goals', section: 'trips' });
  });
});

describe('the catalogs', () => {
  it('has every new key in English and Arabic', () => {
    const keys = Object.keys(en).filter((k) => k.startsWith('fdepth2.') || k.endsWith('_split') || k === 'fplan.debts.tab.trips'
      || k.startsWith('fsug.head.attribution.trip') || k.startsWith('fsug.head.attribution.project') || k === 'fsug.accept.tag' || k === 'fsug.parts' || k === 'fsug.part');
    assert.ok(keys.length > 60, String(keys.length));
    for (const key of keys) assert.ok(key in ar, key);
    for (const key of ['fsug.why.attribution.trip', 'fsug.why.attribution.project']) assert.ok(key in en && key in ar, key);
  });
});
