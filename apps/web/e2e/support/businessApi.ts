import type { Page, Route } from '@playwright/test';
import { mockWorkspaceApi } from './workspaceApi';

// Business (docs/business.md): a small stateful stand-in for /api/v1/business that follows the API's
// rules for the main path (catalog, lead, score, qualify, draft, opportunity, proposal, acceptance
// through an approval with its payload hash, project and milestones). Nothing is seeded.
type Json = Record<string, unknown>;
const now = () => new Date().toISOString();
const scope = { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' };
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });
export const PAYLOAD_HASH = 'b7'.repeat(32);

export interface BusinessMock { calls: { method: string; path: string; body: unknown; query?: string }[]; approvals: number }

export async function mockBusiness(page: Page, options: { role?: 'owner' | 'member'; status?: 403 | 500; webSearch?: boolean; products?: number; failProducts?: number } = {}): Promise<BusinessMock> {
  await mockWorkspaceApi(page);
  const role = options.role ?? 'owner';
  const mock: BusinessMock = { calls: [], approvals: 0 };
  const products: Json[] = [], leads: Json[] = [], opportunities: Json[] = [], proposals: Json[] = [], projects: Json[] = [];
  let counter = 0;
  const id = (prefix: string) => `${prefix}_${++counter}`;
  // Seeded catalog rows for search and paging tests; `failProducts` makes that many Catalog tab page loads answer 500.
  let productFailures = options.failProducts ?? 0;
  for (let index = 1; index <= (options.products ?? 0); index += 1) {
    const live = index % 3 === 0;
    products.push({ id: `bprod_seed_${String(index).padStart(3, '0')}`, ...scope, kind: index % 2 ? 'service_package' : 'product', name: `Package ${String(index).padStart(3, '0')}${index === 7 ? ' Fleet dashboard' : ''}`,
      name_ar: '', summary: index === 7 ? 'Weekly fleet reporting' : '', maturity: live ? 'live' : 'idea', integrations: [], created_by: 'user_local', created_at: now(), updated_at: now(), offers: [] });
  }
  const page_ = (items: Json[]) => ({ items, total: items.length, limit: 25, offset: 0 });

  const offerView = (offer: Json) => {
    const cost = offer.cost as Json;
    const estimated = Number(cost.labor_hours) * Number(cost.hourly_cost) + Number(cost.external_costs ?? 0);
    return { ...offer, estimated_cost: estimated, margin_at_price: offer.price_min === null ? null : Number(offer.price_min) - estimated, margin_negative: false };
  };
  const productView = (product: Json) => {
    const offers = (product.offers as Json[]).map(offerView);
    return { ...product, offers, active_offer: offers.find((offer) => offer.status === 'active') ?? null, versions: offers.length };
  };
  const leadDetail = (lead: Json) => {
    const evidence = lead.evidence as Json[];
    const score = lead.score as Record<string, Json>;
    const gaps = [!score.industry_fit && 'Score industry fit first.', !score.problem_evidence && 'Score the problem evidence first.', !evidence.length && 'Add at least one source (evidence) for this lead.'].filter(Boolean);
    const transitions: Record<string, string[]> = { discovered: ['researched', 'qualified', 'lost', 'suppressed'], qualified: ['contacted', 'lost', 'suppressed'], offered: ['won', 'lost', 'suppressed'], won: ['suppressed'] };
    const opportunity = opportunities.find((item) => item.lead_id === lead.id);
    return {
      lead: { ...lead, evidence: undefined, drafts: undefined, activities: undefined, account: undefined, contact: undefined },
      account: lead.account, contact: lead.contact, product: products.find((item) => item.id === lead.product_id) ?? null, evidence, inferences: [], activities: lead.activities, drafts: lead.drafts,
      research_runs: [], opportunity_id: opportunity?.id ?? null, qualification_gaps: gaps, allowed_transitions: transitions[String(lead.state)] ?? ['lost'], consent_gap: null,
    };
  };
  const totals = (content: Json) => {
    const subtotal = (content.line_items as Json[]).reduce((sum, item) => sum + Number(item.quantity) * Number(item.unit_price), 0);
    const milestones = (content.milestones as Json[]).reduce((sum, item) => sum + Number(item.amount), 0);
    const issues = [!(content.line_items as Json[]).length && 'Add at least one line item.', !(content.deliverables as string[]).length && 'List the deliverables.',
      !(content.acceptance_criteria as string[]).length && 'State how the client accepts the work (acceptance criteria).',
      (content.milestones as Json[]).length > 0 && milestones !== subtotal && `Milestones add up to ${milestones.toFixed(2)} but the total is ${subtotal.toFixed(2)}.`].filter(Boolean);
    return { subtotal, external_costs: 0, tax: 0, total: subtotal, milestone_total: milestones, margin: subtotal - Number(content.internal_cost), margin_pct: null, issues, warnings: [] };
  };
  const proposalView = (proposal: Json) => {
    const versions = proposal.versions as Json[];
    const current = versions.find((item) => item.version === proposal.current_version)!;
    const project = projects.find((item) => item.proposal_id === proposal.id);
    return {
      ...proposal, versions: versions.map((item) => ({ version: item.version, content_hash: item.content_hash, locked: item.locked, total: totals(item).total, created_at: item.created_at })).reverse(),
      current: { ...current, ...totals(current) }, approval_state: proposal.approval ? 'pending' : 'none', approval: proposal.approval ?? null, project_id: project?.id ?? null,
    };
  };

  await page.route('http://localhost:8000/api/v1/business/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/business', '');
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() as Json : {};
    mock.calls.push({ method, path, body, query: url.search });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (options.status === 403) return json(envelope('forbidden', 'Business is for workspace owners and members: it holds contacts\' personal data. Ask an owner to make you a member.'), 403);
    if (options.status === 500) return json(envelope('internal_error', 'Database unavailable'), 500);
    const parts = path.split('/').filter(Boolean);

    if (path === '/setup') {
      return json({
        permissions: { read: true, write: true, manage: role === 'owner', decide: role === 'owner' }, sending_enabled: false, web_search: options.webSearch ?? false, model_connected: false,
        settings: { research_runs_per_day: 10, research_searches_per_day: 30, updated_by: null, updated_at: null }, usage: { runs_today: 0, searches_today: 0 },
        criteria: ['industry_fit', 'problem_evidence', 'reachable_channel', 'project_scale', 'urgency_confirmed', 'authorized_buyer'], conversation_criteria: ['authorized_buyer', 'urgency_confirmed'],
        transitions: {}, contactable_bases: ['consent_given', 'inbound_inquiry', 'existing_relationship', 'referral', 'public_business_channel'],
      });
    }
    if (path === '/kpi') {
      const won = leads.filter((lead) => lead.state === 'won').length;
      const step = (stage: string, reached: number) => ({ stage, reached, of: leads.length, rate_pct: leads.length ? Math.round(reached * 1000 / leads.length) / 10 : null, from_previous_pct: stage === 'created' ? null : leads.length ? 100 : null });
      return json({
        window_from: now(), window_to: now(), days: Number(url.searchParams.get('days') ?? 30), leads_created: leads.length,
        funnel: [step('created', leads.length), step('qualified', leads.filter((lead) => lead.qualified_at).length), step('contacted', 0), step('meeting', 0), step('offered', won), step('won', won)],
        by_state: Object.fromEntries(leads.map((lead) => [lead.state, 1])), open_opportunities: opportunities.filter((item) => item.stage !== 'won').length,
        pipeline_assumed: [], pipeline_weighted: [], opportunities_won: won, opportunities_lost: 0, proposals_sent: proposals.filter((item) => item.sent_at).length,
        proposals_accepted: proposals.filter((item) => item.status === 'accepted').length,
        contracted: proposals.filter((item) => item.status === 'accepted').map((item) => ({ currency: 'SAR', amount: totals((item.versions as Json[])[0]).total })),
        cash_collected: projects.flatMap((project) => (project.milestones as Json[]).filter((m) => m.paid_amount)).map((m) => ({ currency: 'SAR', amount: Number(m.paid_amount) })),
        research_runs: 0, web_searches: 0, truncated: false,
        notes: ['Funnel: of the leads created in this window, how many reached each step by now (a lead counts once per step).', 'Pipeline values and probabilities are the owner\'s assumptions, not forecasts.'],
      });
    }
    if (path === '/duplicates') return json([]);

    // catalog
    if (path === '/products' && method === 'GET') {
      // Only the Catalog tab's pages (25 a page) fail; the 100-product picker list keeps working.
      if (productFailures > 0 && url.searchParams.get('limit') === '25') { productFailures -= 1; return json(envelope('internal_error', 'The catalog could not be read.'), 500); }
      const q = (url.searchParams.get('q') ?? '').toLowerCase(), kind = url.searchParams.get('kind'), maturity = url.searchParams.get('maturity');
      const limit = Number(url.searchParams.get('limit') ?? 50), offset = Number(url.searchParams.get('offset') ?? 0);
      const matched = products.filter((item) => (!q || `${item.name} ${item.name_ar} ${item.summary}`.toLowerCase().includes(q)) && (!kind || item.kind === kind) && (!maturity || item.maturity === maturity))
        .sort((left, right) => String(left.name).localeCompare(String(right.name)));
      return json({ items: matched.slice(offset, offset + limit).map(productView), total: matched.length, limit, offset });
    }
    if (path === '/products' && method === 'POST') {
      if (role !== 'owner') return json(envelope('forbidden', 'Only workspace owners can do this.'), 403);
      const product = { id: id('bprod'), ...scope, ...body, integrations: [], created_by: 'user_local', created_at: now(), updated_at: now(), offers: [] };
      products.push(product);
      return json(productView(product), 201);
    }
    if (parts[0] === 'products' && parts[2] === 'versions') {
      const product = products.find((item) => item.id === parts[1])!;
      (product.offers as Json[]).push({ id: id('boffer'), ...scope, product_id: product.id, version: (product.offers as Json[]).length + 1, status: 'draft', notes: '', activated_at: null, created_by: 'user_local', created_at: now(), updated_at: now(), ...body });
      return json(productView(product), 201);
    }
    if (parts[0] === 'products' && parts.length === 2) return json(productView(products.find((item) => item.id === parts[1])!));
    if (parts[0] === 'offers' && parts[2] === 'activate') {
      const product = products.find((item) => (item.offers as Json[]).some((offer) => offer.id === parts[1]))!;
      for (const offer of product.offers as Json[]) if (offer.id === parts[1]) { offer.status = 'active'; offer.activated_at = now(); }
      return json(productView(product));
    }

    // leads
    if (path === '/leads' && method === 'GET') {
      const state = url.searchParams.get('state');
      const shown = leads.filter((lead) => !state || lead.state === state).map((lead) => leadDetail(lead).lead);
      return json(page_(shown));
    }
    if (path === '/leads' && method === 'POST') {
      const account = { id: id('bacct'), ...scope, ...(body.account as Json), domain: String((body.account as Json).website ?? '').replace(/^https?:\/\//, '') || null, do_not_contact: false, dnc_reason: '' };
      const contactInput = body.contact as Json | null;
      const contact = contactInput ? { id: id('bcont'), ...scope, account_id: account.id, role_title: '', profile_url: null, consent_note: '', consent_recorded_at: now(), do_not_contact: false, dnc_reason: '', source: 'manual', opted_out: [], block_reason: null, contactable: true, ...contactInput } : null;
      const product = products.find((item) => item.id === body.product_id);
      const lead = {
        id: id('blead'), ...scope, account_id: account.id, contact_id: contact?.id ?? null, product_id: body.product_id ?? null, title: body.title, state: 'discovered', owner_id: 'user_local', source: 'manual',
        score: {}, score_total: 0, score_known: 0, next_step: '', lost_reason: '', suppressed_reason: '', qualified_at: null, won_at: null, created_by: 'user_local', created_at: now(), updated_at: now(),
        account_name: account.name, account_domain: account.domain, contact_name: contact?.name ?? null, product_name: product?.name ?? null, block_reason: null,
        account, contact, evidence: [], drafts: [], activities: [{ id: id('bact'), kind: 'lead_created', summary: `Lead "${body.title}" added (manual).`, actor: 'user_local', created_at: now() }],
      };
      leads.push(lead);
      return json(leadDetail(lead), 201);
    }
    if (parts[0] === 'leads' && parts[1]) {
      const lead = leads.find((item) => item.id === parts[1]);
      if (!lead) return json(envelope('not_found', 'Lead not found'), 404);
      if (parts.length === 2) return json(leadDetail(lead));
      if (parts[2] === 'evidence') {
        (lead.evidence as Json[]).push({ id: id('bevi'), ...scope, lead_id: lead.id, url: body.url, title: body.title, published_on: body.published_on, excerpt: body.excerpt, confidence: body.confidence, source: 'manual', created_at: now() });
        return json(leadDetail(lead), 201);
      }
      if (parts[2] === 'score') {
        const criteria = body.criteria as Record<string, Json>;
        lead.score = Object.fromEntries(Object.entries(criteria).filter(([, value]) => value.score !== null));
        const known = Object.values(lead.score as Record<string, Json>);
        lead.score_total = known.reduce((sum, value) => sum + Number(value.score), 0);
        lead.score_known = known.length;
        return json(leadDetail(lead));
      }
      if (parts[2] === 'transition') {
        if (body.to === 'qualified' && leadDetail(lead).qualification_gaps.length) return json(envelope('conflict', `Not ready to qualify: ${leadDetail(lead).qualification_gaps.join(' ')}`), 409);
        lead.state = body.to;
        if (body.to === 'qualified') lead.qualified_at = now();
        (lead.activities as Json[]).unshift({ id: id('bact'), kind: 'state_changed', summary: `Discovered to ${String(body.to)}.`, actor: 'user_local', created_at: now() });
        return json(leadDetail(lead));
      }
      if (parts[2] === 'drafts') {
        const draft = { id: id('bdraft'), ...scope, lead_id: lead.id, contact_id: lead.contact_id, channel: body.channel, language: body.language, subject: `A question for ${(lead.account as Json).name}`,
          body: `Hello Sara,\n\nWould a short call be useful?\n\nIf you would rather not hear from me, reply STOP and I will not contact you again.`, status: 'draft', generator: 'template', untrusted_source: false, issues: [],
          approved_hash: null, approved_by: null, approved_at: null, created_by: 'user_local', created_at: now(), updated_at: now() };
        (lead.drafts as Json[]).unshift(draft);
        return json({ lead: leadDetail(lead), draft, note: 'No model is connected, so this draft comes from a template.' }, 201);
      }
    }
    if (parts[0] === 'drafts' && parts[2] === 'approve') {
      const draft = leads.flatMap((lead) => lead.drafts as Json[]).find((item) => item.id === parts[1])!;
      Object.assign(draft, { status: 'approved', approved_hash: 'd'.repeat(64), approved_by: 'user_local', approved_at: now() });
      return json(draft);
    }

    // pipeline
    if (path === '/opportunities' && method === 'POST') {
      const lead = leads.find((item) => item.id === body.lead_id)!;
      const opportunity = { id: id('bopp'), ...scope, lead_id: lead.id, account_id: lead.account_id, product_id: lead.product_id, name: lead.title, stage: 'qualified', currency: 'SAR', value_amount: null, probability_pct: null,
        assumption_note: '', next_step: '', expected_decision_on: null, last_interaction_at: now(), lost_reason: '', won_at: null, lost_at: null, owner_id: 'user_local', created_by: 'user_local', created_at: now(), updated_at: now(),
        account_name: lead.account_name, lead_title: lead.title, product_name: lead.product_name, weighted_value: null, proposals: 0 };
      opportunities.push(opportunity);
      return json(opportunity, 201);
    }
    if (path === '/opportunities') return json(page_(opportunities));
    if (parts[0] === 'opportunities' && parts.length === 2 && method === 'GET') return json(opportunities.find((item) => item.id === parts[1]));
    if (parts[0] === 'opportunities' && method === 'PATCH') {
      const item = opportunities.find((row) => row.id === parts[1])!;
      Object.assign(item, body, { weighted_value: body.value_amount !== null && body.probability_pct !== null ? Number(body.value_amount) * Number(body.probability_pct) / 100 : null });
      return json(item);
    }

    // proposals
    if (path === '/proposals' && method === 'POST') {
      const opportunity = opportunities.find((item) => item.id === body.opportunity_id)!;
      const product = products.find((item) => item.id === opportunity.product_id);
      const offer = product ? (product.offers as Json[]).find((item) => item.status === 'active') : null;
      const proposal = { id: id('bprop'), ...scope, opportunity_id: opportunity.id, account_id: opportunity.account_id, title: body.title, status: 'draft', current_version: 1, accepted_version: null, accepted_hash: null,
        accepted_by: null, accepted_at: null, requested_hash: null, requested_version: null, approval_id: null, approval_task_id: null, decision_note: '', sent_at: null, created_by: 'user_local', created_at: now(), updated_at: now(),
        account_name: opportunity.account_name, opportunity_name: opportunity.name,
        versions: [{ id: id('bpver'), ...scope, proposal_id: '', version: 1, currency: 'SAR', summary: '', scope: '', deliverables: offer?.deliverables ?? [], exclusions: [], assumptions: [], acceptance_criteria: [],
          line_items: offer ? [{ description: `${String(product!.name)} (catalog version 1)`, kind: 'service', quantity: 1, unit_price: offer.price_min }] : [], milestones: [], tax_rate_pct: 0,
          tax_note: 'Tax is tentative until the jurisdiction is verified.', internal_cost: 0, payment_terms: '', valid_until: null, content_hash: 'a1'.repeat(32), locked: false, created_by: 'user_local', created_at: now() }] };
      proposals.push(proposal);
      opportunity.stage = 'quote';
      return json(proposalView(proposal), 201);
    }
    if (path === '/proposals') return json(page_(proposals.map(proposalView)));
    if (parts[0] === 'proposals' && parts[1]) {
      const proposal = proposals.find((item) => item.id === parts[1])!;
      if (parts.length === 2) return json(proposalView(proposal));
      if (parts[2] === 'content') {
        const versions = proposal.versions as Json[];
        Object.assign(versions[0], body, { content_hash: 'c2'.repeat(32) });
        return json(proposalView(proposal));
      }
      if (parts[2] === 'request-acceptance') {
        const current = (proposal.versions as Json[])[0];
        if (totals(current).issues.length) return json(envelope('conflict', `Fix the proposal first: ${totals(current).issues.join(' ')}`), 409);
        current.locked = true;
        Object.assign(proposal, { status: 'pending_approval', requested_version: 1, requested_hash: current.content_hash, sent_at: now(), approval_id: 'approval_biz',
          approval: { id: 'approval_biz', task_id: 'task_biz', action: 'business.accept_proposal', risk_level: 'high', status: 'pending', reason: 'Always asks.', created_at: now(), run_id: 'run_biz', step_id: 'step_biz',
            payload_hash: PAYLOAD_HASH, expires_at: null, arguments: { proposal_id: proposal.id, title: proposal.title, client: proposal.account_name, version: 1, content_hash: current.content_hash, currency: 'SAR',
              subtotal: totals(current).subtotal.toFixed(2), tax: '0.00', tax_note: current.tax_note, total: totals(current).total.toFixed(2), milestones: (current.milestones as Json[]).map((m) => `${String(m.title)}: SAR ${String(m.amount)}`), deliverables: current.deliverables, exclusions: [] } } });
        const lead = leads.find((item) => item.id === opportunities.find((row) => row.id === proposal.opportunity_id)!.lead_id)!;
        lead.state = 'offered';
        return json(proposalView(proposal));
      }
    }

    // projects
    if (path === '/projects' && method === 'POST') {
      const proposal = proposals.find((item) => item.id === body.proposal_id)!;
      if (proposal.status !== 'accepted') return json(envelope('conflict', 'Start a project from an accepted proposal.'), 409);
      const version = (proposal.versions as Json[])[0];
      const project = { id: id('bproj'), ...scope, proposal_id: proposal.id, proposal_version: 1, opportunity_id: proposal.opportunity_id, account_id: proposal.account_id, name: proposal.title, status: 'planned',
        currency: 'SAR', contract_total: totals(version).total, owner_id: 'user_local', start_on: body.start_on, contract_limits: '', created_by: 'user_local', created_at: now(), updated_at: now(),
        account_name: proposal.account_name, accepted_milestones: 0, accepted_value: 0, paid_value: 0,
        milestones: (version.milestones as Json[]).map((m, position) => ({ id: id('bmile'), ...scope, project_id: '', position, title: m.title, amount: m.amount, currency: 'SAR', due_on: null, status: 'planned',
          acceptance_criteria: m.acceptance, accepted_by: null, accepted_at: null, paid_amount: null, paid_on: null, notes: '', created_at: now(), updated_at: now() })) };
      projects.push(project);
      return json(project, 201);
    }
    if (path === '/projects') return json(page_(projects));
    if (parts[0] === 'projects' && parts.length === 2) return json(projects.find((item) => item.id === parts[1]));
    if (parts[0] === 'milestones') {
      const project = projects.find((item) => (item.milestones as Json[]).some((m) => m.id === parts[1]))!;
      const milestone = (project.milestones as Json[]).find((m) => m.id === parts[1])!;
      Object.assign(milestone, body, body.status === 'accepted' ? { accepted_by: 'user_local', accepted_at: now() } : {}, body.paid_amount ? { paid_on: now().slice(0, 10) } : {});
      const all = project.milestones as Json[];
      Object.assign(project, { accepted_milestones: all.filter((m) => m.status === 'accepted').length, accepted_value: all.filter((m) => m.status === 'accepted').reduce((s, m) => s + Number(m.amount), 0),
        paid_value: all.reduce((s, m) => s + Number(m.paid_amount ?? 0), 0) });
      return json(project);
    }
    return json(envelope('not_found', `Unmocked ${method} ${path}`), 404);
  });

  await page.route('http://localhost:8000/api/v1/approvals/approval_biz/approve', async (route) => {
    const body = route.request().postDataJSON() as Json;
    if (body.payload_hash !== PAYLOAD_HASH) return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify(envelope('conflict', 'The payload hash does not match.')) });
    mock.approvals += 1;
    const proposal = proposals.find((item) => item.approval_id === 'approval_biz')!;
    Object.assign(proposal, { status: 'accepted', accepted_version: 1, accepted_hash: (proposal.versions as Json[])[0].content_hash, accepted_by: 'user_local', accepted_at: now(), approval: null });
    const opportunity = opportunities.find((item) => item.id === proposal.opportunity_id)!;
    opportunity.stage = 'won';
    leads.find((item) => item.id === opportunity.lead_id)!.state = 'won';
    const task = { id: 'task_biz', title: 'Accept proposal', prompt: 'Accept', status: 'completed', ...scope, created_at: now(), updated_at: now() };
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      task, run: { id: 'run_biz', task_id: 'task_biz', status: 'completed', result: 'Accepted: the opportunity is won; start the project from the proposal.', steps: [] },
      approval: { id: 'approval_biz', task_id: 'task_biz', action: 'business.accept_proposal', risk_level: 'high', status: 'approved', reason: 'Always asks.', created_at: now(), payload_hash: PAYLOAD_HASH, arguments: {} },
    }) });
  });
  return mock;
}
