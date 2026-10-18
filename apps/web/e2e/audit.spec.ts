import { expect, test, type Page } from '@playwright/test';

// The audit log and workspace administration against a mocked API
// (docs/governance-and-scale.md, Audit log; docs/identity.md, Workspace lifecycle).
type Json = Record<string, unknown>;
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });

function auditRow(index: number, overrides: Json = {}): Json {
  const created = new Date(Date.UTC(2026, 8, 30, 12, 0) - index * 60_000).toISOString();
  return {
    id: `audit_${String(index).padStart(3, '0')}`, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation',
    actor: index % 2 ? 'user_local' : 'owner_two', action: index % 3 ? 'workspace_member.role_change' : 'policy_pack.created',
    target: `target_${index}`, outcome: 'success', correlation_id: `corr_${index}`, created_at: created,
    metadata: { role: 'viewer', previous_role: 'member', api_key: '[REDACTED]' }, ...overrides,
  };
}

interface Options { forbidden?: boolean; failFirst?: boolean; total?: number; archived?: boolean; canManage?: boolean }

async function mockApi(page: Page, options: Options = {}) {
  const calls: { method: string; path: string; query: URLSearchParams; body: unknown }[] = [];
  const records = Array.from({ length: options.total ?? 60 }, (_, index) => auditRow(index));
  let failures = options.failFirst ? 1 : 0;
  let workspace: Json = { id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', archived_at: options.archived ? '2026-09-01T00:00:00Z' : null, archived_by: options.archived ? 'user_local' : null };
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method, path, query: url.searchParams, body });
    const json = (value: unknown, status = 200, headers: Record<string, string> = {}) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value), headers });

    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/audit-records/actions') return json({ actions: ['policy_pack.created', 'workspace_member.role_change'] });
    if (path === '/api/v1/audit-records') {
      if (options.forbidden) return json(envelope('forbidden', 'Audit log access requires the owner role in this workspace or the organization administrator role.'), 403);
      if (failures > 0) { failures -= 1; return json(envelope('service_unavailable', 'The database is unavailable'), 503); }
      const q = url.searchParams;
      let rows = records.filter((row) => (!q.get('actor') || row.actor === q.get('actor'))
        && (!q.get('action') || (q.get('action')!.endsWith('*') ? String(row.action).startsWith(q.get('action')!.slice(0, -1)) : row.action === q.get('action')))
        && (!q.get('target') || row.target === q.get('target'))
        && (!q.get('correlation_id') || row.correlation_id === q.get('correlation_id'))
        && (!q.get('since') || String(row.created_at) >= q.get('since')!));
      const start = q.get('cursor') ? Number(q.get('cursor')) : 0;
      const limit = Number(q.get('limit') ?? 50);
      rows = rows.slice(start, start + limit + 1);
      const next = rows.length > limit ? String(start + limit) : null;
      return json({ items: rows.slice(0, limit), next_cursor: next, retention: 'Audit records are kept for as long as the workspace exists, including after it is archived. They are append-only: nobody can edit or delete them, owners included.' });
    }
    if (path === '/api/v1/audit/export') {
      return route.fulfill({ status: 200, contentType: 'text/csv', body: 'id,created_at\naudit_000,2026-09-30\n', headers: { 'content-disposition': 'attachment; filename="audit-workspace_foundation-20261011T120000Z.csv"', 'access-control-allow-origin': '*', 'access-control-expose-headers': 'Content-Disposition' } });
    }
    if (path === '/api/v1/workspaces/current' && method === 'GET') return json(workspace);
    if (path === '/api/v1/workspaces/current/permissions') {
      const manage = options.canManage ?? true;
      return json({ workspace_id: 'workspace_foundation', role: manage ? 'owner' : 'viewer', organization_admin: false, can_manage_workspace: manage, can_create_workspace: manage, can_manage_members: manage, can_read_audit: manage, can_export_audit: manage });
    }
    if (path === '/api/v1/workspaces/current' && method === 'PATCH') { workspace = { ...workspace, name: (body as Json).name }; return json(workspace); }
    if (path === '/api/v1/workspaces/current/archive') {
      if ((body as Json).confirm_name !== workspace.name) return json(envelope('validation_error', 'Type the workspace name'), 422);
      workspace = { ...workspace, archived_at: '2026-10-11T12:00:00Z', archived_by: 'user_local' };
      return json(workspace);
    }
    if (path === '/api/v1/workspaces/current/restore') { workspace = { ...workspace, archived_at: null, archived_by: null }; return json(workspace); }
    if (path === '/api/v1/organization/workspaces') {
      return json({ id: 'ws_finance_1a2b3c4d', tenant_id: 'tenant_local', name: (body as Json).name, created_at: '2026-10-11T12:00:00Z', updated_at: '2026-10-11T12:00:00Z', archived_at: null, archived_by: null }, 201);
    }
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: request.headers()['x-workspace-id'], user_id: 'user_local', role: 'owner', active: true, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' });
    if (path === '/api/v1/workspace-members/summary') return json({ total: 2, active: 2, deactivated: 0, active_owners: 1, pending_invitations: 0 });
    if (path === '/api/v1/workspace-members') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', email: null }, { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'member-3f2c9a1e-77b0-4d2b-9c1e-0a1b2c3d4e5f', role: 'member', active: true, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', email: 'a.long.address.for.layout@example.com' }]);
    if (path === '/api/v1/workspace-invitations') return json([]);
    if (path === '/api/v1/onboarding') return json({ complete: true, model_configured: true });
    if (path === '/api/v1/notification-preferences') return json({ task_completed: true, approval_required: true, run_failed: true, automation_failed: true, email_enabled: false, desktop_enabled: false });
    return route.abort();
  });
  return calls;
}

test('owner reads the audit log newest first, pages with a cursor and opens a record', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#audit');

  const table = page.getByRole('table', { name: 'Audit records' });
  await expect(page.getByRole('heading', { name: '50 records loaded' })).toBeVisible();
  await expect(table.getByRole('row')).toHaveCount(51);
  await expect(page.getByText(/Retention\. Audit records are kept for as long as the workspace exists/)).toBeVisible();

  await page.getByRole('button', { name: 'Load older records' }).click();
  await expect(table.getByRole('row')).toHaveCount(61);
  await expect(page.getByRole('button', { name: 'Load older records' })).toHaveCount(0);
  expect(calls.filter((c) => c.path === '/api/v1/audit-records').map((c) => c.query.get('cursor'))).toEqual([null, '50']);

  // Keyboard: open the detail drawer, read it, close with Escape and land back on the row.
  const first = table.getByRole('button', { name: /^Details of policy_pack.created by owner_two/ }).first();
  await first.focus();
  await page.keyboard.press('Enter');
  const drawer = page.getByRole('dialog', { name: 'Audit record' });
  await expect(drawer.getByText('corr_0', { exact: true })).toBeVisible();
  await expect(drawer.getByText('[REDACTED]')).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Close' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(drawer).toHaveCount(0);
  await expect(first).toBeFocused();

  // "Same correlation id" narrows the log to that chain.
  await first.click();
  await page.getByRole('dialog').getByRole('button', { name: 'Same correlation id' }).click();
  await expect(page.getByRole('heading', { name: '1 record' })).toBeVisible();
  expect(calls.at(-1)?.query.get('correlation_id')).toBe('corr_0');
});

test('filters go to the API, an empty result offers a reset, and export downloads the filtered range', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#audit');
  const form = page.getByRole('form', { name: 'Filter the audit log' });
  await form.getByLabel('Actor', { exact: true }).fill('owner_two');
  await form.getByRole('combobox', { name: 'Action' }).selectOption('workspace_member.role_change');
  await form.getByLabel('From', { exact: true }).fill('2026-09-30');
  await form.getByRole('button', { name: 'Apply filters' }).click();
  await expect(page.getByRole('heading', { name: /records?( loaded)?$/ })).toBeVisible();
  const last = calls.filter((c) => c.path === '/api/v1/audit-records').at(-1)!;
  expect(last.query.get('actor')).toBe('owner_two');
  expect(last.query.get('action')).toBe('workspace_member.role_change');
  expect(last.query.get('since')).toBe(new Date(2026, 8, 30).toISOString());

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  expect((await download).suggestedFilename()).toBe('audit-workspace_foundation-20261011T120000Z.csv');
  await expect(page.getByText(/Downloaded audit-workspace_foundation-20261011T120000Z.csv with the current filters/)).toBeVisible();
  const exported = calls.find((c) => c.path === '/api/v1/audit/export')!;
  expect(exported.query.get('format')).toBe('csv');
  expect(exported.query.get('actor')).toBe('owner_two');

  await form.getByLabel('Target', { exact: true }).fill('target_nothing');
  await form.getByRole('button', { name: 'Apply filters' }).click();
  await expect(page.getByRole('heading', { name: 'No records match these filters' })).toBeVisible();
  await page.getByRole('region', { name: 'Audit records' }).getByRole('button', { name: 'Reset filters' }).click();
  await expect(page.getByRole('heading', { name: '50 records loaded' })).toBeVisible();

  await form.getByLabel('From', { exact: true }).fill('2026-10-05');
  await form.getByLabel('To', { exact: true }).fill('2026-10-01');
  await form.getByRole('button', { name: 'Apply filters' }).click();
  await expect(form.getByText('The start date must be on or before the end date.')).toBeVisible();
});

test('members and viewers see who can read the audit log', async ({ page }) => {
  await mockApi(page, { forbidden: true });
  await page.goto('/#audit');
  await expect(page.getByRole('heading', { name: 'Owner or organization administrator access required' })).toBeVisible();
  await expect(page.getByText(/The API answered: Audit log access requires the owner role/)).toBeVisible();
  await expect(page.getByRole('table')).toHaveCount(0);
});

test('a failed load says what failed and retries', async ({ page }) => {
  await mockApi(page, { failFirst: true });
  await page.goto('/#audit');
  await expect(page.getByRole('heading', { name: 'The audit log could not be loaded' })).toBeVisible();
  await expect(page.getByText('The database is unavailable')).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByRole('heading', { name: '50 records loaded' })).toBeVisible();
});

test('an empty log explains what will appear', async ({ page }) => {
  await mockApi(page, { total: 0 });
  await page.goto('/#audit');
  await expect(page.getByRole('heading', { name: 'Nothing recorded yet' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Manage members' })).toBeVisible();
});

test('an owner renames, archives with the typed name, restores and creates a workspace', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#settings');
  const panel = page.getByRole('region', { name: 'This workspace' });
  await expect(panel.getByText('workspace_foundation', { exact: true })).toBeVisible();

  const rename = panel.getByRole('form', { name: 'Rename workspace' });
  await rename.getByLabel('Workspace name').fill('  Foundation   HQ ');
  await rename.getByRole('button', { name: 'Save name' }).click();
  await expect(panel.getByText('Workspace renamed to Foundation HQ.')).toBeVisible();
  expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ name: 'Foundation HQ' });

  await panel.getByRole('button', { name: 'Archive workspace' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Archive Foundation HQ?' });
  await expect(dialog.getByLabel('Workspace name')).toBeFocused();
  await expect(dialog.getByRole('button', { name: 'Archive workspace' })).toBeDisabled();
  await dialog.getByLabel('Workspace name').fill('Foundation HQ');
  await dialog.getByRole('button', { name: 'Archive workspace' }).click();
  await expect(panel.getByText('archived', { exact: true })).toBeVisible();
  await expect(page.getByText('This workspace is archived, so it is read-only.').first()).toBeVisible();
  await panel.getByRole('button', { name: 'Restore workspace' }).click();
  await expect(panel.getByText('Workspace restored. It is writable again.')).toBeVisible();
  await expect(page.getByText('This workspace is archived, so it is read-only.')).toHaveCount(0);

  const create = panel.getByRole('form', { name: 'Create a workspace' });
  await create.getByLabel('Workspace ID (optional)').fill('bad id');
  await create.getByLabel('Name').fill('Finance');
  await create.getByRole('button', { name: 'Create workspace' }).click();
  await expect(create.getByText(/Workspace IDs are 3 to 80 letters/)).toBeVisible();
  await create.getByLabel('Workspace ID (optional)').fill('');
  await create.getByRole('button', { name: 'Create workspace' }).click();
  await expect(panel.getByText('Workspace Finance created. You are its owner.')).toBeVisible();
  await panel.getByRole('button', { name: 'Switch to Finance' }).click();
  await expect(page.getByRole('button', { name: 'Local / Finance 1a2b3c4d' })).toBeVisible();
});

test('a viewer sees the workspace read-only with who can change it', async ({ page }) => {
  await mockApi(page, { canManage: false });
  await page.goto('/#settings');
  const panel = page.getByRole('region', { name: 'This workspace' });
  await expect(panel.getByText(/Renaming, archiving and restoring need the owner role/)).toBeVisible();
  await expect(panel.getByRole('form', { name: 'Rename workspace' })).toHaveCount(0);
  await expect(panel.getByRole('button', { name: 'Archive workspace' })).toHaveCount(0);
  await expect(panel.getByRole('form', { name: 'Create a workspace' })).toHaveCount(0);
});

const sizes = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const [width, height] of sizes) {
  test(`audit log, members and workspace settings fit at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockApi(page);
    for (const hash of ['#audit', '#members', '#settings']) {
      await page.goto(`/${hash}`);
      await page.reload();
      await expect(page.getByRole('heading', { level: 2 }).first()).toBeVisible();
      if (hash === '#audit') await expect(page.getByRole('table', { name: 'Audit records' })).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${hash} horizontal overflow at ${width}px`).toBeLessThanOrEqual(0);
    }
    await page.goto('/#audit'); await page.reload();
    await page.getByRole('table', { name: 'Audit records' }).getByRole('button').first().click();
    const drawer = page.getByRole('dialog', { name: 'Audit record' });
    const box = await drawer.boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width + 1).toBe(true);
  });
}
