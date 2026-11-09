// Supporting documents of the business books (docs/business-books.md#supporting-documents-and-evidence): the pure model and the
// English and Arabic words of the evidence screens.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { VaultDocumentRow } from '@anum/contracts';
import { documentHref, offerable, packProblem, packQuery } from '../src/lib/businessEvidenceModel.ts';
import { settingsBody } from '../src/lib/businessBooks.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const row = (id: string) => ({ id, title: `Synthetic ${id}` }) as unknown as VaultDocumentRow;

describe('business evidence model', () => {
  it('offers only documents not attached yet', () => {
    assert.deepEqual(offerable([row('a'), row('b'), row('c')], ['b']).map((d) => d.id), ['a', 'c']);
    assert.deepEqual(offerable([], ['b']), []);
  });
  it('builds the pack query and refuses a period that ends before it starts', () => {
    assert.equal(packQuery({}), '');
    assert.equal(packQuery({ from: '2026-10-01', to: '2026-10-31' }), '?from=2026-10-01&to=2026-10-31');
    assert.equal(packProblem({ from: '2026-11-01', to: '2026-10-31' }), 'books.evidence.pack.badRange');
    assert.equal(packProblem({ from: '2026-10-01' }), null);
  });
  it('opens a document in the vault', () => {
    assert.equal(documentHref('vdoc 1'), '#vault-documents?doc=vdoc%201');
  });
  it('sends an empty threshold as off and keeps a typed one', () => {
    const form = { base_currency: 'SAR', country: '', fiscal_year_start_month: 1, basis: 'accrual' as const, tax_label: '', rate_max_age_days: '31', mappings: [] };
    assert.equal(settingsBody({ ...form, evidence_threshold: '' }, null).evidence_threshold, null);
    assert.equal(settingsBody({ ...form, evidence_threshold: ' 500 ' }, null).evidence_threshold, '500');
    assert.equal('evidence_threshold' in settingsBody(form, null), false);
  });
  it('words every evidence screen in English and Arabic', () => {
    const keys = Object.keys(en).filter((key) => key.startsWith('books.evidence.') || key === 'books.note.evidence_missing' || key.startsWith('vault.link.business_'));
    assert.ok(keys.length > 30);
    for (const key of keys) assert.ok(key in ar, key);
  });
});
