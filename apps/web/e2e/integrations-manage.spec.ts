import { expect, test, type Page } from '@playwright/test';

// Integrations: last error and how to fix it, test connection, and the workspace's own
// encrypted token for the external REST webhook, against a mocked API
// (docs/tools-and-integrations.md#workspace-configuration).
const now = new Date().toISOString();
type Json = Record<string, unknown>;
const envelope = (message: string) => ({ error: { code: 'forbidden', message, correlation_id: 'corr_e2e', details: [] } });

const base = (over: Json): Json => ({
  latency_ms: 3, credentials: { configured: true, source: 'environment', scopes: [], expires_at: null },
  enabled: true, available: true, remediation: null, environment_variable: null, checked_at: now,
  last_error: null, last_error_at: null, accepts_workspace_credential: false, workspace_credential: null, ...over,
});

async function mockApi(page: Page, role: 'owner' | 'member' = 'owner') {
  const calls: { method: string; path: string; body: unknown }[] = [];
  let valkeyHealthy = false;
  let token: Json | null = null;
  const valkey = () => base({
    id: 'valkey', name: 'Valkey', kind: 'cache', endpoint: 'redis://valkey:6379/0', environment_variable: 'ANUM_VALKEY_URL',
    status: valkeyHealthy ? 'connected' : 'degraded', detail: valkeyHealthy ? 'Health check passed.' : 'Health check failed: ConnectionRefusedError',
    remediation: valkeyHealthy ? null : 'Check that Valkey at ANUM_VALKEY_URL is running and reachable from the API.',
    last_error: 'Health check failed: ConnectionRefusedError', last_error_at: now,
  });
  const webhook = () => base({
    id: 'external-webhook', name: 'External REST webhook', kind: 'tool', endpoint: 'https://hooks.example', status: 'connected', detail: 'Health check passed.',
    environment_variable: 'ANUM_EXTERNAL_WEBHOOK_URL', accepts_workspace_credential: true,
    credentials: { configured: Boolean(token), source: token ? 'workspace' : 'none', scopes: ['external.action'], expires_at: null },
    workspace_credential: { integration_id: 'external-webhook', configured: Boolean(token), hint: token ? '…abcd' : null, updated_by: token ? 'user_local' : null, updated_at: token ? now : null },
  });
  const nats = () => base({
    id: 'nats', name: 'NATS JetStream', kind: 'event_bus', endpoint: 'nats://nats:4222', status: 'disabled', available: false, latency_ms: null,
    detail: 'Not used: events stay in the API process (ANUM_EVENT_BUS=memory). Set ANUM_EVENT_BUS=nats and ANUM_NATS_URL to publish through NATS JetStream.',
  });
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
    if (path === '/api/v1/integrations') return json([nats(), webhook(), valkey()]);
    if (path === '/api/v1/integrations/valkey/test' && method === 'POST') { valkeyHealthy = true; return json(valkey()); }
    if (path === '/api/v1/integrations/external-webhook/configuration' && method === 'GET') return json({ id: 'external-webhook', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', enabled: true, endpoint: null });
    if (path === '/api/v1/integrations/external-webhook/credential' && method === 'PUT') {
      if (role !== 'owner') return json(envelope('Not allowed'), 403);
      token = { hint: '…abcd' };
      return json({ integration_id: 'external-webhook', configured: true, hint: '…abcd', updated_by: 'user_local', updated_at: now });
    }
    if (path === '/api/v1/integrations/external-webhook/credential' && method === 'DELETE') { token = null; return route.fulfill({ status: 204 }); }
    return route.abort();
  });
  return calls;
}

const card = (page: Page, name: string) => page.locator('article.integrationCard', { has: page.getByRole('heading', { name, exact: true }) });

test('a degraded integration says how to fix it, and Test connection records the recovery', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#integrations');
  await expect(page.getByText('1 connected · 1 need attention · 1 not used')).toBeVisible();
  const valkey = card(page, 'Valkey');
  await expect(valkey.getByText('degraded')).toBeVisible();
  await expect(valkey.getByText(/How to fix:/)).toBeVisible();
  await expect(valkey.getByText(/running and reachable from the API/)).toBeVisible();

  await valkey.getByRole('button', { name: 'Test connection' }).click();
  await expect(valkey.getByText(/Connection to Valkey works/)).toBeVisible();
  await expect(valkey.getByText('connected')).toBeVisible();
  // The failure stays visible as history after the recovery.
  await expect(valkey.getByText(/Last error .*: Health check failed: ConnectionRefusedError/)).toBeVisible();
  expect(calls.some((c) => c.method === 'POST' && c.path === '/api/v1/integrations/valkey/test')).toBe(true);
  // An integration the operator did not configure offers nothing to test or switch.
  const nats = card(page, 'NATS JetStream');
  await expect(nats.getByRole('button', { name: 'Test connection' })).toBeDisabled();
  await nats.getByRole('button', { name: 'Configure NATS JetStream' }).click();
  await expect(nats.getByText(/nothing to switch here/)).toBeVisible();
});

test('an owner stores, sees only the hint of, and removes the webhook token', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#integrations');
  const hook = card(page, 'External REST webhook');
  await expect(hook.getByText('No token: calls are sent without an Authorization header.')).toBeVisible();
  await hook.getByRole('button', { name: 'Configure External REST webhook' }).click();
  const form = page.getByRole('form', { name: 'External REST webhook token' });
  await form.getByLabel(/Workspace token/).fill('too short');
  await form.getByRole('button', { name: 'Store token' }).click();
  await expect(hook.getByText('The token cannot contain spaces or line breaks.')).toBeVisible();
  await form.getByLabel(/Workspace token/).fill('example-workspace-abcd');
  await form.getByRole('button', { name: 'Store token' }).click();
  await expect(hook.getByText('Token …abcd stored encrypted for this workspace.')).toBeVisible();
  await expect(hook.getByText(/Workspace token …abcd stored encrypted by user_local/)).toBeVisible();
  await expect(form.getByLabel(/Replace the workspace token/)).toHaveValue('');
  expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ secret: 'example-workspace-abcd' });

  await form.getByRole('button', { name: 'Remove token...' }).click();
  await form.getByRole('button', { name: 'Remove token', exact: true }).click();
  await expect(hook.getByText(/Workspace token removed/)).toBeVisible();
  expect(calls.some((c) => c.method === 'DELETE')).toBe(true);
});

test('a member sees health and who can change it, with no controls', async ({ page }) => {
  await mockApi(page, 'member');
  await page.goto('/#integrations');
  await expect(page.getByText(/Only workspace owners can test connections, turn integrations off or store credentials/)).toBeVisible();
  await expect(card(page, 'Valkey').getByText(/How to fix:/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Test connection' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Configure / })).toHaveCount(0);
});
