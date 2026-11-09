// Card FX details in the transaction drawer (docs/finance.md#card-fx-and-fees): what shows, the rate's label and the fee action.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { FinanceTransactionFx } from '@anum/contracts';
import { differenceTone, plainRate, rateKey, separateAction, showsFx } from '../src/lib/financeFx.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const fee = { amount: '1.88', currency: 'SAR', source: 'markup_stated' as const, transaction_id: null, markup_percent: '2.5', included_in_billed: true, linked: false };
const base = { foreign: true, fee: null, transfer: null, rate: '3.750000', rate_kind: 'implied' as const, split: 'none' as const, can_correct: true };

describe('card FX details', () => {
  it('shows the section for a foreign purchase, a fee or a transfer in two currencies only', () => {
    assert.equal(showsFx(null), false);
    assert.equal(showsFx({ foreign: false, fee: null, transfer: null }), false);
    assert.equal(showsFx({ foreign: true, fee: null, transfer: null }), true);
    assert.equal(showsFx({ foreign: false, fee, transfer: null }), true);
  });
  it('never presents a rate that was not stated', () => {
    assert.equal(rateKey({ rate: null, rate_kind: 'not_stated' }), 'ffx.rate.notStated');
    assert.equal(rateKey({ rate: '3.75', rate_kind: 'printed' }), 'ffx.rate.printed');
    assert.equal(rateKey({ rate: '3.75', rate_kind: 'implied' }), 'ffx.rate.implied');
    assert.equal(plainRate('3.750000'), '3.75');
    assert.equal(plainRate('1.0211000000'), '1.0211');
    assert.equal(plainRate('4'), '4');
    assert.equal(plainRate(null), '-');
  });
  it('offers to split an inline fee, link a fee row, and nothing for a bank message or a done split', () => {
    assert.equal(separateAction({ ...base, fee, split: 'available' }), 'split');
    assert.equal(separateAction({ ...base, fee: { ...fee, source: 'fee_row' }, split: 'link' }), 'link');
    assert.equal(separateAction({ ...base, fee, split: 'available', can_correct: false }), null);
    assert.equal(separateAction({ ...base, fee, split: 'done' }), null);
    assert.equal(separateAction({ ...base, split: 'available' } as Pick<FinanceTransactionFx, 'split' | 'can_correct' | 'fee'>), null);
  });
  it('reads a realised difference as a gain or a loss', () => {
    assert.equal(differenceTone('-1.10'), 'loss');
    assert.equal(differenceTone('2.00'), 'gain');
    assert.equal(differenceTone('0.00'), 'none');
    assert.equal(differenceTone(null), 'none');
  });
  it('words every FX line in English and Arabic', () => {
    const keys = Object.keys(en).filter((key) => key.startsWith('ffx.'));
    assert.ok(keys.length > 30);
    for (const key of keys) assert.ok(key in ar, key);
  });
});
