import type { Page } from '@playwright/test';

// The Contacts directory's API (docs/contacts.md#api), mocked with state: people, a person page with linked
// records and channel facts, possible duplicates, do-not-contact marks, merge, rebuild and export. Register
// it after mockWorkspaceApi: Playwright asks the newest route first, and this one falls back for other paths.
type Json = Record<string, unknown>;
const now = '2026-11-10T09:30:00Z';
export const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });

export interface ContactsMockOptions { status?: 403 | 500; role?: 'owner' | 'member'; empty?: boolean; total?: number }

export function person(id: string, name: string, extra: Json = {}): Json {
  return {
    id, name, organisation: '', notes: '', source: 'calls', phones: [], emails: [], sources: ['call_contact'], do_not_contact: [],
    open_proposals: 0, created_by: 'member_a', created_at: now, updated_at: now, ...extra,
  };
}

/** What the import preview answers for the file the specs choose (docs/contacts.md#import-from-a-csv-file). */
export function importPreview(mapping?: Json): Json {
  const chosen = mapping ?? { name: ['Full name'], organisation: 'Company', notes: null, phones: ['Mobile'], emails: ['Email'], do_not_contact: 'Do not contact' };
  const base = { organisation: '', emails: [], do_not_contact: [], target_id: null, matches: [], duplicate_of: null, problems: [] };
  return {
    columns: ['Full name', 'Company', 'Mobile', 'Email', 'Do not contact'], mapping: chosen, suggested: !mapping, fingerprint: 'f'.repeat(64), delimiter: ',',
    counts: { rows: 4, new: 1, match: 1, possible_duplicate: 0, duplicate_in_file: 1, invalid: 1 },
    rows: [
      { ...base, row: 2, name: 'Sara Ahmed', organisation: 'Tyre Co', phones: ['+971501234567'], status: 'match', action: 'update', actions: ['update', 'create', 'skip'], target_id: 'ct_sara',
        matches: [{ id: 'ct_sara', name: 'Sara Ahmed', shared: ['+971501234567'], do_not_contact: ['phone'] }] },
      { ...base, row: 3, name: 'Rana Saleh', phones: ['+971507770000'], do_not_contact: ['email'], status: 'new', action: 'create', actions: ['create', 'skip'] },
      { ...base, row: 4, name: 'R. Saleh', phones: ['+971507770000'], status: 'duplicate_in_file', action: 'skip', actions: ['skip', 'create'], duplicate_of: 3 },
      { ...base, row: 5, name: 'Bad Number', phones: [], status: 'invalid', action: 'skip', actions: ['skip'], problems: ["'12' is not a phone number with its country code (+971...)."], issues: [{ code: 'not_phone', value: '12' }] },
    ],
  };
}

export async function mockContacts(page: Page, options: ContactsMockOptions = {}) {
  const calls: { method: string; path: string; query: string; body: unknown }[] = [];
  const owner = (options.role ?? 'owner') === 'owner';
  let people: Json[] = options.empty ? [] : [
    person('ct_sara', 'Sara Ahmed', {
      organisation: 'Tyre Co', notes: 'Prefers Arabic', phones: [{ kind: 'phone', value: '+971501234567', label: '050 123 4567' }],
      emails: [{ kind: 'email', value: 'sara@tyre.example', label: 'Sara@Tyre.example' }], sources: ['business_contact', 'call_contact'], open_proposals: 1,
    }),
    person('ct_omar', 'Omar Khan', { source: 'whatsapp', phones: [{ kind: 'phone', value: '+966500000001', label: '+966500000001' }], sources: ['whatsapp_contact'],
      do_not_contact: [{ channel: 'whatsapp', reason: 'Asked to stop', set_by: 'owner_a', set_at: now }] }),
    person('ct_huda', 'Huda Ali', { source: 'manual', sources: [], emails: [{ kind: 'email', value: 'huda@example.com', label: 'huda@example.com' }] }),
    person('ct_s_ahmad', 'S. Ahmad', { source: 'business', sources: ['business_contact'], phones: [{ kind: 'phone', value: '+971501234567', label: '+971501234567' }], open_proposals: 1 }),
  ];
  let proposals: Json[] = options.empty ? [] : [{ id: 'prop_1', reason: 'same_phone', matched_value: '+971501234567', status: 'open', created_at: now, decided_by: null, decided_at: null }];
  const dnc: Record<string, Json[]> = { ct_omar: [{ channel: 'whatsapp', reason: 'Asked to stop', set_by: 'owner_a', set_at: now }] };
  const find = (id: string) => people.find((p) => p.id === id);
  const withMarks = (p: Json) => ({ ...p, do_not_contact: dnc[String(p.id)] ?? [], open_proposals: proposals.filter((x) => x.status === 'open' && ((x as Json).a_id === p.id || (x as Json).b_id === p.id || (p.id === 'ct_sara' || p.id === 'ct_s_ahmad'))).length });
  const counts = () => ({ people: people.length, do_not_contact: Object.values(dnc).filter((m) => m.length).length, open_proposals: proposals.filter((p) => p.status === 'open').length, by_source: { calls: 1, whatsapp: 1, business: 1, manual: 1 } });
  const proposalView = (p: Json) => ({ ...p, a: p.status === 'open' || true ? withMarks(find('ct_s_ahmad') ?? person('ct_s_ahmad', 'S. Ahmad')) : null, b: withMarks(find('ct_sara') ?? person('ct_sara', 'Sara Ahmed')) });
  const channels = (p: Json) => {
    const marks = dnc[String(p.id)] ?? [];
    const blocked = (channel: string) => marks.find((m) => m.channel === channel || m.channel === 'all') ?? null;
    return [
      { channel: 'phone', identifiers: (p.phones as Json[]).map((i) => i.value), do_not_contact: blocked('phone'), can_contact: !blocked('phone'),
        facts: [{ source: 'calls', state: 'granted', detail: 'ai_calls', at: now }] },
      { channel: 'email', identifiers: (p.emails as Json[]).map((i) => i.value), do_not_contact: blocked('email'), can_contact: !blocked('email'),
        facts: [{ source: 'business', state: 'granted', detail: 'public_business_channel', at: now }, { source: 'consent_records', state: 'granted', detail: 'contract', at: now }] },
      { channel: 'whatsapp', identifiers: (p.phones as Json[]).map((i) => i.value), do_not_contact: blocked('whatsapp'), can_contact: !blocked('whatsapp'),
        facts: [{ source: 'whatsapp', state: 'customer_wrote', detail: 'conv_1', at: now }, { source: 'suppression', state: 'blocked', detail: 'marketing:+971•••4567', at: now }] },
    ];
  };
  const detail = (p: Json) => ({
    person: withMarks(p),
    links: p.id === 'ct_huda' ? [] : [
      { id: 'link_1', kind: 'call_contact', source_id: 'contact_omar', found: true, title: String(p.name), detail: 'Tyre Co', href: '#calls-contacts?contact=contact_omar', facts: {} },
      { id: 'link_2', kind: 'business_contact', source_id: 'bcont_1', found: true, title: String(p.name), detail: 'Buyer, Tyre Co', href: '#business-accounts?account=bacct_1&contact=bcont_1', facts: {} },
      { id: 'link_3', kind: 'whatsapp_contact', source_id: 'wac_1', found: false, title: '', detail: '', href: '', facts: {} },
    ],
    channels: channels(p),
    proposals: p.id === 'ct_sara' || p.id === 'ct_s_ahmad' ? proposals.filter((x) => x.status === 'open').map(proposalView) : [],
    mail_contacts: p.id === 'ct_sara' ? [{ id: 'mc_1', email: 'sara@tyre.example', name: 'Sara', href: '#email/server' }] : [],
    can_write: true, can_manage: owner,
  });
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (!path.startsWith('/api/v1/contacts')) return route.fallback();
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    calls.push({ method, path, query: url.search, body: request.postData() ? request.postDataJSON() : null });
    if (options.status === 403) return json(envelope('forbidden', 'Contacts hold personal data from Phone calls, Business, WhatsApp and the Email Center, so viewers cannot see them. Ask a workspace owner to make you a member.'), 403);
    if (options.status === 500) return json(envelope('internal_error', 'Database unavailable'), 500);
    if (path === '/api/v1/contacts/import/preview') {
      if (!owner) return json(envelope('forbidden', 'Only workspace owners can do this (clear a do-not-contact mark, delete a person, import or export the directory).'), 403);
      const body = request.postDataJSON() as Json;
      if (!body.file_base64 && String(body.csv).trim().split('\n').length < 2) return json(envelope('validation_error', 'The file has a header row but no people under it.'), 422);
      return json(importPreview(body.mapping as Json | undefined));
    }
    if (path === '/api/v1/contacts/import/apply') {
      const body = request.postDataJSON() as Json;
      const decisions = body.decisions as { action: string }[];
      const created = decisions.filter((d) => d.action === 'create').length, updated = decisions.filter((d) => d.action === 'update').length;
      people = [...people, ...Array.from({ length: created }, (_, i) => person(`ct_imported_${i}`, `Imported ${i + 1}`, { source: 'manual', sources: [] }))];
      return json({ created, updated, skipped: decisions.length - created - updated, marks_added: 1, proposals_created: 1, rows: decisions.length });
    }
    if (path === '/api/v1/contacts/export') return route.fulfill({ status: 200, contentType: 'text/csv', headers: { 'content-disposition': 'attachment; filename="contacts.csv"' }, body: 'name,organisation\n' });
    if (path === '/api/v1/contacts/directory/rebuild') { people = [...people, person('ct_new', 'New Caller', { phones: [{ kind: 'phone', value: '+971509990000', label: '+971509990000' }] })]; return json({ scanned: 1, linked: 1, people_created: 1, proposals_created: 0, complete: true }); }
    if (path === '/api/v1/contacts/proposals' && method === 'GET') { const state = url.searchParams.get('state') ?? 'open'; const items = proposals.filter((p) => p.status === state).map(proposalView); return json({ items, total: items.length, can_write: true }); }
    const dismiss = path.match(/^\/api\/v1\/contacts\/proposals\/([^/]+)\/dismiss$/);
    if (dismiss) { proposals = proposals.map((p) => (p.id === dismiss[1] ? { ...p, status: 'dismissed', decided_by: 'owner_a', decided_at: now } : p)); return json({ items: [], total: 1, can_write: true }); }
    if (path === '/api/v1/contacts' && method === 'GET') {
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const dncFilter = url.searchParams.get('dnc');
      const offset = Number(url.searchParams.get('offset') ?? 0);
      const limit = Number(url.searchParams.get('limit') ?? 25);
      let shown = people.map(withMarks).filter((p) => !q || String(p.name).toLowerCase().includes(q) || (p.phones as Json[]).some((i) => String(i.value).includes(q)) || (p.emails as Json[]).some((i) => String(i.value).includes(q)));
      if (dncFilter === 'any') shown = shown.filter((p) => (p.do_not_contact as Json[]).length > 0);
      if (dncFilter === 'none') shown = shown.filter((p) => (p.do_not_contact as Json[]).length === 0);
      const total = options.total ?? shown.length;
      const items = offset > 0 ? Array.from({ length: Math.min(limit, total - offset) }, (_, i) => person(`ct_more_${offset + i}`, `Person ${offset + i}`)) : shown.slice(0, limit);
      return json({ items, total, counts: counts(), can_write: true, can_manage: owner, can_export: owner });
    }
    if (path === '/api/v1/contacts' && method === 'POST') {
      const body = request.postDataJSON() as Json;
      const created = person('ct_created', String(body.name), { source: 'manual', sources: [], organisation: body.organisation ?? '', notes: body.notes ?? '',
        phones: (body.phones as string[]).map((v) => ({ kind: 'phone', value: v.startsWith('0') ? `+971${v.replace(/\D/g, '').slice(1)}` : `+${v.replace(/\D/g, '')}`, label: v })),
        emails: (body.emails as string[]).map((v) => ({ kind: 'email', value: v.toLowerCase(), label: v })) });
      people = [created, ...people];
      return json(detail(created), 201);
    }
    const one = path.match(/^\/api\/v1\/contacts\/([^/]+)$/);
    if (one) {
      const found = find(one[1]);
      if (!found) return json(envelope('not_found', 'Person not found'), 404);
      if (method === 'GET') return json(detail(found));
      if (method === 'PATCH') { const body = request.postDataJSON() as Json; Object.assign(found, { name: body.name ?? found.name, organisation: body.organisation ?? found.organisation, notes: body.notes ?? found.notes, updated_at: now }); return json(detail(found)); }
      if (method === 'DELETE') { people = people.filter((p) => p.id !== one[1]); return route.fulfill({ status: 204 }); }
    }
    const merge = path.match(/^\/api\/v1\/contacts\/([^/]+)\/merge$/);
    if (merge) { const body = request.postDataJSON() as Json; const keep = find(merge[1]); const other = find(String(body.other_id)); if (!keep || !other) return json(envelope('not_found', 'The other person was not found'), 404);
      keep.sources = [...new Set([...(keep.sources as string[]), ...(other.sources as string[])])]; people = people.filter((p) => p.id !== other.id); proposals = proposals.map((p) => ({ ...p, status: 'merged', decided_by: 'owner_a', decided_at: now })); return json(detail(keep)); }
    const mark = path.match(/^\/api\/v1\/contacts\/([^/]+)\/do-not-contact(?:\/([a-z]+))?$/);
    if (mark && method === 'PUT') { const body = request.postDataJSON() as Json; const list = dnc[mark[1]] ?? (dnc[mark[1]] = []); const existing = list.find((m) => m.channel === body.channel);
      if (existing) { existing.reason = body.reason; return json({ channel: body.channel, reason: body.reason, set_by: 'user_local', set_at: now }); }
      list.push({ channel: body.channel, reason: body.reason, set_by: 'user_local', set_at: now }); return json({ channel: body.channel, reason: body.reason, set_by: 'user_local', set_at: now }, 201); }
    if (mark && method === 'DELETE') { if (!owner) return json(envelope('forbidden', 'Only workspace owners can do this (clear a do-not-contact mark, delete a person, export the directory).'), 403);
      dnc[mark[1]] = (dnc[mark[1]] ?? []).filter((m) => m.channel !== mark[2]); return route.fulfill({ status: 204 }); }
    return json(envelope('not_found', `No mock for ${method} ${path}`), 404);
  });
  return { calls, state: { people: () => people, dnc, proposals: () => proposals } };
}
