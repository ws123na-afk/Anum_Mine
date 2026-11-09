// Unit tests for the business tax helpers (src/lib/businessTax.ts, docs/business-tax.md) and the tax codes on bills and invoice lines.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { TaxBox } from '@anum/contracts';
import { boxGroups, boxLabel, codeLabel, formFrom, infoLabel, notesFor, numberProblem, settingsBody, settingsProblem, shortHash, warningText } from '../src/lib/businessTax.ts';
import { BOOKS_TABS, billBody, billProblem, booksTabFromId, emptyBill } from '../src/lib/businessBooks.ts';
import { emptyManualLine, manualLines, withTaxCodes } from '../src/lib/invoices.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const box = (id: string, section: TaxBox['section']): TaxBox => ({ box: id, section, label: `Box ${id}`, amount: '0.00', vat: '0.00', adjustment_amount: '0.00',
  adjustment_vat: '0.00', documents: 0 });

describe('business tax', () => {
  test('the tax section opens from its hash', () => {
    assert.ok(BOOKS_TABS.includes('tax'));
    assert.equal(booksTabFromId('tax'), 'tax');
  });

  test('registration numbers follow the jurisdiction', () => {
    assert.equal(numberProblem('SA', '300000000000003'), null);
    assert.equal(numberProblem('SA', '3000 0000 0000 003'), null);
    assert.equal(numberProblem('SA', '300000000000000'), 'tax.problem.saNumber');
    assert.equal(numberProblem('SA', '100000000000003'), 'tax.problem.saNumber');
    assert.equal(numberProblem('AE', '100000000000001'), null);
    assert.equal(numberProblem('AE', '12345'), 'tax.problem.aeNumber');
    assert.equal(numberProblem('OTHER', 'GB 123 456'), null);
    assert.equal(numberProblem('SA', ''), null);
  });

  test('the settings form refuses what the API refuses and starts from the books country', () => {
    const form = formFrom(null, 'SA');
    assert.equal(form.jurisdiction, 'SA');
    assert.equal(form.zatca_enabled, false);
    assert.equal(formFrom(null, 'KW').jurisdiction, 'OTHER');
    assert.equal(settingsProblem({ ...form, registered: true }, '2026-11-09'), 'tax.problem.needNumber');
    assert.equal(settingsProblem({ ...form, zatca_enabled: true, registration_number: '300000000000003' }, '2026-11-09'), 'tax.problem.zatcaRegistered');
    assert.equal(settingsProblem({ ...form, jurisdiction: 'AE', zatca_enabled: true, registered: true, registration_number: '100000000000001' }, '2026-11-09'),
      'tax.problem.zatcaSa');
    assert.equal(settingsProblem({ ...form, registration_date: '2026-12-01' }, '2026-11-09'), 'tax.problem.futureDate');
    assert.equal(settingsProblem({ ...form, registered: true, registration_number: '300000000000003', seller_crn: '10ab' }, '2026-11-09'), 'tax.problem.crn');
    const body = settingsBody({ ...form, jurisdiction: 'AE', zatca_enabled: true, registration_number: ' 1000 ' }, 3);
    assert.equal(body.zatca_enabled, false);
    assert.equal(body.registration_number, '1000');
    assert.equal(body.version, 3);
  });

  test('return boxes group by section with totals kept in their section', () => {
    const groups = boxGroups([box('1', 'sales'), box('6', 'sales_total'), box('7', 'purchases'), box('12', 'purchases_total'), box('13', 'net')]);
    assert.deepEqual(groups.map((group) => [group.section, group.rows.map((row) => row.box)]), [['sales', ['1', '6']], ['purchases', ['7', '12']], ['net', ['13']]]);
    assert.equal(boxLabel('SA', box('9', 'purchases')), 'Imports subject to VAT through the reverse charge');
    assert.equal(boxLabel('OTHER', box('S-standard', 'sales')), 'Box S-standard');
  });

  test('labels and warnings are translated, with the API text as the fallback', () => {
    assert.equal(codeLabel('reverse_charge'), 'Reverse charge (import of services)');
    assert.equal(codeLabel(null), 'No code (unknown)');
    assert.match(warningText({ code: 'inferred', text: 'x', count: 2 }), /: 2\./);
    assert.equal(warningText({ code: 'unposted', text: 'x' }).startsWith('Some changes'), true);
    assert.equal(shortHash('abcdefghijklmnopqrstuvwxyz'), 'abcdefgh...uvwxyz');
    assert.equal(shortHash(''), '-');
    assert.equal(notesFor('AE', ['api']).length, 3);
    assert.deepEqual(notesFor('XX' as never, ['api note']), ['api note']);
    assert.equal(infoLabel('unknown_sales', 'x'), 'Sales without a tax code and without VAT (not in the return)');
    assert.equal(infoLabel('something_new', 'API text'), 'API text');
  });

  test('every tax message exists in both languages and says the not-verified notice', () => {
    const keys = Object.keys(en).filter((key) => key.startsWith('tax.'));
    assert.ok(keys.length > 150);
    for (const key of keys) assert.ok(key in ar, key);
    assert.match(String(en['tax.zatca.notice' as keyof typeof en]), /^Not verified with ZATCA\. ANUM does not claim compliance/);
    assert.ok(String(ar['tax.zatca.notice' as keyof typeof ar]).includes('لا يدعي ANUM الامتثال'));
  });

  test('bills and invoice lines carry their tax code', () => {
    const filled = { ...emptyBill('2026-11-09', 'SAR'), vendor_id: 'v1', description: 'Synthetic licence', subtotal: '1000', tax: '0' };
    assert.equal(billBody(filled).tax_code, null);
    assert.equal(billBody({ ...filled, tax_code: 'reverse_charge' }).tax_code, 'reverse_charge');
    assert.equal(billProblem({ ...filled, tax_code: 'exempt', tax: '5' }), 'tax.problem.billNoTax');
    assert.equal(billProblem({ ...filled, tax_code: 'standard', tax: '150' }), null);
    const lines = manualLines([{ ...emptyManualLine(), description: 'Synthetic design', unitPrice: '100', taxCode: 'export' }, { ...emptyManualLine(), description: 'Legacy', unitPrice: '5' }]);
    assert.ok(lines.ok);
    if (lines.ok) {
      assert.equal(lines.value[0].tax_code, 'export');
      assert.equal('tax_code' in lines.value[1], false);
      assert.deepEqual(withTaxCodes({ lines: lines.value }, ['standard']).tax_codes, ['standard', 'export', null]);
      assert.equal(withTaxCodes({ lines: lines.value }, [null]).tax_codes, undefined);
    }
  });
});
