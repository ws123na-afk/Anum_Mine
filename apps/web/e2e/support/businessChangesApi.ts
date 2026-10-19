import type { Page, Route } from '@playwright/test';
import { mockWorkspaceApi } from './workspaceApi';

// Change orders, the usage ledger and profitability (docs/business.md#change-orders): a stateful
// stand-in for the project routes of /api/v1/business that follows the API's rules (an owner approves
// with the content hash; approving adds a contract version and a milestone; the ledger amount is
// quantity times unit cost; empty figures are null). One accepted project is the test's fixture.
type Json = Record<string, unknown>;
const now = () => new Date().toISOString();
const scope = { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' };
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });
export const CHANGE_HASH = 'c4'.repeat(32);
export const PROJECT_ID = 'bproj_1';

export interface ChangesMock { calls: { method: string; path: string; body: unknown }[] }

export async function mockBusinessChanges(page: Page, options: { role?: 'owner' | 'member'; failChanges?: boolean } = {}): Promise<ChangesMock> {
  await mockWorkspaceApi(page);
  const role = options.role ?? 'owner';
  const mock: ChangesMock = { calls: [] };
  let counter = 0;
  const id = (prefix: string) => `${prefix}_${++counter}`;
  const page_ = (items: Json[]) => ({ items, total: items.length, limit: 25, offset: 0 });
  const milestone = (title: string, amount: number, position: number) => ({ id: id('bmile'), ...scope, project_id: PROJECT_ID, position, title, amount, currency: 'SAR', due_on: null,
    status: 'planned', acceptance_criteria: '', accepted_by: null, accepted_at: null, paid_amount: null, paid_on: null, notes: '', created_at: now(), updated_at: now() });
  const project: Json = {
    id: PROJECT_ID, ...scope, proposal_id: 'bprop_1', proposal_version: 1, opportunity_id: 'bopp_1', account_id: 'bacct_1', name: 'Fleet reporting pilot', status: 'active',
    currency: 'SAR', contract_total: 2500, owner_id: 'user_local', start_on: '2026-11-01', contract_limits: '', created_by: 'user_local', created_at: now(), updated_at: now(),
    account_name: 'Gulf Mixers LLC', accepted_milestones: 0, accepted_value: 0, paid_value: 0, milestones: [milestone('Deposit', 1250, 0), milestone('Handover', 1250, 1)],
  };
  const changes: Json[] = [], usage: Json[] = [];
  const accepted = 2500;
  const delta = (change: Json) => (change.lines as Json[]).reduce((sum, line) => sum + (line.action === 'remove' ? -1 : 1) * Number(line.quantity) * Number(line.unit_price), 0);
  const view = (change: Json) => ({ ...change, project_name: project.name, issues: [], current_version: project.proposal_version, stale: false,
    subtotal_delta: delta(change), tax_delta: 0, total_delta: delta(change) });
  const profitability = () => {
    const approved = changes.filter((item) => item.status === 'approved');
    const changeRevenue = approved.reduce((sum, item) => sum + delta(item), 0);
    const mine = usage.filter((item) => item.currency === 'SAR');
    const labour = mine.filter((item) => item.kind === 'labour'), direct = mine.filter((item) => item.kind !== 'labour');
    const sum = (rows: Json[]) => rows.reduce((total, item) => total + Number(item.amount), 0);
    const payments = (project.milestones as Json[]).filter((m) => m.paid_amount !== null);
    const revenue = accepted + changeRevenue;
    const margin = mine.length ? revenue - sum(mine) : null;
    return {
      project_id: PROJECT_ID, currency: 'SAR', accepted_version: 1, current_version: project.proposal_version, accepted_revenue: accepted, change_orders_revenue: changeRevenue,
      approved_change_orders: approved.length, revenue, tax: 0, contract_total: project.contract_total, cash_collected: payments.length ? payments.reduce((s, m) => s + Number(m.paid_amount), 0) : null,
      outstanding: payments.length ? Number(project.contract_total) - payments.reduce((s, m) => s + Number(m.paid_amount), 0) : null,
      labour_hours: labour.length ? labour.reduce((s, item) => s + Number(item.quantity), 0) : null, labour_cost: labour.length ? sum(labour) : null, direct_costs: direct.length ? sum(direct) : null,
      costs: [], billable_costs: mine.length ? sum(mine.filter((item) => item.billable)) : null, non_billable_costs: mine.length ? sum(mine.filter((item) => !item.billable)) : null,
      gross_margin: margin, gross_margin_pct: margin === null ? null : Math.round(margin * 10000 / revenue) / 100, other_currency: [], reconciled: true,
      notes: ['Revenue is the accepted proposal plus approved change orders, before tax. Cash collected is only the milestone payments recorded by a person; ANUM collects no payments.'],
    };
  };

  await page.route('http://localhost:8000/api/v1/business/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/business', '');
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() as Json : {};
    mock.calls.push({ method, path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const parts = path.split('/').filter(Boolean);

    if (path === '/setup') {
      return json({
        permissions: { read: true, write: true, manage: role === 'owner', decide: role === 'owner' }, sending_enabled: false, web_search: false, model_connected: false,
        settings: { research_runs_per_day: 10, research_searches_per_day: 30, updated_by: null, updated_at: null }, usage: { runs_today: 0, searches_today: 0 },
        criteria: ['industry_fit', 'problem_evidence', 'reachable_channel', 'project_scale', 'urgency_confirmed', 'authorized_buyer'], conversation_criteria: ['authorized_buyer', 'urgency_confirmed'],
        transitions: {}, contactable_bases: ['consent_given', 'inbound_inquiry', 'existing_relationship', 'referral', 'public_business_channel'],
      });
    }
    if (path === '/projects') return json(page_([project]));
    if (path === `/projects/${PROJECT_ID}`) return json(project);
    if (path === `/projects/${PROJECT_ID}/profitability`) return json(profitability());
    if (path === `/projects/${PROJECT_ID}/change-orders`) {
      if (options.failChanges) return json(envelope('internal_error', 'Database unavailable'), 500);
      if (method === 'GET') return json(page_([...changes].reverse().map(view)));
      const change = { id: id('bchg'), ...scope, project_id: PROJECT_ID, proposal_id: 'bprop_1', account_id: 'bacct_1', number: changes.length + 1, status: 'draft', currency: 'SAR',
        base_version: null, base_hash: null, content_hash: null, result_version: null, submitted_by: null, submitted_at: null, decided_by: null, decided_at: null, decision_note: '',
        created_by: 'user_local', created_at: now(), updated_at: now(), ...body };
      changes.push(change);
      return json(view(change), 201);
    }
    if (parts[0] === 'change-orders') {
      const change = changes.find((item) => item.id === parts[1]);
      if (!change) return json(envelope('not_found', 'Change order not found'), 404);
      if (parts[2] === 'submit') Object.assign(change, { status: 'pending_approval', base_version: project.proposal_version, base_hash: 'a'.repeat(64), content_hash: CHANGE_HASH, submitted_by: 'user_local', submitted_at: now() });
      else if (parts[2] === 'approve') {
        if (role !== 'owner') return json(envelope('forbidden', 'Only a workspace owner approves or rejects a change order. A member drafts and sends it; an owner decides.'), 403);
        if (body.content_hash !== CHANGE_HASH) return json(envelope('conflict', 'The change order is not the one you were shown; nothing was approved. Reload it and approve again.'), 409);
        const version = Number(project.proposal_version) + 1;
        Object.assign(change, { status: 'approved', decided_by: 'user_local', decided_at: now(), result_version: version });
        const amount = delta(change);
        (project.milestones as Json[]).push(milestone(`Change order ${String(change.number)}: ${String(change.title)}`, amount, (project.milestones as Json[]).length));
        Object.assign(project, { proposal_version: version, contract_total: Number(project.contract_total) + amount });
      } else if (parts[2] === 'reject' || parts[2] === 'withdraw') Object.assign(change, { status: parts[2] === 'reject' ? 'rejected' : 'withdrawn', decision_note: body.reason });
      else if (method === 'PATCH') Object.assign(change, body);
      return json(view(change));
    }
    if (path === '/usage' && method === 'GET') return json(page_([...usage].reverse()));
    if (path === '/usage' && method === 'POST') {
      const entry = { id: id('busg'), ...scope, account_id: 'bacct_1', project_id: PROJECT_ID, source: 'manual', external_ref: null, created_by: 'user_local', created_at: now(), updated_at: now(),
        account_name: 'Gulf Mixers LLC', project_name: project.name, ...body, amount: Math.round(Number(body.quantity) * Number(body.unit_cost) * 100) / 100 };
      usage.push(entry);
      return json(entry, 201);
    }
    if (path === '/usage/import-ai') return json(envelope('conflict', `No model usage was recorded for this workspace in ${String(body.month)}.`), 409);
    if (parts[0] === 'usage' && method === 'PATCH') { const entry = usage.find((item) => item.id === parts[1])!; Object.assign(entry, body); return json(entry); }
    if (parts[0] === 'usage' && method === 'DELETE') { usage.splice(usage.findIndex((item) => item.id === parts[1]), 1); return route.fulfill({ status: 204 }); }
    return json(envelope('not_found', `Unmocked ${method} ${path}`), 404);
  });
  return mock;
}
