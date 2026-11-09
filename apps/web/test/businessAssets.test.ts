// Unit tests for the fixed asset register, loan and revaluation helpers (src/lib/businessAssets.ts,
// docs/business-books.md#fixed-asset-register, #loan-schedules, #period-end-revaluation).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { FixedAsset } from '@anum/contracts';
import {
  assetBody, assetFormFrom, assetProblem, emptyLoan, loanBody, loanProblem, monthEnds, repaidShare, repaymentBody, repaymentProblem,
} from '../src/lib/businessAssets.ts';
import { BOOKS_TABS, booksTabFromId } from '../src/lib/businessBooks.ts';
import { accountName, issueText, sourceHash } from '../src/lib/businessLedger.ts';
import { businessAssetsAr } from '../src/lib/messages/businessAssets.ar.ts';
import { businessAssetsEn } from '../src/lib/messages/businessAssets.en.ts';

const asset: FixedAsset = {
  id: 'basset_1', name: 'Synthetic forklift', category: 'vehicles', source_kind: 'bill', source_id: 'bill_1', source_live: true, acquired_on: '2026-06-15',
  currency: 'SAR', cost: '1200.00', useful_life_months: 24, residual_value: '0.00', method: 'straight_line', rate_pct: null, depreciation_start: '2026-06-01',
  status: 'active', disposed_on: null, disposal_kind: null, proceeds: '0.00', accumulated_depreciation: '250.00', impairment: '0.00', net_book_value: '950.00',
  as_of: '2026-11-09', gain_or_loss: null, version: 2, pending_impairments: 0,
};

describe('fixed assets, loans and revaluation', () => {
  test('the new sections open from their hash', () => {
    for (const tab of ['assets', 'loans', 'revaluation'] as const) {
      assert.ok(BOOKS_TABS.includes(tab));
      assert.equal(booksTabFromId(tab), tab);
    }
  });

  test('an asset form checks the residual value, the life and the reducing balance rate', () => {
    const form = assetFormFrom(asset);
    assert.equal(form.depreciation_start, '2026-06');
    assert.equal(assetProblem(form, asset.cost), null);
    assert.equal(assetProblem({ ...form, residual_value: '1200' }, asset.cost), 'assets.problem.residual');
    assert.equal(assetProblem({ ...form, useful_life_months: '0' }, asset.cost), 'assets.problem.life');
    assert.equal(assetProblem({ ...form, method: 'reducing_balance', rate_pct: '' }, asset.cost), 'assets.problem.rate');
    assert.equal(assetProblem({ ...form, method: 'reducing_balance', rate_pct: '25' }, asset.cost), null);
    assert.equal(assetProblem({ ...form, name: 'x' }, asset.cost), 'assets.problem.name');
    const body = assetBody({ ...form, method: 'reducing_balance', rate_pct: '25', useful_life_months: '' }, 2);
    assert.deepEqual([body.depreciation_start, body.rate_pct, body.useful_life_months, body.version], ['2026-06-01', '25', null, 2]);
    assert.equal(assetBody(form, 2).rate_pct, null);
  });

  test('a loan needs a whole number of payments and a start not in the future', () => {
    const form = { ...emptyLoan('SAR', '2026-11-09'), lender: 'Synthetic Bank', principal: '12000', annual_rate: '6.5' };
    assert.equal(loanProblem(form, '2026-11-09'), null);
    assert.equal(loanProblem({ ...form, frequency: 'quarterly', term_months: '10' }, '2026-11-09'), 'loans.problem.termFrequency');
    assert.equal(loanProblem({ ...form, start_date: '2026-12-01' }, '2026-11-09'), 'loans.problem.start');
    assert.equal(loanProblem({ ...form, annual_rate: '150' }, '2026-11-09'), 'loans.problem.rate');
    assert.equal(loanProblem({ ...form, principal: '0' }, '2026-11-09'), 'loans.problem.principal');
    assert.deepEqual(loanBody({ ...form, received_adjustment_id: '' }).received_adjustment_id, null);
    assert.equal(loanBody(form).term_months, 12);
  });

  test('a repayment is matched to a bill or an adjustment, never both', () => {
    assert.deepEqual(repaymentBody({ paid_on: '2026-10-01', amount: '1066.19', match: 'bill:bill_9' }),
      { paid_on: '2026-10-01', amount: '1066.19', bill_id: 'bill_9', adjustment_id: null });
    assert.deepEqual(repaymentBody({ paid_on: '2026-10-01', amount: '10', match: 'adjustment:badj_1' }).adjustment_id, 'badj_1');
    assert.deepEqual(repaymentBody({ paid_on: '2026-10-01', amount: '10', match: '' }).bill_id, null);
    assert.equal(repaymentProblem({ paid_on: '2026-09-01', amount: '10', match: '' }, '2026-11-09', '2026-09-15'), 'loans.problem.beforeStart');
    assert.equal(repaymentProblem({ paid_on: '2026-12-01', amount: '10', match: '' }, '2026-11-09', '2026-09-15'), 'loans.problem.paidOn');
    assert.equal(repaymentProblem({ paid_on: '2026-10-01', amount: '', match: '' }, '2026-11-09', '2026-09-15'), 'loans.problem.amount');
    assert.equal(repaidShare('12000.00', '9000.00'), 25);
    assert.equal(repaidShare('0', '0'), 0);
  });

  test('month ends to revalue are the ones that have ended, newest first', () => {
    assert.deepEqual(monthEnds('2026-03-10', 3), ['2026-02-28', '2026-01-31', '2025-12-31']);
  });

  test('the new journal sources, accounts and issues read in words', () => {
    assert.equal(sourceHash('asset', 'basset_1'), 'business-books/assets');
    assert.equal(sourceHash('loan_repayment', 'brepay_1'), 'business-books/loans');
    assert.equal(accountName({ system_key: 'fx_unrealised', name: 'Unrealised exchange gains and losses' }), 'Unrealised exchange gains and losses');
    assert.match(issueText({ event_key: 'revaluation:2026-10-31:EUR', source_kind: 'revaluation', source_id: null, event_date: '2026-10-31',
      reason: 'missing_closing_rate', currency: 'EUR', rate_date: '2026-10-31', description: '' }), /EUR/);
  });

  test('English and Arabic have the same keys and placeholders, and no long dashes', () => {
    assert.deepEqual(Object.keys(businessAssetsAr).sort(), Object.keys(businessAssetsEn).sort());
    const holes = (text: string) => (text.match(/\{[a-z_]+\}/gi) ?? []).sort().join(',');
    for (const [key, value] of Object.entries(businessAssetsEn)) {
      assert.equal(holes(String((businessAssetsAr as Record<string, string>)[key])), holes(String(value)), key);
      assert.doesNotMatch(String(value) + String((businessAssetsAr as Record<string, string>)[key]), /[–—]/, key);
    }
  });
});
