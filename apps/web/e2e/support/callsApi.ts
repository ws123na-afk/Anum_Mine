// A stateful fake of the Phone calls API (/api/v1/calls, docs/calls.md) for the e2e specs: contacts
// with search, favourites and paging, the call log with filters and paging, notes, the AI summary and
// follow-up task, AI call consent, opt-outs, AI call requests waiting for approval and the provider.
// Plainly fake, low-entropy fixtures only.
import type { Page, Route } from '@playwright/test';
import { mockWorkspaceApi } from './workspaceApi';

export interface CallsMockOptions {
  role?: 'owner' | 'member' | 'viewer';
  /** A connected Twilio account (browser calls, recording and AI calls on). */
  provider?: boolean;
  /** Start with no contacts and no calls. */
  empty?: boolean;
  /** The call log answers 503 until this many requests have failed. */
  failLog?: number;
  /** The workspace has no real model. */
  noModel?: boolean;
  /** Extra contacts (paging shows past 25). */
  extraContacts?: number;
}

export interface CallsCalls {
  logged: Record<string, unknown>[];
  patches: Record<string, unknown>[];
  summaries: string[];
  tasks: string[];
  contacts: Record<string, unknown>[];
  consents: string[];
  aiRequests: Record<string, unknown>[];
  providerSaves: Record<string, unknown>[];
  prepared: Record<string, unknown>[];
  listQueries: string[];
}

const now = () => new Date().toISOString();

export async function mockCallsApi(page: Page, options: CallsMockOptions = {}): Promise<{ calls: CallsCalls }> {
  await mockWorkspaceApi(page);
  const role = options.role ?? 'owner';
  const calls: CallsCalls = { logged: [], patches: [], summaries: [], tasks: [], contacts: [], consents: [], aiRequests: [], providerSaves: [], prepared: [], listQueries: [] };
  let failLog = options.failLog ?? 0;
  let provider = options.provider ?? false;
  const contact = (id: string, name: string, phone: string, extra: Record<string, unknown> = {}) => ({
    id, name, phone, phone_normalized: phone, company: null, email: null, notes: null, favourite: false, source: 'manual', language: 'auto', ai_consent: null,
    created_by: 'user_local', created_at: '2026-10-10T08:00:00Z', updated_at: '2026-10-10T08:00:00Z', opted_out: false, can_edit: role !== 'viewer', ...extra,
  });
  const contacts: Record<string, any>[] = options.empty ? [] : [
    contact('contact_sara', 'Sara Example', '+971501234567', { company: 'Al Noor Trading', favourite: true }),
    contact('contact_omar', 'عمر المثال', '+971502223333', { ai_consent: { granted_at: '2026-10-11T09:00:00Z', recorded_by: 'user_local', method: 'written', note: null }, language: 'ar' }),
    ...Array.from({ length: options.extraContacts ?? 0 }, (_, i) => contact(`contact_${i}`, `Driver ${String(i).padStart(2, '0')}`, `+97150000${String(i).padStart(4, '0')}`)),
  ];
  const call = (id: string, extra: Record<string, unknown>) => ({
    id, user_id: 'user_local', contact_id: null, contact_name: null, phone: null, phone_normalized: null, peer_user_id: null, direction: 'outbound', kind: 'phone',
    status: 'logged', outcome: 'answered', started_at: '2026-10-14T09:00:00Z', ended_at: null, duration_seconds: 95, notes: null, summary: null, follow_up: null,
    summary_provenance: null, provider: null, recording_file_id: null, recording_consent: null, recording_expires_at: null, transcript: null, ai_request_id: null,
    cost_cents: null, created_at: '2026-10-14T09:00:00Z', updated_at: '2026-10-14T09:00:00Z', can_edit: role !== 'viewer', ...extra,
  });
  const log: Record<string, any>[] = options.empty ? [] : [
    call('call_quote', { contact_id: 'contact_sara', contact_name: 'Sara Example', phone: '+971501234567', notes: 'Wants a quote for 40 tyres by Friday.' }),
    call('call_busy', { phone: '+97142223333', outcome: 'busy', direction: 'inbound', started_at: '2026-10-13T15:00:00Z', duration_seconds: 0 }),
  ];
  const optOuts: Record<string, any>[] = [];
  const aiCalls: Record<string, any>[] = [];
  const caps = () => ({
    browser_calls: provider, recording: provider, transcription: false, ai_calls: provider && !options.noModel, inbound_ai: false,
    problem: provider ? null : 'No voice provider is connected. A workspace owner connects a Twilio account under Calls > Provider.',
    ai_problem: provider ? (options.noModel ? 'AI calls need a real model. Connect one in Settings > Model.' : null) : 'No voice provider is connected.',
    cost_per_minute_cents: provider ? 5 : null,
    recording_notice: provider ? { en: 'Please note: this call is recorded by ANUM Tyres.', ar: 'تنبيه: هذه المكالمة مسجلة لدى ANUM Tyres.' } : null,
  });
  const providerView = () => provider ? {
    connected: true, provider: 'twilio', account_sid: 'ACexample0001', api_key_sid: 'SKexample0001', twiml_app_sid: 'APexample0001', from_number: '+97145550100',
    credential_hint: '…0001', has_api_key_secret: true, business_name: 'ANUM Tyres', greeting: null, recording_enabled: true, transcription_enabled: false,
    recording_retention_days: 30, ai_calls_enabled: true, inbound_ai_enabled: false, ai_max_seconds: 180, ai_max_cost_cents: 100, cost_per_minute_cents: 5,
    last_check_status: 'ok', last_check_detail: 'Connected to Twilio account ANUM test.', last_checked_at: now(),
    voice_url: 'https://api.example.test/api/v1/calls/webhooks/twilio/route0001/voice', status_url: 'https://api.example.test/api/v1/calls/webhooks/twilio/route0001/status',
    webhooks_ready: true, problem: null, limits: { ai_max_seconds: 600, ai_max_cost_cents: 500 },
  } : { connected: false, provider: 'twilio', account_sid: null, api_key_sid: null, twiml_app_sid: null, from_number: null, credential_hint: null, has_api_key_secret: false,
    business_name: null, greeting: null, recording_enabled: false, transcription_enabled: false, recording_retention_days: 30, ai_calls_enabled: false, inbound_ai_enabled: false,
    ai_max_seconds: 180, ai_max_cost_cents: 100, cost_per_minute_cents: 5, last_check_status: null, last_check_detail: null, last_checked_at: null,
    voice_url: null, status_url: null, webhooks_ready: true, problem: 'No voice provider is connected.', limits: { ai_max_seconds: 600, ai_max_cost_cents: 500 } };

  await page.route('http://localhost:8000/api/v1/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const error = (status: number, message: string) => json({ error: { code: 'error', message } }, status);
    const body = () => (request.postDataJSON() ?? {}) as Record<string, any>;
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role, active: true });
    if (!path.startsWith('/api/v1/calls')) return route.fallback();
    if (role === 'viewer') return error(403, 'Phone calls are not part of the viewer role. Ask a workspace owner to make you a member.');
    const page_ = <T,>(items: T[]) => {
      const offset = Number(url.searchParams.get('offset') ?? 0), limit = Number(url.searchParams.get('limit') ?? 25);
      return { items: items.slice(offset, offset + limit), total: items.length, offset, limit };
    };
    const q = (url.searchParams.get('q') ?? '').toLowerCase();
    if (path === '/api/v1/calls/setup') return json({ can_read: true, can_log: true, can_manage: role === 'owner', can_create_tasks: true, model_ready: !options.noModel, provider_connected: provider, capabilities: caps(), transcription_configured: false });

    if (path === '/api/v1/calls/contacts' && method === 'GET') {
      let items = contacts.filter((c) => !q || `${c.name} ${c.phone} ${c.company ?? ''}`.toLowerCase().includes(q));
      if (url.searchParams.get('favourites') === 'true') items = items.filter((c) => c.favourite);
      items.sort((a, b) => Number(b.favourite) - Number(a.favourite) || a.name.localeCompare(b.name));
      return json(page_(items));
    }
    if (path === '/api/v1/calls/contacts' && method === 'POST') {
      const input = body();
      calls.contacts.push(input);
      const created = contact(`contact_new_${contacts.length}`, input.name, String(input.phone).replace(/[\s()-]/g, ''), { company: input.company ?? null, email: input.email ?? null, notes: input.notes ?? null });
      contacts.push(created);
      return json(created, 201);
    }
    const contactMatch = /^\/api\/v1\/calls\/contacts\/([^/]+)(\/ai-consent)?$/.exec(path);
    if (contactMatch) {
      const found = contacts.find((c) => c.id === decodeURIComponent(contactMatch[1]));
      if (!found) return error(404, 'Contact not found in this workspace');
      if (contactMatch[2]) {
        if (method === 'PUT') { if (role !== 'owner') return error(403, 'Only a workspace owner can do this.'); calls.consents.push(found.id); found.ai_consent = { granted_at: now(), recorded_by: 'user_local', method: body().method, note: body().note ?? null }; }
        else found.ai_consent = null;
        return json(found);
      }
      if (method === 'GET') return json(found);
      if (method === 'PATCH') { Object.assign(found, body()); return json(found); }
      if (method === 'DELETE') { contacts.splice(contacts.indexOf(found), 1); return route.fulfill({ status: 204 }); }
    }

    if (path === '/api/v1/calls/log' && method === 'GET') {
      calls.listQueries.push(url.search);
      if (failLog > 0) { failLog -= 1; return error(503, 'The database did not answer. Try again in a minute.'); }
      let items = log.filter((c) => !q || `${c.contact_name ?? ''} ${c.phone ?? ''} ${c.notes ?? ''} ${c.summary ?? ''}`.toLowerCase().includes(q));
      for (const key of ['direction', 'outcome', 'contact_id'] as const) { const value = url.searchParams.get(key); if (value) items = items.filter((c) => c[key] === value); }
      items.sort((a, b) => b.started_at.localeCompare(a.started_at));
      return json(page_(items));
    }
    if (path === '/api/v1/calls/log' && method === 'POST') {
      const input = body();
      calls.logged.push(input);
      const person = contacts.find((c) => c.id === input.contact_id) ?? contacts.find((c) => c.phone === input.phone);
      const created = call(`call_new_${log.length}`, {
        contact_id: person?.id ?? null, contact_name: person?.name ?? null, phone: input.phone ?? person?.phone ?? null, direction: input.direction, outcome: input.outcome,
        duration_seconds: input.duration_seconds ?? null, notes: input.notes ?? null, started_at: input.started_at ?? now(),
      });
      log.unshift(created);
      return json(created, 201);
    }
    const callMatch = /^\/api\/v1\/calls\/log\/([^/]+)(\/summarize|\/follow-up|\/follow-up\/task)?$/.exec(path);
    if (callMatch) {
      const found = log.find((c) => c.id === decodeURIComponent(callMatch[1]));
      if (!found) return error(404, "Call not found in this workspace's log");
      const action = callMatch[2];
      if (action === '/summarize') {
        if (options.noModel) return error(409, 'Summaries need a real model. Connect one in Settings > Model; the notes are saved.');
        calls.summaries.push(found.id);
        found.summary = 'Sara wants a quote for 40 tyres by Friday.';
        found.follow_up = found.follow_up?.edited ? found.follow_up : { text: 'Send Sara the tyre quote', due_date: '2026-10-17', task_id: null, edited: false };
        found.summary_provenance = { label: 'ai_generated', source: 'call_notes', provider: 'test', model: 'test-model', generated_at: now(), input_chars: 40, truncated: false };
        return json(found);
      }
      if (action === '/follow-up') {
        if (method === 'PUT') found.follow_up = { text: body().text, due_date: body().due_date ?? null, task_id: found.follow_up?.task_id ?? null, edited: true };
        else found.follow_up = null;
        return json(found);
      }
      if (action === '/follow-up/task') { calls.tasks.push(found.id); found.follow_up = { ...found.follow_up, task_id: 'task_follow' }; return json(found); }
      if (method === 'GET') return json(found);
      if (method === 'PATCH') { calls.patches.push(body()); Object.assign(found, body()); return json(found); }
      if (method === 'DELETE') { log.splice(log.indexOf(found), 1); return route.fulfill({ status: 204 }); }
    }

    if (path === '/api/v1/calls/opt-outs' && method === 'GET') return json(page_(optOuts));
    if (path === '/api/v1/calls/opt-outs' && method === 'POST') {
      const created = { id: `optout_${optOuts.length}`, masked: `${String(body().phone).slice(0, 4)}•••`, source: 'owner', note: body().note ?? null, created_by: 'user_local', created_at: now() };
      optOuts.unshift(created);
      return json(created, 201);
    }
    if (path.startsWith('/api/v1/calls/opt-outs/') && method === 'DELETE') { optOuts.splice(0, 1); return route.fulfill({ status: 204 }); }

    if (path === '/api/v1/calls/provider' && method === 'GET') return role === 'owner' ? json(providerView()) : error(403, 'Only a workspace owner can do this.');
    if (path === '/api/v1/calls/provider' && method === 'PUT') {
      calls.providerSaves.push(body());
      if (body().auth_token === 'wrongtoken0000000') return error(422, 'Not saved: Twilio refused the credentials while checking the account: check the Account SID and auth token.');
      provider = true;
      return json(providerView());
    }
    if (path === '/api/v1/calls/provider/check') return json(providerView());
    if (path === '/api/v1/calls/provider' && method === 'DELETE') { provider = false; return route.fulfill({ status: 204 }); }
    if (path === '/api/v1/calls/provider/calls') {
      calls.prepared.push(body());
      return error(409, 'The test browser has no microphone for provider calls.');
    }

    if (path === '/api/v1/calls/ai-calls' && method === 'GET') return json(page_(aiCalls));
    if (path === '/api/v1/calls/ai-calls' && method === 'POST') {
      const input = body();
      calls.aiRequests.push(input);
      const person = contacts.find((c) => c.id === input.contact_id)!;
      const created = {
        id: `aicall_${aiCalls.length}`, requested_by: 'user_local', contact_id: person.id, contact_name: person.name, phone_masked: '+971•••33', purpose: input.purpose, script: input.script,
        language: input.language, max_seconds: input.max_seconds, max_cost_cents: 10, status: 'pending_approval', approval_id: 'approval_ai', task_id: 'task_ai', call_id: null, error: null,
        decided_by: null, created_at: now(), approval: { id: 'approval_ai', status: 'pending', payload_hash: 'ef'.repeat(32) },
      };
      aiCalls.unshift(created);
      return json(created, 201);
    }
    const cancel = /^\/api\/v1\/calls\/ai-calls\/([^/]+)\/cancel$/.exec(path);
    if (cancel) { const found = aiCalls.find((a) => a.id === cancel[1])!; found.status = 'cancelled'; found.approval = null; return json(found); }
    return error(404, `No fake for ${method} ${path}`);
  });
  return { calls };
}
