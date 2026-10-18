// SIMULATED P&L and drawdown over time (src/lib/financeEquity.ts, docs/finance-trading-intel.md#pl-and-drawdown-over-time).
// Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { FinanceEquityPoint, FinanceEquitySeries } from '@anum/contracts';
import {
  RANGES, amount, benchmarkLine, benchmarkValues, chartSummary, drawdownLine, frame, latest, latestBenchmark, percent, pick, scale, scalePnl, tableRows, tick,
} from '../src/lib/financeEquity.ts';
import { setLocale, t } from '../src/lib/i18n.ts';
import { financeEquityAr } from '../src/lib/messages/financeEquity.ar.ts';
import { financeEquityEn } from '../src/lib/messages/financeEquity.en.ts';

afterEach(() => setLocale('en'));

const point = (day: string, value: string | null, pnl: string | null, drawdown: string | null, pct: number | null, complete = true): FinanceEquityPoint =>
  ({ day, value, pnl, drawdown, drawdown_pct: pct, complete });

const series = (points: FinanceEquityPoint[], extra: Partial<FinanceEquitySeries> = {}): FinanceEquitySeries => ({
  currency: 'USD', points, max_drawdown: '-200.00', max_drawdown_pct: -0.182, peak_day: '2026-09-02', trough_day: '2026-09-03', gaps: 1, unpriced: ['EXMPL'], ...extra,
});

const sample = series([
  point('2026-09-01', '999.00', '-1.00', '0.00', 0), point('2026-09-02', '1099.00', '99.00', '0.00', 0),
  point('2026-09-03', '899.00', '-101.00', '-200.00', -0.182), point('2026-09-04', null, null, null, null, false),
  point('2026-09-05', '1049.00', '49.00', '-50.00', -0.045),
]);

describe('scaling', () => {
  test('zero is always in range, gaps break the line and are never bridged', () => {
    const f = frame(208, 136);
    const s = scale([10, 20, null, -10], f);
    assert.equal(s.min, -10);
    assert.equal(s.max, 20);
    assert.equal((s.line.match(/M/g) ?? []).length, 2, 'two runs around the gap');
    assert.ok(s.zeroY > f.top && s.zeroY < f.height - f.bottom);
    assert.equal(s.plotted, 3);
    assert.deepEqual(s.last && { x: s.last.x, value: s.last.value }, { x: 200, value: -10 });
    assert.equal((s.area.match(/Z/g) ?? []).length, 2);
  });

  test('a flat or single-day series still draws inside the frame', () => {
    const f = frame(100, 100);
    const flat = scale([0, 0], f);
    assert.equal(flat.min, -1);
    assert.equal(flat.max, 1);
    const single = scale([5], f);
    assert.match(single.line, /^M48,\d+(\.\d)?L52,/);
    const none = scale([null, null], f);
    assert.equal(none.line, '');
    assert.equal(none.last, null);
  });

  test('a gap day plots nothing; drawdown reads the percentage on paper and the amount on shadow', () => {
    assert.equal(pick(sample.points[3], 'pnl', true), null);
    assert.equal(pick(sample.points[2], 'pnl', true), -101);
    assert.equal(pick(sample.points[2], 'drawdown', true), -0.182);
    assert.equal(pick(sample.points[2], 'drawdown', false), -200);
  });
});

describe('words', () => {
  test('amounts keep the currency, percentages are signed, missing is a dash', () => {
    assert.equal(amount('1049.00', 'USD'), '1,049 USD');
    assert.equal(amount('-0.50', 'AED'), '-0.5 AED');
    assert.equal(amount(null, 'USD'), '-');
    assert.equal(percent(-0.182), '-18.20%');
    assert.equal(percent(0), '0.00%');
    assert.equal(percent(null), '-');
    assert.equal(tick(-0.182, true), '-18.20%');
    assert.equal(tick(1234.5, false), '1,234.5');
  });

  test('the newest day with a value, the deepest drawdown and the text alternative', () => {
    assert.equal(latest(sample)?.day, '2026-09-05');
    assert.equal(latest(series([point('2026-09-01', null, null, null, null, false)])), null);
    assert.equal(drawdownLine(sample, true), '-200 USD (-18.20%), from the high on 2026-09-02 to the low on 2026-09-03');
    assert.equal(drawdownLine(sample, false), '-200 USD, from the high on 2026-09-02 to the low on 2026-09-03');
    assert.equal(drawdownLine(series([], { max_drawdown: '0.00' }), true), 'None so far');
    const aria = chartSummary(sample, true);
    assert.match(aria, /from 2026-09-01 to 2026-09-05/);
    assert.match(aria, /Newest 49 USD, lowest -101 USD, highest 99 USD/);
    assert.match(aria, /Gap days: 1/);
    assert.match(chartSummary(series([point('2026-09-01', null, null, null, null, false)]), true), /every day is a gap/);
  });

  test('the table fallback pages newest first', () => {
    assert.deepEqual(tableRows(sample, 0, 2).map((p) => p.day), ['2026-09-05', '2026-09-04']);
    assert.deepEqual(tableRows(sample, 4, 2).map((p) => p.day), ['2026-09-01']);
  });

  test('ranges stay inside what the API allows', () => {
    assert.ok(RANGES.every((r) => r >= 1 && r <= 730));
  });
});

describe('catalogs', () => {
  test('Arabic has every key with the same placeholders, plurals work, and no long dashes anywhere', () => {
    assert.deepEqual(Object.keys(financeEquityAr).sort(), Object.keys(financeEquityEn).sort());
    const holes = (m: unknown) => new Set(JSON.stringify(m).match(/\{[a-z]+\}/gi) ?? []);
    for (const [key, en] of Object.entries(financeEquityEn)) {
      const ar = (financeEquityAr as Record<string, unknown>)[key];
      for (const hole of holes(ar)) assert.ok(holes(en).has(hole), `${key} ${hole}`);
      assert.doesNotMatch(JSON.stringify(en) + JSON.stringify(ar), /[\u2013\u2014]/, key);
    }
    assert.equal(t('finance.equity.days', { count: 30 }), 'Last 30 days');
    setLocale('ar');
    assert.equal(t('finance.equity.days', { count: 90 }), 'آخر 90 يومًا');
    assert.equal(drawdownLine(series([], { max_drawdown: null }), true), 'لا شيء حتى الآن');
  });

  test('nothing promises a profit', () => {
    assert.doesNotMatch(JSON.stringify(financeEquityEn), /guarantee|profitable|sure to|risk[- ]free/i);
    assert.match(financeEquityEn['finance.equity.note'], /not a promise/);
  });
});

describe('buy and hold benchmark', () => {
  const withBench = series(sample.points, {
    benchmark: { symbols: ['EXMPL'], start: '2026-09-02', reason: null, points: [
      { day: '2026-09-01', pnl: null }, { day: '2026-09-02', pnl: '99.00' }, { day: '2026-09-03', pnl: '150.00' },
      { day: '2026-09-04', pnl: null }, { day: '2026-09-05', pnl: '-300.00' },
    ] },
  });

  test('values line up with the days and the newest one is read', () => {
    assert.deepEqual(benchmarkValues(withBench), [null, 99, 150, null, -300]);
    assert.equal(latestBenchmark(withBench.benchmark), '-300.00');
    assert.equal(latestBenchmark({ symbols: [], start: null, points: [], reason: 'shadow' }), null);
    assert.deepEqual(benchmarkValues(sample), [null, null, null, null, null], 'an older server sends no benchmark');
  });

  test('both lines share one range so they compare', () => {
    const f = frame(208, 136);
    const { portfolio, benchmark } = scalePnl(withBench, f);
    assert.ok(benchmark);
    assert.equal(portfolio.min, -300);
    assert.equal(benchmark.min, -300);
    assert.equal(portfolio.max, benchmark.max);
    assert.equal(scalePnl(sample, f).benchmark, null);
  });

  test('says what it is, or why there is none, with no long dashes', () => {
    assert.match(benchmarkLine(withBench) ?? '', /Buy and hold of EXMPL in equal weights from 2026-09-02/);
    for (const reason of ['shadow', 'no_symbols', 'no_common_price'] as const) {
      const text = benchmarkLine(series(sample.points, { benchmark: { symbols: [], start: null, points: [], reason } }));
      assert.match(text ?? '', /^No buy and hold line/);
      assert.doesNotMatch(text ?? '', /[\u2013\u2014]/);
    }
    assert.equal(benchmarkLine(sample), null);
    assert.match(chartSummary(withBench, true), /Buy and hold of the same symbols, newest -300 USD/);
    setLocale('ar');
    assert.match(benchmarkLine(withBench) ?? '', /الشراء والاحتفاظ/);
  });
});
