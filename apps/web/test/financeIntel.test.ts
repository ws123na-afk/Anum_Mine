// Trading intelligence dashboard helpers (src/lib/financeIntel.ts, docs/finance-trading-intel.md#dashboard).
// Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { FinanceIntelVenue } from '@anum/contracts';
import { FINANCE_TABS, hashForTab, tabFromHash } from '../src/lib/finance.ts';
import {
  AGENTS, STRATEGY_STATES, agentLabel, costsLine, foldMetricKeys, outcomeLabel, formatMetric, metricLabel, orderedMetrics, outcomeTone, paramsLine, sortVenues, stateLabel, stateTone, venueClock,
  verdictLabel, verdictTone,
} from '../src/lib/financeIntel.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { financeIntelAr } from '../src/lib/messages/financeIntel.ar.ts';
import { financeIntelEn } from '../src/lib/messages/financeIntel.en.ts';
import { viewFromHash } from '../src/lib/shell.ts';

afterEach(() => setLocale('en'));

describe('the trading intelligence tab', () => {
  test('is a deep link next to paper trading', () => {
    assert.ok(FINANCE_TABS.includes('intel'));
    assert.ok(FINANCE_TABS.indexOf('intel') === FINANCE_TABS.indexOf('trading') + 1);
    assert.equal(hashForTab('intel'), '#finance-intel');
    assert.equal(tabFromHash('#finance-intel'), 'intel');
    assert.equal(viewFromHash('#finance-intel'), 'finance');
  });
});

describe('lifecycle', () => {
  test('every state has a label in both languages and a tone', () => {
    assert.deepEqual([...STRATEGY_STATES], ['DRAFT', 'BACKTESTING', 'VALIDATION', 'PAPER_ACTIVE', 'PAPER_SUSPENDED', 'RETIRED']);
    for (const state of STRATEGY_STATES) {
      assert.notEqual(stateLabel(state), `finance.intel.state.${state}`);
      setLocale('ar');
      assert.notEqual(stateLabel(state), `finance.intel.state.${state}`);
      setLocale('en');
    }
    assert.equal(stateTone('PAPER_ACTIVE'), 'success');
    assert.equal(stateTone('PAPER_SUSPENDED'), 'warning');
    assert.equal(stateTone('RETIRED'), 'warning');
    assert.equal(stateTone('DRAFT'), 'neutral');
  });
});

describe('metrics', () => {
  test('fractions read as percentages, ratios to 2 decimals, counts whole, missing as a dash', () => {
    assert.equal(formatMetric('total_return', 0.1234), '+12.34%');
    assert.equal(formatMetric('max_drawdown', -0.2), '-20.00%');
    assert.equal(formatMetric('sharpe', 1.234), '1.23');
    assert.equal(formatMetric('trades', 14), '14');
    assert.equal(formatMetric('sharpe', null), '-');
    assert.equal(formatMetric('sharpe', Number.NaN), '-');
    assert.equal(formatMetric('unknown_thing', 2), '2.00');
  });

  test('known metrics come first in a fixed order, the rest by name', () => {
    const order = orderedMetrics({ zeta: 1, trades: 3, sharpe: 0.5, alpha: 2, total_return: 0.1 }).map((m) => m.key);
    assert.deepEqual(order, ['total_return', 'sharpe', 'trades', 'alpha', 'zeta']);
    assert.equal(metricLabel('sharpe'), 'Sharpe ratio');
    assert.equal(metricLabel('some_new_metric'), 'some new metric');
  });

  test('a verdict never says profitable or approved', () => {
    assert.equal(verdictTone('passed'), 'success');
    assert.equal(verdictTone('rejected'), 'warning');
    assert.equal(verdictTone('insufficient_data'), 'warning');
    assert.equal(verdictTone('completed'), 'neutral');
    assert.equal(verdictLabel('completed'), 'Completed, not judged');
    assert.equal(verdictTone(null), 'neutral');
    assert.equal(verdictLabel('passed'), 'Passed the checks');
    assert.equal(verdictLabel(null), 'Not judged');
    assert.equal(verdictLabel('something_else'), 'something_else');
    for (const text of Object.values(financeIntelEn).flatMap((m) => (typeof m === 'string' ? [m] : Object.values(m)))) {
      assert.doesNotMatch(text, /guarantee(d)? (profit|return)|risk[- ]free|sure win/i, text);
    }
  });
});

describe('folds and costs', () => {
  test('fold columns come from the first fold, known metrics first, at most four', () => {
    const fold = (metrics: Record<string, number | null>) => ({ index: 0, train_start: null, train_end: null, test_start: null, test_end: null, metrics });
    assert.deepEqual(foldMetricKeys([fold({ in_sample_sharpe: 0.9, out_of_sample_return: 0.02, out_of_sample_sharpe: 0.4 })]),
      ['out_of_sample_return', 'out_of_sample_sharpe', 'in_sample_sharpe']);
    assert.deepEqual(foldMetricKeys([]), []);
    assert.equal(formatMetric('out_of_sample_return', 0.02), '+2.00%');
    assert.equal(costsLine({ fee_bps: '10', slippage_bps: '5' }), 'fee bps 10, slippage bps 5');
  });
});

describe('agents and outcomes', () => {
  test('known agents have names, unknown ones read from their key', () => {
    assert.equal(agentLabel('risk_guardian'), 'Risk Guardian');
    assert.equal(agentLabel('new_agent'), 'new agent');
    assert.equal(outcomeTone('rejected'), 'warning');
    assert.equal(outcomeTone('ok'), 'success');
    assert.equal(outcomeLabel('refused'), 'Refused');
    assert.equal(outcomeLabel('needs_review'), 'needs review');
    for (const agent of AGENTS) assert.notEqual(agentLabel(agent), agent.replace(/_/g, ' '), agent);
    assert.equal(agentLabel('trading_supervisor'), 'Trading Supervisor');
    assert.equal(outcomeTone('noted'), 'neutral');
    assert.equal(outcomeTone(null), 'neutral');
  });
  test('parameters read as one line', () => {
    assert.equal(paramsLine({ fast_window: 20, slow: 50, adjusted: true }), 'fast window 20, slow 50, adjusted true');
    assert.equal(paramsLine({}), 'No settings');
  });
});

describe('venues', () => {
  const venue = (code: string, name: string, state: FinanceIntelVenue['state']): FinanceIntelVenue =>
    ({ code, name, state, timezone: 'Asia/Dubai', local_time: '2026-10-18T10:32:00+04:00', holidays_known: true, reason: '', note: '' });
  test('open venues first, then by name', () => {
    const sorted = sortVenues([venue('B', 'Beta', 'closed'), venue('C', 'Crypto', 'open'), venue('A', 'Alpha', 'holiday'), venue('D', 'Dubai', 'open')]);
    assert.deepEqual(sorted.map((v) => v.code), ['C', 'D', 'B', 'A']);
  });
  test('the clock is the venue\'s own local time', () => {
    assert.match(venueClock(venue('X', 'Dubai', 'open')), /10:32/);
    assert.equal(venueClock({ local_time: 'not a date', timezone: 'UTC' }), '');
  });
});

describe('catalogs', () => {
  test('Arabic has every key with the same placeholders, and no long dashes anywhere', () => {
    assert.deepEqual(Object.keys(financeIntelAr).sort(), Object.keys(financeIntelEn).sort());
    const holes = (m: unknown) => new Set(JSON.stringify(m).match(/\{[a-z]+\}/gi) ?? []);
    for (const [key, en] of Object.entries(financeIntelEn)) {
      const ar = (financeIntelAr as Record<string, unknown>)[key];
      for (const hole of holes(ar)) assert.ok(holes(en).has(hole), `${key} ${hole}`);
      assert.doesNotMatch(JSON.stringify(en) + JSON.stringify(ar), /[\u2013\u2014]/, key);
    }
  });
});
