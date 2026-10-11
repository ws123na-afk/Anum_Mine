import { expect, test, type Page } from '@playwright/test';

// Settings > Model gateway against a mocked API (docs/model-gateway.md, Per-workspace model
// configuration and Model routing): choosing Anthropic (Claude) with its routing defaults, the
// write-only key, the connection test, and the loading, error and owner-only (403) states.
const now = new Date().toISOString();
type Json = Record<string, unknown>;
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });
const fakeKey = 'sk-ant-example';

interface Options {
  /** GET /model-config answer: a saved config, 404 (server default), 403 or 500. */
  initial?: Json | 404 | 403 | 500;
  /** PUT and test answer 403 (a member whose role cannot manage the organization). */
  writesForbidden?: boolean;
  /** The connection test fails with the API's 502 message. */
  testFails?: boolean;
}

async function mockApi(page: Page, options: Options = {}) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  let config: Json | null = typeof options.initial === 'object' ? options.initial : null;
  let failLoad = options.initial === 500;
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
    if (path === '/api/v1/workspace-memberships/current') {
      return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true, created_at: now, updated_at: now });
    }
    if (path === '/api/v1/onboarding') {
      return json({ complete: true, tenant: { id: 'tenant_local', name: 'ANUM' }, workspace: { id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: now, updated_at: now }, membership: { role: 'owner' }, model_configured: Boolean(config) });
    }
    if (path === '/api/v1/notification-preferences') {
      return json({ task_completed: true, approval_required: true, run_failed: true, automation_failed: true, email_enabled: false, desktop_enabled: true });
    }
    if (path === '/api/v1/model-config' && method === 'GET') {
      if (failLoad) { failLoad = false; return json(envelope('internal_error', 'Database unavailable'), 500); }
      if (options.initial === 403) return json(envelope('forbidden', 'permission_denied: organization:read'), 403);
      return config ? json(config) : json(envelope('not_found', 'Model configuration not found'), 404);
    }
    if (path === '/api/v1/model-config' && method === 'PUT') {
      if (options.writesForbidden) return json(envelope('forbidden', 'permission_denied: organization:manage'), 403);
      const sent = body as Json;
      const key = String(sent.api_key ?? '');
      config = {
        provider: sent.provider, model: sent.model, base_url: sent.base_url ?? 'https://api.anthropic.com',
        fast_model: sent.fast_model ?? null, capable_model: sent.capable_model ?? null, fast_effort: sent.fast_effort ?? null, capable_effort: sent.capable_effort ?? null,
        credential_configured: Boolean(key), credential_hint: key ? `...${key.slice(-4)}` : null, updated_at: now,
      };
      return json(config);
    }
    if (path === '/api/v1/model-config/test') {
      if (options.writesForbidden) return json(envelope('forbidden', 'permission_denied: organization:manage'), 403);
      if (options.testFails) return json(envelope('bad_gateway', 'Anthropic rejected the API key (HTTP 401). Create a key in the Anthropic Console and save it again.'), 502);
      return json({ provider: 'anthropic', model: 'claude-sonnet-5-5', models: ['claude-sonnet-5-5', 'claude-haiku-5-5'], latency_ms: 412, status: 'connected' });
    }
    return route.abort();
  });
  return calls;
}

const savedClaude: Json = {
  provider: 'anthropic', model: 'claude-sonnet-5-5', base_url: 'https://api.anthropic.com', fast_model: 'claude-haiku-5-5', capable_model: 'claude-sonnet-5-5',
  fast_effort: 'low', capable_effort: null, credential_configured: true, credential_hint: '...mple', updated_at: now,
};

test('an owner switches the workspace to Claude with routing defaults, saves the key and tests it', async ({ page }) => {
  const calls = await mockApi(page, { initial: 404 });
  await page.goto('/#settings');

  const panel = page.getByRole('region', { name: 'Model gateway' });
  await expect(panel.getByText('server default', { exact: true })).toBeVisible();
  await panel.getByLabel('Provider').selectOption('anthropic');
  await expect(panel.getByLabel('Quick replies model')).toHaveValue('claude-haiku-5-5');
  await expect(panel.getByLabel('Planning model')).toHaveValue('claude-sonnet-5-5');
  await expect(panel.getByLabel('Quick replies effort')).toHaveValue('low');
  await expect(panel.getByLabel('Planning effort')).toHaveValue('');

  await panel.getByRole('button', { name: 'Save model' }).click();
  await expect(panel.getByText('Paste your Anthropic API key.')).toBeVisible();
  await expect(panel.getByLabel('Anthropic API key')).toHaveAttribute('aria-invalid', 'true');
  expect(calls.some((c) => c.method === 'PUT')).toBe(false);

  await panel.getByLabel('Anthropic API key').fill(fakeKey);
  await panel.getByRole('button', { name: 'Save model' }).click();
  await expect(panel.getByText(/Saved\. Anthropic \(Claude\) now answers this workspace: claude-haiku-5-5 for chat, voice and memory, claude-sonnet-5-5 for planning/)).toBeVisible();
  await expect(panel.getByText('Anthropic (Claude) · key ...mple')).toBeVisible();
  await expect(panel.getByLabel('Anthropic API key')).toHaveValue('');
  expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({
    provider: 'anthropic', model: 'claude-sonnet-5-5', api_key: fakeKey, base_url: 'https://api.anthropic.com',
    fast_model: 'claude-haiku-5-5', capable_model: 'claude-sonnet-5-5', fast_effort: 'low', capable_effort: null,
  });

  await panel.getByRole('button', { name: 'Test connection' }).click();
  await expect(panel.getByText('Connected to claude-sonnet-5-5 and claude-haiku-5-5 in 412 ms.')).toBeVisible();
});

test('a saved Claude model loads into the form, the key stays hidden, and a failed test says how to fix it', async ({ page }) => {
  await mockApi(page, { initial: savedClaude, testFails: true });
  await page.goto('/#settings');

  const panel = page.getByRole('region', { name: 'Model gateway' });
  await expect(panel.getByText('Saved: claude-haiku-5-5 answers chat, voice and memory; claude-sonnet-5-5 plans tasks.', { exact: false })).toBeVisible();
  await expect(panel.getByLabel('Provider')).toHaveValue('anthropic');
  await expect(panel.getByLabel('Anthropic API key')).toHaveValue('');
  await expect(panel.getByText(/Saved key \.\.\.mple is never shown/)).toBeVisible();

  await panel.getByRole('button', { name: 'Test connection' }).click();
  await expect(panel.getByRole('alert')).toContainText('Connection failed. Anthropic rejected the API key (HTTP 401). Create a key in the Anthropic Console and save it again.');
});

test('members without organization:manage see who can change the model', async ({ page }) => {
  await mockApi(page, { initial: savedClaude, writesForbidden: true });
  await page.goto('/#settings');

  const panel = page.getByRole('region', { name: 'Model gateway' });
  await panel.getByLabel('Anthropic API key').fill(fakeKey);
  await panel.getByRole('button', { name: 'Save model' }).click();
  const denied = panel.getByRole('region', { name: 'Owner access required' });
  await expect(denied).toContainText('need the owner role for this organization');
  await expect(denied).toContainText('The API answered: permission_denied: organization:manage');
});

test('a caller who cannot read the model gets the owner explanation instead of a form', async ({ page }) => {
  await mockApi(page, { initial: 403 });
  await page.goto('/#settings');
  await expect(page.getByRole('region', { name: 'Owner access required' })).toContainText('permission_denied: organization:read');
  await expect(page.getByLabel('Provider')).toHaveCount(0);
});

test('a failed load explains what to do and Retry recovers', async ({ page }) => {
  await mockApi(page, { initial: 500 });
  await page.goto('/#settings');

  const panel = page.getByRole('region', { name: 'Model gateway' });
  await expect(panel.getByRole('alert')).toContainText('The model settings could not be loaded. Database unavailable');
  await panel.getByRole('button', { name: 'Retry' }).click();
  await expect(panel.getByText('server default', { exact: true })).toBeVisible();
  await expect(panel.getByLabel('Provider')).toHaveValue('');
});

test('the Claude form fits every viewport without horizontal overflow', async ({ page }) => {
  await mockApi(page, { initial: savedClaude });
  for (const size of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size);
    await page.goto('/#settings');
    const panel = page.getByRole('region', { name: 'Model gateway' });
    await expect(panel.getByLabel('Planning effort')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `overflow at ${size.width}x${size.height}`).toBe(true);
    const box = await panel.boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= size.width + 1, `panel clipped at ${size.width}x${size.height}`).toBe(true);
    for (const name of ['Save model', 'Test connection']) {
      const button = await panel.getByRole('button', { name }).boundingBox();
      expect(button && button.x >= 0 && button.x + button.width <= size.width + 1, `${name} clipped at ${size.width}`).toBe(true);
    }
  }
});
