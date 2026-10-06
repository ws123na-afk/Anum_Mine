import { expect, test, type Page, type Route } from '@playwright/test';

// Owner screens against a mocked API (docs/identity.md, Invitations and Membership Management;
// docs/model-gateway.md, Monthly budgets): members, invitations shown once, accept by link,
// permissions decided by the API's 403, budgets with usage, and the 402 budget refusal.
const now = new Date().toISOString();
const inAWeek = new Date(Date.now() + 7 * 86_400_000).toISOString();
const token = 'anum_inv_e2eTokenValue_0123456789abcdef';

type Json = Record<string, unknown>;
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });

function memberRow(userId: string, role: string, active = true): Json {
  return { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: userId, role, active, created_at: now, updated_at: now };
}

function invitationRow(overrides: Json = {}): Json {
  return {
    id: 'invitation_existing', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'viewer',
    invitee_user_id: 'user_pending', invitee_email: null, status: 'pending', created_by_user_id: 'user_local',
    expires_at: inAWeek, accepted_by_user_id: null, accepted_at: null, revoked_at: null, created_at: now, ...overrides,
  };
}

interface MockOptions { forbidden?: boolean; runStatus?: number }

async function mockApi(page: Page, options: MockOptions = {}) {
  const calls: { method: string; path: string; body: unknown; workspace: string | undefined }[] = [];
  let members = [memberRow('user_local', 'owner'), memberRow('user_member', 'member')];
  let invitations = [invitationRow()];
  let budgets: Json = {
    period_start: '2026-10-01',
    resets_on: '2026-11-01',
    tenant: { budget: null, usage: { input_tokens: 1200, output_tokens: 800, estimated_cost_usd: 1.5, calls: 4, unpriced_calls: 0 }, total_tokens: 2000, exceeded: false },
    workspace: {
      budget: { scope: 'workspace', monthly_cost_limit_usd: 2, monthly_token_limit: 2000, updated_at: now, updated_by: 'user_local' },
      usage: { input_tokens: 1200, output_tokens: 800, estimated_cost_usd: 1.5, calls: 4, unpriced_calls: 1 },
      total_tokens: 2000,
      exceeded: true,
    },
  };
  const forbidden = (route: Route) => route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify(envelope('forbidden', 'permission_denied: membership:manage')) });

  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method, path, body, workspace: request.headers()['x-workspace-id'] });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });

    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' && method === 'GET') return json([]);
    if (path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/tasks' && method === 'POST') return json({ id: 'task_budget', title: 'Web task', prompt: 'p', status: 'created', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now });
    if (path === '/api/v1/tasks/task_budget/run') {
      return json(envelope('model_budget_exceeded', 'This workspace has used its monthly model budget (tokens). It resets on 2026-11-01 (UTC); an owner can raise it in Settings.'), options.runStatus ?? 402);
    }

    if (path === '/api/v1/workspace-memberships/current') {
      const workspace = request.headers()['x-workspace-id'];
      return json({ ...memberRow('user_local', workspace === 'workspace_sales' ? 'member' : 'owner'), workspace_id: workspace });
    }
    if (path === '/api/v1/workspace-members') return options.forbidden ? forbidden(route) : json(members);
    if (path === '/api/v1/workspace-invitations' && method === 'GET') return options.forbidden ? forbidden(route) : json(invitations);
    if (path === '/api/v1/workspace-invitations' && method === 'POST') {
      const created = invitationRow({ id: 'invitation_new', role: (body as Json).role, invitee_user_id: (body as Json).invitee_user_id ?? null, invitee_email: (body as Json).invitee_email ?? null });
      invitations = [created, ...invitations];
      return json({ invitation: created, token }, 201);
    }
    if (path === '/api/v1/workspace-invitations/invitation_existing/revoke') {
      invitations = invitations.map((x) => x.id === 'invitation_existing' ? { ...x, status: 'revoked', revoked_at: now } : x);
      return json(invitations.find((x) => x.id === 'invitation_existing'));
    }
    if (path === '/api/v1/workspace-invitations/accept') {
      return json({ invitation: invitationRow({ status: 'accepted', accepted_by_user_id: 'user_local', accepted_at: now, workspace_id: 'workspace_sales' }), membership: { ...memberRow('user_local', 'member'), workspace_id: 'workspace_sales' } });
    }
    const role = path.match(/^\/api\/v1\/workspace-members\/([^/]+)\/role$/);
    if (role) {
      if (role[1] === 'user_local' && (body as Json).role !== 'owner') return json(envelope('conflict', 'The last active owner cannot be demoted or deactivated'), 409);
      members = members.map((m) => m.user_id === role[1] ? { ...m, role: (body as Json).role } : m);
      return json(members.find((m) => m.user_id === role[1]));
    }
    const toggle = path.match(/^\/api\/v1\/workspace-members\/([^/]+)\/(deactivate|reactivate)$/);
    if (toggle) {
      if (toggle[1] === 'user_local') return json(envelope('conflict', 'The last active owner cannot be demoted or deactivated'), 409);
      members = members.map((m) => m.user_id === toggle[1] ? { ...m, active: toggle[2] === 'reactivate' } : m);
      return json(members.find((m) => m.user_id === toggle[1]));
    }

    if (path === '/api/v1/model-budgets' && method === 'GET') {
      return options.forbidden ? json(envelope('forbidden', 'permission_denied: organization:manage'), 403) : json(budgets);
    }
    if (path === '/api/v1/model-budgets/workspace' && method === 'PUT') {
      const limits = body as Json;
      const workspace = budgets.workspace as Json;
      budgets = { ...budgets, workspace: { ...workspace, budget: { scope: 'workspace', ...limits, updated_at: now, updated_by: 'user_local' }, exceeded: false } };
      return json(budgets);
    }
    return route.abort();
  });
  return calls;
}

test('owner lists members, sees the last-owner refusal, and deactivates a member', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#members');

  const list = page.getByRole('region', { name: 'Members' });
  await expect(list.getByRole('article', { name: 'Member user_local' })).toBeVisible();
  await expect(list.getByText('Last active owner: the API refuses to demote or deactivate them.')).toBeVisible();

  await list.getByRole('article', { name: 'Member user_local' }).getByRole('button', { name: 'Deactivate' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'The last active owner cannot be demoted or deactivated' })).toBeVisible();

  const bob = list.getByRole('article', { name: 'Member user_member' });
  await bob.getByLabel('Role for user_member').selectOption('viewer');
  await expect(page.getByText('user_member is now viewer.')).toBeVisible();
  await bob.getByRole('button', { name: 'Deactivate' }).click();
  await expect(bob.getByText('deactivated', { exact: true })).toBeVisible();
  await expect(bob.getByRole('button', { name: 'Reactivate' })).toBeVisible();

  expect(calls.filter((c) => c.method !== 'GET' && c.path.startsWith('/api/v1/workspace-')).map((c) => `${c.method} ${c.path}`)).toEqual([
    'POST /api/v1/workspace-members/user_local/deactivate',
    'PUT /api/v1/workspace-members/user_member/role',
    'POST /api/v1/workspace-members/user_member/deactivate',
  ]);
});

test('creating an invitation shows the token once with copy actions, and revoke works', async ({ page, context, browserName }) => {
  if (browserName === 'chromium') await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const calls = await mockApi(page);
  await page.goto('/#members');

  const form = page.getByRole('form', { name: 'Invite someone' });
  await form.getByLabel('Email').fill('not-an-email');
  await form.getByRole('button', { name: 'Create invitation' }).click();
  await expect(form.getByText('Enter a valid email address.')).toBeVisible();

  await form.getByLabel('Email').fill('ana@example.com');
  await form.getByLabel('Role').selectOption('owner');
  await form.getByLabel('Expires after (hours)').fill('48');
  await form.getByRole('button', { name: 'Create invitation' }).click();

  const reveal = page.getByRole('region', { name: 'New invitation token' });
  await expect(reveal.getByTestId('invitation-token')).toHaveText(token);
  await expect(reveal.getByText(`#invitation=${token}&workspace=workspace_foundation`)).toBeVisible();
  await expect(reveal.getByText(/cannot be shown again/)).toBeVisible();
  await reveal.getByRole('button', { name: 'Copy token' }).click();
  await expect(reveal.getByText(/Token copied\.|Copying is blocked here/)).toBeVisible();
  expect(calls.find((c) => c.method === 'POST' && c.path === '/api/v1/workspace-invitations')?.body).toEqual({ role: 'owner', invitee_email: 'ana@example.com', ttl_hours: 48 });

  await reveal.getByRole('button', { name: 'Done' }).click();
  await expect(page.getByText(token)).toHaveCount(0);

  const invitations = page.getByRole('region', { name: 'Invitations' });
  await expect(invitations.getByRole('article', { name: 'Invitation for ana@example.com' })).toBeVisible();
  const existing = invitations.getByRole('article', { name: 'Invitation for user_pending' });
  await expect(existing.getByText(/Expires in (6|7) d/)).toBeVisible();
  await existing.getByRole('button', { name: 'Revoke' }).click();
  await expect(existing.getByText('revoked', { exact: true })).toBeVisible();
});

test('non-owners see the API permission answer and can still accept an invitation link', async ({ page }) => {
  const calls = await mockApi(page, { forbidden: true });
  await page.goto(`/#invitation=${token}&workspace=workspace_sales`);

  await expect(page.getByRole('heading', { name: 'Owner access required' })).toBeVisible();
  await expect(page.getByText('The API answered: permission_denied: membership:manage')).toBeVisible();
  // The token is read once and removed from the address bar.
  await expect(page).toHaveURL(/#members$/);

  const accept = page.getByRole('form', { name: 'Accept an invitation' });
  await expect(accept.getByLabel('Invitation token or link')).toHaveValue(token);
  await expect(accept.getByLabel('Workspace')).toHaveValue('workspace_sales');
  await accept.getByRole('button', { name: 'Accept invitation' }).click();
  await expect(accept.getByText('You joined workspace_sales as member. Switch to that workspace to work in it.')).toBeVisible();

  const accepted = calls.find((c) => c.path === '/api/v1/workspace-invitations/accept');
  expect(accepted?.body).toEqual({ token });
  expect(accepted?.workspace).toBe('workspace_sales');

  // The joined workspace is one click away, and after the switch every request names it.
  await accept.getByRole('button', { name: 'Switch to workspace_sales' }).click();
  await expect(page.getByRole('button', { name: /tenant_local \/ workspace_sales/ })).toBeVisible();
  const switchedAt = calls.findIndex((c) => c.path === '/api/v1/workspace-memberships/current' && c.workspace === 'workspace_sales');
  expect(switchedAt).toBeGreaterThan(-1);
  await expect.poll(() => calls.slice(switchedAt + 1).filter((c) => c.path === '/api/v1/workspace-members').length).toBeGreaterThan(0);
  // The development session request carries the workspace in its body; every other call in the header.
  expect(calls.slice(switchedAt + 1).every((c) => c.path === '/api/v1/auth/local/session' ? (c.body as Json).workspace_id === 'workspace_sales' : c.workspace === 'workspace_sales')).toBe(true);
});

test('budgets show usage against limits, the reset date, and save new limits', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#budgets');

  await expect(page.getByText(/It resets on 1 November 2026 \(UTC\)/)).toBeVisible();
  const workspace = page.getByRole('region', { name: 'This workspace budget' });
  await expect(workspace.getByText('budget used up')).toBeVisible();
  await expect(workspace.getByRole('progressbar', { name: 'This workspace estimated cost used' })).toHaveAttribute('aria-valuenow', '75');
  await expect(workspace.getByRole('progressbar', { name: 'This workspace tokens used' })).toHaveAttribute('aria-valuenow', '100');
  await expect(workspace.getByText('Unpriced calls add tokens but no cost: set a token limit too.')).toBeVisible();
  const tenant = page.getByRole('region', { name: 'Organization budget' });
  await expect(tenant.getByText('no budget')).toBeVisible();
  await expect(tenant.getByText('No limit set').first()).toBeVisible();

  await workspace.getByLabel('Monthly token limit').fill('abc');
  await workspace.getByRole('button', { name: 'Save limits' }).click();
  await expect(workspace.getByText(/Token limit must be a whole number/)).toBeVisible();

  await workspace.getByLabel('Monthly cost limit (USD)').fill('25');
  await workspace.getByLabel('Monthly token limit').fill('5,000,000');
  await workspace.getByRole('button', { name: 'Save limits' }).click();
  await expect(workspace.getByText('Budget saved.')).toBeVisible();
  await expect(workspace.getByText('limited', { exact: true })).toBeVisible();
  expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ monthly_cost_limit_usd: 25, monthly_token_limit: 5_000_000 });
});

test('budgets are owner-only: a 403 shows the API answer instead of numbers', async ({ page }) => {
  await mockApi(page, { forbidden: true });
  await page.goto('/#budgets');
  await expect(page.getByRole('heading', { name: 'Owner access required' })).toBeVisible();
  await expect(page.getByText('The API answered: permission_denied: organization:manage')).toBeVisible();
  await expect(page.getByRole('progressbar')).toHaveCount(0);
});

test('a task run refused with 402 shows the budget message and links to budgets', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#tasks');
  await page.getByLabel('Task instructions').fill('Summarise the week');
  await page.getByRole('button', { name: 'Run task' }).click();

  const alert = page.getByRole('alert', { name: 'Model budget reached' });
  await expect(alert.getByText('Monthly model budget reached')).toBeVisible();
  await expect(alert.getByText(/It resets on 2026-11-01 \(UTC\)/)).toBeVisible();
  await alert.getByRole('button', { name: 'Model budgets' }).click();
  await expect(page.getByRole('heading', { name: 'Monthly model budgets' })).toBeVisible();
});
