import { expect, test, type Page } from '@playwright/test';

// Skill signing: signer and verification on cards and details, installs refused for versions that
// do not verify, flagged installations, signing a version later, and the publisher key page
// (add, rotate, revoke, empty, read only, load failure), against a mocked API (docs/skills.md#signing).
const now = new Date().toISOString();
type Json = Record<string, unknown>;
// Plainly fake, low-entropy values: 32 and 64 zero bytes in base64.
const KEY = 'A'.repeat(43) + '=';
const SIG = 'A'.repeat(86) + '==';
const DIGEST = 'ab'.repeat(32);

interface Options { orgAdmin?: boolean; noKeys?: boolean; keysFail?: boolean }

const verified = (signer = 'Acme Labs', keyId = 'acme-2026'): Json => ({ status: 'verified', installable: true, digest: DIGEST, key_id: keyId, signer, key_source: 'tenant', fingerprint: 'abcd:ef01:2345', signed_at: now, detail: `Signed by ${signer} with key ${keyId}.` });
const unsigned = (): Json => ({ status: 'unsigned', installable: false, digest: DIGEST, key_id: null, signer: null, key_source: null, fingerprint: null, signed_at: null, detail: 'This version is not signed. An organization administrator signs it with a trusted publisher key before anyone can install it.' });
const revoked = (): Json => ({ ...verified(), status: 'revoked', installable: false, detail: 'Signed with key acme-2026, which an organization administrator revoked: Laptop lost.' });
const version = (skillId: string, v: string, name: string, verification: Json): Json => ({
  id: `sv_${skillId}_${v}`, skill_id: skillId, version: v, name, description: `${name} for the team`, instructions: 'Do it well.',
  required_tools: [], risk_level: 'low', publisher_tenant_id: 'tenant_local', created_by: 'admin', created_at: now, verification,
});
const key = (id: string, over: Json = {}): Json => ({ id, tenant_id: 'tenant_local', publisher: 'Acme Labs', public_key: KEY, fingerprint: `fp:${id}`, source: 'tenant', status: 'active', created_by: 'admin', created_at: now, rotated_at: null, replaced_by: null, revoked_at: null, revoked_by: null, revoke_reason: null, ...over });

async function mockApi(page: Page, options: Options = {}) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const versions: Json[] = [
    version('acme.notes', '1.0.0', 'Notes', verified()),
    version('acme.notes', '2.0.0', 'Notes', unsigned()),
    version('acme.draft', '1.0.0', 'Draft helper', unsigned()),
    version('acme.legacy', '1.0.0', 'Legacy', revoked()),
  ];
  const installs: Json[] = [
    { id: 'si_1', skill_id: 'acme.notes', version: '1.0.0', approved_tools: [], enabled: true, installed_by: 'user_local', installed_at: now, verification: verified() },
    { id: 'si_2', skill_id: 'acme.legacy', version: '1.0.0', approved_tools: [], enabled: false, installed_by: 'user_local', installed_at: now, verification: revoked(),
      disabled_reason: 'Turned off automatically: the signing key acme-2026 was revoked (Laptop lost).' },
  ];
  let settings: Json = { disable_flagged: true, updated_by: null, updated_at: null, can_manage: Boolean(options.orgAdmin) };
  let keys: Json[] = options.noKeys ? [] : [key('acme-2026'), key('acme-2024', { status: 'revoked', revoked_at: now, revoked_by: 'admin', revoke_reason: 'Laptop lost' })];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method, path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true, created_at: now, updated_at: now });
    if (path === '/api/v1/organization/governance') return json({ tenant_id: 'tenant_local', policy_packs: 0, active_policy_packs: 0, role_templates: 0, approval_rules: 0, memory_governance_configured: false, can_manage_organization_policy: Boolean(options.orgAdmin) });
    if (path === '/api/v1/skills/versions' && method === 'GET') return json(versions);
    if (path === '/api/v1/skills/installations' && method === 'GET') return json(installs);
    if (path === '/api/v1/agents/catalog') return json({ tools: [], templates: [], icons: [], autonomy_floor: { read: 'auto', write: 'ask', external: 'ask' }, skills: [] });
    if (path === '/api/v1/agents/skills/usage') return json({ items: [] });
    if (path === '/api/v1/skills/publisher-keys' && method === 'GET') {
      if (options.keysFail) return json({ error: { code: 'unavailable', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }, 503);
      return json({ keys, platform_keys: [], can_manage: Boolean(options.orgAdmin) });
    }
    if (path === '/api/v1/skills/signing-settings' && method === 'GET') return json(settings);
    if (path === '/api/v1/skills/signing-settings' && method === 'PUT') { settings = { ...settings, disable_flagged: (body as Json).disable_flagged, updated_by: 'user_local', updated_at: now }; return json(settings); }
    const patch = path.match(/^\/api\/v1\/skills\/installations\/([^/]+)$/);
    if (patch && method === 'PATCH') {
      const target = installs.find((i) => i.skill_id === decodeURIComponent(patch[1]))!;
      if ((body as Json).enabled && !(target.verification as Json).installable && !(body as Json).acknowledge_flagged) return json({ error: { code: 'conflict', message: 'Confirm', correlation_id: 'c', details: [] } }, 409);
      Object.assign(target, { enabled: (body as Json).enabled, disabled_reason: null });
      return json(target);
    }
    if (path === '/api/v1/skills/publisher-keys' && method === 'POST') { const created = key((body as Json).id as string, { publisher: (body as Json).publisher }); keys = [...keys, created]; return json(created, 201); }
    const rotate = path.match(/^\/api\/v1\/skills\/publisher-keys\/([^/]+)\/rotate$/);
    if (rotate) {
      const created = key((body as Json).new_id as string);
      keys = keys.map((k) => (k.id === rotate[1] ? { ...k, status: 'rotated', rotated_at: now, replaced_by: created.id } : k)).concat(created);
      return json(created, 201);
    }
    const revoke = path.match(/^\/api\/v1\/skills\/publisher-keys\/([^/]+)\/revoke$/);
    if (revoke) {
      keys = keys.map((k) => (k.id === revoke[1] ? { ...k, status: 'revoked', revoked_at: now, revoked_by: 'user_local', revoke_reason: (body as Json).reason } : k));
      return json({ key: keys.find((k) => k.id === revoke[1]), affected_versions: 1, flagged_installations: 1, affected_packages: 0, auto_disable: settings.disable_flagged, disabled_installations: 3 });
    }
    const sign = path.match(/^\/api\/v1\/skills\/versions\/([^/]+)\/([^/]+)\/signature$/);
    if (sign && method === 'PUT') {
      const target = versions.find((v) => v.skill_id === decodeURIComponent(sign[1]) && v.version === decodeURIComponent(sign[2]))!;
      target.verification = verified();
      return json(target);
    }
    const detail = path.match(/^\/api\/v1\/skills\/([^/]+)$/);
    if (detail && method === 'GET') {
      const skillId = decodeURIComponent(detail[1]);
      const list = versions.filter((v) => v.skill_id === skillId).sort((a, b) => String(b.version).localeCompare(String(a.version)));
      const installation = installs.find((i) => i.skill_id === skillId) ?? null;
      return json({ skill_id: skillId, name: list[0].name, description: list[0].description, latest_version: list[0].version, versions: list, installation,
        tools_version: list[0].version, tools: [], history: [], can_manage: true, can_rollback: Boolean(options.orgAdmin) });
    }
    return route.abort();
  });
  return calls;
}

const card = (page: Page, name: string) => page.locator('article.catalogCard', { has: page.getByRole('heading', { name: new RegExp(`^${name}`) }) });

test('cards show the signer, refuse unsigned installs and flag a revoked installation', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#skills');
  await expect(card(page, 'Notes').getByText('Signed by Acme Labs')).toBeVisible();
  await expect(card(page, 'Notes').getByRole('button', { name: 'Upgrade to 2.0.0' })).toBeDisabled();
  await expect(card(page, 'Notes').getByText(/Cannot install 2.0.0: This version is not signed/)).toBeVisible();
  await expect(card(page, 'Draft helper').getByText('Unsigned', { exact: true })).toBeVisible();
  await expect(card(page, 'Draft helper').getByRole('button', { name: 'Install' })).toBeDisabled();
  await expect(card(page, 'Legacy').getByRole('alert')).toContainText('Flagged: Signed with key acme-2026, which an organization administrator revoked: Laptop lost.');
  await expect(card(page, 'Legacy').getByText('Key revoked')).toBeVisible();
  await expect(card(page, 'Legacy').getByText(/Turned off automatically: the signing key acme-2026 was revoked/)).toBeVisible();
});

test('an owner turns a flagged, auto-disabled skill back on only after confirming', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#skills');
  await card(page, 'Legacy').getByRole('button', { name: 'Enable' }).click();
  await expect(card(page, 'Legacy').getByText(/Its signing key was revoked/)).toBeVisible();
  expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
  await card(page, 'Legacy').getByRole('button', { name: 'Turn on anyway' }).click();
  await expect(page.getByText('Skill enabled.', { exact: false })).toBeVisible();
  expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ enabled: true, acknowledge_flagged: true });
});

test('an org admin switches automatic disabling, and a revoke reports what it turned off', async ({ page }) => {
  const calls = await mockApi(page, { orgAdmin: true });
  await page.goto('/#skills');
  await page.getByRole('tab', { name: 'Publisher keys' }).click();
  const panel = page.getByRole('tabpanel', { name: 'Publisher keys' });
  const toggle = panel.getByRole('checkbox', { name: 'Turn off what a revoked key signed' });
  await expect(toggle).toBeChecked();
  await panel.getByRole('button', { name: 'Revoke acme-2026' }).click();
  const revoke = page.getByRole('dialog', { name: 'Revoke acme-2026?' });
  await revoke.getByLabel(/Reason/).fill('Leaked');
  await revoke.getByRole('button', { name: 'Revoke key' }).click();
  await expect(panel.getByText(/Installations turned off in every workspace: 3./)).toBeVisible();
  await toggle.uncheck();
  await expect(panel.getByText(/Revoking a key now only flags what it signed/)).toBeVisible();
  expect(calls.find((c) => c.method === 'PUT' && c.path === '/api/v1/skills/signing-settings')?.body).toEqual({ disable_flagged: false });
});

test('a member sees the automatic disabling setting read only', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#skills');
  await page.getByRole('tab', { name: 'Publisher keys' }).click();
  const panel = page.getByRole('tabpanel', { name: 'Publisher keys' });
  await expect(panel.getByRole('checkbox', { name: 'Turn off what a revoked key signed' })).toBeDisabled();
  await expect(panel.getByText('Only organization administrators change this setting.')).toBeVisible();
});

test('an org admin signs an unsigned version from its details', async ({ page }) => {
  const calls = await mockApi(page, { orgAdmin: true });
  await page.goto('/#skills');
  await card(page, 'Draft helper').getByRole('button', { name: 'Details' }).click();
  const dialog = page.getByRole('dialog', { name: 'Draft helper' });
  await expect(dialog.getByText(DIGEST)).toBeVisible();
  await dialog.getByRole('button', { name: 'Sign this version' }).click();
  const form = dialog.getByRole('form', { name: 'Sign Draft helper 1.0.0' });
  await form.getByLabel('Publisher key id').fill('acme-2026');
  await form.getByLabel('Signature (base64)').fill('too short');
  await form.getByRole('button', { name: 'Attach signature' }).click();
  await expect(form.getByRole('alert')).toContainText('64-byte Ed25519 signature');
  await form.getByLabel('Signature (base64)').fill(SIG);
  await form.getByRole('button', { name: 'Attach signature' }).click();
  await expect(dialog.getByText('Signature attached. 1.0.0 can now be installed.')).toBeVisible();
  expect(calls.find((c) => c.method === 'PUT')).toMatchObject({ path: '/api/v1/skills/versions/acme.draft/1.0.0/signature', body: { key_id: 'acme-2026', signature: SIG } });
});

test('an org admin adds, rotates and revokes publisher keys', async ({ page }) => {
  const calls = await mockApi(page, { orgAdmin: true });
  await page.goto('/#skills');
  await page.getByRole('tab', { name: 'Publisher keys' }).click();
  const panel = page.getByRole('tabpanel', { name: 'Publisher keys' });
  await expect(panel.getByRole('heading', { name: 'Trusted publisher keys' })).toBeVisible();
  await expect(panel.getByText('Revoked by admin', { exact: false })).toBeVisible();

  await panel.getByRole('button', { name: 'Add publisher key' }).click();
  const add = page.getByRole('dialog', { name: 'Add a trusted publisher key' });
  await add.getByLabel('Key id').fill('acme-2026');
  await add.getByLabel('Publisher name').fill('Acme Labs');
  await add.getByLabel(/Public key/).fill(KEY);
  await add.getByRole('button', { name: 'Trust this key' }).click();
  await expect(add.getByRole('alert')).toContainText('Key id acme-2026 is already used');
  await add.getByLabel('Key id').fill('beta-2026');
  await add.getByRole('button', { name: 'Trust this key' }).click();
  await expect(panel.getByText('Key beta-2026 is now trusted.')).toBeVisible();

  await panel.getByRole('button', { name: 'Rotate acme-2026' }).click();
  const rotate = page.getByRole('dialog', { name: 'Rotate acme-2026' });
  await rotate.getByLabel('New key id').fill('acme-2027');
  await rotate.getByLabel(/New public key/).fill(KEY);
  await rotate.getByRole('button', { name: 'Rotate key' }).click();
  await expect(panel.getByText('Key acme-2026 rotated to acme-2027.')).toBeVisible();
  await expect(panel.getByText(/replaced by acme-2027/)).toBeVisible();

  await panel.getByRole('button', { name: 'Revoke beta-2026' }).click();
  const revoke = page.getByRole('dialog', { name: 'Revoke beta-2026?' });
  await expect(revoke.getByText(/Revoking cannot be undone/)).toBeVisible();
  await revoke.getByRole('button', { name: 'Revoke key' }).click();
  await expect(revoke.getByRole('alert')).toContainText('Reason: 1 to 500 characters.');
  await revoke.getByLabel(/Reason/).fill('Publisher left');
  await revoke.getByRole('button', { name: 'Revoke key' }).click();
  await expect(panel.getByText(/Key beta-2026 revoked. Versions no longer installable: 1/)).toBeVisible();
  expect(calls.find((c) => c.path.endsWith('/revoke'))?.body).toEqual({ reason: 'Publisher left' });

  await panel.getByLabel('Search keys').fill('nothing here');
  await expect(panel.getByRole('heading', { name: 'No keys match' })).toBeVisible();
  await panel.getByRole('button', { name: 'Clear search' }).click();
  await panel.getByRole('radio', { name: 'Revoked', exact: true }).check();
  await expect(panel.getByText('2 of 4 keys')).toBeVisible();
});

test('a member sees keys read only; the empty and failed states say what to do', async ({ page }) => {
  await mockApi(page, { noKeys: true });
  await page.goto('/#skills');
  await page.getByRole('tab', { name: 'Publisher keys' }).click();
  const panel = page.getByRole('tabpanel', { name: 'Publisher keys' });
  await expect(panel.getByText('You can view keys. Only organization administrators add, rotate or revoke them.')).toBeVisible();
  await expect(panel.getByRole('heading', { name: 'No publisher keys yet' })).toBeVisible();
  await expect(panel.getByText(/Ask one to trust your publisher/)).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Add publisher key' })).toHaveCount(0);
});

test('when keys cannot load the page says why and offers a retry', async ({ page }) => {
  await mockApi(page, { keysFail: true, orgAdmin: true });
  await page.goto('/#skills');
  await page.getByRole('tab', { name: 'Publisher keys' }).click();
  const panel = page.getByRole('tabpanel', { name: 'Publisher keys' });
  await expect(panel.getByRole('alert')).toContainText('Could not load publisher keys: The database is not reachable.');
  await expect(panel.getByRole('button', { name: 'Try again' })).toBeVisible();
});

for (const size of [{ width: 1440, height: 900 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
  test(`publisher keys fit without horizontal overflow at ${size.width}x${size.height}`, async ({ page }) => {
    await page.setViewportSize(size);
    await mockApi(page, { orgAdmin: true });
    await page.goto('/#skills');
    await page.getByRole('tab', { name: 'Publisher keys' }).click();
    await expect(page.getByRole('heading', { name: 'Trusted publisher keys' })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}
