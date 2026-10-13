import { expect, test, type Page } from '@playwright/test';

// Governance view: tenant-wide policy is editable only for an organization administrator,
// whatever the caller's workspace role (docs/governance-and-scale.md#who-may-change-what).
// The API is mocked; `can_manage_organization_policy` is its answer for the caller.
const now = new Date().toISOString();
type Json = Record<string, unknown>;
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });
const orgAdminRequired = 'Organization administrator role required: this setting applies to every workspace in the organization, so owning a workspace is not enough. Ask the administrator of your ANUM sign-in (Keycloak) to assign you the realm role org_admin (on a self-hosted server: infra/oracle/keycloak-2fa.sh grant-org-admin <your username>), then sign out and sign in again';

interface Options {
  /** What the summary reports for the caller. */
  canManage: boolean;
  /** Writes answer 403 even though the summary said the caller may manage. */
  writesForbidden?: boolean;
  /** The caller's workspace role (installing a package needs owner). */
  role?: 'owner' | 'member';
  /** The governance summary (the capability check) fails with a 500. */
  summaryFails?: boolean;
}

async function mockApi(page: Page, options: Options) {
  const calls: { method: string; path: string }[] = [];
  const targets: Json[] = [{
    id: 'configured-model', region: 'local', provider: 'ollama', model: 'llama3.2', status: 'unverified',
    modalities: ['text'], sensitivity: ['standard'], cost_per_1k_tokens: 0, latency_ms: 1,
  }];
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
      return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: options.role ?? 'owner', active: true, created_at: now, updated_at: now });
    }
    if (path === '/api/v1/organization/governance' && options.summaryFails) return json(envelope('internal_error', 'Governance store unavailable'), 500);
    if (path === '/api/v1/organization/governance') {
      return json({
        tenant_id: 'tenant_local', policy_packs: packs.length, active_policy_packs: packs.filter((p) => p.active).length, role_templates: 1,
        approval_rules: 1, memory_governance_configured: false, can_manage_organization_policy: options.canManage,
      });
    }
    if (path === '/api/v1/policy-packs' && method === 'GET') return json(packs);
    if (path === '/api/v1/enterprise/operations') {
      return json({ tenant_id: 'tenant_local', active_regions: 1, healthy_targets: 0, degraded_targets: 0, installed_packages: 0, failover_ready: false, generated_at: now });
    }
    if (path === '/api/v1/marketplace/packages' && method === 'GET') {
      return json([{ id: 'skill.research-core', name: 'Research Core', kind: 'skill', version: '1.0.0', publisher: 'ANUM', verified: true, permissions: ['memory:read'], regions: ['local'] }]);
    }
    if (path === '/api/v1/marketplace/installs' && method === 'GET') return json([]);
    if (path === '/api/v1/routing/targets' && method === 'GET') return json(targets);
    if (path.startsWith('/api/v1/routing/targets/') && method === 'PUT') {
      if (options.writesForbidden || !options.canManage) return json(envelope('forbidden', orgAdminRequired), 403);
      const saved = request.postDataJSON() as Json;
      const index = targets.findIndex((t) => t.id === saved.id);
      if (index >= 0) targets[index] = saved; else targets.push(saved);
      return json(saved);
    }
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
  await expect(page.getByText(/assign you the realm role org_admin/)).toBeVisible();
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

test('enterprise: a workspace owner who is not an organization administrator reads routing targets with the reason', async ({ page }) => {
  const calls = await mockApi(page, { canManage: false });
  await page.goto('/#enterprise');

  await expect(page.getByRole('heading', { level: 1, name: 'Enterprise' })).toBeVisible();
  await expect(page.getByText(/Read only: routing targets and the marketplace catalog apply to every workspace in this organization/)).toBeVisible();
  await expect(page.getByRole('rowheader', { name: 'configured-model' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add routing target' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Edit configured-model' })).toHaveCount(0);
  // Installing into this workspace is the workspace owner's call, not the organization's.
  await expect(page.getByRole('button', { name: 'Install Research Core' })).toBeVisible();
  expect(calls.some((c) => c.method === 'PUT' && c.path.startsWith('/api/v1/routing/targets/'))).toBe(false);
});

test('enterprise: a member sees neither routing edits nor installs, and why', async ({ page }) => {
  await mockApi(page, { canManage: false, role: 'member' });
  await page.goto('/#enterprise');

  await expect(page.getByText('Research Core')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Install Research Core' })).toHaveCount(0);
  await expect(page.getByText(/Only workspace owners can install or uninstall packages/)).toBeVisible();
});

test('enterprise: an organization administrator adds a routing target', async ({ page }) => {
  const calls = await mockApi(page, { canManage: true });
  await page.goto('/#enterprise');

  await expect(page.getByText(/Read only:/)).toHaveCount(0);
  await page.getByRole('button', { name: 'Add routing target' }).click();
  const form = page.getByRole('form', { name: 'Routing target' });
  await form.getByLabel('Target id').fill('eu-primary');
  await form.getByLabel('Region').fill('eu-west');
  await form.getByLabel('Provider').fill('openai-compatible');
  await form.getByLabel('Model').fill('primary');
  await form.getByRole('button', { name: 'Save target' }).click();
  await expect(page.getByText('Routing target eu-primary saved.')).toBeVisible();
  await expect(page.getByRole('rowheader', { name: 'eu-primary' })).toBeVisible();
  expect(calls.some((c) => c.method === 'PUT' && c.path === '/api/v1/routing/targets/eu-primary')).toBe(true);
});

test('enterprise: a 403 on saving a routing target shows the API answer in place', async ({ page }) => {
  await mockApi(page, { canManage: true, writesForbidden: true });
  await page.goto('/#enterprise');

  await page.getByRole('button', { name: 'Edit configured-model' }).click();
  await page.getByRole('form', { name: 'Routing target' }).getByRole('button', { name: 'Save target' }).click();
  await expect(page.getByText(`Not allowed: ${orgAdminRequired}`)).toBeVisible();
});

test('enterprise: if the capability check fails the targets are still shown, read only, with the error', async ({ page }) => {
  await mockApi(page, { canManage: true, summaryFails: true });
  await page.goto('/#enterprise');

  await expect(page.getByText(/Could not check whether you may change routing targets, so they are shown read only/)).toBeVisible();
  await expect(page.getByRole('rowheader', { name: 'configured-model' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add routing target' })).toHaveCount(0);
});

// Routing targets: a table whose target id, status and edit button stay readable and inside the
// page at every size (the less important columns drop out on narrow screens).
for (const size of [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
]) {
  test(`enterprise: routing targets are readable at ${size.width}x${size.height}`, async ({ page }) => {
    await page.setViewportSize(size);
    await mockApi(page, { canManage: true });
    await page.goto('/#enterprise');
    const row = page.getByRole('row', { name: /configured-model/ });
    await expect(row.getByRole('rowheader', { name: 'configured-model' })).toBeVisible();
    const edit = row.getByRole('button', { name: 'Edit configured-model' });
    await expect(edit).toBeVisible();
    const editBox = (await edit.boundingBox())!;
    expect(editBox.x).toBeGreaterThanOrEqual(0);
    expect(editBox.x + editBox.width).toBeLessThanOrEqual(size.width);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}
