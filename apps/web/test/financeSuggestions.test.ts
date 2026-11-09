// The Suggestions page's model (docs/finance.md#suggestions): accept modes, confidence, kind chips and the rule form.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  EMPTY_RULE, NEVER_AUTOMATED, NOTICE_KINDS, SUGGESTION_VIEWS, acceptMode, confidencePercent, confidenceTone, fromContent, kindChips, matchSummary, splitWords,
  toContent, toggleNotice, variantOf,
} from '../src/lib/financeSuggestions.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const action = (kind: 'classify' | 'confirm_refund' | 'acknowledge' | 'open') => ({ action: { kind, transaction_ids: [], category: null, subcategory_id: null, transaction_type: null } });

describe('attribution, FX fee and exception notices (docs/finance.md#attribution-suggestions)', () => {
  const link = { canonical_id: 'c', dimension: 'business' as const, target_type: 'business_workspace', target_id: 'ws', amount: '80.00', currency: 'SAR',
    treatment: 'reimbursable' as const, category: 'shopping', project_id: null };
  it('a link suggestion links through the API; one without a proposal has no Accept', () => {
    assert.equal(acceptMode({ action: { kind: 'link', transaction_ids: [], category: null, subcategory_id: null, transaction_type: null, link } }), 'link');
    assert.equal(acceptMode({ action: { kind: 'link', transaction_ids: [], category: null, subcategory_id: null, transaction_type: null, link: null } }), 'none');
  });
  it('words each attribution by the module it links to', () => {
    const a = (params: Record<string, string>, kind = 'attribution' as const, actionKind: 'link' | 'open' = 'link') =>
      variantOf({ kind, params, action: { kind: actionKind, transaction_ids: [], category: null, subcategory_id: null, transaction_type: null } });
    assert.equal(a({ dimension: 'business' }), 'business');
    assert.equal(a({ dimension: 'vehicle', record: 'none' }, 'attribution', 'open'), 'vehicle_record');
    assert.equal(a({ dimension: 'purchase' }), 'purchase');
    assert.equal(variantOf({ kind: 'payment_match', params: {}, action: { kind: 'open', transaction_ids: [], category: null, subcategory_id: null, transaction_type: null } }), null);
    assert.equal(variantOf({ kind: 'fx_fee', params: { source: 'fee_row' }, action: { kind: 'open', transaction_ids: [], category: null, subcategory_id: null, transaction_type: null } }), 'link');
    for (const variant of ['business', 'vehicle', 'vehicle_record', 'purchase', 'debt']) {
      for (const key of [`fsug.head.attribution.${variant}`, `fsug.why.attribution.${variant}`]) {
        assert.ok(key in en, key);
        assert.ok(key in ar, key);
      }
    }
  });
  it('turns exception notices on and off in a stable order, never twice', () => {
    assert.deepEqual(toggleNotice([], 'evidence_missing', true), ['evidence_missing']);
    assert.deepEqual(toggleNotice(['evidence_missing'], 'reconciliation_overdue', true), ['reconciliation_overdue', 'evidence_missing']);
    assert.deepEqual(toggleNotice(['evidence_missing', 'evidence_missing'], 'evidence_missing', false), []);
    for (const kind of NOTICE_KINDS) assert.ok(`fsug.notice.${kind}` in en && `fsug.notice.${kind}` in ar, kind);
  });
});

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
