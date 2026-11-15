// The Personal Finance dashboard (docs/finance.md#dashboard). The fixture is shared with the phone:
// apps/mobile/test/finance/finance_core_test.dart parses the same file and expects the same totals, so web and
// mobile show the same figures for the same answer.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, it } from 'node:test';
import type { FinanceSummary } from '@anum/contracts';
import { dashboardTotals, daysSince, freshnessFor, lastCapture, upcomingPayments } from '../src/lib/financeDashboard.ts';

const fixture = JSON.parse(readFileSync(new URL('../../../packages/contracts/fixtures/finance-summary.json', import.meta.url), 'utf8')) as FinanceSummary;

// The same numbers are written in the Dart test: change both together.
const EXPECTED_SAR = {
  balance: '7429.75', cash: '8750.25', cardOwed: '1320.50', cardAvailable: '13679.50', income: '15250.00', spend: '1130.50', net: '14176.90',
  paymentsDue: '2750.00', upcomingCount: 3, reviewTotal: 4, staleAccounts: 1, salaryReceived: '15000.00',
};

describe('dashboard totals from the shared fixture', () => {
  it('reads every SAR figure as the server sent it', () => {
    const totals = dashboardTotals(fixture, 'SAR');
    const { excluded, currency, ...rest } = totals;
    assert.equal(currency, 'SAR');
    assert.deepEqual(rest, EXPECTED_SAR);
    assert.deepEqual(excluded, {
      own_transfer: { in: '2000.00', out: '2000.00', count: 2 },
      card_payment: { in: '1500.00', out: '1500.00', count: 2 },
      loan_principal: { in: '0.00', out: '1800.00', count: 1 },
    });
  });

  it('keeps each currency on its own and never converts', () => {
    const usd = dashboardTotals(fixture, 'USD');
    assert.equal(usd.balance, '1200.00');
    assert.equal(usd.spend, '15.99');
    assert.equal(usd.cardAvailable, null);
    assert.equal(usd.paymentsDue, '0.00');
    assert.equal(usd.salaryReceived, null);
    assert.deepEqual(usd.excluded, {});
  });

  it('falls back to the bills for an older answer without upcoming payments', () => {
    const older = { ...fixture, upcoming_payments: undefined } as FinanceSummary;
    assert.deepEqual(upcomingPayments(older).map((p) => [p.kind, p.name, p.amount]), [['bill', 'Rent', '2500.00']]);
    assert.equal(upcomingPayments(fixture).length, 3);
  });
});

describe('account freshness', () => {
  it('names the newest capture and the verified balance', () => {
    const card = freshnessFor(fixture, 'acc_card')!;
    assert.deepEqual(lastCapture(card), { source: 'sms', at: '2026-11-15T09:05:00Z' });
    assert.equal(card.balance_kind, 'estimated');
    const cash = freshnessFor(fixture, 'acc_cash')!;
    assert.equal(lastCapture(cash), null);
    assert.equal(cash.state, 'manual');
    assert.equal(freshnessFor(fixture, 'missing'), null);
    assert.equal(daysSince('2026-11-10T00:00:00Z', new Date('2026-11-15T12:00:00Z')), 5);
  });
});
