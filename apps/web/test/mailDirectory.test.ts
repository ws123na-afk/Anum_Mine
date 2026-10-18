// Unit tests for the Email Center phase 3 part A helpers (src/lib/mailDirectory.ts, docs/email.md#email-center).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  accessOf, appendContacts, canSendFrom, contactInput, contactLabel, contactProblem, insertRecipient, ownsMailbox, personName, pickAccount,
  recipientToken, senderLabel, suggestionsFor, typedAddresses, EMPTY_CONTACT, type MailContact,
} from '../src/lib/mailDirectory.ts';

const contact = (id: string, name: string, email: string): MailContact => ({ id, name, email, company: '', phone: '', notes: '', created_at: '', updated_at: '' });

describe('mailbox access', () => {
  test('an answer without access is the caller\'s own mailbox', () => {
    assert.equal(accessOf({}), 'owner');
    assert.equal(ownsMailbox({}), true);
    assert.equal(canSendFrom({ access: 'send' }), true);
    assert.equal(canSendFrom({ access: 'read' }), false);
    assert.equal(ownsMailbox({ access: 'send' }), false);
  });
  test('the remembered mailbox opens while it is still there, else the first', () => {
    const accounts = [{ id: 'a' }, { id: 'b' }];
    assert.equal(pickAccount(accounts, 'b')?.id, 'b');
    assert.equal(pickAccount(accounts, 'gone')?.id, 'a');
    assert.equal(pickAccount(accounts, null)?.id, 'a');
    assert.equal(pickAccount([], 'a'), null);
  });
});

describe('labels', () => {
  test('name and address, or the address alone', () => {
    assert.equal(senderLabel('Sales', 'sales@example.com'), 'Sales <sales@example.com>');
    assert.equal(senderLabel('  ', 'sales@example.com'), 'sales@example.com');
    assert.equal(senderLabel(null, 'sales@example.com'), 'sales@example.com');
    assert.equal(contactLabel(contact('c1', '', 'x@example.com')), 'x@example.com');
    assert.equal(personName({ email: null, grantee_email: null, grantee_user_id: 'user_x' }), 'user_x');
    assert.equal(personName({ email: 'a@example.com', user_id: 'user_a' }), 'a@example.com');
  });
});

describe('recipient suggestions', () => {
  test('the token at the caret is the address being typed', () => {
    const text = 'sara@example.com, Om';
    assert.deepEqual(recipientToken(text, text.length), { start: 17, end: 20, query: 'Om' });
    assert.deepEqual(recipientToken('Om, sara@example.com', 2), { start: 0, end: 2, query: 'Om' });
    assert.equal(recipientToken('a; b\nc', 6).query, 'c');
  });
  test('picking replaces the token and leaves the caret after a comma', () => {
    const text = 'sara@example.com, Om';
    const done = insertRecipient(text, recipientToken(text, text.length), 'omar@example.com');
    assert.equal(done.text, 'sara@example.com, omar@example.com, ');
    assert.equal(done.caret, done.text.length);
    const middle = insertRecipient('Om, sara@example.com', recipientToken('Om, sara@example.com', 2), 'omar@example.com');
    assert.equal(middle.text, 'omar@example.com, sara@example.com');
  });
  test('addresses already in the field are not suggested again', () => {
    const typed = typedAddresses('Sara <SARA@example.com>, omar@example.com; half');
    assert.deepEqual([...typed].sort(), ['omar@example.com', 'sara@example.com']);
    const list = [contact('1', 'Sara', 'sara@example.com'), contact('2', 'Sam', 'sam@example.com'), contact('3', 'Sid', 'sid@example.com')];
    assert.deepEqual(suggestionsFor(list, typed, 1).map((c) => c.id), ['2']);
  });
});

describe('contacts', () => {
  test('the form is checked before it goes to the API', () => {
    assert.equal(contactProblem({ ...EMPTY_CONTACT, email: 'not an address' }), 'email');
    assert.equal(contactProblem({ ...EMPTY_CONTACT, email: 'a@example.com', phone: 'call me' }), 'phone');
    assert.equal(contactProblem({ ...EMPTY_CONTACT, email: ' a@example.com ', phone: '+971 (50) 000-0000' }), null);
  });
  test('editing starts from the stored values; pages never repeat a contact', () => {
    const sara = contact('1', 'Sara', 'sara@example.com');
    assert.deepEqual(contactInput(sara), { name: 'Sara', email: 'sara@example.com', company: '', phone: '', notes: '' });
    assert.deepEqual(appendContacts([sara], [sara, contact('2', 'Sam', 'sam@example.com')]).map((c) => c.id), ['1', '2']);
  });
});
