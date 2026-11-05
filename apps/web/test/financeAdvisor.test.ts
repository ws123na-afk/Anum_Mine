// Finance, Money advisor helpers (docs/finance.md#money-advisor).
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { FinanceAdvisorAction, FinanceAdvisorCard, FinanceAdvisorNote, FinanceAdvisorRunway } from '@anum/contracts';
import {
  actionTab, actionText, cardsDue, headlineScenario, monthsList, pageCount, pickCurrency, runwayTone, severityTone, sortActions, sumMoney, weekdayName,
} from '../src/lib/financeAdvisor.ts';
import { financeAdvisorAr } from '../src/lib/messages/financeAdvisor.ar.ts';
import { financeAdvisorEn } from '../src/lib/messages/financeAdvisor.en.ts';
import { placeholdersOf } from '../src/lib/i18n.ts';

const action = (code: string, severity: FinanceAdvisorAction['severity'], tab: string | null = null): FinanceAdvisorAction =>
  ({ code, severity, currency: 'AED', params: {}, text_en: `${code} en`, text_ar: `${code} ar`, tab });

const runway = (scenarios: FinanceAdvisorRunway['scenarios']): FinanceAdvisorRunway => ({
  status: 'ok', history_days: 120, first_record_on: '2026-07-01', basis: 'months', months: ['2026-09', '2026-10'], liquid: '1000.00', accounts_counted: 1,
  bills_monthly: '0.00', minimums_monthly: '0.00', essential_spending_monthly: '0.00', total_spending_monthly: '0.00', essential_categories: [], scenarios,
});

describe('money advisor helpers', () => {
  it('shows the action in the page language and opens only real Finance pages', () => {
    assert.equal(actionText(action('bill_due', 'high'), 'en'), 'bill_due en');
    assert.equal(actionText(action('bill_due', 'high'), 'ar'), 'bill_due ar');
    assert.equal(actionTab(action('x', 'info', 'debts')), 'debts');
    assert.equal(actionTab(action('x', 'info', 'payments')), null);
    assert.equal(actionTab(action('x', 'info')), null);
  });
  it('keeps the ranking: urgent first, then the API order', () => {
    const sorted = sortActions([action('a', 'info'), action('b', 'urgent'), action('c', 'medium'), action('d', 'urgent')]);
    assert.deepEqual(sorted.map((a) => a.code), ['b', 'd', 'c', 'a']);
    assert.equal(severityTone('urgent'), 'bad');
    assert.equal(severityTone('info'), 'muted');
  });
  it('uses essentials for the headline and falls back to the bare minimum without history', () => {
    const bare = { kind: 'bare' as const, monthly_outflow: '100.00', days: 300, runs_out_on: '2027-09-01' };
    const essential = { kind: 'essential' as const, monthly_outflow: '500.00', days: 45, runs_out_on: '2026-12-20' };
    assert.equal(headlineScenario(runway([bare, essential]))?.kind, 'essential');
    assert.equal(headlineScenario(runway([bare]))?.kind, 'bare');
    assert.deepEqual([runwayTone(10), runwayTone(45), runwayTone(90), runwayTone(null)], ['bad', 'warn', 'good', '']);
  });
  it('picks the chosen currency, else the base currency, else the first', () => {
    const block = (currency: string) => ({ currency }) as FinanceAdvisorNote['currencies'][number];
    const note = { base_currency: 'AED', currencies: [block('USD'), block('AED')] } as FinanceAdvisorNote;
    assert.equal(pickCurrency(note, 'USD')?.currency, 'USD');
    assert.equal(pickCurrency(note, 'EUR')?.currency, 'AED');
    assert.equal(pickCurrency({ ...note, currencies: [] }, null), null);
  });
  it('counts cards that owe money and fall due in 30 days, per currency, and adds money exactly', () => {
    const card = (owed: string, days: number | null, currency = 'AED') => ({ currency, owed, days_until: days }) as FinanceAdvisorCard;
    assert.equal(cardsDue([card('100.00', 3), card('0.00', 3), card('50.00', null), card('20.00', 40), card('5.00', 2, 'USD')], 'AED').length, 1);
    assert.equal(sumMoney(['0.10', '0.20', '1000.05']), '1000.35');
    assert.equal(sumMoney(['-1.50', '0.25']), '-1.25');
    assert.equal(sumMoney(['1.005', '2.010'], 3), '3.015');
    assert.equal(sumMoney([], 2), '0.00');
  });
  it('names weekdays from Monday and pages the history', () => {
    assert.equal(weekdayName(0, 'en-GB'), 'Monday');
    assert.equal(weekdayName(6, 'en-GB'), 'Sunday');
    assert.notEqual(weekdayName(6, 'ar'), 'Sunday');
    assert.deepEqual([pageCount(0), pageCount(8), pageCount(9)], [1, 1, 2]);
    assert.equal(monthsList(['2026-09', '2026-10'], 'en-GB'), monthsList(['2026-09'], 'en-GB') + ', Oct 2026');
  });
  it('has the same keys and placeholders in English and Arabic, and no long dashes', () => {
    assert.deepEqual(Object.keys(financeAdvisorAr).sort(), Object.keys(financeAdvisorEn).sort());
    for (const [key, english] of Object.entries(financeAdvisorEn)) {
      const arabic = financeAdvisorAr[key as keyof typeof financeAdvisorAr];
      assert.deepEqual(new Set(placeholdersOf(arabic)), new Set(placeholdersOf(english)), key);
      assert.ok(!/[–—―]/.test(JSON.stringify([english, arabic])), key);
    }
  });
});
