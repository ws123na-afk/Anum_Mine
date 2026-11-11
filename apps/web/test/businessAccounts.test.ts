// The Accounts tab of Business (src/lib/accounts.ts, docs/business.md#accounts-and-contacts): the list's order,
// the account and contact forms and the message catalogs. Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { BusinessContact } from '@anum/contracts';
import {
  ACCOUNT_SORTS, DEFAULT_ORDER, accountBody, accountDraftOf, accountPlace, ariaSort, contactBody, contactDraftOf, emptyAccountDraft, emptyContactDraft, nextOrder,
} from '../src/lib/accounts.ts';
import { placeholdersOf, setLocale } from '../src/lib/i18n.ts';
import { businessAccountsAr } from '../src/lib/messages/businessAccounts.ar.ts';
import { businessAccountsEn } from '../src/lib/messages/businessAccounts.en.ts';

const contact: BusinessContact = {
  id: 'bcont_1', tenant_id: 't', workspace_id: 'w', created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z', account_id: 'bacct_1', name: 'Sara Khan', role_title: 'Finance',
  email: 'sara@acme.example', phone: null, profile_url: null, consent_basis: 'existing_relationship', consent_note: 'Met at the fair', consent_recorded_at: null, do_not_contact: false,
  dnc_reason: '', source: 'manual', created_by: 'u', opted_out: [], block_reason: null, contactable: true,
};

describe('account list order', () => {
  test('a header click sorts that column ascending, a second click turns it round, dates start newest first', () => {
    assert.deepEqual(DEFAULT_ORDER, { sort: 'name', desc: false });
    assert.deepEqual(nextOrder(DEFAULT_ORDER, 'name'), { sort: 'name', desc: true });
    assert.deepEqual(nextOrder({ sort: 'name', desc: true }, 'place'), { sort: 'place', desc: false });
    assert.deepEqual(nextOrder(DEFAULT_ORDER, 'updated'), { sort: 'updated', desc: true });
    assert.equal(ariaSort(DEFAULT_ORDER, 'name'), 'ascending');
    assert.equal(ariaSort({ sort: 'place', desc: true }, 'place'), 'descending');
    assert.equal(ariaSort(DEFAULT_ORDER, 'place'), 'none');
    assert.deepEqual([...ACCOUNT_SORTS], ['name', 'place', 'industry', 'updated']);
  });
});

describe('account form', () => {
  test('needs a name of two characters and a website that looks like one; the rest is trimmed', () => {
    setLocale('en');
    assert.deepEqual(accountBody(emptyAccountDraft()), { ok: false, error: 'Enter the company name.' });
    assert.deepEqual(accountBody({ ...emptyAccountDraft(), name: 'Acme', website: 'not a site' }), { ok: false, error: 'Enter the website as an address such as example.com.' });
    assert.deepEqual(accountBody({ name: ' Acme Trading ', website: 'acme.example', industry: ' Retail ', country: 'SA ', city: ' Riyadh', notes: ' Pays late ' }),
      { ok: true, value: { name: 'Acme Trading', website: 'acme.example', industry: 'Retail', country: 'SA', city: 'Riyadh', notes: 'Pays late' } });
    const body = accountBody({ ...emptyAccountDraft(), name: 'Acme' });
    assert.ok(body.ok && body.value.website === null);
  });

  test('an edit starts from the stored account', () => {
    assert.deepEqual(accountDraftOf({ id: 'a', tenant_id: 't', workspace_id: 'w', created_at: '', updated_at: '', name: 'Acme', name_key: 'acme', website: null, domain: null, industry: 'Retail', country: 'SA', city: 'Riyadh', notes: '', source: 'manual', owner_id: 'u', do_not_contact: false, dnc_reason: '', created_by: 'u' }),
      { name: 'Acme', website: '', industry: 'Retail', country: 'SA', city: 'Riyadh', notes: '' });
    assert.equal(accountPlace({ city: 'Riyadh', country: 'SA' }), 'Riyadh, SA');
  });
});

describe('contact form', () => {
  test('needs a name; e-mail, phone and profile are checked only when given', () => {
    setLocale('en');
    assert.deepEqual(contactBody(emptyContactDraft()), { ok: false, error: "Enter the contact's name." });
    assert.deepEqual(contactBody({ ...emptyContactDraft(), name: 'Sara', email: 'sara@' }), { ok: false, error: 'Enter a valid e-mail address, or leave it empty.' });
    assert.deepEqual(contactBody({ ...emptyContactDraft(), name: 'Sara', phone: '12' }), { ok: false, error: 'Enter the phone number with its country code, or leave it empty.' });
    assert.equal(contactBody({ ...emptyContactDraft(), name: 'Sara', profileUrl: 'nope' }).ok, false);
    assert.deepEqual(contactBody({ name: ' Sara Khan ', roleTitle: 'CFO ', email: 'sara@acme.example', phone: '+966 50 123 4567', profileUrl: '', consent: 'inbound_inquiry', consentNote: ' Asked on 1 Nov ' }),
      { ok: true, value: { name: 'Sara Khan', role_title: 'CFO', email: 'sara@acme.example', phone: '+966 50 123 4567', profile_url: null, consent_basis: 'inbound_inquiry', consent_note: 'Asked on 1 Nov' } });
  });

  test('an edit starts from the stored contact', () => {
    assert.deepEqual(contactDraftOf(contact), { name: 'Sara Khan', roleTitle: 'Finance', email: 'sara@acme.example', phone: '', profileUrl: '', consent: 'existing_relationship', consentNote: 'Met at the fair' });
  });
});

describe('messages', () => {
  test('every English key has an Arabic twin with the same placeholders, and no long dash', () => {
    assert.deepEqual(Object.keys(businessAccountsAr).sort(), Object.keys(businessAccountsEn).sort());
    for (const [key, value] of Object.entries(businessAccountsEn)) {
      assert.deepEqual(placeholdersOf(businessAccountsAr[key as keyof typeof businessAccountsEn]), placeholdersOf(value), key);
      assert.doesNotMatch(JSON.stringify(value) + JSON.stringify(businessAccountsAr[key as keyof typeof businessAccountsEn]), /[–—]/, key);
    }
  });
});
