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
    modalities: ['text'], sensitivity: ['standard'], cost_per_1k_tokens: 0, latency_ms: 1, enabled: true, last_checked_at: null, measured_latency_ms: null, last_error: null,
  }, {
    id: 'eu-live', region: 'eu-west', provider: 'ollama', model: 'llama3.2', status: 'healthy',
    modalities: ['text'], sensitivity: ['standard'], cost_per_1k_tokens: 0.01, latency_ms: 200, enabled: true, last_checked_at: now, measured_latency_ms: 240, last_error: null,
  }];
  const packs: Json[] = [{
    id: 'policy_1', tenant_id: 'tenant_local', name: 'Organization baseline', description: 'Approval-first defaults', version: 1, active: true,
    rules: [{ action: 'integration.write', effect: 'require_approval', conditions: {} }], created_by: 'admin', created_at: now,
  }, {
    id: 'policy_old', tenant_id: 'tenant_local', name: 'Finance guard', description: 'Payments', version: 1, active: false,
    rules: [{ action: 'payment.send', effect: 'require_approval', conditions: {} }], created_by: 'admin', created_at: now,
  }, {
    id: 'policy_new', tenant_id: 'tenant_local', name: 'Finance guard', description: 'Payments and refunds', version: 2, active: true,
    rules: [{ action: 'payment.send', effect: 'deny', conditions: {} }, { action: 'refund.*', effect: 'require_approval', conditions: { amount_above: 250 } }], created_by: 'admin', created_at: now,
  }];
  let memory: Json | null = null;
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
    if (path === '/api/v1/organization/memory-governance' && method === 'GET') return memory ? json(memory) : json(envelope('not_found', 'Memory governance is not configured'), 404);
    if (path === '/api/v1/organization/memory-governance' && method === 'PUT') {
      if (options.writesForbidden || !options.canManage) return json(envelope('forbidden', orgAdminRequired), 403);
      memory = { ...(request.postDataJSON() as Json), tenant_id: 'tenant_local', updated_by: 'user_local', updated_at: now };
      return json(memory);
    }
    const packDelete = path.match(/^\/api\/v1\/policy-packs\/([^/]+)$/);
    if (packDelete && method === 'DELETE') {
      if (options.writesForbidden || !options.canManage) return json(envelope('forbidden', orgAdminRequired), 403);
      const index = packs.findIndex((p) => p.id === packDelete[1]);
      if (index < 0) return json(envelope('not_found', 'Policy pack not found'), 404);
      if (packs[index].active) return json(envelope('conflict', `${packs[index].name} v${packs[index].version} is active and enforced in every workspace. Archive it first, then delete it.`), 409);
      packs.splice(index, 1);
      return route.fulfill({ status: 204 });
    }
    if (path === '/api/v1/policy-packs' && method === 'POST') {
      if (options.writesForbidden || !options.canManage) return json(envelope('forbidden', orgAdminRequired), 403);
      const body = request.postDataJSON() as Json;
      const same = packs.filter((p) => String(p.name).toLowerCase() === String(body.name).toLowerCase());
      for (const p of same) p.active = false;
      const created = { id: `policy_${packs.length + 1}`, tenant_id: 'tenant_local', ...body, version: same.length + 1, active: true, created_by: 'user_local', created_at: now };
      packs.push(created);
      return json(created, 201);
    }
    const probe = path.match(/^\/api\/v1\/routing\/targets\/([^/]+)\/probe$/);
    if (probe && method === 'POST') {
      if (options.writesForbidden || !options.canManage) return json(envelope('forbidden', orgAdminRequired), 403);
      const index = targets.findIndex((t) => t.id === probe[1]);
      targets[index] = { ...targets[index], status: 'healthy', last_checked_at: now, measured_latency_ms: 310, last_error: null, probe_workspace_id: 'workspace_foundation' };
      return json(targets[index]);
    }
    const targetDelete = path.match(/^\/api\/v1\/routing\/targets\/([^/]+)$/);
    if (targetDelete && method === 'DELETE') {
      if (options.writesForbidden || !options.canManage) return json(envelope('forbidden', orgAdminRequired), 403);
      const index = targets.findIndex((t) => t.id === targetDelete[1]);
      if (index < 0) return json(envelope('not_found', 'Routing target not found'), 404);
      const target = targets[index];
      if (target.enabled && target.last_checked_at && (target.status === 'healthy' || target.status === 'degraded')) return json(envelope('conflict', `Routing target ${target.id} is in use: it is in routing and its last probe found it ${target.status}. Take it out of routing first, then delete it.`), 409);
      targets.splice(index, 1);
      return route.fulfill({ status: 204 });
    }
    if (path === '/api/v1/enterprise/operations') {
      const routed = targets.filter((t) => t.enabled !== false);
      const probed = routed.filter((t) => t.last_checked_at);
      return json({ tenant_id: 'tenant_local', active_regions: probed.length, healthy_targets: probed.filter((t) => t.status === 'healthy').length, degraded_targets: 0, installed_packages: 0, failover_ready: false, generated_at: now, unprobed_targets: routed.length - probed.length, disabled_targets: targets.length - routed.length });
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

  // One heading: the page title.
  await expect(page.getByRole('heading', { level: 1, name: 'Governance' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Governance center' })).toHaveCount(0);
  // Each figure says what it counts.
  await expect(page.getByText('Enforced in every workspace now')).toBeVisible();
  // One line says what is read only; how to get the role waits behind a disclosure.
  const notice = page.getByRole('note').filter({ hasText: 'Read only: policy packs, role templates, approval rules and memory governance apply to every workspace in this organization, so only an organization administrator can change them.' });
  await expect(notice).toBeVisible();
  await expect(notice.getByText(/Owning this workspace is not enough/)).toBeHidden();
  await notice.getByText('How to get access').click();
  await expect(notice.getByText(/Owning this workspace is not enough/)).toBeVisible();
  await expect(notice.getByText(/assign you the realm role org_admin/)).toBeVisible();
  await expect(notice.locator('code')).toHaveText('infra/oracle/keycloak-2fa.sh grant-org-admin <your username>');
  // Everything is still listed.
  await expect(page.getByText('Organization baseline')).toBeVisible();
  await expect(page.getByText('Operator')).toBeVisible();
  await expect(page.getByText('Finance dual control')).toBeVisible();
  // No control to change it.
  await expect(page.getByRole('button', { name: 'New policy pack' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'New version of Organization baseline' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Save memory governance' })).toHaveCount(0);
  await expect(page.getByText('Not saved yet: until an organization administrator saves these settings')).toBeVisible();
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

  await expect(page.getByRole('button', { name: 'New policy pack' })).toBeVisible();
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
  await expect(page.getByRole('button', { name: 'Probe configured-model' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Delete eu-live' })).toHaveCount(0);
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

test('enterprise: health is measured by a probe, never typed, and the figures say what is unprobed', async ({ page }) => {
  const calls = await mockApi(page, { canManage: true });
  await page.goto('/#enterprise');
  // The form has no status field; the built-in target is shown as not yet probed and the figures count only probed targets.
  const configured = page.getByRole('row', { name: /configured-model/ });
  await expect(configured.getByText('Not yet probed')).toBeVisible();
  await expect(configured.getByText('Cost and latency not measured yet')).toBeVisible();
  await expect(page.getByText('1 not yet probed')).toBeVisible();
  await page.getByRole('button', { name: 'Edit configured-model' }).click();
  await expect(page.getByRole('form', { name: 'Routing target' }).getByLabel('Status')).toHaveCount(0);
  await expect(page.getByRole('form', { name: 'Routing target' }).getByLabel('In routing')).toBeChecked();
  await page.getByRole('button', { name: 'Cancel' }).click();
  await page.getByRole('button', { name: 'Probe configured-model' }).click();
  await expect(page.getByText('configured-model probed: healthy.')).toBeVisible();
  await expect(configured.getByText('310 ms, measured', { exact: false })).toBeVisible();
  await expect(page.getByText('None degraded')).toBeVisible();
  expect(calls.some((c) => c.method === 'POST' && c.path === '/api/v1/routing/targets/configured-model/probe')).toBe(true);
});

test('enterprise: a target in use cannot be deleted until it is taken out of routing', async ({ page }) => {
  const calls = await mockApi(page, { canManage: true });
  await page.goto('/#enterprise');
  const live = page.getByRole('row', { name: /eu-live/ });
  await expect(live.getByText('healthy')).toBeVisible();
  // The delete control is disabled while the target serves, and says why.
  const del = page.getByRole('button', { name: 'Delete eu-live' });
  await expect(del).toBeDisabled();
  await expect(del).toHaveAttribute('title', /In use: it is in routing/);
  await page.getByRole('button', { name: 'Take eu-live out of routing' }).click();
  await expect(page.getByText('eu-live taken out of routing.')).toBeVisible();
  await expect(live.getByText('Out of routing')).toBeVisible();
  await page.getByRole('button', { name: 'Delete eu-live' }).click();
  await page.getByRole('alertdialog', { name: 'Delete routing target eu-live?' }).getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText('Routing target eu-live deleted.')).toBeVisible();
  await expect(page.getByRole('rowheader', { name: 'eu-live' })).toHaveCount(0);
  expect(calls.filter((c) => c.method === 'DELETE' && c.path === '/api/v1/routing/targets/eu-live')).toHaveLength(1);
  expect(calls.some((c) => c.method === 'PUT' && c.path === '/api/v1/routing/targets/eu-live')).toBe(true);
});

test('enterprise: the marketplace says plainly that an install is a record only', async ({ page }) => {
  await mockApi(page, { canManage: true });
  await page.goto('/#enterprise');
  await expect(page.getByText(/An install here records this workspace's adoption of a package and nothing more/).first()).toBeVisible();
});

test('governance: the editor publishes a pack from the baseline template', async ({ page }) => {
  const calls = await mockApi(page, { canManage: true });
  await page.goto('/#governance');
  await page.getByRole('button', { name: 'New policy pack' }).click();
  const form = page.getByRole('form', { name: 'New policy pack' });
  await form.getByLabel('Start from').selectOption('baseline');
  await expect(form.getByLabel('Pack name')).toHaveValue('Organization baseline');
  await expect(form.getByLabel('Action pattern of rule 1')).toHaveValue('integration.*.write');
  // Reorder, remove and add rules; a bad JSON condition is refused in place.
  await form.getByRole('button', { name: 'Move rule 3 up' }).click();
  await expect(form.getByLabel('Action pattern of rule 2')).toHaveValue('memory.delete');
  await form.getByRole('button', { name: 'Remove rule 3' }).click();
  await form.getByRole('button', { name: 'Add rule' }).click();
  await form.getByLabel('Action pattern of rule 3').fill('email.send');
  await form.getByLabel('Effect of rule 3').selectOption('deny');
  await form.getByLabel('Conditions of rule 3').fill('nope');
  await form.getByRole('button', { name: 'Publish' }).click();
  await expect(form.getByRole('alert')).toHaveText(/Rule 3: conditions must be a JSON object/);
  await form.getByLabel('Conditions of rule 3').fill('');
  await form.getByRole('button', { name: 'Publish' }).click();
  await expect(page.getByText('Organization baseline v2 published and active.')).toBeVisible();
  const created = calls.find((c) => c.method === 'POST' && c.path === '/api/v1/policy-packs');
  expect(created).toBeTruthy();
  await expect(page.getByRole('article').filter({ hasText: 'Organization baseline' }).getByText('Active: v2')).toBeVisible();
});

test('governance: versions can be listed, compared and an archived one deleted', async ({ page }) => {
  const calls = await mockApi(page, { canManage: true });
  await page.goto('/#governance');
  await page.getByRole('button', { name: '2 versions of Finance guard' }).click();
  const versions = page.getByRole('list', { name: '2 versions of Finance guard' });
  await expect(versions.locator('.govVersion')).toHaveText(['v2', 'v1']);
  await page.getByRole('button', { name: 'Compare versions of Finance guard' }).click();
  const compare = page.getByRole('dialog', { name: 'Compare Finance guard' });
  await expect(compare.getByText('Changed')).toBeVisible();
  await expect(compare.getByText('Added')).toBeVisible();
  await expect(compare.getByText('refund.*: require_approval {"amount_above":250}')).toBeVisible();
  await compare.getByRole('button', { name: 'Close' }).click();
  // The active version has no delete; the archived one does.
  await expect(page.getByRole('button', { name: 'Delete Finance guard v2' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Delete Finance guard v1' }).click();
  await page.getByRole('alertdialog', { name: 'Delete Finance guard v1?' }).getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText('Finance guard v1 deleted.')).toBeVisible();
  expect(calls.some((c) => c.method === 'DELETE' && c.path === '/api/v1/policy-packs/policy_old')).toBe(true);
  // Search narrows the groups and says how many are shown.
  await page.getByLabel('Search policy packs').fill('finance');
  await expect(page.getByText('1 of 2 packs shown')).toBeVisible();
  await expect(page.getByText('Organization baseline', { exact: true })).toHaveCount(0);
  await page.getByLabel('Search policy packs').fill('zzz');
  await expect(page.getByText('No policy pack matches "zzz".')).toBeVisible();
});

test('governance: memory governance is saved through the existing route', async ({ page }) => {
  const calls = await mockApi(page, { canManage: true });
  await page.goto('/#governance');
  const form = page.getByRole('form', { name: 'Memory governance' });
  await form.getByLabel('Default retention (days)').fill('0');
  await form.getByRole('button', { name: 'Save memory governance' }).click();
  await expect(form.getByRole('alert')).toHaveText(/from 1 to 3650/);
  await form.getByLabel('Default retention (days)').fill('90');
  await form.getByLabel('Allowed source types (comma separated)').fill('task, conversation');
  await form.getByLabel('Allow permanent retention').check();
  await form.getByRole('button', { name: 'Save memory governance' }).click();
  await expect(page.getByText('Memory governance saved.')).toBeVisible();
  await expect(page.getByText('Saved by user_local,', { exact: false })).toBeVisible();
  const put = calls.find((c) => c.method === 'PUT' && c.path === '/api/v1/organization/memory-governance');
  expect(put).toBeTruthy();
  // Role templates and approval rules search in place.
  await page.getByLabel('Search role templates').fill('run');
  await expect(page.getByText('1 of 1 template shown')).toBeVisible();
  await page.getByLabel('Search role templates').fill('nothing');
  await expect(page.getByText('No role template matches "nothing".')).toBeVisible();
  await page.getByLabel('Search approval rules').fill('mail');
  await expect(page.getByText('No approval rule matches "mail".')).toBeVisible();
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
