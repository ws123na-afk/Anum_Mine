import { expect, test, type Page } from '@playwright/test';

// Governance view: tenant-wide policy is editable only for an organization administrator,
// whatever the caller's workspace role (docs/governance-and-scale.md#who-may-change-what).
// The API is mocked; `can_manage_organization_policy` is its answer for the caller.
const now = new Date().toISOString();
type Json = Record<string, unknown>;
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });
const orgAdminRequired = 'Organization administrator role required: this setting applies to every workspace in the organization';

interface Options {
  /** What the summary reports for the caller. */
  canManage: boolean;
  /** Writes answer 403 even though the summary said the caller may manage. */
  writesForbidden?: boolean;
}

async function mockApi(page: Page, options: Options) {
  const calls: { method: string; path: string }[] = [];
  const packs: Json[] = [{
    id: 'policy_1', tenant_id: 'tenant_local', name: 'Organization baseline', description: 'Approval-first defaults', version: 1, active: true,
    rules: [{ action: 'integration.write', effect: 'require_approval', conditions: {} }], created_by: 'admin', created_at: now,
  }];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    calls.push({ method, path });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });

    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/workspace-memberships/current') {
      return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true, created_at: now, updated_at: now });
    }
    if (path === '/api/v1/organization/governance') {
      return json({
        tenant_id: 'tenant_local', policy_packs: packs.length, active_policy_packs: packs.filter((p) => p.active).length, role_templates: 1,
        approval_rules: 1, memory_governance_configured: false, can_manage_organization_policy: options.canManage,
      });
    }
    if (path === '/api/v1/policy-packs' && method === 'GET') return json(packs);
    if (path === '/api/v1/role-templates' && method === 'GET') return json([{ id: 'role_1', tenant_id: 'tenant_local', name: 'Operator', permissions: ['task:read', 'task:run'], created_at: now }]);
    if (path === '/api/v1/organization/approval-rules' && method === 'GET') {
      return json([{ id: 'rule_1', tenant_id: 'tenant_local', name: 'Finance dual control', action_pattern: 'finance.*', minimum_approvers: 2, required_roles: ['owner'], enabled: true, created_at: now }]);
    }
    if (method === 'POST' && (path.startsWith('/api/v1/policy-packs') || path === '/api/v1/role-templates' || path === '/api/v1/organization/approval-rules')) {
      if (options.writesForbidden || !options.canManage) return json(envelope('forbidden', orgAdminRequired), 403);
      if (path === '/api/v1/policy-packs/policy_1/archive') {
        packs[0] = { ...packs[0], active: false };
        return json(packs[0]);
      }
    }
    return route.abort();
  });
  return calls;
}

test('a workspace owner who is not an organization administrator reads tenant-wide policy with the reason', async ({ page }) => {
  const calls = await mockApi(page, { canManage: false });
  await page.goto('/#governance');

  await expect(page.getByRole('heading', { name: 'Governance center' })).toBeVisible();
  await expect(page.getByText(/Read only: policy packs, role templates and approval rules apply to every workspace in this organization/)).toBeVisible();
  await expect(page.getByText(/Owning this workspace is not enough/)).toBeVisible();
  // Everything is still listed.
  await expect(page.getByText('Organization baseline')).toBeVisible();
  await expect(page.getByText('Operator')).toBeVisible();
  await expect(page.getByText('Finance dual control')).toBeVisible();
  // No control to change it.
  await expect(page.getByRole('button', { name: 'Add baseline' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Archive Organization baseline/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Add role template' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Add approval rule' })).toHaveCount(0);
  // The workspace's own audit export stays available.
  await expect(page.getByRole('button', { name: 'Export audit' })).toBeVisible();
  const governanceWrite = /^\/api\/v1\/(policy-packs|role-templates|organization\/)/;
  expect(calls.some((c) => c.method !== 'GET' && governanceWrite.test(c.path))).toBe(false);
});

test('an organization administrator changes tenant-wide policy', async ({ page }) => {
  await mockApi(page, { canManage: true });
  await page.goto('/#governance');

  await expect(page.getByRole('button', { name: 'Add baseline' })).toBeVisible();
  await expect(page.getByText(/Read only:/)).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Add role template' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add approval rule' })).toBeVisible();

  await page.getByRole('button', { name: 'Archive Organization baseline v1' }).click();
  await expect(page.getByText('Organization baseline v1 archived.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Activate Organization baseline v1' })).toBeVisible();
});

test('a 403 on a change shows the API answer in place', async ({ page }) => {
  await mockApi(page, { canManage: true, writesForbidden: true });
  await page.goto('/#governance');

  await page.getByRole('button', { name: 'Archive Organization baseline v1' }).click();
  await expect(page.getByText(`Not allowed: ${orgAdminRequired}`)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Archive Organization baseline v1' })).toBeVisible();
});
