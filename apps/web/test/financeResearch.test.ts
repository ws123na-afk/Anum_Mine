// Fund research view helpers (src/lib/financeResearch.ts, docs/finance-research.md).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { chartPaths, checkDraft, factValue, groupDigits, percentText, toInput, tone, type BacktestDraft } from '../src/lib/financeResearch.ts';
import { financeResearchEn } from '../src/lib/messages/financeResearch.en.ts';
import { financeResearchAr } from '../src/lib/messages/financeResearch.ar.ts';

const draft = (patch: Partial<BacktestDraft> = {}): BacktestDraft => ({
  rule: 'monthly', first: 'SPUS', second: 'HLAL', monthly: '10', total: '50', perOrder: '10', years: 10, weight: 60, every: 12, dipPercent: 10, dipAmount: '10',
  perShare: '0.005', minimum: '1.00', maxPercent: '1', ...patch,
});
const limits = { max_order: '10', max_capital: '50' };

describe('fund research helpers', () => {
  test('a missing figure is shown as missing, never as a number', () => {
    assert.equal(factValue({ value: null, source: null, as_of: null, unavailable: 'No source', unit: 'percent' }), null);
    assert.equal(factValue({ value: '-33.8', source: 'stooq', as_of: '2020', unavailable: null, unit: 'percent' }), '-33.8%');
    assert.equal(factValue({ value: '12.5', source: 'stooq', as_of: '2020', unavailable: null, unit: 'percent' }), '+12.5%');
    assert.equal(factValue({ value: '987654321', source: 'sec_nport', as_of: '2026', unavailable: null, unit: 'usd' }), 'USD 987,654,321');
    assert.equal(factValue({ value: '220', source: 'sec_nport', as_of: '2026', unavailable: null, unit: 'count' }), '220');
    assert.equal(percentText(null), null);
    assert.equal(groupDigits('-1234567.50'), '-1,234,567.50');
    assert.equal(tone('-1'), 'negative');
    assert.equal(tone('0'), '');
  });

  test('the backtest form applies the owner limits before the server does', () => {
    assert.equal(checkDraft(draft(), limits), null);
    assert.equal(checkDraft(draft({ perOrder: '20' }), limits), 'overOrder');
    assert.equal(checkDraft(draft({ total: '60' }), limits), 'overTotal');
    assert.equal(checkDraft(draft({ monthly: 'ten' }), limits), 'amount');
    assert.equal(checkDraft(draft({ monthly: '0' }), limits), 'amount');
    assert.equal(checkDraft(draft({ perOrder: '5' }), limits), 'monthlyOverOrder');
    assert.equal(checkDraft(draft({ rule: 'rebalance', second: 'SPUS' }), limits), 'twoFunds');
    assert.deepEqual(toInput(draft({ rule: 'rebalance' })).symbols, ['SPUS', 'HLAL']);
    assert.equal(toInput(draft({ rule: 'dip' })).dip_percent, 10);
    assert.equal(toInput(draft()).weight_percent, undefined);
  });

  test('the chart uses only the timeline it is given', () => {
    const paths = chartPaths([{ value: '10', put_in: '10' }, { value: '20', put_in: '20' }], 100, 50);
    assert.equal(paths.max, 20);
    assert.equal(paths.value, 'M0.0,25.0 L100.0,0.0');
    assert.equal(chartPaths([{ value: '1', put_in: '1' }], 100, 50).value, '');
  });

  test('every English string has an Arabic twin with the same placeholders', () => {
    for (const [key, english] of Object.entries(financeResearchEn)) {
      const arabic = financeResearchAr[key as keyof typeof financeResearchEn];
      assert.ok(arabic, key);
      const names = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
      assert.deepEqual(names(String(arabic)), names(String(english)), key);
    }
  });

  test('the copy says plainly it is not advice and promises nothing', () => {
    const all = Object.values(financeResearchEn).join(' ').toLowerCase();
    assert.ok(all.includes('never runs live'));
    for (const word of ['guarantee', 'will rise', 'target price', 'risk-free']) assert.ok(!all.includes(word), word);
  });
});
