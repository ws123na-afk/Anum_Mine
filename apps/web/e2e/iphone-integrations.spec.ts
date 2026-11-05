import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';

// Settings, Integrations, iPhone (docs/iphone-integrations.md): devices with a token shown once,
// scopes, rotate, pause and disconnect; one card per integration with its switch, last connection,
// failed events, a synthetic test and the Shortcut setup; loading, empty, error and permission-denied
// states; and the responsive and accessibility gate. The API is mocked at localhost:8000.
interface Mock { failFirst?: boolean; denied?: boolean; withDevice?: boolean }

const now = '2026-11-04T09:00:00Z';

async function mockApi(page: Page, options: Mock = {}) {
  const sent: { method: string; path: string; body: unknown }[] = [];
  let failures = options.failFirst ? 1 : 0;
  const devices: Record<string, unknown>[] = options.withDevice ? [{
    id: 'dev1', name: 'Work iPhone', status: 'active', token_hint: 'wxyz', scopes: ['finance.sms', 'attendance'], created_at: now, rotated_at: null, revoked_at: null,
    last_seen_at: now, last_ok_at: now, last_error: 'disabled', last_error_at: now,
  }] : [];
  const integrations = [
    { key: 'finance.sms', scope: 'finance.sms', enabled: true, allowed: true, ingest_path: '/api/v1/finance/sms/ingest', method: 'POST', body_fields: ['message', 'sender'],
      per_minute: 30, per_day: 1000, devices_with_scope: options.withDevice ? 1 : 0, last_connection_at: options.withDevice ? now : null, last_ok_at: options.withDevice ? now : null,
      failed_7d: 0, needs_safety_check: false },
    { key: 'finance.wallet', scope: 'finance.wallet', enabled: true, allowed: true, ingest_path: '/api/v1/finance/wallet/ingest', method: 'POST',
      body_fields: ['source', 'amount', 'merchant', 'card', 'name'], per_minute: 30, per_day: 1000, devices_with_scope: 0, last_connection_at: null, last_ok_at: null,
      failed_7d: 0, needs_safety_check: false },
    { key: 'attendance.location', scope: 'attendance', enabled: false, allowed: true, ingest_path: '/api/v1/attendance/device/events', method: 'POST',
      body_fields: ['event', 'site', 'occurred_at', 'idempotency_key'], per_minute: 10, per_day: 200, devices_with_scope: options.withDevice ? 1 : 0,
      last_connection_at: options.withDevice ? now : null, last_ok_at: null, failed_7d: options.withDevice ? 1 : 0, needs_safety_check: false },
  ];
  const overview = () => ({ devices, integrations, scopes: ['finance.sms', 'finance.wallet', 'vehicle', 'attendance', 'documents', 'purchases'], max_devices: 5,
    safety_check_passed: true, ping_path: '/api/v1/integrations/iphone/ping' });
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, code = 200) => route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'owner', active: true }]);
    if (!path.startsWith('/api/v1/integrations/iphone')) return json([]);
    const body = request.postData() ? request.postDataJSON() : null;
    sent.push({ method: request.method(), path, body });
    if (options.denied) return json({ detail: 'Your role cannot see this.' }, 403);
    const rest = path.replace('/api/v1/integrations/iphone', '');
    if (rest === '' && request.method() === 'GET') {
      if (failures > 0) { failures -= 1; return json({ detail: 'The database is not reachable' }, 503); }
      return json(overview());
    }
    if (rest === '/devices' && request.method() === 'POST') {
      const device = { id: `dev${devices.length + 2}`, name: (body as { name: string }).name, status: 'active', token_hint: 'abcd', scopes: (body as { scopes: string[] }).scopes,
        created_at: now, rotated_at: null, revoked_at: null, last_seen_at: null, last_ok_at: null, last_error: null, last_error_at: null };
      devices.push(device);
      for (const item of integrations) if (device.scopes.includes(item.scope)) item.devices_with_scope += 1;
      return json({ device, token: 'anum_sms.example.example.example.dev2.exampleexampleexampleexample', headers: { Authorization: 'Bearer <token>', 'Content-Type': 'application/json' } }, 201);
    }
    const change = rest.match(/^\/devices\/([^/]+)\/(rotate|pause|resume|revoke|scopes)$/);
    if (change) {
      const device = devices.find((d) => d.id === change[1]) as Record<string, unknown>;
      if (change[2] === 'rotate') return json({ device, token: 'anum_sms.example.example.example.dev1.rotatedrotatedrotatedrotated', headers: {} });
      if (change[2] === 'pause') device.status = 'paused';
      if (change[2] === 'resume') device.status = 'active';
      if (change[2] === 'revoke') { device.status = 'revoked'; device.revoked_at = now; }
      if (change[2] === 'scopes') device.scopes = (body as { scopes: string[] }).scopes;
      return json(device);
    }
    const switchMatch = rest.match(/^\/integrations\/([^/]+)$/);
    if (switchMatch && request.method() === 'PUT') {
      const item = integrations.find((i) => i.key === switchMatch[1])!;
      item.enabled = (body as { enabled: boolean }).enabled;
      return json(item);
    }
    const testMatch = rest.match(/^\/integrations\/([^/]+)\/test$/);
    if (testMatch) {
      const item = integrations.find((i) => i.key === testMatch[1])!;
      const checks = [{ check: 'device_with_scope', passed: item.devices_with_scope > 0 }, { check: 'switched_on', passed: item.enabled }];
      return json({ integration: item.key, passed: checks.every((c) => c.passed), checks });
    }
    if (rest.match(/^\/integrations\/attendance\.location\/events$/)) {
      return json({ items: options.withDevice ? [{ id: 'ev1', device_id: 'dev1', outcome: 'denied', code: 'disabled', created_at: now }] : [], total: options.withDevice ? 1 : 0, limit: 10, offset: 0 });
    }
    if (rest.match(/^\/integrations\/[^/]+\/events$/)) return json({ items: [], total: 0, limit: 10, offset: 0 });
    return route.abort();
  });
  return { sent };
}

const card = (page: Page, name: string) => page.locator('article.iphCard').filter({ has: page.getByRole('heading', { name, level: 3 }) });

test('connect an iPhone for attendance: the token is shown once and only the ticked scopes are sent', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#iphone');
  await expect(page.getByRole('heading', { name: 'iPhone integrations' })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText('No iPhone connected yet')).toBeVisible();
  await page.getByLabel('Device name').fill('Site phone');
  const form = page.locator('form.iphAdd');
  await form.getByLabel('Bank SMS').uncheck();
  await form.getByLabel('Apple Wallet').uncheck();
  await form.getByLabel('Attendance').check();
  await page.getByRole('button', { name: 'Create token' }).click();
  const dialog = page.getByRole('dialog', { name: 'Your device token' });
  await expect(dialog.getByText('ANUM shows it only once')).toBeVisible();
  await expect(dialog.getByRole('textbox', { name: 'Device token' })).toHaveValue(/^anum_sms\./);
  await expect(dialog.getByText('Authorization: Bearer <token>')).toBeVisible();
  await dialog.getByRole('button', { name: 'I saved it' }).click();
  await expect(page.locator('.iphDevice').getByText('Site phone')).toBeVisible();
  await expect.poll(() => sent.find((s) => s.path.endsWith('/devices') && s.method === 'POST')?.body).toEqual({ name: 'Site phone', scopes: ['attendance'] });
  // The token never shows again once the dialog is closed.
  await expect(page.getByText(/anum_sms\.example/)).toHaveCount(0);
});

test('an integration card: switch on, test, failed events and the exact Shortcut setup', async ({ page }) => {
  const { sent } = await mockApi(page, { withDevice: true });
  await page.goto('/#iphone');
  const attendance = card(page, 'Attendance arrive and leave');
  await expect(attendance).toBeVisible({ timeout: 20_000 });
  await expect(attendance.getByRole('status').first()).toHaveText('Off');
  await attendance.getByRole('button', { name: 'Run test' }).click();
  await expect(attendance.getByText('Test found something to fix:')).toBeVisible();
  await expect(attendance.getByText('The integration is on')).toBeVisible();
  await attendance.getByRole('switch', { name: 'Turn Attendance arrive and leave on or off' }).check();
  await expect.poll(() => sent.find((s) => s.method === 'PUT')?.body).toEqual({ enabled: true });
  await attendance.getByRole('button', { name: 'Run test' }).click();
  await expect(attendance.getByText('Test passed. Nothing was recorded.')).toBeVisible();
  await attendance.getByText('Failed events (1)').click();
  await expect(attendance.getByText('Refused: The integration was off')).toBeVisible();
  await attendance.getByText('Shortcut setup').click();
  await expect(attendance.getByText(/\/api\/v1\/attendance\/device\/events$/).first()).toBeVisible();
  await expect(attendance.getByText('event = arrive (or leave)')).toBeVisible();
  await expect(attendance.getByText('{"integration":"attendance.location"}')).toBeVisible();
  await expect(attendance.getByText(/Shortcuts, Automation, New Automation, Arrive/)).toBeVisible();
  await expect(card(page, 'Bank SMS').getByRole('status').first()).toHaveText('Working');
});

test('a device: change scopes, new token, pause and disconnect', async ({ page }) => {
  const { sent } = await mockApi(page, { withDevice: true });
  await page.goto('/#iphone');
  const device = page.locator('.iphDevice').filter({ hasText: 'Work iPhone' });
  await expect(device).toBeVisible({ timeout: 20_000 });
  await expect(device.getByText(/Last error: The integration was off/)).toBeVisible();
  await device.getByRole('button', { name: 'Change' }).click();
  await device.getByLabel('Apple Wallet').check();
  await device.getByRole('button', { name: 'Save' }).click();
  await expect.poll(() => sent.find((s) => s.path.endsWith('/scopes'))?.body).toEqual({ scopes: ['finance.sms', 'attendance', 'finance.wallet'] });
  await device.getByRole('button', { name: 'New token' }).click();
  await expect(page.getByRole('dialog', { name: 'Your device token' }).getByRole('textbox')).toHaveValue(/rotated/);
  await page.getByRole('button', { name: 'I saved it' }).click();
  await device.getByRole('button', { name: 'Pause' }).click();
  await expect(device.getByText(/^Paused/)).toBeVisible();
  await device.getByRole('button', { name: 'Disconnect' }).click();
  await page.getByRole('alertdialog', { name: 'Disconnect this device?' }).getByRole('button', { name: 'Disconnect' }).click();
  await expect(device.getByText(/^Disconnected/)).toBeVisible();
  await expect(device.getByRole('button', { name: 'New token' })).toHaveCount(0);
});

test('a load error says what failed and retries; a denied role is told who can', async ({ page }) => {
  await mockApi(page, { failFirst: true });
  await page.goto('/#iphone');
  await expect(page.getByText(/Could not load iPhone integrations/)).toBeVisible({ timeout: 20_000 });
  await page.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByRole('heading', { name: 'Devices' })).toBeVisible();
});

test('permission denied', async ({ page }) => {
  await mockApi(page, { denied: true });
  await page.goto('/#iphone');
  await expect(page.getByText('Your role cannot see iPhone integrations. A workspace owner or admin can.')).toBeVisible({ timeout: 20_000 });
});

const VIEWPORTS = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const locale of ['en', 'ar'] as const) {
  for (const [width, height] of VIEWPORTS) {
    test(`responsive @gate: the iPhone page fits with no sideways scroll at ${width}x${height} (${locale})`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      if (locale === 'ar') await useSavedLocale(page, 'ar');
      await mockApi(page, { withDevice: true });
      await page.goto('/#iphone');
      await expect(page.getByRole('heading', { name: locale === 'ar' ? 'تكاملات آيفون' : 'iPhone integrations' })).toBeVisible({ timeout: 20_000 });
      await expect(page.locator('article.iphCard')).toHaveCount(3);
      for (const details of await page.locator('details.iphMore').all()) await details.evaluate((el) => { (el as HTMLDetailsElement).open = true; });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      for (const control of await page.locator('.iph button:visible, .iph input:visible').all()) {
        const box = await control.boundingBox();
        if (!box) continue;
        expect(box.x).toBeGreaterThanOrEqual(-0.5);
        expect(box.x + box.width).toBeLessThanOrEqual(width + 0.5);
      }
      const serious = (await new AxeBuilder({ page }).include('main').analyze()).violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
      expect(serious).toEqual([]);
      await page.screenshot({ path: test.info().outputPath(`iphone-${locale}-${width}x${height}.png`), fullPage: true });
    });
  }
}
