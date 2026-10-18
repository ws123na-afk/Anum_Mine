// Trading intelligence action helpers and messages (src/lib/financeIntel.ts, FinanceIntelActions.tsx).
// Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { FinanceIntelRule } from '@anum/contracts';
import { PHASES, STRATEGY_STATES, barsFileProblem, isWeightyMove, nextStates, phaseLabel, rulesByCandidate } from '../src/lib/financeIntel.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { financeIntelActionsAr } from '../src/lib/messages/financeIntelActions.ar.ts';
import { financeIntelActionsEn } from '../src/lib/messages/financeIntelActions.en.ts';

afterEach(() => setLocale('en'));

describe('lifecycle moves offered to the person', () => {
  test('mirror intel/lifecycle.py and never offer a live state', () => {
    assert.deepEqual(nextStates('DRAFT'), ['BACKTESTING', 'RETIRED']);
    assert.deepEqual(nextStates('BACKTESTING'), ['VALIDATION', 'DRAFT', 'RETIRED']);
    assert.deepEqual(nextStates('VALIDATION'), ['PAPER_ACTIVE', 'BACKTESTING', 'RETIRED']);
    assert.deepEqual(nextStates('PAPER_ACTIVE'), ['PAPER_SUSPENDED', 'RETIRED']);
    assert.deepEqual(nextStates('PAPER_SUSPENDED'), ['PAPER_ACTIVE', 'RETIRED']);
    assert.deepEqual(nextStates('RETIRED'), []);
    for (const state of STRATEGY_STATES) for (const next of nextStates(state)) assert.ok(STRATEGY_STATES.includes(next) && !next.includes('LIVE'));
  });

  test('starting paper trading and retiring ask for the stronger confirmation', () => {
    assert.equal(isWeightyMove('PAPER_ACTIVE'), true);
    assert.equal(isWeightyMove('RETIRED'), true);
    assert.equal(isWeightyMove('BACKTESTING'), false);
  });

  test('returns a copy the caller may change', () => {
    nextStates('DRAFT').push('PAPER_ACTIVE');
    assert.deepEqual(nextStates('DRAFT'), ['BACKTESTING', 'RETIRED']);
  });
});

describe('supervisor phases and rules', () => {
  test('phases match intel/supervisor.py PHASES and read in both languages', () => {
    assert.deepEqual([...PHASES], ['pre_open', 'intraday', 'post_close', 'weekly', 'monthly']);
    assert.equal(phaseLabel('post_close'), 'After the close');
    setLocale('ar');
    assert.equal(phaseLabel('post_close'), 'بعد الإغلاق');
  });

  test('rules are grouped by candidate with the benchmark first', () => {
    const rule = (key: string, candidate: number): FinanceIntelRule => ({ key, name: key, code_version: '1.0.0', candidate, candidate_name: `c${candidate}`,
      hypothesis: 'h', instruments: 'i', timeframe: 't', entry: 'e', exit: 'x', defaults: {}, min_symbols: 1, max_symbols: 10 });
    const groups = rulesByCandidate([rule('breakout', 3), rule('buy_and_hold', 0), rule('momentum', 3), rule('periodic', 1)]);
    assert.deepEqual(groups.map((g) => g.candidate), [0, 1, 3]);
    assert.deepEqual(groups[2].rules.map((r) => r.key), ['breakout', 'momentum']);
  });
});

describe('bar files', () => {
  test('are checked before they are sent', () => {
    assert.equal(barsFileProblem({ name: 'aapl.csv', size: 10 }), null);
    assert.equal(barsFileProblem({ name: 'aapl.CSV', size: 10 }), null);
    assert.equal(barsFileProblem({ name: 'aapl.csv', size: 0 }), 'finance.intel.data.fileEmpty');
    assert.equal(barsFileProblem({ name: 'aapl.csv', size: 2_000_001 }), 'finance.intel.data.fileTooBig');
    assert.equal(barsFileProblem({ name: 'aapl.xlsx', size: 10 }), 'finance.intel.data.fileType');
  });
});

describe('action messages', () => {
  const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
  test('every English key has an Arabic twin with the same placeholders', () => {
    assert.deepEqual(Object.keys(financeIntelActionsAr).sort(), Object.keys(financeIntelActionsEn).sort());
    for (const [key, text] of Object.entries(financeIntelActionsEn)) {
      assert.deepEqual(placeholders(financeIntelActionsAr[key as keyof typeof financeIntelActionsEn]), placeholders(text), key);
    }
  });

  test('no long dashes and no promise of profit or real orders', () => {
    for (const text of [...Object.values(financeIntelActionsEn), ...Object.values(financeIntelActionsAr)]) {
      assert.ok(!/[–—―]/.test(text), text);
      assert.ok(!/profitable|guarantee|real order|live trad/i.test(text), text);
    }
  });
});
