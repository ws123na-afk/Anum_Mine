// The Suggestions page's model (docs/finance.md#suggestions): accept modes, confidence, kind chips and the rule form.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  EMPTY_RULE, NEVER_AUTOMATED, SUGGESTION_VIEWS, acceptMode, confidencePercent, confidenceTone, fromContent, kindChips, matchSummary, splitWords, toContent,
} from '../src/lib/financeSuggestions.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const action = (kind: 'classify' | 'confirm_refund' | 'acknowledge' | 'open') => ({ action: { kind, transaction_ids: [], category: null, subcategory_id: null, transaction_type: null } });

describe('finance suggestions model', () => {
  it('says what Accept does: apply changes rows, acknowledge only records, open has no Accept', () => {
    assert.equal(acceptMode(action('classify')), 'apply');
    assert.equal(acceptMode(action('confirm_refund')), 'apply');
    assert.equal(acceptMode(action('acknowledge')), 'acknowledge');
    assert.equal(acceptMode(action('open')), 'none');
  });
  it('reads confidences as whole percentages and never invents one', () => {
    assert.equal(confidencePercent('0.85'), 85);
    assert.equal(confidencePercent(null), null);
    assert.equal(confidencePercent('x'), null);
    assert.equal(confidenceTone('0.95'), 'ok');
    assert.equal(confidenceTone('0.6'), 'warn');
    assert.equal(confidenceTone(null), 'muted');
  });
  it('builds the kind chips with an all chip that adds up', () => {
    const chips = kindChips({ counts: [{ kind: 'bill_due', count: 2 }, { kind: 'uncategorized', count: 5 }] });
    assert.deepEqual(chips, [{ kind: 'all', count: 7 }, { kind: 'bill_due', count: 2 }, { kind: 'uncategorized', count: 5 }]);
  });
  it('splits phrases on commas, once each, whatever the case', () => {
    assert.deepEqual(splitWords(' zebra  mart, ZEBRA MART ,, quokka '), ['zebra mart', 'quokka']);
  });
  it('refuses a rule that matches nothing specific, or an amount range without its currency', () => {
    assert.deepEqual(toContent(EMPTY_RULE), { error: 'needMatch' });
    assert.deepEqual(toContent({ ...EMPTY_RULE, words: 'zebramart', minAmount: '100' }), { error: 'needCurrency' });
    assert.deepEqual(toContent({ ...EMPTY_RULE, words: 'zebramart', minAmount: '300', maxAmount: '100', currency: 'aed' }), { error: 'badRange' });
  });
  it('turns the form into rule content and back', () => {
    const made = toContent({ ...EMPTY_RULE, merchant: 'Zebra Mart', words: 'card 44', direction: 'out', minAmount: '100', maxAmount: '200', currency: 'aed',
      accountId: 'fin_acct_1', category: 'shopping', autoApply: true });
    assert.ok('content' in made);
    assert.equal(made.content.match.currency, 'AED');
    assert.deepEqual(made.content.match.account_ids, ['fin_acct_1']);
    assert.equal(made.content.auto_apply, true);
    const back = fromContent(made.content);
    assert.equal(back.merchant, 'Zebra Mart');
    assert.equal(back.words, 'card 44');
    assert.equal(back.accountId, 'fin_acct_1');
    assert.deepEqual(matchSummary(made.content.match), ['Zebra Mart', 'card 44']);
  });
  it('labels every view, kind, refusal and never-automated action in both languages', () => {
    const keys = [
      ...SUGGESTION_VIEWS.map((v) => `fsug.view.${v}`),
      ...['classification', 'conflict', 'uncategorized', 'statement_hold', 'sms_review', 'duplicate', 'refund_match', 'payment_match', 'bill_due', 'subscription',
        'unusual_spend', 'missing_statement', 'cash_shortage', 'reconciliation', 'external'].flatMap((k) => [`fsug.kind.${k}`, `fsug.head.${k}`]),
      ...NEVER_AUTOMATED.map((k) => `fsug.never.${k}`),
      'fsug.why.reconciliation.blocking', 'fsug.why.reconciliation.warning', 'fsug.why.reconciliation.missing',
      'finance.tab.suggestions',
    ];
    for (const key of keys) {
      assert.ok(key in en, `en ${key}`);
      assert.ok(key in ar, `ar ${key}`);
    }
  });
});
