// The post-close daily trading reports (src/lib/financeReports.ts, docs/finance-trading-intel.md#daily-report).
// Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { tabFromHash } from '../src/lib/finance.ts';
import { REPORTS_HASH, commentaryNote, markdownName, needsAction, pct, reportFromHash, reportHash, summaryLine, summaryRows } from '../src/lib/financeReports.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { financeReportsAr } from '../src/lib/messages/financeReports.ar.ts';
import { financeReportsEn } from '../src/lib/messages/financeReports.en.ts';

afterEach(() => setLocale('en'));

describe('daily reports', () => {
  test('deep links open the Trading intelligence tab and the right report', () => {
    assert.equal(reportFromHash(REPORTS_HASH), '');
    assert.equal(reportFromHash('#finance-intel-report=fin_dec_1'), 'fin_dec_1');
    assert.equal(reportFromHash(reportHash('fin_dec_2')), 'fin_dec_2');
    assert.equal(reportFromHash('#finance-intel'), null);
    assert.equal(reportFromHash('#finance-intel-report=<script>'), null, 'only plain ids are read from the link');
    assert.equal(tabFromHash(REPORTS_HASH), 'intel');
    assert.equal(tabFromHash('#finance-intel-report=fin_dec_1'), 'intel');
    assert.equal(tabFromHash('#finance-trading'), 'trading');
  });

  test('summary figures keep a fixed order, missing ones read as zero', () => {
    const rows = summaryRows({ proposed_orders: 2, suspended: 1 });
    assert.deepEqual(rows.map((r) => r.key), ['active_strategies', 'proposed_orders', 'waiting_for_approval', 'refused_signals', 'suspended', 'problems', 'market_closed']);
    assert.deepEqual(rows.map((r) => r.value), [0, 2, 0, 0, 1, 0, 0]);
    assert.equal(summaryLine({ active_strategies: 1, proposed_orders: 2 }), 'Active 1 · proposed 2 · refused 0 · suspended 0');
    assert.equal(needsAction({ waiting_for_approval: 1 }), true);
    assert.equal(needsAction({ problems: 0, suspended: 0 }), false);
  });

  test('percentages and the download name', () => {
    assert.equal(pct(0.012), '+1.2%');
    assert.equal(pct(-0.031), '-3.1%');
    assert.equal(pct(null), '-');
    assert.equal(markdownName('2026-10-15'), 'anum-trading-report-2026-10-15.md');
    assert.equal(markdownName('../x'), 'anum-trading-report-report.md');
  });

  test('both catalogs have the same keys and placeholders, no long dashes, no promise of profit', () => {
    assert.deepEqual(Object.keys(financeReportsAr).sort(), Object.keys(financeReportsEn).sort());
    const holes = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join();
    for (const [key, en] of Object.entries(financeReportsEn)) {
      const ar = (financeReportsAr as Record<string, unknown>)[key];
      assert.equal(holes(String(ar)), holes(String(en)), key);
      for (const text of [String(en), String(ar)]) assert.doesNotMatch(text, /[\u2013\u2014]/, key);
      assert.doesNotMatch(String(en), /guarantee|profitable/i, key);
    }
    setLocale('ar');
    assert.match(summaryRows({})[0].label, /الاستراتيجيات/);
  });

  test('commentary says why there is no text', () => {
    const base = { text: null, model: null, label: 'x', created_at: '2026-10-15T14:00:00Z' };
    assert.equal(commentaryNote(null), null);
    assert.equal(commentaryNote({ ...base, status: 'ok', reason: null, text: 'Simulated.' }), null);
    assert.match(commentaryNote({ ...base, status: 'skipped', reason: 'no_model' }) ?? '', /no AI model is set up/);
    assert.match(commentaryNote({ ...base, status: 'skipped', reason: 'model_budget' }) ?? '', /model budget/);
    assert.match(commentaryNote({ ...base, status: 'withheld', reason: 'profit_claim' }) ?? '', /withheld/);
    assert.match(commentaryNote({ ...base, status: 'failed', reason: 'model_unavailable' }) ?? '', /did not answer/);
  });
});
