// The Arabic legal pages (docs/legal-pages.md): every document in docs/legal has an Arabic text in
// docs/legal/ar with the same placeholders, the same DRAFT rule and the same links, and the values the
// server generates in English read in Arabic.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  LEGAL_DOCS, arabicFile, legalLanguage, legalState, localizeLegalValues, placeholdersIn, readLegalDocument, rewriteDocLinks,
} from '../src/lib/publicSite.ts';
import { setLocale, t } from '../src/lib/i18n.ts';

const repo = new URL('../../../', import.meta.url);
const read = (path: string) => readFileSync(new URL(path, repo), 'utf8');

test('each document has an Arabic text with exactly the same placeholders', () => {
  for (const d of LEGAL_DOCS) {
    const english = read(`docs/legal/${d.file}`), arabic = read(`docs/legal/${arabicFile(d.file)}`);
    assert.deepEqual(placeholdersIn(arabic).sort(), placeholdersIn(english).sort(), d.file);
    const doc = readLegalDocument(arabic);
    assert.match(doc.title, /[؀-ۿ]/, `${d.file}: Arabic title`);
    assert.equal(doc.updated, '{{EFFECTIVE_DATE}}', `${d.file}: "آخر تحديث" is read as the date line`);
    // The lawyer rule stays: the source starts with the DRAFT notice, which the page replaces with its own banner.
    assert.match(arabic, /^# .+\n\n> \*\*DRAFT \/ مسودة: يجب أن يراجعها المالك ومحامٍ قبل النشر\.\*\*/, d.file);
    assert.ok(!doc.body.includes('**DRAFT'), `${d.file}: the notice is not part of the body`);
    assert.equal(legalState(d.id, doc, new Set([d.id]), {}), 'blocked', `${d.file}: published with facts missing stays a draft`);
    // Links between the documents go to the same routes as the English ones.
    const routes = (text: string) => [...rewriteDocLinks(text).matchAll(/https:\/\/anum-public\.invalid(\/[a-z/]+)/g)].map((m) => m[1]).sort();
    assert.deepEqual([...new Set(routes(arabic))], [...new Set(routes(english))], `${d.file}: links`);
  }
});

test('the page language follows the site language', () => {
  assert.equal(legalLanguage('ar'), 'ar');
  assert.equal(legalLanguage('en'), 'en');
  assert.equal(legalLanguage('fr'), 'en');
  setLocale('ar');
  assert.match(t('public.legal.translationNote'), /محامٍ/);
  setLocale('en');
  assert.match(t('public.legal.translationNote'), /lawyer approves/);
});

test('generated English values read in Arabic; what the owner typed stays as typed', () => {
  const values = {
    IN_USE_MOYASAR: 'Yes', IN_USE_STRIPE: 'No', STRIPE_REGION: 'Not used', IN_USE_TWILIO: 'Only when a workspace connects its own account',
    B2_REGION: 'United States (us-east-005)', LICENSING_AUTHORITY: 'Ministry of Commerce, Saudi Arabia', LEGAL_ENTITY_NAME: 'Shahzeb Business Group LLC',
    ORDER_FORM_OR_PLAN_PAGE: 'the plan page at https://app.example/#pricing or the order form we agree with you', MINIMUM_AGE: null,
  };
  const arabic = localizeLegalValues(values, 'ar')!;
  assert.equal(arabic.IN_USE_MOYASAR, 'نعم');
  assert.equal(arabic.IN_USE_STRIPE, 'لا');
  assert.equal(arabic.STRIPE_REGION, 'غير مستخدم');
  assert.equal(arabic.B2_REGION, 'الولايات المتحدة (us-east-005)');
  assert.equal(arabic.LICENSING_AUTHORITY, 'Ministry of Commerce, المملكة العربية السعودية');
  assert.equal(arabic.LEGAL_ENTITY_NAME, 'Shahzeb Business Group LLC');
  assert.equal(arabic.ORDER_FORM_OR_PLAN_PAGE, 'صفحة الخطط على https://app.example/#pricing أو نموذج الطلب الذي نتفق عليه معكم');
  assert.equal(arabic.MINIMUM_AGE, null);
  assert.equal(localizeLegalValues(values, 'en'), values);
  assert.equal(localizeLegalValues(null, 'ar'), null);
});
