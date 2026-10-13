// Unit tests for the contact opt-out list helpers (src/lib/suppression.ts, docs/privacy.md#contact-suppression).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { addedBy, channelLabel, contactKind, emptySuppressionDraft, exportName, purposeLabel, removalReason, sourceLabel, suppressionBody } from '../src/lib/suppression.ts';
import { setLocale } from '../src/lib/i18n.ts';

describe('contact opt-outs', () => {
  test('phone numbers and e-mail addresses are told apart like the API does', () => {
    assert.equal(contactKind('+971 50 123 4567'), 'phone');
    assert.equal(contactKind('050 123 4567'), 'phone');
    assert.equal(contactKind('ahmed@example.com'), 'email');
    assert.equal(contactKind('ahmed@'), null);
    assert.equal(contactKind('123'), null);
  });

  test('the add form needs a contact, a fitting channel and a reason', () => {
    const draft = { ...emptySuppressionDraft(), recipient: ' +971501234567 ', reason: ' Asked by phone ' };
    assert.deepEqual(suppressionBody(draft), { ok: true, value: { recipient: '+971501234567', channel: 'all', purpose: 'all', reason: 'Asked by phone' } });
    assert.equal(suppressionBody({ ...draft, recipient: 'nope' }).ok, false);
    assert.equal(suppressionBody({ ...draft, channel: 'email' }).ok, false);
    assert.equal(suppressionBody({ ...draft, recipient: 'a@example.com', channel: 'whatsapp' }).ok, false);
    assert.equal(suppressionBody({ ...draft, reason: 'x' }).ok, false);
    assert.equal(removalReason('  ').ok, false);
    assert.deepEqual(removalReason(' Asked to start again '), { ok: true, value: 'Asked to start again' });
  });

  test('labels in both languages', () => {
    setLocale('en');
    assert.equal(channelLabel('whatsapp'), 'WhatsApp');
    assert.equal(purposeLabel('marketing'), 'Marketing only');
    assert.equal(sourceLabel('whatsapp_keyword'), 'WhatsApp STOP');
    assert.equal(addedBy({ created_by: 'system:inbound' }), 'Added automatically');
    assert.equal(addedBy({ created_by: 'owner_a' }), 'Added by owner_a');
    setLocale('ar');
    assert.equal(channelLabel('whatsapp'), 'واتساب');
    assert.equal(purposeLabel('all'), 'كل الرسائل');
    setLocale('en');
    assert.equal(exportName('attachment; filename="contact-suppressions.csv"'), 'contact-suppressions.csv');
    assert.equal(exportName('attachment; filename="../x y"'), 'contact-suppressions.csv');
  });
});
