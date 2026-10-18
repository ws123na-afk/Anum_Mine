import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { setLocale, t } from '../src/lib/i18n.ts';
import { ApiError, apiErrorFrom, describeError, isBillingCode, isBudgetExceeded } from '../src/lib/errors.ts';
import {
  ONBOARDING_LINKS, blockedFromError, bpToPercent, consoleOrgPath, formatBytes, formatMoney, formatRate, invoiceTone, invoicesPath, meterValues, minorDigits,
  minorToInput, pageRange, parseBlockedBy, parseMoney, percentToBp, planFromHash, previewPath, proofProblem, publicRoute, signupProblems, subscriptionsPath,
  usagePercent, usageTone,
} from '../src/lib/billing.ts';
import { billingEn } from '../src/lib/messages/billing.en.ts';
import { billingAr } from '../src/lib/messages/billing.ar.ts';

afterEach(() => setLocale('en'));
// Intl puts a no-break space between the currency code and the number; compare on plain spaces.
const plain = (text: string) => text.replace(/[  ]/g, ' ');

describe('customer billing helpers', () => {
  test('money comes from integer minor units in the currency, SAR by default', () => {
    assert.equal(minorDigits('SAR'), 2);
    assert.equal(minorDigits('JPY'), 0);
    assert.equal(minorDigits('KWD'), 3);
    assert.equal(plain(formatMoney(29900, 'SAR', 'en')), 'SAR 299.00');
    assert.equal(plain(formatMoney(150000)), 'SAR 1,500.00');
    assert.equal(plain(formatMoney(1234, 'USD', 'en')), '$12.34');
    assert.equal(plain(formatMoney(500, 'JPY', 'en')), '¥500');
    assert.equal(formatMoney(null), '-');
    assert.equal(plain(formatMoney(100, 'bad', 'en')), 'SAR 1.00');
    // Arabic keeps Western digits (docs/web-localization.md) and still names the currency.
    const arabic = formatMoney(29900, 'SAR', 'ar');
    assert.match(arabic, /299\.00|299٫00/);
    assert.doesNotMatch(arabic, /[٠-٩]/);
  });

  test('typed amounts become minor units, and back', () => {
    assert.equal(parseMoney('1500'), 150000);
    assert.equal(parseMoney('1,500.5'), 150050);
    assert.equal(parseMoney('0.01'), 1);
    assert.equal(parseMoney('12.345'), null);
    assert.equal(parseMoney('-3'), null);
    assert.equal(parseMoney('abc'), null);
    assert.equal(parseMoney('500', 'JPY'), 500);
    assert.equal(minorToInput(150050), '1500.50');
  });

  test('tax rates are basis points shown as a percent', () => {
    assert.equal(formatRate(1500, 'en'), '15%');
    assert.equal(formatRate(750, 'en'), '7.5%');
    assert.equal(percentToBp('15'), 1500);
    assert.equal(percentToBp('7.5'), 750);
    assert.equal(percentToBp('101'), null);
    assert.equal(percentToBp(''), null);
    assert.equal(bpToPercent(1500), '15');
  });

  test('usage percent is 0 to 100, null when unlimited, with a tone', () => {
    assert.equal(usagePercent(3, 5), 60);
    assert.equal(usagePercent(5, 5), 100);
    assert.equal(usagePercent(9, 5), 100);
    assert.equal(usagePercent(0, 0), 0);
    assert.equal(usagePercent(2, null), null);
    assert.equal(usageTone(60), 'ok');
    assert.equal(usageTone(80), 'warn');
    assert.equal(usageTone(95), 'danger');
    assert.equal(usageTone(null), 'ok');
  });

  test('storage meters read in bytes, other meters as counts', () => {
    assert.equal(formatBytes(1536, 'en'), '1.5 KB');
    assert.equal(formatBytes(5 * 1024 ** 3, 'en'), '5 GB');
    assert.deepEqual(meterValues({ key: 'storage', used: 1024 ** 3, limit: 10 * 1024 ** 3 }, 'en'), { used: '1 GB', limit: '10 GB' });
    assert.deepEqual(meterValues({ key: 'seats', used: 1200, limit: null }, 'en'), { used: '1,200', limit: null });
  });

  test('blocked_by comes from the preview or from a 409 refusal', () => {
    assert.deepEqual(parseBlockedBy([{ key: 'seats', used: 7, limit: 5 }, { nope: 1 }]), [{ key: 'seats', used: 7, limit: 5 }]);
    const envelope = { error: { code: 'conflict', message: 'Too much in use.', details: [{ location: 'seats', message: '7 of 5', type: 'blocked_by' }, { location: 'body.x', message: 'bad', type: 'value_error' }] } };
    assert.deepEqual(parseBlockedBy(envelope), [{ key: 'seats', used: 7, limit: 5 }]);
    assert.deepEqual(parseBlockedBy(null), []);
    const error = apiErrorFrom(409, envelope);
    assert.deepEqual(blockedFromError(error), [{ key: 'seats', used: 7, limit: 5 }]);
    assert.deepEqual(blockedFromError(apiErrorFrom(422, envelope)), []);
    assert.deepEqual(blockedFromError(new Error('x')), []);
  });

  test('402 billing refusals show their own sentence and are not budget stops', () => {
    const limit = new ApiError(402, 'plan_limit_reached', 'Your plan allows 5 seats and 5 are in use.');
    const readOnly = new ApiError(402, 'billing_read_only', 'This organisation is read-only.');
    const budget = new ApiError(402, 'model_budget_exceeded', 'Budget used up.');
    assert.equal(isBillingCode(limit), true);
    assert.equal(isBillingCode(readOnly), true);
    assert.equal(isBillingCode(budget), false);
    assert.equal(isBudgetExceeded(limit), false);
    assert.equal(isBudgetExceeded(budget), true);
    assert.equal(describeError(limit), 'Your plan allows 5 seats and 5 are in use.');
    assert.equal(describeError(new ApiError(402, 'billing_read_only', null)), t('billing.refusal.fallback'));
  });

  test('API paths carry search, status and paging', () => {
    assert.equal(invoicesPath('/api/v1/billing', { q: ' INV-7 ', status: 'open', limit: 20, offset: 40 }), '/api/v1/billing/invoices?limit=20&offset=40&q=INV-7&status=open');
    assert.equal(invoicesPath('/api/v1/billing'), '/api/v1/billing/invoices?limit=20&offset=0');
    assert.equal(subscriptionsPath({ q: 'acme', status: 'past_due' }), '/api/v1/platform/billing/subscriptions?limit=20&offset=0&q=acme&status=past_due');
    assert.equal(previewPath('plan 1'), '/api/v1/billing/subscription/preview?plan_id=plan%201');
    assert.equal(consoleOrgPath('acme/x'), '/api/v1/platform/billing/organizations/acme%2Fx');
    assert.equal(invoicesPath(consoleOrgPath('acme'), { status: 'paid' }), '/api/v1/platform/billing/organizations/acme/invoices?limit=20&offset=0&status=paid');
  });

  test('paging ranges', () => {
    assert.deepEqual(pageRange(45, 20, 20), { from: 21, to: 40, hasPrev: true, hasNext: true });
    assert.deepEqual(pageRange(45, 40, 5), { from: 41, to: 45, hasPrev: true, hasNext: false });
    assert.deepEqual(pageRange(0, 0, 0), { from: 0, to: 0, hasPrev: false, hasNext: false });
  });

  test('public routes and the plan in the hash', () => {
    assert.equal(publicRoute('#pricing'), 'pricing');
    assert.equal(publicRoute('#signup?plan=plan_pro'), 'signup');
    assert.equal(publicRoute('#settings'), null);
    assert.equal(planFromHash('#signup?plan=plan_pro'), 'plan_pro');
    assert.equal(planFromHash('#signup?plan=<x>'), null);
    assert.equal(planFromHash('#signup'), null);
  });

  test('sign-up form checks mirror the API', () => {
    const body = { organization_name: 'Riyadh Tyres', plan_id: 'plan_pro', country: 'SA', accept_terms: true };
    assert.deepEqual(signupProblems(body), []);
    assert.deepEqual(signupProblems({ ...body, organization_name: ' a ', accept_terms: false }), ['signup.problem.name', 'signup.problem.terms']);
    assert.deepEqual(signupProblems({ ...body, organization_name: 'See www.example.com' }), ['signup.problem.link']);
    assert.deepEqual(signupProblems({ ...body, plan_id: '', country: 'SAU' }), ['signup.problem.plan', 'signup.problem.country']);
    assert.deepEqual(signupProblems({ ...body, organization_name: 'شركة الإطارات' }), []);
  });

  test('proof files are PDF, PNG or JPEG up to 5 MB', () => {
    assert.equal(proofProblem({ size: 1000, type: 'application/pdf' }), null);
    assert.equal(proofProblem({ size: 1000, type: 'image/gif' }), 'billing.console.proofType');
    assert.equal(proofProblem({ size: 6 * 1024 * 1024, type: 'image/png' }), 'billing.console.proofSize');
    assert.equal(proofProblem({ size: 0, type: 'image/jpeg' }), 'billing.console.proofSize');
  });

  test('invoice tones and onboarding links', () => {
    assert.equal(invoiceTone({ status: 'paid', overdue: false }), 'success');
    assert.equal(invoiceTone({ status: 'open', overdue: true }), 'dangerTone');
    assert.equal(invoiceTone({ status: 'open', overdue: false }), 'warning');
    assert.equal(invoiceTone({ status: 'void', overdue: false }), 'neutral');
    assert.equal(ONBOARDING_LINKS.choose_plan.hash, '#billing-plans');
    assert.equal(ONBOARDING_LINKS.add_payment_details.hash, '#billing-contact');
  });

  test('both catalogs have the same keys, and messages read in Arabic', () => {
    assert.deepEqual(Object.keys(billingAr).sort(), Object.keys(billingEn).sort());
    setLocale('ar');
    assert.equal(t('settings.tab.billing'), 'الفوترة');
    assert.equal(t('billing.trialDays', { count: 14 }), 'تجربة مجانية لـ 14 يومًا');
    setLocale('en');
    assert.equal(t('billing.trialDays', { count: 14 }), '14 days free trial');
  });
});
