// A loan's person from Contacts (docs/finance.md#people-from-contacts): which name shows, what is said under it,
// and when "Add to Contacts" is offered. Every name here is SYNTHETIC.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { contactNote, hasExactName, loanName, personHint } from '../src/lib/financeLoanContacts.ts';

test('the current Contacts name shows when the caller can read it, else the typed one', () => {
  assert.equal(loanName({ person: 'Sara', contact_id: 'c1', contact_name: 'Sara Ahmed', contact_state: 'linked' }), 'Sara Ahmed');
  assert.equal(loanName({ person: 'Sara', contact_id: 'c1', contact_name: null, contact_state: 'deleted' }), 'Sara');
  assert.equal(loanName({ person: 'Sara', contact_id: 'c1', contact_name: null, contact_state: 'hidden' }), 'Sara');
  assert.equal(loanName({ person: 'Cousin Omar' }), 'Cousin Omar');
});

test('the typed name is kept as history after a rename; a deleted or hidden person says so', () => {
  assert.deepEqual(contactNote({ person: 'Sara', contact_id: 'c1', contact_name: 'Sara Al Mansoori', contact_state: 'linked' }), { kind: 'typed', name: 'Sara' });
  assert.equal(contactNote({ person: 'sara  ahmed', contact_id: 'c1', contact_name: 'Sara Ahmed', contact_state: 'linked' }), null);
  assert.deepEqual(contactNote({ person: 'Sara', contact_id: 'c1', contact_name: null, contact_state: 'deleted' }), { kind: 'deleted' });
  assert.deepEqual(contactNote({ person: 'Sara', contact_id: 'c1', contact_name: null, contact_state: 'hidden' }), { kind: 'hidden' });
  assert.equal(contactNote({ person: 'Cousin Omar', contact_id: null }), null);
});

test('Add to Contacts is offered only when nobody found has exactly that name', () => {
  const found = [{ name: 'Sara Ahmed' }, { name: 'Sara Ali' }];
  assert.equal(hasExactName(found, ' sara   ahmed '), true);
  assert.equal(hasExactName(found, 'Sara'), false);
  assert.equal(hasExactName(found, '  '), false);
  assert.equal(personHint({ organisation: ' Tyre Co ' }), 'Tyre Co');
  assert.equal(personHint({ organisation: '' }), null);
});
