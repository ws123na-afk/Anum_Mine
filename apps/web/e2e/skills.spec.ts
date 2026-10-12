import { expect, test, type Page } from '@playwright/test';

// Skills: search, details (tools, risk, approvals, versions, history), install with a review,
// rollback for organization administrators and publishing, against a mocked API (docs/skills.md).
const now = new Date().toISOString();
type Json = Record<string, unknown>;

interface Options { role?: 'owner' | 'member'; orgAdmin?: boolean; empty?: boolean; many?: number; usageFails?: boolean }

const version = (skillId: string, v: string, name: string, tools: string[], risk = 'low'): Json => ({
  id: `sv_${skillId}_${v}`, skill_id: skillId, version: v, name, description: `${name} for the team`, instructions: 'Do it well.',
  required_tools: tools, risk_level: risk, publisher_tenant_id: 'tenant_local', created_by: 'admin', created_at: now,
});
const tool = (name: string, granted: boolean): Json => ({
  name, registered: name !== 'crm.lookup', description: name === 'external.action' ? 'Perform an external action.' : 'Respond.',
  risk_level: name === 'external.action' ? 'high' : name === 'crm.lookup' ? null : 'low',
  approval: name === 'external.action' ? 'always' : name === 'crm.lookup' ? 'unavailable' : 'rule',
  approval_text: name === 'external.action' ? 'Every call waits for a human approval.' : name === 'crm.lookup' ? 'Not available in this deployment.' : 'Runs without approval unless an organization approval rule or policy pack asks for one.',
  target: null, granted,
});

async function mockApi(page: Page, options: Options = {}) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const versions: Json[] = options.empty ? [] : [
    version('acme.release-notes', '1.0.0', 'Release notes', ['anum.respond']),
    version('acme.release-notes', '2.0.0', 'Release notes', ['anum.respond', 'external.action'], 'high'),
    version('acme.crm', '1.0.0', 'CRM helper', ['crm.lookup']),
    ...Array.from({ length: options.many ?? 0 }, (_, i) => version(`bulk.skill-${String(i).padStart(2, '0')}`, '1.0.0', `Bulk skill ${String(i).padStart(2, '0')}`, [])),
  ];
  let install: Json | null = options.empty ? null : { id: 'si_1', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', skill_version_id: 'sv_acme.release-notes_2.0.0', skill_id: 'acme.release-notes', version: '2.0.0', approved_tools: ['anum.respond', 'external.action'], enabled: true, installed_by: 'user_local', installed_at: now };
  const history: Json[] = [{ id: 'a1', action: 'skill.installed', actor: 'user_local', outcome: 'success', created_at: now, metadata: { version: '2.0.0', previous_version: '1.0.0' } }];
  const installs = () => (install ? [install] : []);
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method, path: `${path}${url.search}`, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: options.role ?? 'owner', active: true, created_at: now, updated_at: now });
    if (path === '/api/v1/organization/governance') return json({ tenant_id: 'tenant_local', policy_packs: 0, active_policy_packs: 0, role_templates: 0, approval_rules: 0, memory_governance_configured: false, can_manage_organization_policy: Boolean(options.orgAdmin) });
    if (path === '/api/v1/skills/versions' && method === 'GET') return json(versions);
    if (path === '/api/v1/skills/versions' && method === 'POST') { const v = { ...version((body as Json).skill_id as string, (body as Json).version as string, (body as Json).name as string, (body as Json).required_tools as string[]) }; versions.push(v); return json(v, 201); }
    if (path === '/api/v1/skills/installations' && method === 'GET') return json(installs());
    if (path === '/api/v1/skills/installations' && method === 'POST') {
      install = { id: 'si_2', skill_id: (body as Json).skill_id, version: (body as Json).version, approved_tools: (body as Json).approved_tools, enabled: true, installed_by: 'user_local', installed_at: now };
      return json(install, 201);
    }
    if (path === '/api/v1/skills/installations/acme.release-notes/rollback' && method === 'POST') {
      install = { ...install!, version: (body as Json).version, approved_tools: ['anum.respond'] };
      history.unshift({ id: 'a2', action: 'skill.rolled_back', actor: 'user_local', outcome: 'success', created_at: now, metadata: { from_version: '2.0.0', to_version: '1.0.0' } });
      return json(install);
    }
    if (path === '/api/v1/agents/catalog') {
      return json({ tools: [], templates: [], icons: [], autonomy_floor: { read: 'auto', write: 'ask', external: 'ask' }, skills: [
        { id: 'anum.web-search', name: 'Web search', description: 'Looks things up on the web.', source: 'builtin', required_tools: ['web_search'] },
        { id: 'acme.release-notes', name: 'Release notes', description: 'Release notes for the team', source: 'installed', required_tools: ['anum.respond'] },
      ] });
    }
    if (path === '/api/v1/agents/skills/usage') {
      if (options.usageFails) return json({ error: { code: 'unavailable', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }, 503);
      return json({ items: [
        { skill_id: 'acme.release-notes', count: 2, agents: [
          { id: 'agent_fleet', name: 'Fleet desk', icon: 'truck', color: '#0f766e', status: 'active' },
          { id: 'agent_ops', name: 'Ops helper', icon: 'bot', color: '#7c3aed', status: 'paused' },
        ] },
      ] });
    }
    const detail = path.match(/^\/api\/v1\/skills\/([^/]+)$/);
    if (detail && method === 'GET') {
      const skillId = decodeURIComponent(detail[1]);
      const list = versions.filter((v) => v.skill_id === skillId).sort((a, b) => String(b.version).localeCompare(String(a.version)));
      const installed = install && install.skill_id === skillId ? install : null;
      const wanted = url.searchParams.get('version') ?? (installed?.version as string | undefined) ?? (list[0].version as string);
      const shown = list.find((v) => v.version === wanted)!;
      const granted = new Set(installed && installed.version === shown.version ? (installed.approved_tools as string[]) : []);
      return json({
        skill_id: skillId, name: shown.name, description: shown.description, latest_version: list[0].version, versions: list, installation: installed,
        tools_version: shown.version, tools: (shown.required_tools as string[]).map((t) => tool(t, granted.has(t))),
        history: skillId === 'acme.release-notes' ? history : [], can_manage: options.role !== 'member', can_rollback: Boolean(options.orgAdmin),
      });
    }
    return route.abort();
  });
  return calls;
}

const skillCard = (page: Page, name: string) => page.locator('article.catalogCard', { has: page.getByRole('heading', { name: new RegExp(`^${name}`) }) });

test('search and filter the skill list', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#skills');
  await expect(page.getByText('3 of 3 skills')).toBeVisible();
  await page.getByLabel('Search skills').fill('crm.lookup');
  await expect(page.getByText('1 of 3 skills')).toBeVisible();
  await expect(skillCard(page, 'CRM helper')).toBeVisible();
  await page.getByLabel('Search skills').fill('nothing here');
  await expect(page.getByRole('heading', { name: 'No skills match' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear search' }).click();
  await page.getByRole('radio', { name: 'Installed', exact: true }).check();
  await expect(page.getByText('1 of 3 skills')).toBeVisible();
  await expect(skillCard(page, 'Release notes').getByText('installed', { exact: true })).toBeVisible();
});

test('details show tools with risk and approval, versions and history; an org admin rolls back', async ({ page }) => {
  const calls = await mockApi(page, { orgAdmin: true });
  await page.goto('/#skills');
  await skillCard(page, 'Release notes').getByRole('button', { name: 'Details' }).click();
  const dialog = page.getByRole('dialog', { name: 'Release notes' });
  await expect(dialog.getByText('Every call waits for a human approval.')).toBeVisible();
  await expect(dialog.getByText('granted').first()).toBeVisible();
  await expect(dialog.getByText('Installed 2.0.0 (was 1.0.0)')).toBeVisible();

  await dialog.getByRole('button', { name: 'Roll back to 1.0.0' }).click();
  await dialog.getByRole('button', { name: 'Confirm rollback' }).click();
  await expect(dialog.getByText(/Rolled back to 1.0.0/)).toBeVisible();
  await expect(dialog.getByText('Rolled back 2.0.0 → 1.0.0')).toBeVisible();
  expect(calls.find((c) => c.path.endsWith('/rollback'))?.body).toEqual({ version: '1.0.0' });
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
});

test('an owner who is not an org admin cannot roll back and is told who can', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#skills');
  await skillCard(page, 'Release notes').getByRole('button', { name: 'Details' }).click();
  const dialog = page.getByRole('dialog', { name: 'Release notes' });
  await expect(dialog.getByText(/Only organization administrators can publish versions or roll an installation back/)).toBeVisible();
  await expect(dialog.getByRole('button', { name: /Roll back/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Publish version' })).toHaveCount(0);
});

test('installing shows each tool risk before granting it', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#skills');
  await skillCard(page, 'CRM helper').getByRole('button', { name: 'Install' }).click();
  const dialog = page.getByRole('dialog', { name: 'Install CRM helper 1.0.0?' });
  await expect(dialog.getByText('Not available in this deployment.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Install and grant' }).click();
  await expect(page.getByText('CRM helper 1.0.0 installed.')).toBeVisible();
  expect(calls.find((c) => c.method === 'POST' && c.path === '/api/v1/skills/installations')?.body).toEqual({ skill_id: 'acme.crm', version: '1.0.0', approved_tools: ['crm.lookup'] });
});

test('a member sees skills read only and who can change them', async ({ page }) => {
  await mockApi(page, { role: 'member' });
  await page.goto('/#skills');
  await expect(page.getByText(/Only workspace owners can install, enable, disable or uninstall skills/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Install' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Disable|Uninstall/ })).toHaveCount(0);
});

test('an org admin publishes the first skill from the empty state', async ({ page }) => {
  const calls = await mockApi(page, { empty: true, orgAdmin: true });
  await page.goto('/#skills');
  await expect(page.getByRole('heading', { name: 'No published skills' })).toBeVisible();
  await page.locator('.emptyState').getByRole('button', { name: 'Publish version' }).click();
  const dialog = page.getByRole('dialog', { name: 'Publish a skill version' });
  await dialog.getByLabel('Skill id').fill('Bad');
  await dialog.getByRole('button', { name: 'Publish' }).click();
  await expect(dialog.getByText(/Skill id: 3 to 128 lowercase/)).toBeVisible();
  await dialog.getByLabel('Skill id').fill('acme.digest');
  await dialog.getByLabel('Name').fill('Digest');
  await dialog.getByLabel('Description').fill('Daily digest');
  await dialog.getByLabel(/Required tools/).fill('anum.respond');
  await dialog.getByLabel('Instructions agents follow').fill('Summarize the day.');
  await dialog.getByRole('button', { name: 'Publish' }).click();
  await expect(page.getByText(/Digest 1.0.0 published to every workspace/)).toBeVisible();
  await expect(skillCard(page, 'Digest')).toBeVisible();
  expect(calls.find((c) => c.method === 'POST' && c.path === '/api/v1/skills/versions')?.body).toMatchObject({ skill_id: 'acme.digest', required_tools: ['anum.respond'], risk_level: 'low' });
});

test('a long list pages with Show more', async ({ page }) => {
  await mockApi(page, { many: 20 });
  await page.goto('/#skills');
  await expect(page.locator('article.catalogCard')).toHaveCount(12);
  await page.getByRole('button', { name: 'Show 10 more' }).click();
  await expect(page.locator('article.catalogCard')).toHaveCount(22);
});

test('built-in skills and the agents that load each skill; disabling warns which agents stop loading it', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#skills');
  await expect(skillCard(page, 'Release notes').getByText('Loaded by Fleet desk, Ops helper')).toBeVisible();
  await expect(skillCard(page, 'CRM helper').getByText('No agent loads it yet')).toBeVisible();
  const builtins = page.getByRole('region', { name: 'Built into ANUM' });
  await expect(builtins.getByRole('heading', { name: /Web search/ })).toBeVisible();
  await page.getByRole('radio', { name: 'Built in', exact: true }).check();
  await expect(page.locator('article.catalogCard')).toHaveCount(0);
  await expect(builtins.getByRole('heading', { name: /Web search/ })).toBeVisible();
  await page.getByRole('radio', { name: 'All', exact: true }).check();

  await skillCard(page, 'Release notes').getByRole('button', { name: 'Disable' }).click();
  await expect(skillCard(page, 'Release notes').getByText('2 agents load this skill and will stop loading it.')).toBeVisible();
  expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
  await skillCard(page, 'Release notes').getByRole('button', { name: 'Cancel' }).click();

  await skillCard(page, 'Release notes').getByRole('button', { name: 'Details' }).click();
  const dialog = page.getByRole('dialog', { name: 'Release notes' });
  await expect(dialog.getByRole('heading', { name: 'Agents that load it' })).toBeVisible();
  await expect(dialog.getByText('Ops helper')).toBeVisible();
  await expect(dialog.getByText('paused', { exact: true })).toBeVisible();
});

test('when agent usage cannot load, the page says so and still lists skills', async ({ page }) => {
  await mockApi(page, { usageFails: true });
  await page.goto('/#skills');
  await expect(page.getByRole('alert').getByText(/Could not load which agents use skills: The database is not reachable./)).toBeVisible();
  await expect(skillCard(page, 'Release notes')).toBeVisible();
});
