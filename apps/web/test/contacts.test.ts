// Unit tests for the Contacts directory helpers (src/lib/contacts.ts, docs/contacts.md#web).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  blockedChannels, channelLabel, contactsHash, draftOf, emptyPersonDraft, exportName, factText, linkKindLabel, looksLikeEmail, looksLikePhone,
  parseContactsHash, personBody, reasonText, sourceLabel, splitValues, type PersonView,
} from '../src/lib/contacts.ts';
import { viewFromHash } from '../src/lib/shell.ts';
import { setLocale } from '../src/lib/i18n.ts';

const person: PersonView = {
  id: 'ct_1', name: 'Sara Ahmed', organisation: 'Tyre Co', notes: 'x', source: 'calls', sources: ['call_contact'], open_proposals: 0,
  phones: [{ kind: 'phone', value: '+971501234567', label: '050 123 4567' }], emails: [{ kind: 'email', value: 'sara@tyre.example', label: '' }],
  do_not_contact: [{ channel: 'email', reason: 'Asked', set_by: 'u', set_at: '2026-11-10T09:30:00Z' }], created_by: 'u', created_at: '2026-11-10T09:30:00Z', updated_at: '2026-11-10T09:30:00Z',
};

describe('contacts directory', () => {
  test('hashes name the tab and the open person, and the shell routes them', () => {
    assert.deepEqual(parseContactsHash('#contacts'), { tab: 'people', person: null });
    assert.deepEqual(parseContactsHash('#contacts-duplicates'), { tab: 'duplicates', person: null });
    assert.deepEqual(parseContactsHash('#contacts?person=ct_1'), { tab: 'people', person: 'ct_1' });
    assert.equal(contactsHash('people'), '#contacts');
    assert.equal(contactsHash('duplicates', 'a b'), '#contacts-duplicates?person=a%20b');
    for (const hash of ['#contacts', '#contacts-duplicates', '#contacts?person=ct_1']) assert.equal(viewFromHash(hash), 'contacts');
  });

  test('the person form checks what the API would refuse', () => {
    assert.deepEqual(splitValues(' +971501234567, 050 123 4567\n\n'), ['+971501234567', '050 123 4567']);
    assert.equal(looksLikePhone('050 123 4567'), true);
    assert.equal(looksLikePhone('12'), false);
    assert.equal(looksLikeEmail('Sara@Tyre.example'), true);
    assert.equal(looksLikeEmail('nope'), false);
    setLocale('en');
    assert.equal(personBody(emptyPersonDraft()).ok, false);
    const bad = personBody({ ...emptyPersonDraft(), name: 'Sara', phones: 'nope' });
    assert.equal(bad.ok, false);
    if (!bad.ok) assert.match(bad.error, /"nope" is not a phone number/);
    assert.equal(personBody({ ...emptyPersonDraft(), name: 'Sara', emails: 'x@y' }).ok, false);
    assert.deepEqual(personBody({ name: '  Sara   Ahmed ', organisation: ' Tyre Co ', notes: ' n ', phones: '+971501234567', emails: 'A@B.example' }),
      { ok: true, value: { name: 'Sara Ahmed', organisation: 'Tyre Co', notes: 'n', phones: ['+971501234567'], emails: ['A@B.example'] } });
    assert.equal(personBody({ ...emptyPersonDraft(), name: 'x', phones: Array.from({ length: 11 }, (_, i) => `+9715000000${i}`).join('\n') }).ok, false);
    assert.deepEqual(draftOf(person), { name: 'Sara Ahmed', organisation: 'Tyre Co', notes: 'x', phones: '050 123 4567', emails: 'sara@tyre.example' });
    assert.equal(reasonText('ab').ok, false);
    assert.deepEqual(reasonText(' Asked by phone '), { ok: true, value: 'Asked by phone' });
  });

  test('words in both languages', () => {
    setLocale('en');
    assert.equal(sourceLabel('calls'), 'Phone calls');
    assert.equal(linkKindLabel('whatsapp_contact'), 'WhatsApp contact');
    assert.equal(channelLabel('all'), 'All channels');
    assert.equal(factText({ source: 'business', state: 'granted', detail: 'contract', at: null }), 'Business: consent basis contract');
    assert.equal(factText({ source: 'calls', state: 'unknown', detail: '', at: null }), 'calls: unknown');
    assert.deepEqual(blockedChannels(person.do_not_contact), ['email']);
    assert.deepEqual(blockedChannels([{ channel: 'all', reason: 'x', set_by: 'u', set_at: '' }]), ['phone', 'email', 'whatsapp']);
    assert.equal(exportName('attachment; filename="contacts.csv"'), 'contacts.csv');
    assert.equal(exportName('attachment; filename="../x y"'), 'contacts.csv');
    setLocale('ar');
    assert.equal(sourceLabel('whatsapp'), 'واتساب');
    assert.equal(channelLabel('email'), 'البريد الإلكتروني');
    setLocale('en');
  });
});
