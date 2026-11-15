// The debt overview, cards, promises, alerts and advisor answers (docs/finance.md#debt-overview, #credit-cards,
// #financial-alerts, #advisor-answers): the page helpers and the catalog keys. Every value is fake and low entropy.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  ALERT_CHANNELS, alertChannel, alertSettingsBody, answerParams, channelReasonKey, channelUsable, evidenceKey, evidenceTone, grouped, profileBody, PROFILE_TYPES,
  QUESTIONS, statusTone, withChannel, type AlertChannels, type AlertKind, type Obligation,
} from '../src/lib/financeDebts.ts';
import { financeDebtsEn } from '../src/lib/messages/financeDebts.en.ts';
import { financeDebtsAr } from '../src/lib/messages/financeDebts.ar.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const ob = (over: Partial<Obligation> = {}): Obligation => ({
  id: 'debt:a', ref_type: 'debt', ref_id: 'a', type: 'personal_loan', direction: 'owe', name: 'Example', counterparty: null, currency: 'AED',
  original_amount: null, start_on: null, schedule: 'monthly', interest: 'unknown', rate: null, expected_installment: null, paid: '0', remaining: '100.00',
  interest_paid: null, fees: null, next_due_on: null, next_due_amount: '0', overdue_amount: '0', status: 'open', verified_balance: null, verified_on: null,
  difference: null, discrepancy: false, disputed_amount: null, unverified_amount: '0', payoff_on: null, payoff_basis: null, assumptions: [], history: [],
  promises: [], sources: [], missing: [], contact_id: null, ...over,
});

describe('the debt overview helpers', () => {
  it('groups what I owe, what is owed to me and what is finished', () => {
    const groups = grouped([ob(), ob({ id: 'p', direction: 'owed_to_me' }), ob({ id: 'z', status: 'paid_off' }), ob({ id: 'c', status: 'closed', direction: 'owed_to_me' })]);
    assert.deepEqual([groups.owe.map((o) => o.id), groups.owedToMe.map((o) => o.id), groups.finished.map((o) => o.id)], [['debt:a'], ['p'], ['z', 'c']]);
  });

  it('only a matched or statement-posted payment is green; a provider report never is', () => {
    assert.equal(evidenceTone('verified'), 'ok');
    assert.equal(evidenceTone('posted'), 'ok');
    assert.equal(evidenceTone('reported'), 'warn');
    assert.equal(evidenceTone('provisional'), 'warn');
    assert.equal(evidenceTone('pending'), 'warn');
    assert.equal(statusTone('overdue'), 'bad');
    assert.equal(statusTone('disputed'), 'warn');
    assert.equal(statusTone('paid_off'), 'ok');
  });

  it('sends only the parameters a question uses', () => {
    const all = { category: 'food', words: 'laundry', country: 'pakistan', currency: 'AED', month: '2026-11', amount: '1000' };
    assert.deepEqual(answerParams('owe', all), {});
    assert.deepEqual(answerParams('category_spend', all), { category: 'food', month: '2026-11' });
    assert.deepEqual(answerParams('afford', { amount: ' 1000 ', currency: '' }), { amount: '1000' });
    assert.deepEqual(answerParams('sent_home', all), { country: 'pakistan', currency: 'AED', month: '2026-11' });
  });

  it('turns empty profile fields into null so they are cleared', () => {
    assert.deepEqual(profileBody({ verified_balance: '2500', verified_on: '2026-11-01', card_statement_day: '15', dispute_note: '  ' }), {
      debt_type: null, verified_balance: '2500', verified_on: '2026-11-01', disputed_amount: null, dispute_note: null, fees: null, card_limit: null,
      card_statement_day: 15, card_statement_balance: null, card_statement_minimum: null, card_statement_on: null,
    });
  });

  it('names every evidence and source code it knows and leaves others alone', () => {
    assert.equal(evidenceKey('reported'), 'fdebt.evidence.reported');
    assert.equal(evidenceKey('provider_statement'), 'fdebt.source.provider_statement');
    assert.equal(evidenceKey('something_new'), null);
  });
});

describe('the alert channels', () => {
  const kind = (over: Partial<AlertKind> = {}): AlertKind => ({ kind: 'card_payment_due', on: true, title: 'Card payment due', in_app: true, push: true, email: false, ...over });
  const reach = (push: boolean, email: boolean, reason: string | null = null): AlertChannels => ({
    in_app: { available: true, reason: null, message: null },
    push: { available: push, reason: push ? null : 'no_device', message: null },
    email: { available: email, reason: email ? null : reason, message: null },
  });

  it('reads each channel, and an older answer with only the in-app switch', () => {
    assert.deepEqual(ALERT_CHANNELS.map((c) => alertChannel(kind(), c)), [true, true, false]);
    assert.deepEqual(ALERT_CHANNELS.map((c) => alertChannel({ kind: 'refund_received', on: false, title: 'Refund received' }, c)), [false, false, false]);
  });

  it('turns one channel on or off and keeps the in-app switch the older server reads in step', () => {
    const off = withChannel(kind(), 'in_app', false);
    assert.equal(off.on, false);
    assert.equal(off.in_app, false);
    assert.equal(withChannel(kind(), 'email', true).email, true);
    assert.deepEqual(alertSettingsBody([withChannel(kind(), 'push', false)]), {
      kinds: { card_payment_due: true }, channels: { card_payment_due: { in_app: true, push: false, email: false } },
    });
  });

  it('says why a channel cannot be used, with a catalog key in both languages', () => {
    const none = reach(false, false, 'not_configured');
    assert.equal(channelUsable(none, 'in_app'), true);
    assert.equal(channelUsable(none, 'push'), false);
    assert.equal(channelReasonKey(none, 'in_app'), null);
    assert.equal(channelReasonKey(none, 'push'), 'falert.why.push.no_device');
    assert.equal(channelReasonKey(none, 'email'), 'falert.why.email.not_configured');
    assert.equal(channelReasonKey(reach(true, true), 'email'), null);
    for (const key of ['push.not_configured', 'push.push_off', 'push.no_device', 'email.not_configured', 'email.email_off', 'email.no_address']) {
      assert.ok(`falert.why.${key}` in financeDebtsEn && `falert.why.${key}` in financeDebtsAr, key);
    }
    for (const c of ALERT_CHANNELS) assert.ok(`falert.channel.${c}` in financeDebtsEn, c);
    assert.ok('falert.kind.loan_due_soon' in financeDebtsEn && 'falert.hint.loan_due_soon' in financeDebtsAr);
  });

  it('names and explains every alert kind the server sends, failed capture included, in English and Arabic', () => {
    // The server's list (services/api/anum_api/finance/alerts.py KINDS).
    const kinds = ['installment_due_tomorrow', 'installment_paid', 'unverified_payment', 'card_payment_due', 'loan_due_soon', 'loan_installment_missed',
      'loan_payment_linked', 'source_stopped', 'capture_failed', 'budget_nearly_exhausted', 'unexpected_bank_charge', 'suspected_duplicate',
      'refund_received', 'new_recurring_charge', 'salary_received', 'salary_differs', 'lender_balance_differs'];
    for (const kind of kinds) {
      for (const part of ['kind', 'hint']) {
        assert.ok(`falert.${part}.${kind}` in financeDebtsEn, `${part} ${kind} en`);
        assert.ok(`falert.${part}.${kind}` in financeDebtsAr, `${part} ${kind} ar`);
      }
    }
  });
});

describe('the catalogs', () => {
  it('have the same keys in English and Arabic, all spread into the full catalogs', () => {
    assert.deepEqual(Object.keys(financeDebtsAr).sort(), Object.keys(financeDebtsEn).sort());
    for (const key of Object.keys(financeDebtsEn)) {
      assert.ok(key in en && key in ar, key);
    }
  });

  it('name every question, type and evidence code the pages use', () => {
    for (const q of QUESTIONS) assert.ok(`fanswer.q.${q}` in financeDebtsEn, q);
    for (const type of [...PROFILE_TYPES, 'borrowed_person', 'lent_person']) assert.ok(`fdebt.type.${type}` in financeDebtsEn, type);
    for (const code of ['verified', 'reported', 'provider_statement', 'ledger', 'schedule_and_matched_payments']) {
      const key = evidenceKey(code);
      assert.ok(key && key in financeDebtsEn, code);
    }
  });

  it('keep the same placeholders in both languages and never use a long dash', () => {
    const holes = (value: unknown) => JSON.stringify(value).match(/\{[a-z_]+\}/gi)?.sort() ?? [];
    for (const [key, value] of Object.entries(financeDebtsEn)) {
      const arabic = (financeDebtsAr as Record<string, unknown>)[key];
      assert.deepEqual([...new Set(holes(arabic))], [...new Set(holes(value))], key);
      assert.ok(!/[–—]/.test(JSON.stringify(value)) && !/[–—]/.test(JSON.stringify(arabic)), key);
    }
  });
});
