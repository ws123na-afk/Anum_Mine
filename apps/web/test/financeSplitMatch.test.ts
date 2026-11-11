// One statement row for several recorded payments in the import preview (src/lib/financeMatch.ts, src/lib/financePdf.ts,
// docs/finance.md#one-row-for-several-recorded-payments). Every row is SYNTHETIC. Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { FinanceRowMatch } from '@anum/contracts';
import { decisionsFor, groupsFor, outcomeOf, sameRecords, splitsOf, tally, type MatchRow } from '../src/lib/financeMatch.ts';
import { financeMatchAr } from '../src/lib/messages/financeMatch.ar.ts';
import { financeMatchEn } from '../src/lib/messages/financeMatch.en.ts';
import { financeConnectionsAr } from '../src/lib/messages/financeConnections.ar.ts';
import { financeConnectionsEn } from '../src/lib/messages/financeConnections.en.ts';

const record = (id: string, amount: string) => ({ record_id: id, canonical_id: id, transaction_id: null, account_id: 'card', source: 'sms', last4: '1234',
  posted_on: '2026-11-03', amount, currency: 'SAR', description: 'EXAMPLE MARKET' });
const group = [record('e1:card', '-100.00'), record('e2:card', '-50.00')];
const match = (patch: Partial<FinanceRowMatch>): FinanceRowMatch => ({
  status: 'new', code: 'no_match', reason: '', record: null, candidates: [], lag_days: null, amount_difference: null, bill: null, transfer: null,
  default_import: true, splits: [group], ...patch,
});
const row = (m: FinanceRowMatch): MatchRow => ({ id: '2', line: 2, posted_on: '2026-11-04', description: 'EXAMPLE MARKET', amount: '-150.00', match: m });

describe('one row for several recorded payments', () => {
  test('groups are offered only on new rows and possible duplicates, which then wait for the person', () => {
    assert.equal(splitsOf(match({})).length, 1);
    assert.equal(splitsOf(match({ status: 'possible_duplicate' })).length, 1);
    assert.equal(splitsOf(match({ status: 'matched_existing' })).length, 0);
    assert.deepEqual(groupsFor(match({})), ['decide']);
    assert.deepEqual(groupsFor(match({ splits: [] })), ['new']);
  });

  test('picking a group attaches the row instead of importing it, and the import sends exactly the group', () => {
    const picked = { split: ['e2:card', 'e1:card'] };
    assert.equal(sameRecords(picked.split, group), true);
    assert.equal(sameRecords(['e1:card'], group), false);
    assert.equal(outcomeOf(match({}), undefined), 'import');
    assert.equal(outcomeOf(match({}), picked), 'attach');
    assert.equal(outcomeOf(match({ status: 'possible_duplicate' }), picked), 'attach');
    assert.equal(outcomeOf(match({}), { ...picked, action: 'skip' }), 'skip');
    assert.equal(outcomeOf(match({}), { split: ['e9:card', 'e8:card'] }), 'import');  // not an offered group
    assert.deepEqual(decisionsFor([row(match({}))], { 2: picked }), [{ line: 2, split: ['e2:card', 'e1:card'] }]);
    assert.deepEqual(decisionsFor([row(match({}))], { 2: { ...picked, action: 'skip' } }), [{ line: 2, action: 'skip' }]);
    assert.equal(tally([row(match({}))], { 2: picked }).attach, 1);
  });

  test('every new message exists in English and Arabic with the same placeholders', () => {
    for (const key of ['fmatch.split.legend', 'fmatch.split.help', 'fmatch.split.option'] as const) assert.ok(financeMatchEn[key] && financeMatchAr[key], key);
    for (const key of ['fimprows.split.undo', 'fimprows.split.undoText', 'fimprows.reason.split_match', 'fimprows.reason.split_changed', 'fimprows.reason.split_undone'] as const) {
      assert.ok(financeConnectionsEn[key] && financeConnectionsAr[key], key);
    }
  });
});
