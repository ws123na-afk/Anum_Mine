import { expect, test, type Page } from '@playwright/test';

// Integrations > Connection setup (docs/integrations-setup.md): live status per integration from
// the API, what is missing on the server, the callback URLs to register, where each one is
// connected, and the organization administrator's connection check, against a mocked API.
const now = new Date().toISOString();
type Json = Record<string, unknown>;

const item = (id: string, group: string, status: string, over: Json = {}): Json => ({
  id, group, status, connect_view: null, connections: null, failing: 0, missing: [], redirect_urls: [], doc: '1-ai-model', checkable: false, ...over,
});

function setupBody(details: boolean, check: boolean): Json {
  const hide = (value: Json): Json => (details ? value : { ...value, missing: [], redirect_urls: [] });
  return {
    can_view_setup: details,
    can_check: check,
    items: [
      item('model', 'ai', 'connected', { connect_view: 'settings', checkable: true }),
      item('speech', 'ai', 'not_configured', { connect_view: 'voice', missing: ['ANUM_SPEECH_STT_BASE_URL', 'ANUM_SPEECH_STT_MODEL'], doc: '2-voice-speech-to-text-and-text-to-speech' }),
      item('email_gmail', 'email', 'not_configured', { connect_view: 'inbox', missing: ['ANUM_EMAIL_GOOGLE_CLIENT_ID', 'ANUM_EMAIL_GOOGLE_CLIENT_SECRET'], redirect_urls: ['https://api.example.com/api/v1/email/oauth/callback'], doc: '5-email-gmail-outlook-and-zoho' }),
      item('email_outlook', 'email', 'connected', { connect_view: 'inbox', connections: 2, checkable: true, doc: '5-email-gmail-outlook-and-zoho' }),
      item('whatsapp', 'messaging', 'error', { connect_view: 'whatsapp', connections: 1, failing: 1, redirect_urls: ['https://api.example.com/api/v1/whatsapp/webhook'], checkable: true, doc: '6-whatsapp-business' }),
      item('push', 'notifications', 'connected', { checkable: true, doc: '8-phone-push-firebase-cloud-messaging' }),
      item('commerce', 'commerce', 'ready', { connect_view: 'commerce', connections: 0, doc: '15-sales-channels-amazon-noon-and-your-own-store' }),
    ].map(hide),
  };
}

type Mode = { details?: boolean; check?: boolean; status?: number };

async function mockApi(page: Page, mode: Mode = {}) {
  const calls: { method: string; path: string }[] = [];
  let failures = mode.status && mode.status >= 500 ? 1 : 0;
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    calls.push({ method, path });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/integrations') return json([]);
    if (path === '/api/v1/workspace-memberships/current') {
      return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: mode.details === false ? 'member' : 'owner', active: true, created_at: now, updated_at: now });
    }
    if (path === '/api/v1/integrations/setup') {
      if (mode.status === 403) return json({ error: { code: 'forbidden', message: 'Not allowed', details: [] } }, 403);
      if (failures > 0) { failures -= 1; return json({ error: { code: 'internal', message: 'The API had a problem.', details: [] } }, 500); }
      return json(setupBody(mode.details ?? true, mode.check ?? true));
    }
    if (path === '/api/v1/integrations/setup/whatsapp/check' && method === 'POST') {
      return json({ id: 'whatsapp', reachable: false, latency_ms: null, detail: 'TimeoutError', checked_at: now });
    }
    if (path === '/api/v1/integrations/setup/model/check' && method === 'POST') {
      return json({ id: 'model', reachable: true, latency_ms: 87, detail: 'Reachable', checked_at: now });
    }
    return route.abort();
  });
  return calls;
}

const panel = (page: Page) => page.locator('section.setupPanel');
const row = (page: Page, name: string) => panel(page).locator('li.setupRow', { has: page.getByRole('heading', { name, exact: true }) });

test('an organization administrator sees what is missing, copies the callback URL and runs a check', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => undefined);
  const calls = await mockApi(page);
  await page.goto('/#integrations');
  await expect(page.getByRole('heading', { level: 2, name: 'Connection setup' })).toBeVisible();
  await expect(panel(page).getByText('3 connected · 1 ready to connect · 3 need setup')).toBeVisible();

  const gmail = row(page, 'Gmail / Google Workspace');
  await expect(gmail.getByText('Not set up', { exact: true })).toBeVisible();
  await expect(gmail.getByText('ANUM_EMAIL_GOOGLE_CLIENT_ID')).toBeVisible();
  await expect(gmail.getByText('https://api.example.com/api/v1/email/oauth/callback')).toBeVisible();
  await expect(gmail.getByRole('link', { name: /Setup guide for Gmail/ })).toHaveAttribute('href', /integrations-setup\.md#5-email-gmail-outlook-and-zoho$/);
  await gmail.getByRole('button', { name: 'Copy' }).click();
  await expect(gmail.getByRole('status').or(gmail.getByRole('alert'))).toBeVisible();

  const whatsapp = row(page, 'WhatsApp Business');
  await expect(whatsapp.getByText('One connected account is failing', { exact: false })).toBeVisible();
  await whatsapp.getByRole('button', { name: 'Check connection to WhatsApp Business' }).click();
  await expect(whatsapp.getByRole('alert')).toContainText('WhatsApp Business did not answer (TimeoutError)');
  expect(calls.some((c) => c.method === 'POST' && c.path === '/api/v1/integrations/setup/whatsapp/check')).toBe(true);

  const model = row(page, 'AI model');
  await model.getByRole('button', { name: 'Check connection to AI model' }).click();
  await expect(model.getByRole('status')).toContainText('AI model answered in 87 ms.');

  // A connected item with accounts says how many; Open goes to where it is connected.
  await expect(row(page, 'Outlook / Microsoft 365').getByText('Working: 2 accounts connected in this workspace.')).toBeVisible();
  await row(page, 'Sales channels').getByRole('button', { name: 'Open Commerce' }).click();
  await expect(page.locator('section.setupPanel')).toHaveCount(0);
});

test('search and filters narrow the list and an empty match offers a way back', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#connections');
  await expect(panel(page)).toBeVisible();
  await panel(page).getByRole('radio', { name: 'Needs setup' }).check();
  await expect(panel(page).locator('li.setupRow')).toHaveCount(3);
  await panel(page).getByRole('radio', { name: 'All' }).check();
  await panel(page).getByLabel('Search integrations').fill('google_client');
  await expect(panel(page).locator('li.setupRow')).toHaveCount(1);
  await panel(page).getByLabel('Search integrations').fill('telegram');
  await expect(panel(page).getByRole('heading', { name: 'No integration matches' })).toBeVisible();
  await panel(page).getByRole('button', { name: 'Show all' }).click();
  await expect(panel(page).locator('li.setupRow')).toHaveCount(7);
});

test('a member sees the status but no setting names, callback URLs or checks', async ({ page }) => {
  await mockApi(page, { details: false, check: false });
  await page.goto('/#integrations');
  await expect(panel(page).getByText(/Only workspace owners and organization administrators see which server settings are missing/)).toBeVisible();
  await expect(row(page, 'Gmail / Google Workspace').getByText('Not set up yet. Your administrator connects it on the server.')).toBeVisible();
  await expect(panel(page).getByText('ANUM_EMAIL_GOOGLE_CLIENT_ID')).toHaveCount(0);
  await expect(panel(page).getByRole('button', { name: /Check connection/ })).toHaveCount(0);
});

test('a workspace owner without the organization role is told who runs checks', async ({ page }) => {
  await mockApi(page, { details: true, check: false });
  await page.goto('/#integrations');
  await expect(panel(page).getByText(/Connection checks are for organization administrators/)).toBeVisible();
  await expect(panel(page).getByText('ANUM_EMAIL_GOOGLE_CLIENT_ID')).toBeVisible();
  await expect(panel(page).getByRole('button', { name: /Check connection/ })).toHaveCount(0);
});

test('a failed load says what failed and Try again recovers', async ({ page }) => {
  await mockApi(page, { status: 500 });
  await page.goto('/#integrations');
  await expect(panel(page).getByRole('alert')).toContainText('Could not load connection setup');
  await panel(page).getByRole('button', { name: 'Try again' }).click();
  await expect(row(page, 'AI model')).toBeVisible();
});

test('a role that cannot read integrations is told who can change it', async ({ page }) => {
  await mockApi(page, { status: 403 });
  await page.goto('/#integrations');
  await expect(panel(page).getByText(/Ask a workspace owner to change your role/)).toBeVisible();
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
  test(`connection setup fits ${width}x${height} without horizontal overflow`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockApi(page);
    await page.goto('/#integrations');
    await expect(row(page, 'WhatsApp Business')).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    for (const button of await panel(page).getByRole('button').all()) {
      const box = await button.boundingBox();
      if (box) expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
    }
  });
}
