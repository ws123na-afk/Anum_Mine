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

/** What the mock mail server does with an invitation email (docs/identity.md#invitation-email). */
type MailMode = 'sent' | 'failed' | 'off';
interface MockOptions { mail?: MailMode; forbidden?: boolean; runStatus?: number; /** The API's `can_manage_tenant_budget` for the caller (org_admin). */ orgAdmin?: boolean; /** USD spent on agent meetings this month. */ meetingUsed?: number }

async function mockApi(page: Page, options: MockOptions = {}) {
  const calls: { method: string; path: string; body: unknown; workspace: string | undefined }[] = [];
  let members: Json[] = [memberRow('user_local', 'owner'), { ...memberRow('user_member', 'member'), email: 'member@example.com' }];
  let invitations = [invitationRow()];
  let meetingBudget: Json = {
    limit_usd: 50, default_limit_usd: 50, is_default: true, used_usd: options.meetingUsed ?? 12.34, turns: 9, period_start: '2026-10-01', resets_on: '2026-11-01',
    updated_by: null, updated_at: null, message: null, can_manage: true,
  };
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
    can_manage_tenant_budget: options.orgAdmin ?? false,
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
    const query = new URL(request.url()).searchParams;
    if (path === '/api/v1/workspace-members/summary') {
      if (options.forbidden) return forbidden(route);
      const active = members.filter((m) => m.active);
      return json({ total: members.length, active: active.length, deactivated: members.length - active.length, active_owners: active.filter((m) => m.role === 'owner').length, pending_invitations: invitations.filter((x) => x.status === 'pending').length });
    }
    if (path === '/api/v1/workspace-members') {
      if (options.forbidden) return forbidden(route);
      const q = (query.get('q') ?? '').toLowerCase();
      const status = query.get('status');
      const wanted = members.filter((m) => (!q || String(m.user_id).toLowerCase().includes(q) || String(m.email ?? '').toLowerCase().includes(q))
        && (!status || (status === 'active') === m.active) && (!query.get('role') || m.role === query.get('role')));
      return json(wanted);
    }
    if (path === '/api/v1/workspace-invitations' && method === 'GET') {
      if (options.forbidden) return forbidden(route);
      const status = query.get('status');
      return json(status ? invitations.filter((x) => x.status === status) : invitations);
    }
    if (path === '/api/v1/workspace-invitations/email-status') return json({ configured: (options.mail ?? 'sent') !== 'off', link_base: 'http://localhost:4173/' });
    const mailAnswer = (to: unknown, wanted: unknown) => {
      if (!to || wanted === false) return { status: 'not_requested', to: to ?? null, attempts: 0, error: null, link_included: false };
      if (options.mail === 'off') return { status: 'not_configured', to, attempts: 0, error: 'No mail server is set up on this ANUM server', link_included: false };
      if (options.mail === 'failed') return { status: 'failed', to, attempts: 3, error: 'The mail server could not be reached (TimeoutError)', link_included: true };
      return { status: 'sent', to, attempts: 1, error: null, link_included: true };
    };
    if (path === '/api/v1/workspace-invitations/invitation_existing/resend') {
      invitations = invitations.map((x) => x.id === 'invitation_existing' ? { ...x, status: 'pending', expires_at: inAWeek } : x);
      return json({ invitation: invitations.find((x) => x.id === 'invitation_existing'), token: `${token}_resent`, email: mailAnswer(null, true) });
    }
    if (path === '/api/v1/workspace-invitations' && method === 'POST') {
      const created = invitationRow({ id: 'invitation_new', role: (body as Json).role, invitee_user_id: (body as Json).invitee_user_id ?? null, invitee_email: (body as Json).invitee_email ?? null });
      invitations = [created, ...invitations];
      return json({ invitation: created, token, email: mailAnswer(created.invitee_email, (body as Json).send_email) }, 201);
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

    if (path === '/api/v1/agent-meetings/budget') {
      if (options.forbidden) return json(envelope('forbidden', 'permission_denied: workspace:manage'), 403);
      if (method === 'PUT') {
        const limit = (body as Json).monthly_cost_limit_usd as number | null;
        meetingBudget = { ...meetingBudget, limit_usd: limit ?? 50, is_default: limit === null, updated_by: limit === null ? null : 'user_local', updated_at: limit === null ? null : now };
      }
      const used = meetingBudget.used_usd as number, limit = meetingBudget.limit_usd as number;
      return json({ ...meetingBudget, remaining_usd: Math.max(0, limit - used), percent: limit > 0 ? Math.min(100, Math.floor((used / limit) * 1000) / 10) : 100, exceeded: used >= limit });
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
    if (path === '/api/v1/model-budgets/tenant' && method === 'PUT') {
      if (!options.orgAdmin) return json(envelope('forbidden', 'Organization administrator role required: this setting applies to every workspace in the organization'), 403);
      const tenant = budgets.tenant as Json;
      budgets = { ...budgets, tenant: { ...tenant, budget: { scope: 'tenant', ...(body as Json), updated_at: now, updated_by: 'user_local' } } };
      return json(budgets);
    }
    return route.abort();
  });
  return calls;
}

test('owner lists members, sees the last-owner protection, and changes a role and access after confirming', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#members');

  const list = page.getByRole('region', { name: 'Members' });
  const me = list.getByRole('article', { name: 'Member user_local' });
  await expect(me).toBeVisible();
  await expect(me.getByText(/Last active owner: make someone else an owner/)).toBeVisible();
  // The only active owner cannot be deactivated or demoted from the page; the API refuses it too (409).
  await expect(me.getByRole('button', { name: 'Deactivate' })).toBeDisabled();
  await expect(me.getByLabel('Role for user_local').locator('option[value="viewer"]')).toHaveJSProperty('disabled', true);

  const bob = list.getByRole('article', { name: 'Member user_member' });
  await expect(bob.getByText('member@example.com')).toBeVisible();
  await bob.getByLabel('Role for user_member').selectOption('viewer');
  const dialog = page.getByRole('alertdialog', { name: 'Make user_member viewer?' });
  await expect(dialog.getByText(/Viewers can read but not create or run tasks/)).toBeVisible();
  // Cancel changes nothing.
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(bob.getByLabel('Role for user_member')).toHaveValue('member');

  await bob.getByLabel('Role for user_member').selectOption('viewer');
  await page.getByRole('alertdialog').getByRole('button', { name: 'Change role' }).click();
  await expect(page.getByText('user_member is now viewer.')).toBeVisible();
  await bob.getByRole('button', { name: 'Deactivate' }).click();
  await page.getByRole('alertdialog', { name: 'Deactivate user_member?' }).getByRole('button', { name: 'Deactivate' }).click();
  await expect(bob.getByText('deactivated', { exact: true })).toBeVisible();
  await expect(bob.getByRole('button', { name: 'Reactivate' })).toBeVisible();

  expect(calls.filter((c) => c.method !== 'GET' && c.path.startsWith('/api/v1/workspace-')).map((c) => `${c.method} ${c.path}`)).toEqual([
    'PUT /api/v1/workspace-members/user_member/role',
    'POST /api/v1/workspace-members/user_member/deactivate',
  ]);
});

test('members search by email and filter by status', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#members');
  const list = page.getByRole('region', { name: 'Members' });
  await expect(list.getByRole('article')).toHaveCount(2);
  await list.getByLabel('Search').fill('member@');
  await expect(list.getByRole('article')).toHaveCount(1);
  await expect(list.getByRole('article', { name: 'Member user_member' })).toBeVisible();
  await expect.poll(() => calls.some((c) => c.path === '/api/v1/workspace-members' && c.method === 'GET')).toBe(true);
  await list.getByLabel('Search').fill('nobody');
  await expect(list.getByRole('heading', { name: 'No members match' })).toBeVisible();
  await list.getByRole('button', { name: 'Clear filters' }).click();
  await expect(list.getByRole('article')).toHaveCount(2);
  await list.getByLabel('Status').selectOption('deactivated');
  await expect(list.getByRole('heading', { name: 'No members match' })).toBeVisible();
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
  expect(calls.find((c) => c.method === 'POST' && c.path === '/api/v1/workspace-invitations')?.body).toEqual({ role: 'owner', invitee_email: 'ana@example.com', ttl_hours: 48, app_url: expect.stringMatching(/^http:\/\/127\.0\.0\.1:\d+\//) });

  await reveal.getByRole('button', { name: 'Done' }).click();
  await expect(page.getByText(token)).toHaveCount(0);

  const invitations = page.getByRole('region', { name: 'Invitations' });
  await expect(invitations.getByRole('article', { name: 'Invitation for ana@example.com' })).toBeVisible();
  const existing = invitations.getByRole('article', { name: 'Invitation for user_pending' });
  await expect(existing.getByText(/Expires in (6|7) d/)).toBeVisible();
  await existing.getByRole('button', { name: 'Resend' }).click();
  await expect(page.getByRole('region', { name: 'New invitation token' }).getByTestId('invitation-token')).toHaveText(`${token}_resent`);
  await expect(page.getByText(/The previous one no longer works/)).toBeVisible();
  await page.getByRole('region', { name: 'New invitation token' }).getByRole('button', { name: 'Done' }).click();
  await existing.getByRole('button', { name: 'Revoke' }).click();
  await page.getByRole('alertdialog', { name: 'Revoke the invitation for user_pending?' }).getByRole('button', { name: 'Revoke invitation' }).click();
  // Revoked invitations leave the pending list; the "Revoked" filter shows them.
  await expect(existing).toHaveCount(0);
  await invitations.getByRole('button', { name: 'Revoked' }).click();
  await expect(invitations.getByRole('article', { name: 'Invitation for user_pending' }).getByText('revoked', { exact: true })).toBeVisible();
  expect(calls.some((c) => c.method === 'POST' && c.path === '/api/v1/workspace-invitations/invitation_existing/resend')).toBe(true);
});

test('an invitation is emailed and the page says so, while the token stays copyable', async ({ page }) => {
  const calls = await mockApi(page, { mail: 'sent' });
  await page.goto('/#members');
  const form = page.getByRole('form', { name: 'Invite someone' });
  await expect(form.getByText(/Add an email address and ANUM emails the invitation/)).toBeVisible();
  await form.getByLabel('Email').fill('ana@example.com');
  await expect(form.getByRole('checkbox', { name: 'Send the invitation by mail' })).toBeChecked();
  await form.getByRole('button', { name: 'Create invitation' }).click();
  const reveal = page.getByRole('region', { name: 'New invitation token' });
  await expect(reveal.getByText('Emailed to ana@example.com with a link to accept.')).toBeVisible();
  await expect(reveal.getByTestId('invitation-token')).toHaveText(token);
  await expect(page.getByText('Invitation created and emailed to ana@example.com.')).toBeVisible();
  expect((calls.find((c) => c.method === 'POST' && c.path === '/api/v1/workspace-invitations')?.body as Json).send_email).toBeUndefined();
});

test('turning the email off sends nothing and says so', async ({ page }) => {
  const calls = await mockApi(page, { mail: 'sent' });
  await page.goto('/#members');
  const form = page.getByRole('form', { name: 'Invite someone' });
  await form.getByLabel('Email').fill('ana@example.com');
  await form.getByRole('checkbox', { name: 'Send the invitation by mail' }).uncheck();
  await form.getByRole('button', { name: 'Create invitation' }).click();
  await expect(page.getByRole('region', { name: 'New invitation token' }).getByText('Not emailed, as you chose.')).toBeVisible();
  expect((calls.find((c) => c.method === 'POST' && c.path === '/api/v1/workspace-invitations')?.body as Json).send_email).toBe(false);
});

test('a failed email tells the owner to send the link themselves', async ({ page }) => {
  await mockApi(page, { mail: 'failed' });
  await page.goto('/#members');
  const form = page.getByRole('form', { name: 'Invite someone' });
  await form.getByLabel('Email').fill('ana@example.com');
  await form.getByRole('button', { name: 'Create invitation' }).click();
  const reveal = page.getByRole('region', { name: 'New invitation token' });
  await expect(reveal.getByRole('alert')).toContainText('could not be sent after 3 tries');
  await expect(reveal.getByRole('alert')).toContainText('send it yourself');
  await expect(reveal.getByRole('button', { name: 'Copy link' })).toBeVisible();
});

test('without a mail server the form and the result say ANUM does not email', async ({ page }) => {
  await mockApi(page, { mail: 'off' });
  await page.goto('/#members');
  const form = page.getByRole('form', { name: 'Invite someone' });
  await form.getByLabel('Email').fill('ana@example.com');
  await expect(form.getByText(/no mail server set up, so ANUM cannot email invitations/)).toBeVisible();
  await expect(form.getByRole('checkbox', { name: 'Send the invitation by mail' })).toHaveCount(0);
  await form.getByRole('button', { name: 'Create invitation' }).click();
  const reveal = page.getByRole('region', { name: 'New invitation token' });
  await expect(reveal.getByRole('alert')).toContainText('this server has no mail server set up');
  await expect(reveal.getByTestId('invitation-token')).toHaveText(token);
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
  await expect(page.getByRole('button', { name: 'Local / Sales' })).toBeVisible();
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

test('a workspace owner who is not an organization administrator reads the organization budget', async ({ page }) => {
  const calls = await mockApi(page, { orgAdmin: false });
  await page.goto('/#budgets');

  const tenant = page.getByRole('region', { name: 'Organization budget' });
  await expect(tenant.getByText(/Read only: organization model budgets apply to every workspace in this organization/)).toBeVisible();
  // How to get the role waits behind a disclosure.
  await tenant.getByText('How to get access').click();
  await expect(tenant.getByText(/Owning this workspace is not enough/)).toBeVisible();
  // The numbers are still shown; only the controls are gone.
  await expect(tenant.getByRole('progressbar', { name: 'Organization estimated cost used' })).toBeVisible();
  await expect(tenant.getByRole('button', { name: 'Save limits' })).toHaveCount(0);
  await expect(tenant.getByLabel('Monthly cost limit (USD)')).toHaveCount(0);
  // This workspace's own budget stays editable for its owner.
  const workspace = page.getByRole('region', { name: 'This workspace budget' });
  await expect(workspace.getByRole('button', { name: 'Save limits' })).toBeVisible();
  await expect(workspace.getByText(/Read only:/)).toHaveCount(0);
  expect(calls.some((c) => c.method === 'PUT' && c.path === '/api/v1/model-budgets/tenant')).toBe(false);
});

test('an organization administrator sets the organization budget', async ({ page }) => {
  const calls = await mockApi(page, { orgAdmin: true });
  await page.goto('/#budgets');

  const tenant = page.getByRole('region', { name: 'Organization budget' });
  await expect(tenant.getByText(/Read only:/)).toHaveCount(0);
  await tenant.getByLabel('Monthly token limit').fill('9000');
  await tenant.getByRole('button', { name: 'Save limits' }).click();
  await expect(tenant.getByText('Budget saved.')).toBeVisible();
  await expect(tenant.getByText('limited', { exact: true })).toBeVisible();
  expect(calls.find((c) => c.method === 'PUT' && c.path === '/api/v1/model-budgets/tenant')?.body).toEqual({ monthly_cost_limit_usd: null, monthly_token_limit: 9000 });
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

test('the owner reads the agent meeting budget and changes it, with the default one click away', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#budgets');
  const card = page.getByRole('region', { name: 'Agent meeting budget' });
  await expect(card.getByRole('heading', { name: 'Agent meeting budget' })).toBeVisible();
  await expect(card.getByText('12.34', { exact: false }).first()).toBeVisible();
  await expect(card.getByRole('progressbar', { name: 'Spent on agent meetings this month' })).toHaveAttribute('aria-valuenow', '24');
  await expect(card.getByText('Within budget')).toBeVisible();
  await expect(card.getByText('This is the default limit. No owner has changed it.')).toBeVisible();
  await expect(card.getByRole('button', { name: /Use the default/ })).toHaveCount(0);

  await card.getByLabel('Monthly limit (USD)').fill('-5');
  await card.getByRole('button', { name: 'Save limit' }).click();
  await expect(card.getByText('Enter an amount in USD from 0 to 100000, with at most two decimals.')).toBeVisible();
  expect(calls.some((c) => c.method === 'PUT' && c.path === '/api/v1/agent-meetings/budget')).toBe(false);

  await card.getByLabel('Monthly limit (USD)').fill('20');
  await card.getByRole('button', { name: 'Save limit' }).click();
  await expect(card.getByText('Agent meeting budget saved.')).toBeVisible();
  expect(calls.find((c) => c.method === 'PUT' && c.path === '/api/v1/agent-meetings/budget')?.body).toEqual({ monthly_cost_limit_usd: 20 });
  await expect(card.getByText('Nearly used')).toHaveCount(0);
  await expect(card.getByText(/Last changed by user_local/)).toBeVisible();

  await card.getByRole('button', { name: 'Use the default (USD 50.00)' }).click();
  await expect(card.getByText('Agent meeting budget set back to the default.')).toBeVisible();
  expect(calls.filter((c) => c.method === 'PUT' && c.path === '/api/v1/agent-meetings/budget').map((c) => c.body)).toEqual([{ monthly_cost_limit_usd: 20 }, { monthly_cost_limit_usd: null }]);
});

test('a used up agent meeting budget says so plainly and says how to fix it', async ({ page }) => {
  await mockApi(page, { meetingUsed: 50.4 });
  await page.goto('/#budgets');
  const card = page.getByRole('region', { name: 'Agent meeting budget' });
  await expect(card.getByText('Used up', { exact: true })).toBeVisible();
  await expect(card.getByText(/Monthly agent meeting budget reached\. This workspace has used USD 50\.40 of its USD 50\.00/)).toBeVisible();
  await expect(card.getByText(/A workspace owner can raise the limit under Budgets/)).toBeVisible();
});

const gateSizes = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const [width, height] of gateSizes) {
  test(`the agent meeting budget card fits ${width}x${height} without overflow or clipped controls @gate`, async ({ page }) => {
    await mockApi(page, { meetingUsed: 41 });
    await page.setViewportSize({ width, height });
    await page.goto('/#budgets');
    const card = page.getByRole('region', { name: 'Agent meeting budget' });
    await expect(card.getByText('Nearly used')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    const clipped = await card.locator('button, input').evaluateAll((items) => items.filter((b) => { const r = b.getBoundingClientRect(); return r.width > 0 && (r.right > innerWidth + 1 || r.left < -1); }).length);
    expect(clipped).toBe(0);
    if (process.env.ANUM_SCREENSHOTS) await page.screenshot({ path: `${process.env.ANUM_SCREENSHOTS}/budget-${width}x${height}.png`, fullPage: true });
  });
}
