import type { Page, Route } from '@playwright/test';
import { mockWorkspaceApi } from './workspaceApi';

// Business, the Accounts tab (docs/business.md#accounts-and-contacts): a small stateful stand-in for
// /api/v1/business/{setup,accounts,contacts,portal} that follows the API's rules: one account per website
// domain and one contact per e-mail (409), deletions and clearing a do-not-contact mark for owners only (403),
// an account with leads, tickets or invoices is kept (409), the list sorts and pages on the server, the export
// is a CSV file. Starts with the accounts given in `accounts` (none by default).
type Json = Record<string, unknown>;
const now = () => new Date().toISOString();
const scope = { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' };
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });
const MANAGE_DENIED = 'Only workspace owners can do this (the catalog, research caps, deletions and removing a do-not-contact mark).';

export interface AccountsMock { calls: { method: string; path: string; body: unknown; query?: string }[]; accounts: Json[]; contacts: Json[] }

export interface SeedAccount { name: string; city?: string; country?: string; industry?: string; website?: string | null; inUse?: 'leads' | 'tickets' | 'invoices'; contacts?: { name: string; email?: string | null; dnc?: boolean }[] }

export async function mockBusinessAccounts(page: Page, options: { role?: 'owner' | 'member'; accounts?: SeedAccount[]; failListOnce?: boolean } = {}): Promise<AccountsMock> {
  await mockWorkspaceApi(page);
  const role = options.role ?? 'owner';
  const mock: AccountsMock = { calls: [], accounts: [], contacts: [] };
  let counter = 0;
  const id = (prefix: string) => `${prefix}_${++counter}`;
  const domainOf = (website: unknown) => (typeof website === 'string' && website ? website.replace(/^https?:\/\//, '').replace(/\/.*$/, '').toLowerCase() : null);
  const account = (seed: SeedAccount) => {
    const row: Json = { id: id('bacct'), ...scope, name: seed.name, name_key: seed.name.toLowerCase(), website: seed.website ?? null, domain: domainOf(seed.website), industry: seed.industry ?? '', country: seed.country ?? '', city: seed.city ?? '',
      notes: '', source: 'manual', owner_id: 'user_local', do_not_contact: false, dnc_reason: '', created_by: 'user_local', created_at: now(), updated_at: now(), in_use: seed.inUse ?? null };
    mock.accounts.push(row);
    for (const c of seed.contacts ?? []) contact(String(row.id), { name: c.name, email: c.email ?? null, phone: null, profile_url: null, role_title: '', consent_basis: 'existing_relationship', consent_note: '' }, c.dnc ? 'Asked us to stop' : '');
    return row;
  };
  const contact = (accountId: string, fields: Json, dnc = '') => {
    const row: Json = { id: id('bcont'), ...scope, account_id: accountId, role_title: '', profile_url: null, consent_note: '', consent_recorded_at: now(), source: 'manual', created_by: 'user_local', created_at: now(), updated_at: now(),
      do_not_contact: Boolean(dnc), dnc_reason: dnc, opted_out: [], ...fields };
    mock.contacts.push(row);
    return row;
  };
  const contactView = (row: Json, owner: Json) => ({ ...row, block_reason: row.do_not_contact ? 'Do not contact' : owner.do_not_contact ? 'The account is do not contact' : null, contactable: !row.do_not_contact && !owner.do_not_contact && row.consent_basis !== 'unknown' });
  const detail = (row: Json) => {
    const { in_use: _inUse, ...clean } = row;
    return { ...clean, contacts: mock.contacts.filter((c) => c.account_id === row.id).sort((a, b) => String(a.name).localeCompare(String(b.name))).map((c) => contactView(c, row)), leads: [] };
  };
  const listRow = (row: Json) => { const { in_use: inUse, ...clean } = row; return { ...clean, contacts: mock.contacts.filter((c) => c.account_id === row.id).length, open_leads: inUse === 'leads' ? 2 : 0 }; };
  for (const seed of options.accounts ?? []) account(seed);
  let failList = Boolean(options.failListOnce);

  await page.route('http://localhost:8000/api/v1/business/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/business', '');
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() as Json : {};
    mock.calls.push({ method, path, body, query: url.search });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const denied = () => json(envelope('forbidden', MANAGE_DENIED), 403);
    const parts = path.split('/').filter(Boolean);

    if (path === '/setup') {
      return json({
        permissions: { read: true, write: true, manage: role === 'owner', decide: role === 'owner' }, sending_enabled: false, web_search: false, model_connected: false,
        settings: { research_runs_per_day: 10, research_searches_per_day: 30, updated_by: null, updated_at: null }, usage: { runs_today: 0, searches_today: 0 },
        criteria: ['industry_fit', 'problem_evidence', 'reachable_channel', 'project_scale', 'urgency_confirmed', 'authorized_buyer'], conversation_criteria: ['authorized_buyer', 'urgency_confirmed'],
        transitions: {}, contactable_bases: ['consent_given', 'inbound_inquiry', 'existing_relationship', 'referral', 'public_business_channel'],
      });
    }
    if (path === '/products') return json({ items: [], total: 0, limit: 100, offset: 0 });
    if (parts[0] === 'portal') {
      if (parts[1] === 'access' && method === 'GET') return json([]);
      if (parts[1] === 'decisions') return json([]);
      if (parts[1] === 'sign-in' && method === 'GET') return json({ enabled: false, email_configured: true, sign_in_path: '/portal#signin=tenant_local.workspace_foundation', codes_last_30_days: 0, sign_ins_last_30_days: 0 });
      return denied();
    }

    const ordered = () => {
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const sort = url.searchParams.get('sort') ?? 'name', desc = url.searchParams.get('desc') === 'true';
      const key = sort === 'place' ? 'city' : sort === 'industry' ? 'industry' : sort === 'updated' ? 'updated_at' : sort === 'created' ? 'created_at' : 'name';
      if (!['name', 'place', 'industry', 'updated', 'created'].includes(sort)) return null;
      const rows = mock.accounts.filter((row) => !q || [row.name, row.domain, row.industry, row.city, row.country].some((v) => String(v ?? '').toLowerCase().includes(q)));
      rows.sort((a, b) => String(a[key]).localeCompare(String(b[key])) || String(a.id).localeCompare(String(b.id)));
      return desc ? rows.reverse() : rows;
    };
    if (path === '/accounts' && method === 'GET') {
      if (failList) { failList = false; return json(envelope('internal_error', 'Database unavailable'), 500); }
      const rows = ordered();
      if (!rows) return json(envelope('invalid', 'Sort the accounts by one of: name, place, industry, updated, created.'), 422);
      const offset = Number(url.searchParams.get('offset') ?? 0), limit = Number(url.searchParams.get('limit') ?? 25);
      return json({ items: rows.slice(offset, offset + limit).map(listRow), total: rows.length, limit, offset });
    }
    if (path === '/accounts/export' && method === 'GET') {
      const rows = ordered() ?? [];
      const lines = ['name,website,domain,industry,city,country,do_not_contact,dnc_reason,contacts,open_leads,notes,created_at,updated_at', ...rows.map((row) => [row.name, row.website ?? '', row.domain ?? '', row.industry, row.city, row.country, row.do_not_contact, row.dnc_reason, listRow(row).contacts, listRow(row).open_leads, row.notes, row.created_at, row.updated_at].join(','))];
      return route.fulfill({ status: 200, contentType: 'text/csv; charset=utf-8', headers: { 'content-disposition': 'attachment; filename="anum-accounts.csv"' }, body: `${lines.join('\r\n')}\r\n` });
    }
    if (path === '/accounts' && method === 'POST') {
      const domain = domainOf(body.website);
      if (domain && mock.accounts.some((row) => row.domain === domain)) return json(envelope('conflict', `An account with the website ${domain} already exists.`), 409);
      const row = account({ name: String(body.name), city: String(body.city ?? ''), country: String(body.country ?? ''), industry: String(body.industry ?? ''), website: (body.website as string | null) ?? null });
      row.notes = body.notes ?? '';
      return json(detail(row), 201);
    }
    if (parts[0] === 'accounts' && parts[1]) {
      const row = mock.accounts.find((item) => item.id === parts[1]);
      if (!row) return json(envelope('not_found', 'Account not found'), 404);
      if (parts.length === 2 && method === 'GET') return json(detail(row));
      if (parts.length === 2 && method === 'PATCH') {
        const domain = 'website' in body ? domainOf(body.website) : row.domain;
        const other = mock.accounts.find((item) => item.id !== row.id && domain && item.domain === domain);
        if (other) return json(envelope('conflict', `Another account (${other.name}) has the website ${domain}.`), 409);
        Object.assign(row, body, { domain, updated_at: now() });
        return json(detail(row));
      }
      if (parts.length === 2 && method === 'DELETE') {
        if (role !== 'owner') return denied();
        if (row.in_use === 'leads') return json(envelope('conflict', 'This account has leads or opportunities. Delete those first.'), 409);
        if (row.in_use === 'tickets') return json(envelope('conflict', 'This account has support tickets, which are kept as records. Close them instead of deleting the account.'), 409);
        if (row.in_use === 'invoices') return json(envelope('conflict', 'This account has invoices, which are kept as records. Delete the drafts and void the rest; an account with invoices is not deleted.'), 409);
        mock.accounts.splice(mock.accounts.indexOf(row), 1);
        mock.contacts = mock.contacts.filter((c) => c.account_id !== row.id);
        return route.fulfill({ status: 204 });
      }
      if (parts[2] === 'do-not-contact' && method === 'POST') {
        if (!body.on && role !== 'owner') return denied();
        Object.assign(row, { do_not_contact: Boolean(body.on), dnc_reason: body.on ? String(body.reason) : '', updated_at: now() });
        return json(detail(row));
      }
      if (parts[2] === 'contacts' && method === 'POST') {
        if (body.email && mock.contacts.some((c) => c.email === body.email)) return json(envelope('conflict', 'A contact with this e-mail address already exists.'), 409);
        contact(String(row.id), body);
        return json(detail(row), 201);
      }
    }
    if (parts[0] === 'contacts' && parts[1]) {
      const row = mock.contacts.find((item) => item.id === parts[1]);
      if (!row) return json(envelope('not_found', 'Contact not found'), 404);
      const owner = mock.accounts.find((item) => item.id === row.account_id)!;
      if (parts.length === 2 && method === 'PATCH') {
        if (body.email && body.email !== row.email && mock.contacts.some((c) => c.email === body.email)) return json(envelope('conflict', 'A contact with this e-mail address already exists.'), 409);
        Object.assign(row, body, { updated_at: now() });
        return json(contactView(row, owner));
      }
      if (parts.length === 2 && method === 'DELETE') {
        if (role !== 'owner') return denied();
        mock.contacts.splice(mock.contacts.indexOf(row), 1);
        return route.fulfill({ status: 204 });
      }
      if (parts[2] === 'do-not-contact' && method === 'POST') {
        if (!body.on && role !== 'owner') return denied();
        Object.assign(row, { do_not_contact: Boolean(body.on), dnc_reason: body.on ? String(body.reason) : '', updated_at: now() });
        return json(contactView(row, owner));
      }
    }
    return json(envelope('not_found', `No mock for ${method} ${path}`), 404);
  });
  return mock;
}
