// Unit tests for the Document vault page model (src/lib/vault.ts, docs/documents.md#the-web-page).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  activityLabel, categoryLabel, editDraft, editPatch, expiryText, expiryTone, filterQuery, hashOf, mergeDocuments, reviewDraft, reviewProblems,
  tabFromHash, uploadProblem, warningTexts, type DocumentExtraction, type VaultDocument,
} from '../src/lib/vault.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { viewFromHash } from '../src/lib/shell.ts';

const doc = (extra: Partial<VaultDocument> = {}): VaultDocument => ({
  id: 'vdoc_1', title: 'Passport', category: 'passport', access_class: 'private', status: 'active', issue_date: '2022-03-05', expiry_date: '2032-03-04',
  expiry_state: 'valid', days_left: 2000, issuer: null, country: 'SA', tags: ['family'], description: '', number_masked: '•••• 4567',
  assistant_may_read: false, reminder_days: null, retention_until: null, receipt: null, source: 'web', owner_user_id: 'alice', is_owner: true,
  can_edit: true, row_version: 3, created_by: 'alice', created_at: '2026-11-01T10:00:00Z', updated_by: 'alice', updated_at: '2026-11-01T10:00:00Z',
  deleted_at: null, purge_after: null, file: null, ...extra,
});

const extraction = (extra: Partial<DocumentExtraction> = {}): DocumentExtraction => ({
  id: 'vext_1', document_id: 'vdoc_1', kind: 'receipt', status: 'pending', engine: 'tesseract 5', languages: 'eng+ara',
  fields: { total: { value: '150.00', confidence: 0.9, source: 'Total SAR 150.00' }, currency: { value: 'SAR', confidence: 0.9, source: null } },
  items: [], warnings: [], error: null, requested_by: 'alice', decided_by: null, decided_at: null, created_at: '2026-11-01T10:00:00Z', ...extra,
});

describe('document vault', () => {
  test('tabs open by link and the shell routes them to the vault', () => {
    assert.equal(tabFromHash('#vault'), 'overview');
    assert.equal(tabFromHash('#vault-review'), 'review');
    assert.equal(tabFromHash('#vault-nothing'), 'overview');
    assert.equal(hashOf('trash'), '#vault-trash');
    assert.equal(viewFromHash('#vault-settings'), 'vault');
    assert.equal(viewFromHash('#vault'), 'vault');
  });

  test('categories, expiry and activity read as words in English and Arabic', () => {
    setLocale('en');
    assert.equal(categoryLabel('national_id'), 'National ID');
    assert.equal(categoryLabel('vcat_abcd', [{ id: 'vcat_abcd', label: 'Medical', built_in: false, reads: null, identity: false }]), 'Medical');
    assert.equal(categoryLabel('vcat_gone'), 'Removed category');
    assert.equal(expiryText(doc({ expiry_state: 'soon', days_left: 1 })), 'Expires in 1 day');
    assert.equal(expiryText(doc({ expiry_state: 'expired', days_left: -3 })), 'Expired 3 days ago');
    assert.equal(expiryText(doc({ expiry_state: 'soon', days_left: 0 })), 'Expires today');
    assert.equal(expiryTone('expired'), 'danger');
    assert.equal(activityLabel('number_revealed'), 'Number shown');
    assert.equal(activityLabel('something_new'), 'Other change');
    setLocale('ar');
    assert.equal(categoryLabel('passport'), 'جواز سفر');
    assert.match(expiryText(doc({ expiry_state: 'soon', days_left: 5 })), /ينتهي بعد/);
    setLocale('en');
  });

  test('the edit form sends only what changed and refuses what the server would', () => {
    setLocale('en');
    const base = doc();
    const draft = editDraft(base);
    assert.deepEqual(editPatch(base, draft), { patch: null, problem: 'Nothing changed.' });
    assert.deepEqual(editPatch(base, { ...draft, title: 'Passport (new)', tags: 'family, travel, family', number: 'AB 1234' }).patch,
      { row_version: 3, title: 'Passport (new)', tags: ['family', 'travel'], number: 'AB 1234' });
    assert.equal(editPatch(base, { ...draft, issue_date: '2033-01-01' }).problem, 'The issue date must be on or before the expiry date.');
    assert.equal(editPatch(base, { ...draft, expiry_date: '2026-02-30' }).problem, 'Use a real date (year, month, day).');
    assert.equal(editPatch(base, { ...draft, country: 'SAU' }).problem, 'The country is a two-letter code such as SA or AE.');
    assert.equal(editPatch(base, { ...draft, number: '12' }).problem, 'A number has 4 to 40 letters and digits.');
    assert.match(editPatch(base, { ...draft, assistant_may_read: true }).problem ?? '', /workspace documents only/);
    assert.deepEqual(editPatch(base, { ...draft, access_class: 'workspace', assistant_may_read: true }).patch,
      { row_version: 3, access_class: 'workspace', assistant_may_read: true });
    assert.deepEqual(editPatch(base, { ...draft, expiry_date: '' }).patch, { row_version: 3, expiry_date: null });
  });

  test('the review form starts from the proposed values and checks money, dates and currency', () => {
    setLocale('en');
    const draft = reviewDraft(extraction());
    assert.deepEqual(draft, { merchant: '', date: '', total: '150.00', currency: 'SAR', tax: '', reference: '' });
    assert.deepEqual(reviewProblems('receipt', draft), {});
    assert.deepEqual(Object.keys(reviewProblems('receipt', { ...draft, total: '1,5x', currency: '' })).sort(), ['currency', 'total']);
    assert.equal(reviewProblems('receipt', { ...draft, date: '2026-13-01' }).date, 'Use a real date (year, month, day).');
    assert.deepEqual(reviewProblems('dates', { issue_date: '2030-01-01', expiry_date: '2020-01-01' }), { expiry_date: 'The issue date must be on or before the expiry date.' });
    assert.deepEqual(warningTexts(extraction({ warnings: ['instructions_in_text', 'unknown_code'] })).length, 1);
  });

  test('uploads are checked before sending, pages merge without duplicates and filters build the query', () => {
    setLocale('en');
    const meta = { max_upload_bytes: 10, accepted_extensions: ['.pdf', '.png'] };
    assert.equal(uploadProblem({ name: 'a.pdf', size: 0 }, meta), 'a.pdf is empty.');
    assert.equal(uploadProblem({ name: 'a.pdf', size: 11 }, meta), 'a.pdf is larger than this server accepts.');
    assert.equal(uploadProblem({ name: 'a.exe', size: 5 }, meta), 'a.exe is a kind of file the vault does not accept.');
    assert.equal(uploadProblem({ name: 'A.PDF', size: 5 }, meta), null);
    assert.deepEqual(mergeDocuments([doc()], [doc(), doc({ id: 'vdoc_2' })]).map((d) => d.id), ['vdoc_1', 'vdoc_2']);
    assert.equal(filterQuery({ q: ' lease ', category: 'contract', access: 'private', expiry: 'soon' }, 'active', 'c1'),
      'limit=50&status=active&q=lease&category=contract&access_class=private&expiry=soon&cursor=c1');
  });
});
