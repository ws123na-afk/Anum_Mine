// Unit tests for the Business helpers (src/lib/business.ts, docs/business.md).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  NO_CATALOG_FILTERS, catalogFiltered, catalogQuery, emptyLeadDraft, emptyOfferDraft, formatMoney, leadBody, linesOf, manualTransitions, needsReason, offerBody, parseAmount, proposalTotals, scoreText,
  stateLabel, stateTone, tabFromHash,
} from '../src/lib/business.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { viewFromHash } from '../src/lib/shell.ts';

describe('business', () => {
  test('catalog search and paging send only the filters in use', () => {
    assert.deepEqual(catalogQuery(NO_CATALOG_FILTERS, 0), { limit: 25, offset: 0 });
    assert.deepEqual(catalogQuery({ q: '  dash ', kind: 'product', maturity: 'live' }, 25), { q: 'dash', kind: 'product', maturity: 'live', limit: 25, offset: 25 });
    assert.deepEqual(catalogQuery({ q: '   ', kind: '', maturity: 'idea' }, -5, 10), { maturity: 'idea', limit: 10, offset: 0 });
    assert.equal(catalogFiltered(NO_CATALOG_FILTERS), false);
    assert.equal(catalogFiltered({ q: ' ', kind: '', maturity: '' }), false);
    assert.equal(catalogFiltered({ q: 'x', kind: '', maturity: '' }), true);
    assert.equal(catalogFiltered({ q: '', kind: 'service_package', maturity: '' }), true);
  });

  test('hashes open the right tab and record', () => {
    assert.equal(tabFromHash('#business'), 'overview');
    assert.equal(tabFromHash('#business-leads'), 'leads');
    assert.equal(tabFromHash('#business-proposals/bprop_1'), 'proposals');
    assert.equal(tabFromHash('#business-nope'), null);
    assert.equal(tabFromHash('#marketing'), null);
    assert.equal(viewFromHash('#business-leads/blead_1'), 'business');
    assert.equal(viewFromHash('#business'), 'business');
  });

  test('the score shows the total and how many criteria are known; unknown is not zero', () => {
    setLocale('en');
    assert.deepEqual(scoreText({ score_total: 7, score_known: 2 }), { total: '7/30', known: '2 of 6 known' });
    setLocale('ar');
    assert.equal(scoreText({ score_total: 7, score_known: 2 }).known, '2 من 6 معروفة');
    setLocale('en');
  });

  test('states: labels, tones, reasons and the transition ANUM makes itself', () => {
    assert.equal(stateLabel('suppressed'), 'Suppressed');
    assert.equal(stateTone('won'), 'success');
    assert.equal(stateTone('lost'), 'dangerTone');
    assert.equal(needsReason('lost') && needsReason('suppressed') && !needsReason('qualified'), true);
    assert.deepEqual(manualTransitions(['won', 'lost', 'suppressed']), ['lost', 'suppressed']);
  });

  test('a new lead needs a company; contact details are checked and optional', () => {
    assert.equal(leadBody(emptyLeadDraft()).ok, false);
    assert.equal(leadBody({ ...emptyLeadDraft(), company: 'Gulf Mixers', website: 'not a site' }).ok, false);
    assert.equal(leadBody({ ...emptyLeadDraft(), company: 'Gulf Mixers', email: 'ops@' }).ok, false);
    const plain = leadBody({ ...emptyLeadDraft(), company: ' Gulf Mixers ', website: 'gulfmixers.example' });
    assert.ok(plain.ok);
    assert.deepEqual(plain.value, { title: 'Gulf Mixers', account: { name: 'Gulf Mixers', website: 'gulfmixers.example', industry: '', city: '', country: '' }, contact: null, product_id: null });
    const withContact = leadBody({ ...emptyLeadDraft(), company: 'Gulf Mixers', contactName: 'Sara', email: 'ops@gulfmixers.example', consent: 'inbound_inquiry', productId: 'bprod_1' });
    assert.ok(withContact.ok);
    assert.deepEqual(withContact.value.contact, { name: 'Sara', email: 'ops@gulfmixers.example', phone: null, consent_basis: 'inbound_inquiry' });
  });

  test('a priced version needs the owner price; costs are amounts', () => {
    assert.equal(offerBody({ ...emptyOfferDraft(), priceMin: '' }).ok, false);
    assert.equal(offerBody({ ...emptyOfferDraft(), priceType: 'range', priceMin: '2000', priceMax: '1000' }).ok, false);
    assert.equal(offerBody({ ...emptyOfferDraft(), priceMin: '1500', contingency: '150' }).ok, false);
    const quote = offerBody({ ...emptyOfferDraft(), priceType: 'quote' });
    assert.ok(quote.ok);
    assert.equal(quote.value.price_min, null);
    const fixed = offerBody({ ...emptyOfferDraft(), priceMin: '2,500', deliverables: 'Dashboard\n\nDashboard\nExport', laborHours: '12', hourlyCost: '80.5' });
    assert.ok(fixed.ok);
    assert.deepEqual([fixed.value.price_min, fixed.value.deliverables, (fixed.value.cost as { hourly_cost: number }).hourly_cost], [2500, ['Dashboard', 'Export'], 80.5]);
    assert.equal(parseAmount('0.1'), 0.1);
    assert.equal(parseAmount('1.005'), null);
    assert.equal(parseAmount('-1'), null);
  });

  test('the proposal preview reconciles in cents like the API', () => {
    const totals = proposalTotals({ line_items: [{ description: 'Build', kind: 'service', quantity: 1.5, unit_price: 1333.35 }], milestones: [{ title: 'All', amount: 2100.03, due_in_days: null, acceptance: '' }], tax_rate_pct: 5, internal_cost: 500 });
    assert.deepEqual([totals.subtotal, totals.tax, totals.total, totals.margin, totals.milestonesMatch], [2000.03, 100, 2100.03, 1500.03, true]);
    const off = proposalTotals({ line_items: [{ description: 'a', kind: 'service', quantity: 1, unit_price: 0.1 }, { description: 'b', kind: 'service', quantity: 1, unit_price: 0.2 }], milestones: [{ title: 'x', amount: 0.3, due_in_days: null, acceptance: '' }], tax_rate_pct: 0, internal_cost: 0 });
    assert.equal(off.total, 0.3);
    assert.equal(off.milestonesMatch, true);
    assert.deepEqual(linesOf(' a \n\n b\na '), ['a', 'b']);
  });

  test('money is formatted per currency with Western digits in both languages', () => {
    setLocale('en');
    assert.match(formatMoney(2500, 'SAR'), /^SAR\s2,500\.00$/u);
    assert.equal(formatMoney(null, 'SAR'), 'Unknown');
    setLocale('ar');
    assert.match(formatMoney(2500, 'AED'), /2,500\.00/);
    setLocale('en');
  });
});
