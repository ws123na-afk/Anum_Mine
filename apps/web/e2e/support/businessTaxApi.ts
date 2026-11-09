import type { Page, Route } from '@playwright/test';

// The business tax API (docs/business-tax.md) for the browser tests. Registered after mockBooks, so it answers the
// /books/tax routes first. Seller, buyers, numbers and amounts are SYNTHETIC; the VAT number is a plainly fake shape.
type Json = Record<string, unknown>;
export interface TaxOptions { settings?: boolean; zatca?: boolean; canEdit?: boolean; failReturn?: number; documents?: number; emptyReturn?: boolean }
export interface TaxMock { calls: { method: string; path: string; body: Json | null }[] }

const at = '2026-11-01T09:00:00Z';
const CSV_HEADERS = { 'access-control-allow-origin': '*', 'access-control-expose-headers': 'Content-Disposition' };
const NOTICE = { en: 'Not verified with ZATCA. ANUM does not claim compliance until the business completes onboarding and the invoices pass ZATCA validation.',
  ar: 'لم يتم التحقق مع هيئة الزكاة والضريبة والجمارك.' };

function settingsRow(zatca: boolean): Json {
  return { id: 'btax_1', jurisdiction: 'SA', registered: true, registration_number: '300000000000003', registration_date: '2024-01-01', filing_frequency: 'quarterly',
    period_start_month: 1, zatca_enabled: zatca, zatca_onboarding: 'not_started', seller_crn: '1010000000', seller_street: 'Synthetic Street', seller_building: '1234',
    seller_district: 'Synthetic District', seller_city: 'Riyadh', seller_postal_code: '12345', version: 1, updated_by: 'user_local', updated_at: at, created_at: at };
}

const rates: Json[] = [
  { id: 'r1', jurisdiction: 'SA', code: 'standard', rate_pct: '5', effective_from: '2018-01-01', effective_to: '2020-06-30', note: 'KSA standard rate before 1 July 2020' },
  { id: 'r2', jurisdiction: 'SA', code: 'standard', rate_pct: '15', effective_from: '2020-07-01', effective_to: null, note: 'KSA standard rate from 1 July 2020' },
  { id: 'r3', jurisdiction: 'SA', code: 'zero_rated', rate_pct: '0', effective_from: '2018-01-01', effective_to: null, note: '' },
  { id: 'r4', jurisdiction: 'SA', code: 'exempt', rate_pct: '0', effective_from: '2018-01-01', effective_to: null, note: '' },
];

const box = (id: string, section: string, label: string, amount: string, vat: string, documents = 0, adj = '0.00') =>
  ({ box: id, section, label, amount, vat, adjustment_amount: adj, adjustment_vat: '0.00', documents });

function vatReturn(empty: boolean): Json {
  const zero = (id: string, section: string, label: string) => box(id, section, label, '0.00', '0.00');
  return {
    period: { from: '2026-07-01', to: '2026-09-30' }, jurisdiction: 'SA', base_currency: 'SAR', registration_number: '300000000000003', closed: false, closed_through: null,
    complete: false,
    boxes: empty ? [zero('1', 'sales', 'Standard rated sales'), zero('6', 'sales_total', 'Total sales'), zero('13', 'net', 'Total VAT due for the current period')] : [
      box('1', 'sales', 'Standard rated sales', '12000.00', '1800.00', 30, '200.00'), zero('2', 'sales', 'x'), box('3', 'sales', 'x', '500.00', '0.00', 1),
      box('4', 'sales', 'x', '300.00', '0.00', 1), box('5', 'sales', 'x', '200.00', '0.00', 1), box('6', 'sales_total', 'x', '13000.00', '1800.00'),
      box('7', 'purchases', 'x', '4000.00', '600.00', 3), zero('8', 'purchases', 'x'), box('9', 'purchases', 'x', '1000.00', '150.00', 1), zero('10', 'purchases', 'x'),
      zero('11', 'purchases', 'x'), box('12', 'purchases_total', 'x', '5000.00', '750.00'), box('13', 'net', 'x', '0.00', '1200.00'), zero('14', 'net', 'x'),
      zero('15', 'net', 'x'), box('16', 'net', 'x', '0.00', '1200.00')],
    info: empty ? [] : [{ key: 'unknown_sales', label: 'Sales without a tax code and without VAT (not in the return)', amount: '70.00', vat: '0.00', documents: 1 }],
    notes: ['Row 2 is not tracked by ANUM: complete it on the ZATCA portal.'],
    warnings: empty ? [{ code: 'period_open', text: 'x' }] : [{ code: 'inferred', text: 'x', count: 2 }, { code: 'unknown', text: 'x', count: 1 }, { code: 'period_open', text: 'x' }],
    journal: { output_vat_movement: '1950.00', input_vat_movement: '750.00', output_vat_in_return: '1950.00', input_vat_in_return: '750.00', manual_output: '0.00',
      manual_input: '0.00', ties: true },
  };
}

function documents(count: number, offset: number, limit: number): Json {
  const all = Array.from({ length: count }, (_, index) => ({ entry_id: `bje_${index}`, source_kind: 'invoice', source_id: `binv_${index}`,
    description: `Invoice INV-2026-${String(index + 1).padStart(4, '0')} issued`, entry_date: '2026-08-10', event_date: index === 0 ? '2026-06-28' : '2026-08-10',
    box: '1', tax_code: index < 2 ? 'standard' : 'standard', inferred: index === 1, adjustment: index === 0, net: '400.00', vat: '60.00' }));
  return { items: all.slice(offset, offset + limit), total: count, limit, offset };
}

export async function mockTax(page: Page, options: TaxOptions = {}): Promise<TaxMock> {
  const mock: TaxMock = { calls: [] };
  let settings: Json | null = options.settings === false ? null : settingsRow(Boolean(options.zatca));
  let failures = options.failReturn ?? 0;
  const generated: Record<string, Json> = {};
  let counter = 0;
  await page.route('http://localhost:8000/api/v1/business/books/tax**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/business/books/tax', '');
    const body = request.postData() ? request.postDataJSON() as Json : null;
    mock.calls.push({ method: request.method(), path, body });
    const json = (status: number, value: unknown) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const canEdit = options.canEdit !== false;
    if (request.method() === 'GET' && path === '') {
      return json(200, { settings, rates: settings ? rates : [], can_edit: canEdit, jurisdictions: ['SA', 'AE', 'OTHER'], codes: [], sales_codes: [],
        frequencies: ['monthly', 'quarterly'], notice: NOTICE,
        periods: [{ from: '2026-10-01', to: '2026-12-31' }, { from: '2026-07-01', to: '2026-09-30' }, { from: '2026-04-01', to: '2026-06-30' }] });
    }
    if (request.method() === 'PUT' && path === '/settings') {
      if (!canEdit) return json(403, { detail: 'Only an owner or an accountant of this business can do this.', code: 'forbidden' });
      settings = { ...settingsRow(Boolean(body?.zatca_enabled)), ...body, version: settings ? Number(settings.version) + 1 : 1 };
      return json(200, { settings, rates });
    }
    if (request.method() === 'POST' && path === '/rates') return json(201, { id: 'r9', jurisdiction: 'SA', note: '', effective_to: null, ...body });
    if (path === '/return.csv') return route.fulfill({ status: 200, contentType: 'text/csv', headers: { 'content-disposition': 'attachment; filename="vat-return-2026-07-01-2026-09-30.csv"', ...CSV_HEADERS }, body: 'box,label\n' });
    if (path === '/return') {
      if (failures > 0) { failures -= 1; return json(500, { detail: 'The return could not be read.' }); }
      return json(200, vatReturn(Boolean(options.emptyReturn)));
    }
    if (path === '/return/documents') return json(200, documents(options.documents ?? 30, Number(url.searchParams.get('offset') ?? 0), Number(url.searchParams.get('limit') ?? 25)));
    if (path === '/zatca') {
      const invoices = Array.from({ length: 3 }, (_, index) => {
        const id = `binv_${index}`;
        return { invoice_id: id, number: `INV-2026-000${index + 1}`, issue_date: '2026-10-1' + index, currency: 'SAR', total: '1150.00', tax: '150.00',
          buyer: index === 0 ? 'شركة المشتري التجريبية' : 'Synthetic Buyer Co', kind: index === 0 ? 'standard' : 'simplified', uncoded_lines: index === 2 ? 1 : 0,
          document: generated[id] ?? null };
      });
      const enabled = Boolean(settings?.zatca_enabled);
      return json(200, { enabled, onboarding: 'not_started', notice: NOTICE, onboarding_required: 'Onboarding first.', can_edit: canEdit, counter, last_hash: '',
        generated: counter, missing: ['buyer_address_structured', 'cryptographic_stamp', 'csid', 'qr_phase2'],
        invoices: { items: invoices, total: 3, limit: 25, offset: 0 } });
    }
    if (request.method() === 'POST' && path === '/zatca/generate') {
      counter += 1;
      const id = String(body?.source_id);
      generated[id] = { id: `bzdoc_${counter}`, source_kind: 'invoice', source_id: id, document_number: `INV-2026-000${counter}`, type_code: '388', subtype: '0200000',
        counter, uuid: '00000000-0000-4000-8000-000000000001', invoice_hash: 'c3ludGhldGljLWhhc2gtZXhhbXBsZS12YWx1ZQ==', previous_hash: 'cHJldmlvdXM=',
        issue_date: '2026-10-10', qr: '', missing: [], status: 'generated', created_by: 'user_local', created_at: at };
      return json(201, generated[id]);
    }
    if (path.endsWith('/submit')) {
      return json(409, { detail: 'Sending invoices to ZATCA needs onboarding first: the business generates a one-time password on the Fatoora portal, creates a certificate signing request and receives its cryptographic stamp identifier (CSID). ANUM has not been onboarded, so nothing is sent to ZATCA.', code: 'zatca_onboarding_required' });
    }
    if (path.endsWith('/xml')) return route.fulfill({ status: 200, contentType: 'application/xml', headers: { 'content-disposition': 'attachment; filename="INV-2026-0001.xml"', ...CSV_HEADERS }, body: '<Invoice/>' });
    return json(404, { detail: 'Not found' });
  });
  return mock;
}
