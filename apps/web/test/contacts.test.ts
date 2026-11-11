// Unit tests for the Contacts directory helpers (src/lib/contacts.ts, docs/contacts.md#web).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  blockedChannels, channelLabel, contactsHash, draftOf, emptyPersonDraft, exportName, factText, linkKindLabel, looksLikeEmail, looksLikePhone,
  parseContactsHash, personBody, reasonText, sourceLabel, splitValues, type PersonView,
  importDecisions, importFileProblem, importIssues, importRowsShown, importStatusLabel, importTotals, type ImportRow,
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
    assert.deepEqual(parseContactsHash('#contacts-import'), { tab: 'import', person: null });
    assert.equal(contactsHash('import'), '#contacts-import');
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

describe('import from a CSV file', () => {
  const row = (n: number, extra: Partial<ImportRow> = {}): ImportRow => ({
    row: n, name: `Person ${n}`, organisation: '', phones: [`+97150000000${n}`], emails: [], do_not_contact: [], status: 'new', action: 'create',
    actions: ['create', 'skip'], target_id: null, matches: [], duplicate_of: null, problems: [], ...extra,
  });
  const rows = [
    row(2),
    row(3, { status: 'match', action: 'update', actions: ['update', 'create', 'skip'], target_id: 'ct_a', matches: [{ id: 'ct_a', name: 'A', shared: ['+971500000003'], do_not_contact: ['phone'] }] }),
    row(4, { status: 'invalid', action: 'skip', actions: ['skip'], problems: ['bad'] }),
    row(5, { status: 'possible_duplicate', actions: ['create', 'update', 'skip'], matches: [{ id: 'ct_b', name: 'B', shared: [], do_not_contact: [] }, { id: 'ct_c', name: 'C', shared: [], do_not_contact: [] }] }),
  ];

  test('every row is sent with the action the owner left, and a choice outside its actions is ignored', () => {
    const decisions = importDecisions(rows, { 2: { action: 'skip', target_id: null }, 4: { action: 'create', target_id: null }, 5: { action: 'update', target_id: 'ct_c' } });
    assert.deepEqual(decisions, [
      { row: 2, action: 'skip', target_id: null },
      { row: 3, action: 'update', target_id: 'ct_a' },
      { row: 4, action: 'skip', target_id: null },
      { row: 5, action: 'update', target_id: 'ct_c' },
    ]);
    assert.deepEqual(importTotals(decisions), { create: 0, update: 2, skip: 2 });
    // Update without a chosen person uses the first match.
    assert.equal(importDecisions(rows, { 5: { action: 'update', target_id: null } })[3].target_id, 'ct_b');
  });

  test('rows are filtered by status and found by name, number or row number', () => {
    assert.deepEqual(importRowsShown(rows, 'invalid', '').map((r) => r.row), [4]);
    assert.deepEqual(importRowsShown(rows, '', 'person 5').map((r) => r.row), [5]);
    assert.deepEqual(importRowsShown(rows, '', '+971500000002').map((r) => r.row), [2]);
    assert.deepEqual(importRowsShown(rows, '', '3').map((r) => r.row), [3]);
    assert.equal(importStatusLabel('duplicate_in_file'), 'Repeated in the file');
  });

  test('a row problem is worded from its code, with the API text as the fallback', () => {
    assert.deepEqual(importIssues(row(6, { problems: ["'12' is not a phone"], issues: [{ code: 'not_phone', value: '12' }] })), ['"12" is not a phone number with its country code (+971...).']);
    assert.deepEqual(importIssues(row(7, { problems: ['Old server text'] })), ['Old server text']);
  });

  test('a file is checked before it is read', () => {
    assert.equal(importFileProblem({ name: 'people.csv', size: 10, type: 'text/csv' }), null);
    assert.match(importFileProblem({ name: 'people.xlsx', size: 10, type: 'application/vnd.ms-excel' }) ?? '', /Choose a CSV file/);
    assert.match(importFileProblem({ name: 'people.csv', size: 0, type: 'text/csv' }) ?? '', /empty/);
    assert.match(importFileProblem({ name: 'people.csv', size: 9_000_000, type: 'text/csv' }) ?? '', /too large/);
  });
});
