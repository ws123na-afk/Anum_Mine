// Ready to sell (docs/selling-readiness.md): legal facts filling the documents, the checklist helpers,
// refunds and the console wiring.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  LEGAL_DOCS, fillPlaceholders, legalState, placeholderKey, publishedIds, readLegalDocument, remainingPlaceholders,
} from '../src/lib/publicSite.ts';
import {
  fixTarget, missingCompany, missingKeys, missingTotal, newIdempotencyKey, refundableOf, sortReadiness, validDomain, validEmail,
  type ReadinessItem,
} from '../src/lib/selling.ts';
import { billingAr } from '../src/lib/messages/billing.ar.ts';
import { billingEn } from '../src/lib/messages/billing.en.ts';

const repo = new URL('../../../', import.meta.url);
const read = (path: string) => readFileSync(new URL(path, repo), 'utf8');

test('legal facts fill placeholders and what is left keeps a published document a draft', () => {
  const doc = readLegalDocument('# T\n\n> **DRAFT.** Every `{{PLACEHOLDER}}` must go.\n\nLast updated: {{EFFECTIVE_DATE}}\n\n## A\n\n{{LEGAL_ENTITY_NAME}} at {{REGISTERED_ADDRESS}}.\n');
  // The DRAFT notice's own example is not a fact.
  assert.deepEqual(doc.placeholders, ['{{EFFECTIVE_DATE}}', '{{LEGAL_ENTITY_NAME}}', '{{REGISTERED_ADDRESS}}']);
  const values = { LEGAL_ENTITY_NAME: 'Example LLC', REGISTERED_ADDRESS: null, EFFECTIVE_DATE: '1 Dec 2026' };
  assert.deepEqual(remainingPlaceholders(doc, values), ['{{REGISTERED_ADDRESS}}']);
  assert.equal(legalState('terms', doc, new Set(['terms']), values), 'blocked');
  assert.equal(legalState('terms', doc, new Set(['terms']), { ...values, REGISTERED_ADDRESS: 'Riyadh' }), 'published');
  assert.equal(legalState('terms', doc, new Set(), { ...values, REGISTERED_ADDRESS: 'Riyadh' }), 'draft');
  assert.deepEqual(fillPlaceholders('By {{LEGAL_ENTITY_NAME}} at {{REGISTERED_ADDRESS}}', values), [
    { text: 'By ', kind: 'text' }, { text: 'Example LLC', kind: 'value', key: 'LEGAL_ENTITY_NAME' }, { text: ' at ', kind: 'text' },
    { text: '{{REGISTERED_ADDRESS}}', kind: 'missing', key: 'REGISTERED_ADDRESS' },
  ]);
  assert.equal(placeholderKey('{{A_B}}'), 'A_B');
  assert.deepEqual(fillPlaceholders('{{X}}', null), [{ text: '{{X}}', kind: 'missing', key: 'X' }]);
});

test('published ids combine the build flag and the owner\'s choice, ignoring unknown ids', () => {
  assert.deepEqual([...publishedIds(new Set(['terms']), ['privacy', 'nope'])].sort(), ['privacy', 'terms']);
  assert.deepEqual([...publishedIds(new Set(), null)], []);
});

test('every legal document still has facts only the owner can give', () => {
  for (const d of LEGAL_DOCS) {
    const doc = readLegalDocument(read(`docs/legal/${d.file}`));
    assert.ok(doc.placeholders.length > 0, d.file);
    assert.ok(!doc.placeholders.includes('{{PLACEHOLDER}}'), `${d.file}: the DRAFT notice example is not a fact`);
  }
  const privacy = read('docs/legal/privacy-policy.md');
  for (const needle of ['Personal data breaches', 'SDAIA', 'Data export and deletion', 'How long records are kept', 'Moyasar or Stripe']) {
    assert.ok(privacy.includes(needle), needle);
  }
  assert.ok(read('docs/legal/subprocessors.md').includes('{{IN_USE_MOYASAR}}'));
  assert.ok(!read('docs/legal/data-processing-addendum.md').includes('does not yet offer a self-service deletion'));
});

test('checklist helpers: order, targets, validation', () => {
  const item = (key: ReadinessItem['key'], state: ReadinessItem['state']): ReadinessItem => ({ key, state, fix: 'company', detail: {} });
  assert.deepEqual(sortReadiness([item('plans', 'done'), item('legal_pages', 'warning'), item('company_details', 'missing'), item('backups_local', 'unknown')]).map((i) => i.state),
    ['missing', 'warning', 'unknown', 'done']);
  assert.deepEqual(fixTarget('payments'), { tab: 'settings' });
  assert.deepEqual(fixTarget('legal'), { tab: 'company' });
  assert.deepEqual(fixTarget('mail'), { href: '#email' });
  assert.deepEqual(fixTarget('backups'), {});
  assert.ok(validDomain('anum360os.com') && validDomain('Mail.Example.co.') && !validDomain('localhost') && !validDomain('a b.com') && !validDomain('127.0.0.1'));
  assert.ok(validEmail('privacy@example.com') && !validEmail('a@b') && !validEmail('a@@b.c') && !validEmail('a b@c.d') && !validEmail(''));
  assert.deepEqual(missingCompany({ legal_name: 'X', address: ' ' }, ['legal_name', 'address', 'dpo_email']), ['address', 'dpo_email']);
  assert.deepEqual(missingKeys(['A', 'B'], { A: 'x', B: null }), ['B']);
  assert.equal(missingTotal({ terms: ['A'], privacy: ['B', 'C'] }), 3);
});

test('refund helpers never offer more than the API allows and use a fresh key', () => {
  assert.equal(refundableOf({ amount_minor: 500, refundable_minor: 200 }), 200);
  assert.equal(refundableOf({ amount_minor: 500 }), 500);
  assert.equal(refundableOf({ amount_minor: 500, refundable_minor: -1 }), 0);
  assert.equal(newIdempotencyKey(() => 'abc'), 'refund-abc');
  assert.match(newIdempotencyKey(), /^refund-[0-9a-f-]{36}$/);
});

test('every new message exists in English and Arabic with the same placeholders', () => {
  const keys = Object.keys(billingEn).filter((k) => k.startsWith('selling.') || k.startsWith('billing.moyasar.') || k.startsWith('billing.refund.') || k.startsWith('billing.receipt.'));
  assert.ok(keys.length > 100);
  const names = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
  for (const key of keys) {
    const ar = (billingAr as Record<string, string>)[key];
    assert.ok(ar, key);
    assert.deepEqual(names(ar), names((billingEn as Record<string, string>)[key]), key);
  }
});

test('the console has the Ready to sell and Company tabs and the refund dialog keeps one key per attempt', () => {
  const consoleSource = read('apps/web/src/billing/BillingConsole.tsx');
  assert.match(consoleSource, /'company', 'ready'\]/);
  const org = read('apps/web/src/billing/ConsoleOrganisation.tsx');
  assert.match(org, /const \[key\] = useState\(\(\) => newIdempotencyKey\(\)\)/);
  const readiness = read('apps/web/src/billing/ConsoleReadiness.tsx');
  assert.ok(readiness.includes('checkDns(domain)'));
  // The public pages load only the small public client, never the billing client.
  const site = read('apps/web/src/PublicSite.tsx');
  assert.ok(site.includes("from './lib/publicLegalApi'") && !site.includes('billingApi') && !site.includes('sellingApi'));
});
