import { expect, test, type Page } from '@playwright/test';

// Integrations view: live health including the private web search (SearXNG) behind the
// `web_search` agent tool, and the owner's per-workspace switch, against a mocked API
// (docs/tools-and-integrations.md#web-search).
const now = new Date().toISOString();
type Json = Record<string, unknown>;

const health = (id: string, name: string, kind: string, status: string, endpoint: string, detail: string, latency: number | null = 3): Json => ({
  id, name, kind, status, endpoint, latency_ms: latency, detail,
  credentials: { configured: status !== 'disabled', source: 'environment', scopes: [], expires_at: null },
});

async function mockApi(page: Page, searchStatus: 'connected' | 'disabled' | 'degraded') {
  const calls: { method: string; path: string; body: unknown }[] = [];
  let enabled = searchStatus !== 'disabled';
  const search = () => {
    if (!enabled) return health('searxng', 'SearXNG web search', 'search', 'disabled', 'http://searxng:8080', 'Turned off for this workspace by an owner.', null);
    if (searchStatus === 'degraded') return health('searxng', 'SearXNG web search', 'search', 'degraded', 'http://searxng:8080', 'Health check failed: ConnectError', 2500);
    return health('searxng', 'SearXNG web search', 'search', 'connected', 'http://searxng:8080', 'Health check passed.', 12);
  };
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
    if (path === '/api/v1/integrations') {
      return json([
        health('postgresql', 'PostgreSQL + pgvector', 'database', 'connected', 'postgresql+psycopg://***@postgres:5432/anum', 'Health check passed.'),
        health('valkey', 'Valkey', 'cache', 'connected', 'redis://valkey:6379/0', 'Health check passed.'),
        search(),
      ]);
    }
    if (path === '/api/v1/integrations/searxng/configuration' && method === 'GET') {
      return json({ id: 'searxng', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', enabled, endpoint: 'http://searxng:8080' });
    }
    if (path === '/api/v1/integrations/searxng/configuration' && method === 'PUT') {
      enabled = Boolean((body as Json).enabled);
      return json({ id: 'searxng', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', ...(body as Json) });
    }
    return route.abort();
  });
  return calls;
}

const searchCard = (page: Page) => page.locator('article.integrationCard', { has: page.getByRole('heading', { name: 'SearXNG web search' }) });

test('a connected SearXNG says agents can use web_search', async ({ page }) => {
  await mockApi(page, 'connected');
  await page.goto('/#integrations');
  const card = searchCard(page);
  await expect(card).toBeVisible();
  await expect(card.getByText('connected')).toBeVisible();
  await expect(card.getByText('Agent tool web_search: available.')).toBeVisible();
  // Infrastructure integrations carry no tool note.
  const valkey = page.locator('article.integrationCard', { has: page.getByRole('heading', { name: 'Valkey' }) });
  await expect(valkey.getByText(/Agent tool/)).toHaveCount(0);
});

test('a degraded SearXNG explains that web_search calls fail', async ({ page }) => {
  await mockApi(page, 'degraded');
  await page.goto('/#integrations');
  const card = searchCard(page);
  await expect(card.getByText('degraded')).toBeVisible();
  await expect(card.getByText(/Health check failed: ConnectError/)).toBeVisible();
  await expect(card.getByText('Agent tool web_search: calls fail until search answers.')).toBeVisible();
});

test('an owner switches web search off for the workspace and the card says why', async ({ page }) => {
  const calls = await mockApi(page, 'connected');
  await page.goto('/#integrations');
  const card = searchCard(page);
  await card.getByRole('button', { name: 'Configure SearXNG web search' }).click();
  const form = page.getByRole('form', { name: 'SearXNG web search configuration' });
  const toggle = form.getByRole('checkbox', { name: 'Use SearXNG web search in this workspace' });
  await expect(toggle).toBeChecked();
  await toggle.uncheck();
  await form.getByRole('button', { name: 'Save configuration' }).click();

  await expect(card.getByText(/SearXNG web search turned off for workspace workspace_foundation/)).toBeVisible();
  await expect(card.getByText('Not used')).toBeVisible();
  await expect(card.getByText('Turned off for this workspace by an owner.')).toBeVisible();
  await expect(card.getByText('Agent tool web_search: unavailable here.')).toBeVisible();
  const put = calls.find((c) => c.method === 'PUT');
  expect(put?.body).toEqual({ enabled: false, endpoint: 'http://searxng:8080' });
});
