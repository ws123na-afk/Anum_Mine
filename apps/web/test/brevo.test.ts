import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { setLocale, t } from '../src/lib/i18n.ts';
import {
  DRAFT_STATUSES, campaignStatusName, draftActions, draftProblems, draftStatusName, draftsPath, fromLocalInput, keyProblem, pageRange, percent, statusTone, toLocalInput,
  type DraftBody,
} from '../src/lib/brevo.ts';
import { brevoEn } from '../src/lib/messages/brevo.en.ts';
import { brevoAr } from '../src/lib/messages/brevo.ar.ts';

afterEach(() => setLocale('en'));

const body: DraftBody = { name: 'Winter tyres', subject: 'Fit winter tyres', preview_text: '', sender_id: 1, html: '<p>Book now</p>', list_ids: [7] };

describe('Brevo email campaigns helpers', () => {
  test('API keys: an API key passes, SMTP keys and other text say what is wrong', () => {
    assert.equal(keyProblem('xkeysib-example-example-example'), null);
    assert.equal(keyProblem('  '), 'brevo.key.empty');
    assert.equal(keyProblem('xsmtpsib-example-example-example'), 'brevo.key.smtp');
    assert.equal(keyProblem('sk-example'), 'brevo.key.shape');
  });

  test('draft problems mirror the server rules', () => {
    assert.deepEqual(draftProblems(body), []);
    assert.deepEqual(draftProblems({ ...body, name: ' ', subject: '', sender_id: 0, list_ids: [], html: '' }),
      ['brevo.problem.name', 'brevo.problem.subject', 'brevo.problem.sender', 'brevo.problem.lists', 'brevo.problem.html']);
    assert.ok(draftProblems({ ...body, list_ids: Array.from({ length: 11 }, (_, i) => i + 1) }).includes('brevo.problem.tooManyLists'));
    assert.ok(draftProblems({ ...body, html: 'x'.repeat(512 * 1024 + 1) }).includes('brevo.problem.htmlTooLong'));
  });

  test('actions follow the status and the permissions; sending needs a connection', () => {
    const member = { can_write: true, can_manage: false }, ownerPerms = { can_write: true, can_manage: true }, viewer = { can_write: false, can_manage: false };
    assert.deepEqual(draftActions({ status: 'draft' }, member, true), { edit: true, test: false, ask: true, withdraw: false, remove: true });
    assert.equal(draftActions({ status: 'draft' }, ownerPerms, true).test, true);
    assert.equal(draftActions({ status: 'draft' }, ownerPerms, false).ask, false);
    assert.deepEqual(draftActions({ status: 'pending_approval' }, member, true), { edit: true, test: false, ask: false, withdraw: true, remove: true });
    assert.deepEqual(draftActions({ status: 'sent' }, ownerPerms, true), { edit: false, test: false, ask: false, withdraw: false, remove: true });
    assert.equal(draftActions({ status: 'sending' }, ownerPerms, true).remove, false);
    assert.deepEqual(draftActions({ status: 'draft' }, viewer, true), { edit: false, test: false, ask: false, withdraw: false, remove: false });
  });

  test('tones, names, rates and paging', () => {
    assert.equal(statusTone('sent'), 'success');
    assert.equal(statusTone('unknown'), 'danger');
    assert.equal(statusTone('pending_approval'), 'warning');
    assert.equal(campaignStatusName('in_process'), 'Sending');
    assert.equal(campaignStatusName('unknown'), 'Unknown');
    assert.equal(percent(null), '-');
    assert.equal(percent(48.456), '48.5%');
    assert.deepEqual(pageRange({ total: 45, limit: 20, offset: 20 }, 20), { from: 21, to: 40, hasPrev: true, hasNext: true });
    assert.deepEqual(pageRange({ total: 0, limit: 20, offset: 0 }, 0), { from: 0, to: 0, hasPrev: false, hasNext: false });
    assert.equal(draftsPath({ query: ' winter ', status: 'failed', limit: 20, offset: 40 }), '/api/v1/brevo/drafts?limit=20&offset=40&q=winter&status=failed');
    for (const status of DRAFT_STATUSES) assert.ok(draftStatusName(status).length > 0);
  });

  test('local times round-trip through UTC', () => {
    assert.equal(fromLocalInput(''), null);
    assert.equal(fromLocalInput('not a time'), null);
    const iso = fromLocalInput('2026-11-02T09:30');
    assert.ok(iso);
    assert.equal(toLocalInput(iso), '2026-11-02T09:30');
    assert.equal(toLocalInput(null), '');
  });

  test('English and Arabic have the same keys and placeholders, and Arabic reads Arabic', () => {
    assert.deepEqual(Object.keys(brevoAr).sort(), Object.keys(brevoEn).sort());
    for (const [key, english] of Object.entries(brevoEn)) {
      const names = (text: string) => [...String(text).matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
      assert.deepEqual(names(String(brevoAr[key as keyof typeof brevoEn])), names(english), key);
    }
    setLocale('ar');
    assert.equal(t('brevo.status.pending_approval'), 'بانتظار الموافقة');
    assert.equal(t('marketing.tab.email'), 'حملات البريد');
  });
});
