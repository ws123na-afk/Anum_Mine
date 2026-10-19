import { expect, test, type Page } from '@playwright/test';

// Marketplace (Enterprise view): search and filters, install with a permission summary,
// uninstall, and the organization administrator's publish and delete, against a mocked API
// (docs/governance-and-scale.md#marketplace).
const now = new Date().toISOString();
type Json = Record<string, unknown>;

const check = (status: string, detail: string): Json => ({ status, installable: status === 'verified', digest: 'ab'.repeat(32), key_id: status === 'unsigned' ? null : 'acme-2026', signer: status === 'unsigned' ? null : 'Acme', key_source: status === 'unsigned' ? null : 'tenant', fingerprint: null, signed_at: null, detail });

async function mockApi(page: Page, { orgAdmin = false, role = 'owner', signing = false }: { orgAdmin?: boolean; role?: string; signing?: boolean } = {}) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const packages: Json[] = [
    { id: 'acme.crm-sync', name: 'CRM sync', kind: 'integration', version: '1.2.0', publisher: 'Acme', verified: true, permissions: ['integration:read', 'memory:read'], regions: ['eu-west'], verification: check('verified', 'Signed by Acme with key acme-2026.') },
    { id: 'beta.notes', name: 'Meeting notes', kind: 'skill', version: '0.3.0', publisher: 'Beta Labs', verified: false, permissions: [], regions: [], verification: null },
  ];
  let installs: Json[] = [];
  if (signing) {
    packages.push({ id: 'gamma.erp', name: 'ERP bridge', kind: 'integration', version: '2.0.0', publisher: 'Gamma', verified: true, permissions: ['integration:write'], regions: [], verification: check('unsigned', 'This package is not signed. An organization administrator signs it with a trusted publisher key before anyone can install it.') });
    packages.push({ id: 'delta.mail', name: 'Mail relay', kind: 'integration', version: '1.0.0', publisher: 'Delta', verified: true, permissions: [], regions: [], verification: check('revoked', 'Signed with key acme-2026, which an organization administrator revoked: Leaked.') });
    installs = [{ package_id: 'delta.mail', version: '1.0.0', enabled: false, installed_by: 'user_local', installed_at: now, disabled_reason: 'Turned off automatically: the signing key acme-2026 was revoked (Leaked).', verification: check('revoked', 'Signed with key acme-2026, which an organization administrator revoked: Leaked.') }];
  }
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method, path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role, active: true, created_at: now, updated_at: now });
    if (path === '/api/v1/organization/governance') return json({ tenant_id: 'tenant_local', policy_packs: 0, active_policy_packs: 0, role_templates: 0, approval_rules: 0, memory_governance_configured: false, can_manage_organization_policy: orgAdmin });
    if (path === '/api/v1/enterprise/operations') return json({ tenant_id: 'tenant_local', active_regions: 1, healthy_targets: 1, degraded_targets: 0, installed_packages: installs.length, failover_ready: false, generated_at: now });
    if (path === '/api/v1/routing/targets') return json([]);
    if (path === '/api/v1/marketplace/packages' && method === 'GET') return json(packages);
    if (path === '/api/v1/marketplace/installs') return json(installs);
    const install = path.match(/^\/api\/v1\/marketplace\/packages\/([^/]+)\/install$/);
    if (install && method === 'POST') { installs.push({ package_id: install[1], version: (body as Json).version, enabled: true, installed_by: 'user_local', installed_at: now }); return json(installs.at(-1), 201); }
    if (install && method === 'PATCH') {
      const target = installs.find((i) => i.package_id === install[1])!;
      if ((body as Json).enabled && !(target.verification as Json).installable && !(body as Json).acknowledge_flagged) return json({ error: { code: 'conflict', message: 'Confirm', correlation_id: 'c', details: [] } }, 409);
      Object.assign(target, { enabled: (body as Json).enabled, disabled_reason: null });
      return json(target);
    }
    const sign = path.match(/^\/api\/v1\/marketplace\/packages\/([^/]+)\/signature$/);
    if (sign && method === 'PUT') { const p = packages.find((x) => x.id === sign[1])!; p.verification = check('verified', 'Signed by Acme with key acme-2026.'); return json(p); }
    if (install && method === 'DELETE') { installs = installs.filter((i) => i.package_id !== install[1]); return route.fulfill({ status: 204 }); }
    const pkg = path.match(/^\/api\/v1\/marketplace\/packages\/([^/]+)$/);
    if (pkg && method === 'PUT') { packages.push(body as Json); return json(body); }
    if (pkg && method === 'DELETE') {
      if (installs.some((i) => i.package_id === pkg[1])) return json({ error: { code: 'conflict', message: 'Installed marketplace package cannot be deleted', correlation_id: 'c', details: [] } }, 409);
      packages.splice(packages.findIndex((p) => p.id === pkg[1]), 1); return route.fulfill({ status: 204 });
    }
    return route.abort();
  });
  return calls;
}

test('an owner filters the catalog, reviews permissions, installs and uninstalls', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#enterprise');
  await expect(page.getByText('2 of 2 packages · 0 installed')).toBeVisible();
  await page.getByLabel('Verified only').check();
  await expect(page.getByText('1 of 2 packages · 0 installed')).toBeVisible();
  await page.getByLabel('Verified only').uncheck();
  await page.getByLabel('Search packages').fill('beta');
  await expect(page.getByText('Meeting notes')).toBeVisible();
  await expect(page.getByText('CRM sync')).toHaveCount(0);
  await page.getByLabel('Search packages').fill('');

  await page.getByRole('button', { name: 'Install Meeting notes' }).click();
  const unverified = page.getByRole('dialog', { name: 'Install Meeting notes?' });
  await expect(unverified.getByText(/Not verified/)).toBeVisible();
  await unverified.getByRole('button', { name: 'Cancel' }).click();

  await page.getByRole('button', { name: 'Install CRM sync' }).click();
  const dialog = page.getByRole('dialog', { name: 'Install CRM sync?' });
  await expect(dialog.getByText('Integration CRM sync v1.2.0 from Acme.')).toBeVisible();
  await expect(dialog.getByText('Requests 2 permissions in this workspace.')).toBeVisible();
  await expect(dialog.getByText('memory:read')).toBeVisible();
  await dialog.getByRole('button', { name: 'Install', exact: true }).click();
  await expect(page.getByText('CRM sync installed in this workspace.')).toBeVisible();
  expect(calls.find((c) => c.method === 'POST' && c.path.endsWith('/install'))?.body).toEqual({ version: '1.2.0' });

  await page.getByRole('button', { name: 'Uninstall' }).click();
  await page.locator('.marketActions').getByRole('button', { name: 'Uninstall' }).click();
  await expect(page.getByText('CRM sync uninstalled from this workspace.')).toBeVisible();
});

test('an organization administrator publishes and deletes packages; an installed one explains the conflict', async ({ page }) => {
  await mockApi(page, { orgAdmin: true });
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: 'Publish package' }).click();
  const dialog = page.getByRole('dialog', { name: 'Publish a package' });
  await dialog.getByLabel('Package id').fill('acme.digest');
  await dialog.getByLabel('Name', { exact: true }).fill('Digest');
  await dialog.getByLabel('Publisher', { exact: true }).fill('Acme');
  await dialog.getByLabel(/Permissions it requests/).fill('memory:read');
  await dialog.getByRole('button', { name: 'Publish' }).click();
  await expect(page.getByText('Digest v1.0.0 published.')).toBeVisible();

  await page.getByRole('button', { name: 'Install CRM sync' }).click();
  await page.getByRole('dialog', { name: 'Install CRM sync?' }).getByRole('button', { name: 'Install', exact: true }).click();
  await page.getByRole('button', { name: 'Delete CRM sync' }).click();
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.getByText(/Installed marketplace package cannot be deleted/)).toBeVisible();

  await page.getByRole('button', { name: 'Delete Meeting notes' }).click();
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.getByText('Meeting notes removed from the catalog.')).toBeVisible();
});

test('integration packages: unsigned ones cannot be installed, a flagged install is turned on only after confirming', async ({ page }) => {
  const calls = await mockApi(page, { signing: true });
  await page.goto('/#enterprise');
  await expect(page.getByText('Signed by Acme').first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Install ERP bridge' })).toBeDisabled();
  await expect(page.getByText(/Cannot install 2.0.0: This package is not signed/)).toBeVisible();
  await expect(page.getByText(/Turned off automatically: the signing key acme-2026 was revoked/)).toBeVisible();
  await expect(page.getByRole('alert').filter({ hasText: 'Flagged: Signed with key acme-2026' })).toBeVisible();

  await page.getByRole('button', { name: 'Turn on', exact: true }).click();
  await expect(page.getByText(/Its signing key was revoked/)).toBeVisible();
  expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
  await page.getByRole('button', { name: 'Turn on anyway' }).click();
  await expect(page.getByText('Mail relay is on in this workspace.')).toBeVisible();
  expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ enabled: true, acknowledge_flagged: true });
});

test('an org admin signs an unsigned integration package', async ({ page }) => {
  const calls = await mockApi(page, { signing: true, orgAdmin: true });
  await page.goto('/#enterprise');
  await page.locator('.marketRow', { hasText: 'ERP bridge' }).getByRole('button', { name: 'Sign this version' }).click();
  const form = page.getByRole('form', { name: 'Sign ERP bridge 2.0.0' });
  await form.getByLabel('Publisher key id').fill('acme-2026');
  await form.getByLabel('Signature (base64)').fill('A'.repeat(86) + '==');
  await form.getByRole('button', { name: 'Attach signature' }).click();
  await expect(page.getByText('Signature attached. 2.0.0 can now be installed.')).toBeVisible();
  expect(calls.find((c) => c.method === 'PUT' && c.path.endsWith('/signature'))?.path).toBe('/api/v1/marketplace/packages/gamma.erp/signature');
});
