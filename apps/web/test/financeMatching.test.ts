// Automatic matching on Finance settings (docs/finance-matching.md): the policy body, when anything links on its own, the
// history progress, the words keys and the catalogs. Every value is fake and low entropy.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { FinanceMatchingPolicy } from '@anum/contracts';
import {
  COUNT_KEYS, REVIEW_TABS, SOURCES, SWITCHES, canApplyHistory, installmentTone, linksOnItsOwn, percent, policyChanged, policyInput, reasonKey, runProgress,
  shownCounts,
} from '../src/lib/financeMatching.ts';
import { NEVER_AUTOMATED } from '../src/lib/financeSuggestions.ts';
import { financeMatchingEn } from '../src/lib/messages/financeMatching.en.ts';
import { financeMatchingAr } from '../src/lib/messages/financeMatching.ar.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const policy = (over: Partial<FinanceMatchingPolicy> = {}): FinanceMatchingPolicy => ({
  match_transactions: true, reconcile_installments: true, link_recurring_bills: false, link_card_repayments: false, link_bank_loan_repayments: false,
  prevent_duplicates: true, reprocess_history: false, notify_exceptions: false, min_confidence: '0.9', review_confidence: '0.5',
  settled_sources: ['statement', 'sms'], source_account_ids: ['fin_acct_a'], revision: 3, approved_at: '2026-11-01T00:00:00Z', approved_by: 'owner', ...over,
});

describe('matching policy', () => {
  it('sends back only what the owner may change, with the revision it was read at', () => {
    const body = policyInput(policy(), true);
    assert.equal(body.approve, true);
    assert.equal(body.expected_revision, 3);
    assert.ok(!('approved_at' in body) && !('revision' in body) && !('approved_by' in body));
  });

  it('sees a change in a switch, the threshold or a list, but not in the order of a list', () => {
    assert.equal(policyChanged(policy(), policy()), false);
    assert.equal(policyChanged(policy(), policy({ settled_sources: ['sms', 'statement'] })), false);
    assert.equal(policyChanged(policy(), policy({ min_confidence: '0.95' })), true);
    assert.equal(policyChanged(policy(), policy({ source_account_ids: [] })), true);
    assert.equal(policyChanged(policy(), policy({ link_card_repayments: true })), true);
  });

  it('links on its own only with the switch, a kind, an account and the approval', () => {
    assert.equal(linksOnItsOwn({ approved: true, policy: policy() }), true);
    assert.equal(linksOnItsOwn({ approved: false, policy: policy() }), false);
    assert.equal(linksOnItsOwn({ approved: true, policy: policy({ source_account_ids: [] }) }), false);
    assert.equal(linksOnItsOwn({ approved: true, policy: policy({ reconcile_installments: false }) }), false);
    assert.equal(canApplyHistory({ approved: true, policy: policy() }), false);
    assert.equal(canApplyHistory({ approved: true, policy: policy({ reprocess_history: true }) }), true);
  });
});

describe('matching words and figures', () => {
  it('reads confidence, reasons, tones and progress', () => {
    assert.equal(percent('0.925'), '93%');
    assert.equal(percent(null), '-');
    assert.equal(reasonKey('link_refused_over_allocated'), 'fmatch.reason.link_refused');
    assert.equal(reasonKey('ambiguous_obligation'), 'fmatch.reason.ambiguous_obligation');
    assert.equal(installmentTone('verified_paid'), 'good');
    assert.equal(installmentTone('provisional'), 'warn');
    assert.equal(installmentTone('overdue'), 'bad');
    assert.equal(runProgress({ start_on: '2026-01-05', end_on: '2026-04-20', cursor_on: '2026-02-28', status: 'running' }), 50);
    assert.equal(runProgress({ start_on: '2026-01-05', end_on: '2026-04-20', cursor_on: null, status: 'running' }), 0);
    assert.equal(runProgress({ start_on: null, end_on: null, cursor_on: null, status: 'done' }), 100);
    assert.deepEqual(shownCounts({ matched: 2, errors: 0, auto_linked: 1 }).map((c) => c.key), ['matched', 'auto_linked']);
  });

  it('has every key in English and Arabic with the same placeholders, and no long dashes', () => {
    const keys = [
      ...SWITCHES.map((k) => `fmatch.switch.${k}`), ...SOURCES.map((k) => `fmatch.source.${k}`), ...REVIEW_TABS.map((k) => `fmatch.review.tab.${k}`),
      ...REVIEW_TABS.map((k) => `fmatch.review.empty.${k}`), ...COUNT_KEYS.map((k) => `fmatch.count.${k}`), ...NEVER_AUTOMATED.map((k) => `fsug.never.${k}`),
    ];
    for (const key of keys) {
      assert.ok(key in en, `en ${key}`);
      assert.ok(key in ar, `ar ${key}`);
    }
    const placeholders = (text: unknown) => [...String(text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join();
    for (const [key, text] of Object.entries(financeMatchingEn)) {
      const arabic = (financeMatchingAr as Record<string, unknown>)[key];
      assert.ok(arabic !== undefined, `missing Arabic ${key}`);
      assert.equal(placeholders(arabic), placeholders(text), key);
      assert.ok(!/[–—]/.test(String(text)) && !/[–—]/.test(String(arabic)), key);
    }
    assert.equal(Object.keys(financeMatchingAr).length, Object.keys(financeMatchingEn).length);
  });
});
