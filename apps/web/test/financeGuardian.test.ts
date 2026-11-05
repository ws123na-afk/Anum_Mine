// Finance, Guardian helpers (src/lib/financeGuardian.ts, docs/finance.md#guardian). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { FinanceGuardianRules } from '@anum/contracts';
import { EMPTY_RULES, describeRules, draftFromRules, rulesFromDraft } from '../src/lib/finance.ts';
import {
  RULE_KINDS, activeKinds, activityCells, configuredKinds, draftAdding, lastTriggered, monitoringState, reasonText, testPrefill, thresholdLines,
  withAllPaused, withPaused,
} from '../src/lib/financeGuardian.ts';
import { setLocale } from '../src/lib/i18n.ts';

afterEach(() => setLocale('en'));
const strip = (text: string) => text.replace(/[⁦-⁩]/g, '').replace(/\s/g, ' ');

const RULES: FinanceGuardianRules = {
  ...EMPTY_RULES,
  max_single: [{ currency: 'AED', amount: '5000.00' }],
  category_caps: [{ category: 'dining', currency: 'AED', amount: '400.00' }],
  blocked_merchants: ['casino'],
  quiet_hours: { start: '22:00', end: '07:00' },
};

describe('monitoring state and pausing', () => {
  test('kinds are listed in the evaluator order, paused ones are kept but not active', () => {
    assert.deepEqual(configuredKinds(RULES), ['max_single', 'category_caps', 'blocked_merchants', 'quiet_hours']);
    const paused = withPaused(RULES, 'category_caps', true);
    assert.deepEqual(paused.paused, ['category_caps']);
    assert.deepEqual(activeKinds(paused), ['max_single', 'blocked_merchants', 'quiet_hours']);
    assert.deepEqual(withPaused(paused, 'category_caps', false).paused, []);
    // A kind that is not set cannot be paused.
    assert.deepEqual(withPaused(RULES, 'cooling_off', true).paused, []);
    assert.equal(RULE_KINDS.length, 9);
  });

  test('monitoring is none without rules, on while one applies, off when all are paused', () => {
    assert.equal(monitoringState(EMPTY_RULES), 'none');
    assert.equal(monitoringState(RULES), 'on');
    const off = withAllPaused(RULES, true);
    assert.equal(monitoringState(off), 'off');
    assert.equal(monitoringState(withAllPaused(off, false)), 'on');
  });

  test('an edit keeps the pauses and the description names them', () => {
    const paused = withPaused(RULES, 'max_single', true);
    const back = rulesFromDraft(draftFromRules(paused));
    assert.ok(back.ok);
    assert.deepEqual(back.ok && back.value.paused, ['max_single']);
    const plain = rulesFromDraft(draftFromRules(RULES));
    assert.ok(plain.ok && !('paused' in plain.value));
    assert.equal(describeRules(paused).at(-1), 'Paused, not applied: Single payment limit');
  });
});

describe('rule cards', () => {
  test('threshold lines read like the mockup and come from the rules', () => {
    assert.deepEqual(thresholdLines(RULES, 'max_single').map(strip), ['Above AED 5,000.00']);
    assert.deepEqual(thresholdLines(RULES, 'category_caps').map(strip), ['Dining: AED 400.00']);
    assert.deepEqual(thresholdLines(RULES, 'quiet_hours').map(strip), ['22:00 to 07:00']);
    assert.deepEqual(thresholdLines(RULES, 'cooling_off'), []);
    assert.deepEqual(thresholdLines({ ...EMPTY_RULES, cooling_off: { hours: 24, at_or_above: [] } }, 'cooling_off').map(strip), ['24 hours']);
  });

  test('test prefills go just over a limit or name the blocked merchant', () => {
    assert.deepEqual(testPrefill(RULES, 'max_single', 'AED'), { amount: '5001', currency: 'AED', category: '', merchant: '' });
    assert.deepEqual(testPrefill(RULES, 'category_caps', 'AED'), { amount: '401', currency: 'AED', category: 'dining', merchant: '' });
    assert.equal(testPrefill(RULES, 'blocked_merchants', 'AED').merchant, 'casino');
    assert.equal(testPrefill(EMPTY_RULES, 'max_single', 'USD').currency, 'USD');
  });

  test('adding a kind opens the editor with one row to fill in', () => {
    const draft = draftFromRules(EMPTY_RULES);
    assert.deepEqual(draftAdding(draft, 'max_single', 'AED').maxSingle, [{ currency: 'AED', amount: '' }]);
    assert.equal(draftAdding(draft, 'quiet_hours', 'AED').quietOn, true);
    assert.equal(draftAdding(draft, 'category_caps', 'AED').caps.length, 1);
  });

  test('last triggered: a date, nothing this month, or checked on actions only', () => {
    assert.equal(lastTriggered('max_single', { kind: 'max_single', count: 2, last: '2026-11-04' }, 'November 2026'), 'Nov 4, 2026 (2 payments)');
    assert.equal(lastTriggered('max_single', { kind: 'max_single', count: 0, last: null }, 'November 2026'), 'Not in November 2026');
    assert.equal(lastTriggered('approval_above', undefined, 'November 2026'), 'Checked on actions, not on recorded payments');
  });
});

describe('audit log and reasons', () => {
  const at = '2026-11-05T09:00:00Z';
  test('each recorded action has its own words, never an amount', () => {
    assert.deepEqual(activityCells({ id: 'a', action: 'finance.guardian.review', target: 'fin_txn_1', created_at: at, metadata: { decision: 'expected', codes: ['over_single_limit'] } }),
      { rule: 'Single payment limit', action: 'Marked expected', loosens: false, tone: 'ok' });
    assert.equal(activityCells({ id: 'b', action: 'finance.guardian.review', target: 'x', created_at: at, metadata: { decision: 'snoozed', days: 7, codes: [] } }).action, 'Snoozed for 7 days');
    const confirm = activityCells({ id: 'c', action: 'finance.guardian.confirm', target: 'x', created_at: at, metadata: { version: 3, loosens: ['max_single'] } });
    assert.equal(confirm.action, 'Confirmed version 3');
    assert.equal(confirm.loosens, true);
    assert.equal(confirm.rule, 'Single payment limit');
    assert.equal(activityCells({ id: 'd', action: 'finance.guardian.reject', target: 'x', created_at: at, metadata: {} }).action, 'Rejected a change');
  });

  test('reasons read in Arabic, with the server text as a fallback for new codes', () => {
    const reason = { code: 'over_single_limit', outcome: 'deny' as const, message: 'x', rule: 'max_single', params: { currency: 'AED', limit: '100.00' } };
    assert.match(strip(reasonText(reason)), /AED 100\.00/);
    assert.equal(reasonText({ ...reason, code: 'brand_new_code', message: 'Server words' }), 'Server words');
    setLocale('ar');
    assert.equal(activityCells({ id: 'e', action: 'finance.guardian.reject', target: 'x', created_at: at, metadata: {} }).action, 'رفضت تغييرًا');
  });
});
