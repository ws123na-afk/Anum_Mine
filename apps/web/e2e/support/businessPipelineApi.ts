import type { Page, Route } from '@playwright/test';
import { mockBusiness } from './businessApi';

// Business Prospecting and Outreach (docs/business.md#prospecting-pipeline, #outreach): a small stateful
// stand-in for /api/v1/business/pipeline and /api/v1/business/outreach, layered over mockBusiness.
// Two products with target profiles, one discovery run, one profiled lead, one approved draft that is
// queued, approved with its payload hash and held because sending is off, and a reply that stops it.
type Json = Record<string, unknown>;
const now = () => new Date().toISOString();
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });
export const SEND_HASH = 'c4'.repeat(32);

export interface PipelineMock { calls: { method: string; path: string; body: unknown }[] }

export async function mockPipeline(page: Page, options: { role?: 'owner' | 'member'; status?: 500 } = {}): Promise<PipelineMock> {
  await mockBusiness(page, { role: options.role, webSearch: true });
  const owner = (options.role ?? 'owner') === 'owner';
  const mock: PipelineMock = { calls: [] };
  const products = [
    { id: 'bprod_tyre', kind: 'product', name: 'TyrePulse', name_ar: '', summary: 'Fleet tyre management.', maturity: 'live', integrations: [], created_by: 'u', created_at: now(), updated_at: now(), offers: [], active_offer: null, versions: 0 },
    { id: 'bprod_voice', kind: 'product', name: 'VoiceQuote', name_ar: '', summary: 'Quotes by voice.', maturity: 'pilot', integrations: [], created_by: 'u', created_at: now(), updated_at: now(), offers: [], active_offer: null, versions: 0 },
  ];
  const targets: Json[] = [
    { product_id: 'bprod_tyre', product_name: 'TyrePulse', saved: false, industries: [], company_sizes: [], regions: ['Saudi Arabia', 'United Arab Emirates'], roles: [], keywords: [], disqualifiers: [], trigger_events: [], company_size_note: '', value_proposition: 'Fleet tyre management.', value_proposition_ar: '', updated_by: null, updated_at: null },
    { product_id: 'bprod_voice', product_name: 'VoiceQuote', saved: false, industries: [], company_sizes: [], regions: ['Saudi Arabia'], roles: [], keywords: [], disqualifiers: [], trigger_events: [], company_size_note: '', value_proposition: 'Quotes by voice.', value_proposition_ar: '', updated_by: null, updated_at: null },
  ];
  const signals: Json[] = [
    { code: 'FOUND_IN_SEARCH', label: 'Found in a market search', description: 'The company appeared in a web search.', category: 'discovery', value_type: 'text', cost_note: 'Included.', criterion: null, status: 'proposed', decided_by: null, decided_at: null },
    { code: 'TECH_STACK', label: 'Website platform and tools', description: 'What the site is built on.', category: 'profile', value_type: 'text', cost_note: 'Free.', criterion: 'project_scale', status: 'proposed', decided_by: null, decided_at: null },
    { code: 'PUBLISHED_BUSINESS_EMAIL', label: 'Business address published on the site', description: 'A role address on the company domain.', category: 'channel', value_type: 'text', cost_note: 'Free.', criterion: 'reachable_channel', status: 'proposed', decided_by: null, decided_at: null },
  ];
  const settings = { fetches_per_day: 100, pages_per_lead: 5, sending_enabled: false, daily_quota: { email: 20, whatsapp: 20 }, max_bounce_rate_pct: 5, follow_up_max: 2, follow_up_min_days: 3,
    paused: false, paused_reason: '', paused_at: null, research_searches_per_day: 30, research_runs_per_day: 10, usage_today: { runs: 0, searches: 0, page_fetches: 0, model_calls: 0 },
    tools: { web_search: true, page_fetch: true }, model_connected: false, can_manage: owner,
    excluded: ['LinkedIn or any browser automation', 'Paid data providers (listings, rankings, enrichment)', 'Reading pages behind a login or a captcha'] };
  const runs: Json[] = [];
  const lead = { id: 'blead_gulf', title: 'Gulf Mixers: TyrePulse', state: 'discovered', account_name: 'Gulf Mixers', domain: 'gulfmixers.example' };
  const observation = { id: 'bobs_1', lead_id: lead.id, account_id: 'bacc_1', run_id: 'brun_2', evidence_id: 'bevi_1', signal_code: 'PUBLISHED_BUSINESS_EMAIL', value_num: null,
    value_text: 'info@gulfmixers.example', url: 'https://gulfmixers.example/', excerpt: 'Published on the company\'s site: info@gulfmixers.example', observed_on: now().slice(0, 10), confidence: 1 };
  const draft = { id: 'bdraft_1', lead_id: lead.id, contact_id: 'bcon_1', channel: 'email', language: 'en', subject: 'A question for Gulf Mixers', body: 'Hello,\n\nWe offer TyrePulse.\n\nReply STOP and I will not write again.',
    status: 'approved', generator: 'template', untrusted_source: false, issues: [], approved_hash: SEND_HASH, approved_by: 'user_local', approved_at: now(), created_by: 'u', created_at: now(), updated_at: now() };
  const outbox: Json[] = [];
  let ready = true;
  const page_ = (items: unknown[]) => ({ items, total: items.length, limit: 25, offset: 0 });
  const estimate = (searches: number, pages: number) => ({ searches, page_fetches: pages, model_calls: 0, estimated_tokens: 0, estimated_cost_usd: 0, searches_left_today: 30, fetches_left_today: 100,
    budget_cost_left_usd: null, budget_tokens_left: null, fits: signals.some((s) => s.status === 'confirmed'), problems: signals.some((s) => s.status === 'confirmed') ? [] : ['No signal is confirmed yet.'], notes: [] });

  await page.route('http://localhost:8000/api/v1/business/products**', (route: Route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(page_(products)) }));
  await page.route(/http:\/\/localhost:8000\/api\/v1\/business\/leads\?/, (route: Route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(page_(
    runs.some((run) => run.kind === 'discover' && run.status === 'succeeded') ? [{ id: lead.id, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', account_id: 'bacc_1', contact_id: null, product_id: 'bprod_tyre',
      title: lead.title, state: lead.state, owner_id: 'user_local', source: 'research', score: {}, score_total: 0, score_known: 0, next_step: '', account_name: 'Gulf Mixers', account_domain: 'gulfmixers.example',
      contact_name: null, product_name: 'TyrePulse', block_reason: null, created_at: now(), updated_at: now() }] : [])) }));
  await page.route(/http:\/\/localhost:8000\/api\/v1\/business\/(pipeline|outreach)(\/.*)?(\?.*)?$/, async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/business', '');
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() as Json : {};
    mock.calls.push({ method, path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (options.status === 500) return json(envelope('internal_error', 'Database unavailable'), 500);

    if (path === '/pipeline/settings' && method === 'GET') return json(settings);
    if (path === '/pipeline/settings' && method === 'PUT') { Object.assign(settings, body); return json(settings); }
    if (path === '/pipeline/targets') return json(targets);
    if (path.startsWith('/pipeline/products/') && method === 'PUT') {
      if (!owner) return json(envelope('forbidden', 'Only workspace owners can do this.'), 403);
      const target = targets.find((item) => item.product_id === path.split('/')[3])!;
      Object.assign(target, body, { saved: true, updated_by: 'user_local', updated_at: now() });
      return json(target);
    }
    if (path === '/pipeline/signals' && method === 'GET') return json(signals);
    if (path === '/pipeline/signals' && method === 'PUT') {
      for (const [code, status] of Object.entries(body.decisions as Json)) Object.assign(signals.find((item) => item.code === code)!, { status, decided_by: 'user_local', decided_at: now() });
      return json(signals);
    }
    if (path === '/pipeline/runs/estimate') {
      const discover = body.kind === 'discover';
      const run = { id: `brun_${runs.length + 1}`, kind: body.kind, status: 'estimated', product_id: body.product_id ?? null,
        plan: discover ? { queries: ['concrete UAE', 'concrete Saudi Arabia'], terms: ['concrete'], places: ['UAE'] } : { lead_ids: body.lead_ids },
        estimate: estimate(discover ? 2 : 0, discover ? 0 : 4), used: { searches: 0, page_fetches: 0, model_calls: 0 }, steps: [], results: {}, stop_requested: false, stop_reason: '',
        confirmed_by: null, confirmed_at: null, finished_at: null, created_by: 'user_local', created_at: now(), updated_at: now() };
      runs.unshift(run);
      return json(run, 201);
    }
    const runMatch = path.match(/^\/pipeline\/runs\/([^/]+)(\/(confirm|stop))?$/);
    if (runMatch) {
      const run = runs.find((item) => item.id === runMatch[1]);
      if (!run) return json(envelope('not_found', 'Run not found'), 404);
      if (runMatch[3] === 'confirm') {
        const discover = run.kind === 'discover';
        Object.assign(run, { status: 'succeeded', confirmed_by: 'user_local', confirmed_at: now(), finished_at: now(), used: { searches: discover ? 2 : 0, page_fetches: discover ? 0 : 2, model_calls: 0 },
          steps: [{ at: now(), step: discover ? 'search' : 'profile', status: 'done', summary: discover ? '3 result(s): 1 new lead(s), 0 already known.' : 'gulfmixers.example: 2 page(s) read, 1 signal(s) recorded.', target: discover ? 'concrete UAE' : 'Gulf Mixers' }],
          results: discover ? { lead_ids: [lead.id], known: 0, junk: 2, possible_duplicates: [] } : { profiled: [lead.id], observations: 1 } });
        if (!discover) lead.state = 'researched';
        return json(run);
      }
      if (runMatch[3] === 'stop') { Object.assign(run, { status: 'stopped', stop_reason: 'Cancelled before it started.' }); return json(run); }
      const profiled = run.kind === 'profile';
      return json({ ...run, leads: run.status === 'estimated' ? [] : [lead],
        evidence: run.status === 'estimated' ? [] : [{ id: 'bevi_1', lead_id: lead.id, account_id: 'bacc_1', url: 'https://gulfmixers.example/', url_key: 'gulfmixers.example', title: 'Gulf Mixers | Concrete fleet', published_on: null,
          excerpt: 'Ready-mix concrete across Dubai.', confidence: 'high', source: 'research', research_run_id: run.id, created_by: 'u', created_at: now(), updated_at: now() }],
        observations: profiled && run.status !== 'estimated' ? [observation] : [] });
    }
    if (path === '/pipeline/runs') return json(page_(runs));
    if (path === `/pipeline/leads/${lead.id}/profile`) {
      return json({ lead_id: lead.id, score_total: 0, score_known: 0,
        product_fits: [{ product_id: 'bprod_tyre', product_name: 'TyrePulse', score: 4, disqualified: false, reasons: ['Industry matches: concrete.', 'In a target region: united arab emirates.'], current: true },
          { product_id: 'bprod_voice', product_name: 'VoiceQuote', score: 0, disqualified: true, reasons: ['Disqualified: mentions concrete.'], current: false }],
        best_fit: { product_id: 'bprod_tyre', product_name: 'TyrePulse', score: 4, disqualified: false, reasons: [], current: true },
        breakdown: ['industry_fit', 'problem_evidence', 'reachable_channel', 'project_scale', 'urgency_confirmed', 'authorized_buyer'].map((criterion) => ({
          criterion, score: null, reason: '', evidence_ids: [], known: false, conversation_only: criterion === 'urgency_confirmed' || criterion === 'authorized_buyer',
          suggestion: criterion === 'reachable_channel' ? { criterion, score: 4, reason: 'The company publishes a business address on its own site.', rule: 'published role address on own domain = 4', observation_ids: ['bobs_1'], evidence_ids: ['bevi_1'] } : null })),
        observations: [observation], facts: [], inferences: [], signal_labels: { PUBLISHED_BUSINESS_EMAIL: 'Business address published on the site' } });
    }

    if (path === '/outreach' && method === 'GET') {
      const count = (status: string) => outbox.filter((item) => item.status === status).length;
      return json({ sending_enabled: settings.sending_enabled, paused: settings.paused, paused_reason: settings.paused_reason, daily_quota: settings.daily_quota, sent_today: { email: 0, whatsapp: 0 },
        waiting: { pending_approval: count('pending_approval'), held: count('held'), scheduled: count('scheduled') },
        metrics: { days: 30, sent: { email: 0, whatsapp: 0 }, sent_total: 0, by_status: {}, replies: { count: 0, of: 0, rate_pct: null }, opt_outs: { count: 0, of: 0, rate_pct: null },
          bounces: { count: 0, of: 0, rate_pct: null }, escalations: 0, opt_out_alarm: false, truncated: false }, channels_connected: [] });
    }
    if (path === '/outreach/ready') return json(page_(ready ? [{ draft, lead_title: lead.title, company: 'Gulf Mixers', contact_name: 'Sales desk', queued_for: [] }] : []));
    if (path === '/outreach/items') return json(page_(outbox));
    if (path === `/outreach/drafts/${draft.id}/queue`) {
      ready = false;
      const item = { id: 'bout_1', lead_id: lead.id, contact_id: 'bcon_1', draft_id: draft.id, channel: 'email', step: 1, follow_up_of: null, recipient: 'info@gulfmixers.example',
        subject: draft.subject, body: draft.body, content_hash: SEND_HASH, status: 'pending_approval', gate_issues: [], approval_id: 'approval_send', send_after: null, sent_at: null, provider_ref: '',
        note: 'Waiting for an owner\'s approval in Approvals.', created_at: now(), updated_at: now(), company: 'Gulf Mixers', contact_name: 'Sales desk', lead_title: lead.title, lead_state: 'qualified', due: false,
        approval: { id: 'approval_send', task_id: 'task_send', action: 'business.send_outreach', risk_level: 'high', status: 'pending', reason: 'Always asks.', created_at: now(), payload_hash: SEND_HASH,
          arguments: { to: 'info@gulfmixers.example', subject: draft.subject, text: draft.body } } };
      outbox.unshift(item);
      return json(item, 201);
    }
    if (path === `/outreach/leads/${lead.id}/replies`) {
      const cancelled = outbox.filter((item) => ['held', 'pending_approval', 'scheduled'].includes(String(item.status))).length;
      outbox.forEach((item) => { if (['held', 'pending_approval', 'scheduled'].includes(String(item.status))) Object.assign(item, { status: 'cancelled', note: 'The lead replied (stop-on-reply).', approval: null }); });
      return json({ id: 'brep_1', lead_id: lead.id, contact_id: 'bcon_1', outbox_id: null, channel: 'email', kind: 'reply', cancelled, source: 'manual', note: 'The lead replied (stop-on-reply).', created_at: now() }, 201);
    }
    if (path === '/outreach/pause') { Object.assign(settings, { paused: body.on, paused_reason: body.on ? body.reason : '' }); return json({ paused: settings.paused, paused_reason: settings.paused_reason, paused_at: null }); }
    return json(envelope('not_found', `Unmocked ${method} ${path}`), 404);
  });

  await page.route('http://localhost:8000/api/v1/approvals/approval_send/approve', async (route) => {
    const body = route.request().postDataJSON() as Json;
    if (body.payload_hash !== SEND_HASH) return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify(envelope('conflict', 'The payload hash does not match.')) });
    Object.assign(outbox[0], { status: 'held', note: 'Approved, but sending is turned off (Business, Outreach settings). Nothing was sent.', approval: null });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      task: { id: 'task_send', title: 'Outreach', prompt: 'Send', status: 'failed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now(), updated_at: now() },
      run: { id: 'run_send', task_id: 'task_send', status: 'failed', result: 'Not sent: sending is turned off.', steps: [] },
      approval: { id: 'approval_send', task_id: 'task_send', action: 'business.send_outreach', risk_level: 'high', status: 'approved', reason: 'Always asks.', created_at: now(), payload_hash: SEND_HASH, arguments: {} },
    }) });
  });
  return mock;
}
