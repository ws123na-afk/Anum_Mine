// Loan suggestions wording, the adjustment approval threshold and the new catalog keys
// (docs/finance.md#lent-and-borrowed, docs/business-books.md#adjustment-approval).
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { variantOf } from '../src/lib/financeSuggestions.ts';
import { settingsBody } from '../src/lib/businessBooks.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const open = { kind: 'acknowledge' as const, transaction_ids: [], category: null, subcategory_id: null, transaction_type: null };

describe('loans with people in the Suggestions queue', () => {
  it('words an overdue repayment and a possible repayment by who owes whom', () => {
    assert.equal(variantOf({ kind: 'loan_overdue', params: { direction: 'lent' }, action: open }), 'lent');
    assert.equal(variantOf({ kind: 'loan_repayment', params: { direction: 'borrowed' }, action: open }), 'borrowed');
    for (const kind of ['loan_overdue', 'loan_repayment']) {
      for (const side of ['lent', 'borrowed']) {
        for (const key of [`fsug.head.${kind}.${side}`, `fsug.why.${kind}.${side}`, `fsug.kind.${kind}`]) {
          assert.ok(key in en && key in ar, key);
        }
      }
    }
  });
});

describe('books settings and the accountant review', () => {
  const form = { base_currency: 'SAR', country: '', fiscal_year_start_month: 1, basis: 'accrual' as const, tax_label: '', rate_max_age_days: '31', mappings: [] };
  it('sends the approval threshold, and empty turns the step off', () => {
    assert.equal(settingsBody({ ...form, adjustment_approval_threshold: '1000' }, null).adjustment_approval_threshold, '1000');
    assert.equal(settingsBody({ ...form, adjustment_approval_threshold: ' ' }, null).adjustment_approval_threshold, null);
    assert.ok(!('adjustment_approval_threshold' in settingsBody(form, null)));
  });
  it('every review kind has its words in both languages', () => {
    for (const kind of ['draft_bill', 'adjustment_approval', 'unconverted', 'posting_issue', 'allocation_review', 'vat', 'receipt_match', 'external']) {
      for (const key of [`breview.kind.${kind}`, `breview.count.${kind}`]) assert.ok(key in en && key in ar, key);
    }
    for (const code of ['return_due_soon', 'return_overdue', 'missing_tax_number', 'missing_tax_code', 'vat_unreconciled']) {
      assert.ok(`fdepth.vat.${code}` in en && `fdepth.vat.${code}` in ar, code);
      assert.ok(!/complian/i.test(JSON.stringify((en as Record<string, unknown>)[`fdepth.vat.${code}`])), 'never claims compliance');
    }
  });
});
