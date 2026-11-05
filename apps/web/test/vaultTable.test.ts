// Unit tests for the vault's documents table, calendar and overview helpers (src/lib/vault.ts, docs/documents.md#the-web-page).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { DocumentExtraction } from '@anum/contracts';
import {
  documentFromHash, documentHash, expiriesByDay, filterQuery, isIdentity, linkedPercent, monthGrid, rowStatus, rowStatusTone, suggestionFields, tabFromHash,
} from '../src/lib/vault.ts';
import { vaultAr } from '../src/lib/messages/vault.ar.ts';
import { vaultEn } from '../src/lib/messages/vault.en.ts';

describe('vault table', () => {
  test('a row status: expired, then review, then soon, restricted identity papers, else valid', () => {
    const row = { expiry_state: 'valid' as const, review_pending: false, access_class: 'workspace' as const, category: 'contract' };
    assert.equal(rowStatus(row), 'valid');
    assert.equal(rowStatus({ ...row, access_class: 'private', category: 'passport' }), 'restricted');
    assert.equal(rowStatus({ ...row, expiry_state: 'soon', access_class: 'private', category: 'passport' }), 'soon');
    assert.equal(rowStatus({ ...row, review_pending: true, expiry_state: 'soon' }), 'review');
    assert.equal(rowStatus({ ...row, review_pending: true, expiry_state: 'expired' }), 'expired');
    assert.equal(rowStatusTone('expired'), 'bad');
    assert.equal(rowStatusTone('valid'), 'ok');
    assert.equal(isIdentity('visa'), true);
    assert.equal(isIdentity('vcat_x', [{ id: 'vcat_x', label: 'Mine', built_in: false, reads: null, identity: false }]), false);
  });

  test('records linked is a whole percentage, and unknown without documents', () => {
    assert.equal(linkedPercent({ active: 86, linked: 79 }), 92);
    assert.equal(linkedPercent({ active: 0, linked: 0 }), null);
  });

  test('the sort goes to the API; created is the default and is not sent', () => {
    assert.match(filterQuery({ q: '', category: '', access: '', expiry: '', sort: 'expiry' }, 'active', null, 100), /limit=100.*sort=expiry/);
    assert.doesNotMatch(filterQuery({ q: '', category: '', access: '', expiry: '', sort: 'created' }), /sort=/);
  });

  test('the calendar grid and the expiries of a month', () => {
    const november = monthGrid(2026, 10);
    assert.equal(november[0].filter((d) => d === null).length, 0, '1 Nov 2026 is a Sunday');
    assert.equal(november.flat().filter(Boolean).length, 30);
    assert.ok(november.every((week) => week.length === 7));
    const marks = expiriesByDay([{ title: 'Registration', expiry_date: '2026-11-18' }, { title: 'Lease', expiry_date: '2027-01-31' }, { title: 'None', expiry_date: null }], 2026, 10);
    assert.deepEqual(marks, { 18: ['Registration'] });
  });

  test('a link opens one document, and only a plain id is read from it', () => {
    assert.equal(documentHash('vdoc_1'), '#vault-documents?doc=vdoc_1');
    assert.equal(tabFromHash('#vault-documents?doc=vdoc_1'), 'documents');
    assert.equal(documentFromHash('#vault-documents?doc=vdoc_1'), 'vdoc_1');
    assert.equal(documentFromHash('#vault-documents?doc=%3Cscript%3E'), null);
    assert.equal(tabFromHash('#vault-access'), 'access');
  });

  test('review suggestions show the shop, amount with currency and date', () => {
    const extraction = { kind: 'receipt', fields: { merchant: { value: 'Al Drees', confidence: 0.9, source: null }, total: { value: '420.00', confidence: 0.9, source: null },
      currency: { value: 'SAR', confidence: 0.9, source: null }, date: { value: '2025-11-05', confidence: 0.6, source: null } } } as unknown as DocumentExtraction;
    assert.deepEqual(suggestionFields(extraction), [{ key: 'merchant', value: 'Al Drees' }, { key: 'total', value: 'SAR 420.00' }, { key: 'date', value: '2025-11-05' }]);
    assert.deepEqual(suggestionFields({ kind: 'dates', fields: {} } as unknown as DocumentExtraction), []);
  });

  test('every vault message has both languages, without long dashes', () => {
    for (const key of Object.keys(vaultEn)) assert.ok(key in vaultAr, key);
    for (const text of [...Object.values(vaultEn), ...Object.values(vaultAr)]) assert.doesNotMatch(JSON.stringify(text), /[–—]/);
  });
});
