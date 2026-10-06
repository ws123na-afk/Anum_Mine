import { expect, test, type Page } from '@playwright/test';

// Workspace approval policy, the two-person refusal on the approval card, and the workspace
// switcher, against a mocked API (docs/approvals-and-risk.md, Workspace Approval Policy;
// docs/identity.md, Workspace switcher).
const now = new Date().toISOString();
const later = new Date(Date.now() + 6 * 3600_000).toISOString();
const twoPersonMessage = 'This workspace requires two people for high-risk actions: you created or started this task, so another owner must approve it. You can still reject it.';

type Json = Record<string, unknown>;
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });
const member = (userId: string, role: string, workspaceId = 'workspace_foundation'): Json => ({ tenant_id: 'tenant_local', workspace_id: workspaceId, user_id: userId, role, active: true, created_at: now, updated_at: now });

interface Options {
  /** Role the current-membership route reports; null answers 404. */
  role?: string | null;
  /** The PUT answers 403 even though the role looked fine. */
  putForbidden?: boolean;
  /** Approving answers the two-person 403. */
  twoPerson?: boolean;
  /** Workspaces the caller is an active member of. */
  memberOf?: string[];
  /** Answer `GET /me/workspace-memberships` with these memberships (otherwise the call fails). */
  directory?: { workspace_id: string; workspace_name: string | null; role: string }[];
}

async function mockApi(page: Page, options: Options = {}) {
  const calls: { method: string; path: string; body: unknown; workspace: string | undefined }[] = [];
  let policy: Json = { two_person_rule: false, medium_risk_requires_approval: false, updated_by: null, updated_at: null };
  const memberOf = options.memberOf ?? ['workspace_foundation'];
  const task = { id: 'task_mine', title: 'Publish update', prompt: 'Publish', status: 'waiting_approval', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now, created_by: 'user_local' };
  const approval = {
    id: 'approval_mine', task_id: 'task_mine', action: 'external.action', risk_level: 'high', status: 'pending',
    reason: 'External or high-impact actions require explicit approval.', created_at: now, run_id: 'run_mine', step_id: 'step_1',
    arguments: { action: 'Publish' }, payload_hash: '3f'.repeat(32), expires_at: later, decided_at: null, decided_by: null, requested_by: 'user_local',
  };
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const workspace = request.headers()['x-workspace-id'];
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method, path, body, workspace });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });

    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks') return json(options.twoPerson ? [task] : []);
    if (path === '/api/v1/approvals') return json(options.twoPerson ? [approval] : []);
    if (path === '/api/v1/approvals/approval_mine/approve') return json(envelope('forbidden', twoPersonMessage), 403);
    if (path === '/api/v1/workspace-memberships/current') {
      if (!workspace || !memberOf.includes(workspace)) return json(envelope('forbidden', 'Active workspace membership required'), 403);
      if (options.role === null) return json(envelope('not_found', 'Membership not found'), 404);
      return json(member('user_local', options.role ?? 'owner', workspace));
    }
    if (path === '/api/v1/me/workspace-memberships' && options.directory) {
      return json(options.directory.map((entry) => ({ tenant_id: 'tenant_local', status: 'active', ...entry })));
    }
    if (path === '/api/v1/workspace-members') return json([member('user_local', 'owner'), member('user_member', 'member')]);
    if (path === '/api/v1/approval-policy' && method === 'GET') return json(policy);
    if (path === '/api/v1/approval-policy' && method === 'PUT') {
      if (options.putForbidden) return json(envelope('forbidden', 'permission_denied: policy:manage'), 403);
      policy = { ...(body as Json), updated_by: 'user_local', updated_at: now };
      return json(policy);
    }
    return route.abort();
  });
  return calls;
}

test('owners see each switch explained, are warned about a single owner, and save the policy', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#policy');

  await expect(page.getByRole('heading', { name: 'Who must approve risky actions' })).toBeVisible();
  const settings = page.getByRole('region', { name: 'Approval policy settings' });
  await expect(settings.getByText('Never changed: both switches are off by default.')).toBeVisible();
  await expect(settings.getByText(/Another owner must approve them/)).toBeVisible();
  await expect(settings.getByText(/Medium-risk tools pause and wait for an owner/)).toBeVisible();

  const twoPerson = settings.getByRole('switch', { name: 'Two-person rule for high-risk actions' });
  const medium = settings.getByRole('switch', { name: 'Approval for medium-risk actions' });
  await expect(twoPerson).not.toBeChecked();
  await expect(settings.getByRole('button', { name: 'Save policy' })).toBeDisabled();

  await twoPerson.check();
  await expect(settings.getByText(/This workspace has one active owner/)).toBeVisible();
  await medium.check();
  await settings.getByRole('button', { name: 'Save policy' }).click();

  await expect(settings.getByText(/Approval policy saved/)).toBeVisible();
  await expect(settings.getByText(/Last changed .* by user_local\./)).toBeVisible();
  await expect(settings.getByRole('button', { name: 'Save policy' })).toBeDisabled();
  const put = calls.find((c) => c.method === 'PUT');
  expect(put?.body).toEqual({ two_person_rule: true, medium_risk_requires_approval: true });
  expect(put?.workspace).toBe('workspace_foundation');
});

test('members see the policy read-only with the owner access explanation', async ({ page }) => {
  const calls = await mockApi(page, { role: 'member' });
  await page.goto('/#policy');

  await expect(page.getByRole('heading', { name: 'Owner access required' })).toBeVisible();
  await expect(page.getByText(/Your role here is member\./)).toBeVisible();
  await expect(page.getByRole('switch', { name: 'Two-person rule for high-risk actions' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Save policy' })).toHaveCount(0);
  expect(calls.some((c) => c.path === '/api/v1/workspace-members')).toBe(false);
});

test('a 403 on saving shows the API answer and keeps the policy unchanged', async ({ page }) => {
  await mockApi(page, { role: null, putForbidden: true });
  await page.goto('/#policy');

  const medium = page.getByRole('switch', { name: 'Approval for medium-risk actions' });
  await medium.check();
  await page.getByRole('button', { name: 'Save policy' }).click();

  await expect(page.getByRole('heading', { name: 'Owner access required' })).toBeVisible();
  await expect(page.getByText('The API answered: permission_denied: policy:manage')).toBeVisible();
  await expect(medium).not.toBeChecked();
  await expect(medium).toBeDisabled();
});

test('approving your own high-risk task under the two-person rule shows the refusal on the card', async ({ page }) => {
  await mockApi(page, { twoPerson: true });
  await page.goto('/#approvals');

  const card = page.getByRole('article', { name: 'Approval for external.action' });
  await card.getByRole('button', { name: 'Approve' }).click();

  const refusal = card.getByRole('alert');
  await expect(refusal.getByText('Decision refused')).toBeVisible();
  await expect(refusal.getByText(twoPersonMessage)).toBeVisible();
  // The card stays pending and the page itself shows no error.
  await expect(card.getByRole('button', { name: 'Reject' })).toBeEnabled();
  await expect(page.locator('.errorNotice')).toHaveCount(0);
});

test('the workspace switcher checks membership, then every request names the new workspace', async ({ page }) => {
  const calls = await mockApi(page, { memberOf: ['workspace_foundation', 'workspace_sales'] });
  await page.goto('/#policy');

  const switcher = page.getByRole('button', { name: /tenant_local \/ workspace_foundation/ });
  await switcher.click();
  const panel = page.getByRole('dialog', { name: 'Switch workspace' });
  await expect(panel.getByText('Current workspace')).toBeVisible();

  await panel.getByLabel('Another workspace ID').fill('workspace_other');
  await panel.getByRole('button', { name: 'Switch', exact: true }).click();
  await expect(panel.getByRole('alert')).toHaveText('Active workspace membership required');

  await panel.getByLabel('Another workspace ID').fill('workspace_sales');
  await panel.getByRole('button', { name: 'Switch', exact: true }).click();
  await expect(page.getByRole('button', { name: /tenant_local \/ workspace_sales/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Who must approve risky actions' })).toBeVisible();

  const switchedAt = calls.findIndex((c) => c.path === '/api/v1/workspace-memberships/current' && c.workspace === 'workspace_sales');
  expect(switchedAt).toBeGreaterThan(-1);
  await expect.poll(() => calls.slice(switchedAt + 1).filter((c) => c.path === '/api/v1/approval-policy').length).toBeGreaterThan(0);
  // The development session request carries the workspace in its body; every other call in the header.
  expect(calls.slice(switchedAt + 1).every((c) => c.path === '/api/v1/auth/local/session' ? (c.body as Json).workspace_id === 'workspace_sales' : c.workspace === 'workspace_sales')).toBe(true);

  // The selection survives a reload, and the previous workspace stays one click away.
  await page.reload();
  await page.getByRole('button', { name: /tenant_local \/ workspace_sales/ }).click();
  await expect(page.getByRole('dialog', { name: 'Switch workspace' }).getByRole('button', { name: /workspace_foundation/ })).toBeVisible();
});

test('the switcher lists every membership from the API and still checks before switching', async ({ page }) => {
  // docs/identity.md, My workspaces: GET /api/v1/me/workspace-memberships fills the list.
  const calls = await mockApi(page, {
    memberOf: ['workspace_foundation', 'workspace_finance'],
    directory: [
      { workspace_id: 'workspace_finance', workspace_name: 'Finance', role: 'viewer' },
      { workspace_id: 'workspace_foundation', workspace_name: 'Foundation', role: 'owner' },
      // Listed by the API but deactivated meanwhile: the membership check refuses the switch.
      { workspace_id: 'workspace_legal', workspace_name: 'Legal', role: 'member' },
    ],
  });
  await page.goto('/#policy');

  await page.getByRole('button', { name: /tenant_local \/ workspace_foundation/ }).click();
  const panel = page.getByRole('dialog', { name: 'Switch workspace' });
  const list = panel.getByRole('list', { name: 'Workspaces' });
  await expect(list.getByRole('button', { name: /workspace_finance.*Finance · your role: viewer/ })).toBeVisible();
  await expect(list.getByRole('button', { name: /workspace_foundation.*Current workspace · Foundation · your role: owner/ })).toBeVisible();
  await expect(panel.getByText(/every workspace in tenant_local where you are an active member/)).toBeVisible();

  await list.getByRole('button', { name: /workspace_legal/ }).click();
  await expect(panel.getByRole('alert')).toHaveText('Active workspace membership required');
  await expect(page.getByRole('button', { name: /tenant_local \/ workspace_foundation/ })).toBeVisible();

  await list.getByRole('button', { name: /workspace_finance/ }).click();
  await expect(page.getByRole('button', { name: /tenant_local \/ workspace_finance/ })).toBeVisible();
  expect(calls.some((c) => c.path === '/api/v1/workspace-memberships/current' && c.workspace === 'workspace_finance')).toBe(true);
});

test('the switcher falls back to known workspaces when the membership list cannot be loaded', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#policy');
  await page.getByRole('button', { name: /tenant_local \/ workspace_foundation/ }).click();
  const panel = page.getByRole('dialog', { name: 'Switch workspace' });
  await expect(panel.getByText(/could not be loaded, so only the workspaces this browser knows are shown/)).toBeVisible();
  await expect(panel.getByRole('button', { name: /workspace_foundation/ })).toBeVisible();
});
