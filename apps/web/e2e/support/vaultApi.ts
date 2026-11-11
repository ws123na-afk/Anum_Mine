import type { Page, Route } from '@playwright/test';
import { logoPng } from './pngFixture';

// The Document vault API (`/api/v1/documents`, docs/documents.md#api) as a small in-memory server for
// e2e/vault.spec.ts and the UI gate. Registered after mockWorkspaceApi: anything else falls back to it.
type Json = Record<string, unknown>;
const now = '2026-11-04T09:00:00.000Z';
const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);

const categories = ['passport', 'national_id', 'residency', 'visa', 'contract', 'salary_certificate', 'vehicle_registration', 'insurance', 'bank_statement',
  'receipt', 'invoice', 'purchase_order', 'agreement', 'warranty', 'maintenance_invoice', 'certificate', 'other']
  .map((id) => ({ id, label: null, built_in: true, reads: ['receipt', 'invoice', 'maintenance_invoice', 'purchase_order'].includes(id) ? 'receipt'
    : ['passport', 'national_id', 'residency', 'visa', 'insurance', 'vehicle_registration', 'warranty', 'certificate'].includes(id) ? 'dates' : null,
  identity: ['passport', 'national_id', 'residency', 'visa'].includes(id) }));

export function vaultDocument(id: string, extra: Json = {}): Json {
  return {
    id, title: 'Passport', category: 'passport', access_class: 'private', status: 'active', issue_date: '2022-03-05', expiry_date: day(20), expiry_state: 'soon',
    days_left: 20, issuer: 'Ministry of Interior', country: 'SA', tags: ['family'], description: '', number_masked: '•••• 4567', assistant_may_read: false,
    reminder_days: null, retention_until: null, receipt: null, source: 'web', owner_user_id: 'user_local', is_owner: true, can_edit: true, row_version: 1,
    created_by: 'user_local', created_at: now, updated_by: 'user_local', updated_at: now, deleted_at: null, purge_after: null,
    file: { name: 'passport.png', content_type: 'image/png', size_bytes: 2048, version: 1, kind: 'image', previewable: true, scan_status: 'clean' }, ...extra,
  };
}

export interface VaultMockOptions {
  empty?: boolean;
  role?: 'owner' | 'member' | 'viewer';
  metaStatus?: 200 | 403 | 500;
  ocr?: boolean;
  manyDocuments?: number;
}

export interface VaultMock {
  calls: { method: string; path: string; query: string; body: unknown }[];
  docs: Json[];
}

export async function mockVault(page: Page, options: VaultMockOptions = {}): Promise<VaultMock> {
  const role = options.role ?? 'owner';
  const lease = vaultDocument('vdoc_lease', { title: 'Flat lease, a long title that keeps going to check that rows wrap well', category: 'contract', access_class: 'workspace', expiry_date: day(400), expiry_state: 'valid', days_left: 400, number_masked: null, tags: ['home'] });
  const iqama = vaultDocument('vdoc_iqama', { title: 'Iqama', category: 'residency', expiry_date: day(-3), expiry_state: 'expired', days_left: -3 });
  const receipt = vaultDocument('vdoc_receipt', { title: 'Fuel receipt', category: 'receipt', expiry_date: null, expiry_state: 'none', days_left: null, number_masked: null, source: 'device',
    file: { name: 'scan.jpg', content_type: 'image/jpeg', size_bytes: 1200, version: 1, kind: 'image', previewable: true, scan_status: 'clean' } });
  const docs: Json[] = options.empty ? [] : [vaultDocument('vdoc_passport'), iqama, lease, receipt];
  for (let index = 0; index < (options.manyDocuments ?? 0); index++) docs.push(vaultDocument(`vdoc_more_${index}`, { title: `Warranty ${index + 1}`, category: 'warranty', expiry_state: 'valid', days_left: 300 }));
  const trash: Json[] = options.empty ? [] : [vaultDocument('vdoc_old', { title: 'Old visa', category: 'visa', status: 'deleted', deleted_at: now, purge_after: '2026-12-04T09:00:00.000Z' })];
  let extraction: Json | null = options.empty ? null : {
    id: 'vext_1', document_id: 'vdoc_receipt', kind: 'receipt', status: 'pending', engine: 'tesseract 5.3.4', languages: 'eng+ara',
    fields: { merchant: { value: 'AL NOOR STATION', confidence: 0.58, source: 'AL NOOR STATION' }, date: { value: '2026-10-14', confidence: 0.86, source: 'Date: 14/10/2026' },
      total: { value: '150.00', confidence: 0.9, source: 'Total SAR 150.00' }, currency: { value: 'SAR', confidence: 0.9, source: 'Total SAR 150.00' },
      tax: { value: '19.57', confidence: 0.74, source: 'VAT 15% 19.57' } },
    items: [], warnings: ['instructions_in_text'], error: null, requested_by: 'user_local', decided_by: null, decided_at: null, created_at: now,
  };
  const settings = { reminder_days: 30, trash_days: 30, ocr_receipts: true, ocr_identity_dates: false, updated_by: null, updated_at: null };
  const devices: Json[] = [];
  const shared: Json[] = [];
  // Older versions (Files versions under the vault, docs/documents.md#versions-trash-and-retention): one by default.
  const versions: Json[] = [{ id: 'filever_1', version: 1, content_type: 'image/png', size_bytes: 1900, created_by: 'user_local', created_at: now, replaced_by: 'user_local', replaced_at: now, downloadable: true }];
  const calls: VaultMock['calls'] = [];
  const meta = () => ({
    summary: { active: docs.length, private: docs.filter((d) => d.access_class === 'private').length, expiring: docs.filter((d) => d.expiry_state === 'soon').length,
      expired: docs.filter((d) => d.expiry_state === 'expired').length, review: extraction?.status === 'pending' ? 1 : 0, trash: trash.length, linked: docs.some((d) => d.id === 'vdoc_lease') ? 1 : 0 },
    categories, settings, ocr_connected: options.ocr ?? true, can_write: role !== 'viewer', can_manage: role === 'owner', max_upload_bytes: 26_214_400,
    accepted: 'PDF, images, text', accepted_extensions: ['.pdf', '.png', '.jpg', '.jpeg', '.txt'], link_types: ['vehicle', 'person', 'business', 'purchase'], reminder_window_days: 30,
  });
  const detail = (doc: Json) => ({ ...doc, shared_with: doc.id === 'vdoc_passport' ? shared : [], links: [], extractions: doc.id === 'vdoc_receipt' && extraction ? [extraction] : [],
    activity: [{ id: 'vlog_1', document_id: doc.id, actor: 'user_local', action: 'upload', detail: {}, created_at: now }], can_share: doc.access_class === 'private' && doc.is_owner, can_extract: true });

  await page.route('http://localhost:8000/api/v1/documents**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\/v1\/documents/, '') || '/';
    const method = request.method();
    const body = request.postData() && (request.headers()['content-type'] ?? '').includes('json') ? request.postDataJSON() : null;
    calls.push({ method, path, query: url.search, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const error = (status: number, code: string, message: string, details: Json[] = []) => json({ error: { code, message, correlation_id: 'corr_e2e', details } }, status);
    if (role === 'viewer' || options.metaStatus === 403) return error(403, 'forbidden', 'The document vault needs a member or owner role in this workspace. Viewers do not see documents. Ask a workspace owner.');
    if (options.metaStatus === 500 && path === '/meta') return error(500, 'internal_error', 'Database unavailable');
    if (path === '/meta') return json(meta());
    if (path === '/review') return json(extraction && extraction.status === 'pending' ? [{ extraction, document: receipt }] : []);
    if (path === '/people') return json([{ user_id: 'user_amina' }, { user_id: 'user_omar' }]);
    if (path === '/devices' && method === 'GET') return json(devices);
    if (path === '/devices' && method === 'POST') {
      const device = { id: 'vdev_1', name: (body as Json).name, hint: 'k9Qz', scopes: ['documents'], status: 'active', last_seen_at: null, last_error: null, upload_count: 0, created_at: now };
      devices.push(device);
      return json({ device, token: 'anumdoc.example.example.example.vdev_1.examplesecretexampleexampleexample', upload_url: '/api/v1/documents/device/upload' }, 201);
    }
    if (path === '/settings' && method === 'PUT') { Object.assign(settings, body); return json(settings); }
    if (path === '/categories' && method === 'POST') { const made = { id: 'vcat_medical1', label: (body as Json).label, built_in: false, reads: null, identity: false }; categories.push(made as never); return json(made, 201); }
    if (path === '/' && method === 'GET') {
      const status = url.searchParams.get('status') ?? 'active';
      let rows = status === 'deleted' ? trash : docs;
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      if (q) rows = rows.filter((d) => String(d.title).toLowerCase().includes(q) || String(d.number_masked ?? '').includes(q));
      const category = url.searchParams.get('category');
      if (category) rows = rows.filter((d) => d.category === category);
      const expiry = url.searchParams.get('expiry');
      if (expiry) rows = rows.filter((d) => d.expiry_state === (expiry === 'soon' ? 'soon' : 'expired'));
      const access = url.searchParams.get('access_class');
      if (access) rows = rows.filter((d) => d.access_class === access);
      if (url.searchParams.get('sort') === 'expiry') rows = rows.filter((d) => d.expiry_date && String(d.expiry_date) >= day(0)).sort((a, b) => String(a.expiry_date).localeCompare(String(b.expiry_date)));
      rows = rows.map((d) => ({ ...d, links_count: d.id === 'vdoc_lease' ? 2 : 0, first_link: d.id === 'vdoc_lease' ? { id: 'vlink_1', document_id: d.id, target_type: 'vehicle', target_id: 'KSA 4823', label: 'Example van', status: 'confirmed', created_by: 'user_local', created_at: now } : null,
        review_pending: d.id === 'vdoc_receipt' && extraction?.status === 'pending' }));
      const cursor = url.searchParams.get('cursor');
      const start = cursor ? Number(cursor) : 0;
      const limit = Number(url.searchParams.get('limit') ?? 50);
      return json({ items: rows.slice(start, start + limit), next_cursor: start + limit < rows.length ? String(start + limit) : null });
    }
    if (path === '/' && method === 'POST') {
      const name = url.searchParams.get('name') ?? 'file';
      if (name === 'duplicate.pdf') return error(409, 'conflict', 'This exact file is already in the vault as Passport. Open it, or add it anyway as a separate document.', [{ type: 'duplicate', message: 'vdoc_passport' }, { type: 'duplicate_status', message: 'active' }]);
      const made = vaultDocument('vdoc_new', { title: url.searchParams.get('title'), category: url.searchParams.get('category'), access_class: url.searchParams.get('access_class'),
        expiry_date: null, expiry_state: 'none', days_left: null, number_masked: null, file: { name, content_type: 'application/pdf', size_bytes: 300, version: 1, kind: 'pdf', previewable: true, scan_status: 'clean' } });
      docs.unshift(made);
      return json(made, 201);
    }
    const extract = /^\/extractions\/(\w+)\/(confirm|reject)$/.exec(path);
    if (extract && extraction) {
      extraction = { ...extraction, status: extract[2] === 'confirm' ? 'confirmed' : 'rejected' };
      if (extract[2] === 'confirm') Object.assign(receipt, { receipt: { ...(body as Json).fields as Json, items: [], confirmed_by: 'user_local', confirmed_at: now, extraction_id: 'vext_1' } });
      return json(extract[2] === 'confirm' ? receipt : extraction);
    }
    const one = /^\/(vdoc_\w+)(?:\/(.*))?$/.exec(path);
    if (one) {
      const [, id, rest] = one;
      const doc = [...docs, ...trash].find((d) => d.id === id);
      if (!doc) return error(404, 'not_found', 'Document not found. It may have been deleted, or it is private to someone else.');
      if (!rest && method === 'GET') return json(detail(doc));
      if (!rest && method === 'PATCH') { Object.assign(doc, body, { row_version: Number(doc.row_version) + 1 }); return json(doc); }
      if (!rest && method === 'DELETE') {
        if (url.searchParams.get('permanent')) { trash.splice(trash.indexOf(doc), 1); return route.fulfill({ status: 204 }); }
        docs.splice(docs.indexOf(doc), 1); Object.assign(doc, { status: 'deleted', deleted_at: now, purge_after: '2026-12-04T09:00:00.000Z' }); trash.push(doc); return json(doc);
      }
      if (rest === 'restore') { trash.splice(trash.indexOf(doc), 1); Object.assign(doc, { status: 'active', deleted_at: null, purge_after: null }); docs.push(doc); return json(doc); }
      if (rest === 'number') return json({ number: 'A1234567' });
      if (rest === 'versions' && method === 'GET') return json({ current_version: 2, current_size_bytes: 2048, total: versions.length, items: versions });
      if (rest?.startsWith('versions/') && method === 'DELETE') { const index = versions.findIndex((v) => v.id === rest.split('/')[1]); if (index < 0) return error(404, 'not_found', 'Version not found'); versions.splice(index, 1); return route.fulfill({ status: 204 }); }
      if (rest === 'link') return json({ url: `/api/v1/documents/links/token_${id}`, expires_at: now }, 201);
      if (rest === 'access' && method === 'POST') { shared.push({ document_id: id, user_id: (body as Json).user_id, granted_by: 'user_local', granted_at: now }); return json(shared, 201); }
      if (rest === 'links' && method === 'POST') return json({ id: 'vlnk_1', document_id: id, ...(body as Json), status: 'confirmed', created_by: 'user_local', created_at: now }, 201);
      if (rest === 'extract') return json({ ...extraction, id: 'vext_2', document_id: id, status: 'pending' }, 201);
      if (rest === 'content') return route.fulfill({ status: 200, contentType: 'application/octet-stream', body: 'bytes' });
    }
    if (path.startsWith('/links/token_')) return route.fulfill({ status: 200, contentType: 'image/png', body: logoPng() });
    return error(404, 'not_found', 'Not mocked');
  });
  return { calls, docs };
}
