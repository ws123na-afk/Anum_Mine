import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// Finance, Connections and data capture (docs/finance.md#connections-and-data-capture): the KPI row from
// real figures, accounts with the source that really feeds them (no bank sync, no consent date), the
// iPhone SMS capture panel (endpoint with copy, the token only as a hint with rotate, never revealed;
// pause, revoke, the security log, recent messages, the review policy), import history with CSV and PDF
// statements and undo, and permissions; plus loading, empty, error and denied states. Messages are
// SYNTHETIC; the API is mocked at localhost:8000.
interface Mock { empty?: boolean; denied?: boolean; failAccounts?: boolean; noDevice?: boolean }

async function mockApi(page: Page, options: Mock = {}) {
  const state = financeState({ empty: options.empty, denied: options.denied });
  if (!options.empty) state.imports.unshift({ id: 'fin_imp_pdf', account_id: 'fin_acct_card', filename: 'card-november.pdf', preset: 'pdf', date_format: '%d/%m/%Y', row_count: 12, imported_count: 9, duplicate_count: 1, error_count: 0, reconciled: 2, held_for_review: 1, created_at: '2026-11-04T08:00:00Z' });
  const sent: { method: string; path: string; body: unknown }[] = [];
  let settings = {
    enabled: true, ledger_kind: 'personal', auto_post: true, body_retention_days: 30, verified_senders: [], extra_senders: [],
    account_map: options.empty ? [] : [{ last4: '1234', account_id: 'fin_acct_current', credit_limit: null }], wallet_cards: [],
    default_account_id: null, cash_account_id: null, ai: { enabled: false, opted_in_at: null, daily_cap: 20, used_on: null, used: 0 },
    self_test_passed_at: '2026-11-04T08:00:00Z', version: 1, updated_at: null,
  };
  let device = { id: 'dev1', name: 'Test iPhone', status: 'active', token_hint: 'wxyz', scopes: ['finance.sms', 'finance.wallet'], created_at: '2026-11-04T09:00:00Z', rotated_at: null,
    revoked_at: null, last_seen_at: '2026-11-05T09:24:00Z', last_ok_at: '2026-11-05T09:24:00Z', last_error: null, last_error_at: null, message_count: 3 };
  const devices = () => (options.noDevice || options.empty ? [] : [device]);
  const messages = options.empty ? [] : [
    { id: 'm1', device_id: 'dev1', source: 'device', received_at: '2026-11-05T09:12:00Z', sender: 'testbank', bank_key: null, status: 'posted', reason: null, body_masked: '',
      body_kept_until: null, parsed: { kind: 'card_purchase', amount: '420.00', currency: 'SAR', merchant: 'TEST FUEL' }, rule: 'generic', entry_id: 'je1', duplicate_of: null, observed_balance: null, observed_currency: null, attempts: 1 },
    { id: 'm2', device_id: 'dev1', source: 'device', received_at: '2026-11-05T09:05:00Z', sender: 'testbank', bank_key: null, status: 'review', reason: 'format_unverified', body_masked: '',
      body_kept_until: null, parsed: { kind: 'transfer_in', amount: '620.00', currency: 'SAR' }, rule: 'generic', entry_id: null, duplicate_of: null, observed_balance: null, observed_currency: null, attempts: 1 },
  ];
  const status = () => ({
    enabled: settings.enabled, ledger_kind: settings.ledger_kind, self_test_passed_at: settings.self_test_passed_at, ready: true, ingest_path: '/api/v1/finance/sms/ingest',
    devices_active: devices().filter((d) => d.status === 'active').length, last_received_at: options.empty ? null : '2026-11-05T09:24:00Z', last_ok_at: null, last_error: null, last_error_at: null,
    counts: options.empty ? {} : { posted: 4, attached: 1, review: 2, test_ok: 1 }, review_count: options.empty ? 0 : 2,
  });
  const integration = (key: string, scope: string, enabled: boolean) => ({ key, scope, enabled, allowed: true, ingest_path: `/api/v1/${key}`, method: 'POST', body_fields: [], per_minute: 30, per_day: 1000,
    devices_with_scope: devices().filter((d) => d.scopes.includes(scope)).length, last_connection_at: null, last_ok_at: null, failed_7d: 0, needs_safety_check: false });
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, code = 200) => route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'member', active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'member', active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    const body = request.postData() ? request.postDataJSON() : null;
    if (path.startsWith('/api/v1/finance') || path.startsWith('/api/v1/integrations')) sent.push({ method: request.method(), path, body });
    if (path === '/api/v1/integrations/iphone') return json({ devices: devices(), integrations: [integration('finance.sms', 'finance.sms', true), integration('finance.wallet', 'finance.wallet', true), integration('attendance.location', 'attendance', false)], scopes: ['finance.sms', 'finance.wallet', 'attendance'], max_devices: 5, safety_check_passed: true, ping_path: '/api/v1/integrations/iphone/ping' });
    if (path === '/api/v1/integrations/iphone/integrations/finance.sms/events') return json({ items: [{ id: 'ev1', device_id: 'dev1', outcome: 'denied', code: 'scope_missing', created_at: '2026-11-05T08:00:00Z' }], total: 1, limit: 20, offset: 0 });
    if (path.startsWith('/api/v1/integrations/iphone/integrations/')) return json({ items: [], total: 0, limit: 20, offset: 0 });
    const sub = path.replace('/api/v1/finance', '');
    if (options.denied && path.startsWith('/api/v1/finance')) return json({ detail: 'Finance is for workspace owners and members.' }, 403);
    if (options.failAccounts && sub === '/accounts') return json({ error: { code: 'internal_error', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }, 503);
    if (sub === '/sms/status') return json(status());
    if (sub === '/sms/settings' && request.method() === 'GET') return json(settings);
    if (sub === '/sms/settings') { settings = { ...settings, ...(body as object), version: settings.version + 1 }; return json(settings); }
    if (sub === '/sms/devices' && request.method() === 'GET') return json(devices());
    if (sub === '/sms/devices/dev1/pause') { device = { ...device, status: 'paused' }; return json(device); }
    if (sub === '/sms/devices/dev1/resume') { device = { ...device, status: 'active' }; return json(device); }
    if (sub === '/sms/devices/dev1/revoke') { device = { ...device, status: 'revoked' }; return json(device); }
    if (sub === '/sms/devices/dev1/rotate') {
      device = { ...device, token_hint: 'abcd' };
      return json({ device, token: 'anum_sms.example.example.example.dev1.rotatedexample', ingest_path: '/api/v1/finance/sms/ingest', headers: { Authorization: 'Bearer <token>', 'Content-Type': 'application/json' }, json_fields: { message: 'Shortcut Input, Content', sender: 'Shortcut Input, Sender' } });
    }
    if (sub === '/sms/messages') return json({ items: messages, total: messages.length, limit: 5, offset: 0 });
    if (sub === '/sms/banks' || sub === '/sms/rules') return json([]);
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { sent };
}

test('connected accounts show their real source and figures, never a bank sync', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-connections');
  await expect(page.getByRole('heading', { name: 'Connections and data capture', level: 1 })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('tab', { name: 'Connected accounts' })).toHaveAttribute('aria-selected', 'true');
  const kpis = page.getByRole('region', { name: 'Data capture at a glance' });
  await expect(kpis.getByRole('listitem').filter({ hasText: 'Accounts in ANUM' })).toContainText('2');
  await expect(kpis.getByRole('listitem').filter({ hasText: 'Shortcut routes' })).toContainText('Working, of 3 available');
  await expect(kpis.getByRole('listitem').filter({ hasText: 'Records awaiting review' })).toContainText('2');
  await expect(page.getByText('ANUM imports read-only activity and cannot initiate transfers or payments.').first()).toBeVisible();
  const accounts = page.getByRole('region', { name: 'Accounts (2)' });
  const everyday = accounts.getByRole('listitem').filter({ hasText: 'Everyday account' });
  await expect(everyday).toContainText('SMS capture');
  await expect(everyday).toContainText('Statements');
  await expect(everyday).toContainText('Read-only');
  const card = accounts.getByRole('listitem').filter({ hasText: 'Rewards card' });
  await expect(card).toContainText('Statements');
  // No invented bank-sync figures.
  await expect(page.getByText(/Consent expires|Synced transactions|Pause sync|Reconnect/)).toHaveCount(0);
  await accounts.getByRole('button', { name: 'Add a source' }).click();
  await expect(page.getByText('Live bank connections (Open Finance) are not available yet.', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: /Import a statement/ }).first().click();
  await expect(page).toHaveURL(/#finance-import$/);
});

test('SMS capture: the endpoint copies, the token is only a hint and rotates once, pause, revoke and the security log', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const { sent } = await mockApi(page);
  await page.goto('/#finance-connections');
  const capture = page.getByRole('region', { name: 'iPhone SMS capture' });
  await expect(capture.locator('.fconnCaptureHead').getByText('Active', { exact: true })).toBeVisible({ timeout: 20_000 });
  await expect(capture.getByRole('textbox', { name: 'Endpoint' })).toHaveValue(/\/api\/v1\/finance\/sms\/ingest$/);
  await capture.getByRole('button', { name: 'Copy the endpoint' }).click();
  await expect(capture.getByRole('button', { name: 'Copied' })).toBeVisible();
  const token = capture.getByRole('textbox', { name: 'Token' });
  await expect(token).toHaveValue('••••••••••••wxyz');
  await expect(capture.getByRole('button', { name: /reveal|show token/i })).toHaveCount(0);
  await capture.getByRole('button', { name: 'Rotate token' }).click();
  await page.getByRole('alertdialog', { name: 'Rotate this token?' }).getByRole('button', { name: 'Rotate token' }).click();
  const dialog = page.getByRole('dialog', { name: 'Your device token' });
  await expect(dialog.getByRole('textbox', { name: 'Token' })).toHaveValue(/rotatedexample$/);
  await dialog.getByRole('button', { name: 'I saved it' }).click();
  await expect(token).toHaveValue('••••••••••••abcd');
  await capture.getByRole('button', { name: 'Pause capture' }).click();
  await expect(capture.getByRole('button', { name: 'Resume capture' })).toBeVisible();
  await capture.getByRole('button', { name: 'View security log' }).click();
  const log = page.getByRole('dialog', { name: 'Security log' });
  await expect(log.getByRole('cell', { name: 'Test iPhone' })).toBeVisible();
  await log.getByRole('button', { name: 'Close' }).click();
  // Recent messages: real statuses, amounts and readings; no invented confidence.
  const recent = capture.getByRole('region', { name: /Recent SMS messages/ });
  await expect(recent.getByRole('row').filter({ hasText: 'TEST FUEL' })).toContainText('Recorded');
  await expect(recent.getByRole('row').filter({ hasText: 'TEST FUEL' })).toContainText('Card purchase');
  await expect(recent.getByRole('row').filter({ hasText: '620' })).toContainText('Format not confirmed yet');
  // Review policy: the real auto_post and retention settings.
  await capture.getByRole('switch', { name: 'Always require review before recording' }).click();
  await expect(capture.getByRole('switch', { name: 'Always require review before recording' })).toHaveAttribute('aria-checked', 'true');
  expect(sent.find((s) => s.path.endsWith('/sms/settings') && s.method === 'PUT')?.body).toMatchObject({ auto_post: false, version: 1 });
  await capture.getByRole('button', { name: 'Revoke access' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Disconnect' }).click();
  await expect(capture.getByText('No device yet.')).toBeVisible();
  expect(sent.some((s) => s.path.endsWith('/sms/devices/dev1/revoke'))).toBe(true);
});

test('work scope labels the ledger as business or personal', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-connections');
  const scope = page.getByRole('radiogroup', { name: 'Work scope:' });
  await expect(scope.getByRole('radio', { name: 'Personal' })).toHaveAttribute('aria-checked', 'true', { timeout: 20_000 });
  await scope.getByRole('radio', { name: 'Business' }).click();
  await expect(scope.getByRole('radio', { name: 'Business' })).toHaveAttribute('aria-checked', 'true');
  expect(sent.find((s) => s.path.endsWith('/sms/settings') && s.method === 'PUT')?.body).toMatchObject({ ledger_kind: 'business' });
});

test('import history lists CSV and PDF statements and undoes one; permissions say what ANUM cannot do', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-connections');
  await page.getByRole('tab', { name: 'Import history' }).click();
  const history = page.getByRole('region', { name: 'Import history' });
  await expect(history.getByRole('row').filter({ hasText: 'card-november.pdf' })).toContainText('PDF', { timeout: 20_000 });
  await expect(history.getByRole('row').filter({ hasText: 'october.csv' })).toContainText('CSV');
  await history.getByRole('button', { name: 'Undo the import of card-november.pdf' }).click();
  await page.getByRole('alertdialog', { name: 'Undo this import?' }).getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByText(/card-november\.pdf/).first()).toBeVisible();
  expect(sent.some((s) => s.method === 'DELETE' && s.path.endsWith('/imports/fin_imp_pdf'))).toBe(true);
  await page.getByRole('tab', { name: 'Permissions' }).click();
  await expect(page.getByText(/ANUM cannot log in to a bank, move money or pay/)).toBeVisible();
  await expect(page.getByRole('region', { name: 'Device tokens' }).getByRole('cell', { name: '••••••••••••wxyz' })).toBeVisible();
  // Keyboard: arrow keys move between the tabs.
  await page.getByRole('tab', { name: 'Permissions' }).focus();
  await page.keyboard.press('ArrowLeft');
  await expect(page.getByRole('tab', { name: 'Import history' })).toBeFocused();
  await page.getByRole('tab', { name: 'SMS capture' }).click();
  await expect(page.getByRole('heading', { name: 'iPhone SMS forwarding' })).toBeVisible();
});

test('empty, error and denied states', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#finance-connections');
  await expect(page.getByRole('heading', { name: 'No accounts yet' })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText('Nothing received yet')).toBeVisible();
  await expect(page.getByText('No messages yet.', { exact: false })).toBeVisible();
  await page.getByRole('tab', { name: 'Import history' }).click();
  await expect(page.getByRole('heading', { name: 'No imports yet' })).toBeVisible();
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { failAccounts: true });
  await page.goto('/#finance-accounts');
  await page.goto('/#finance-connections');
  await expect(page.getByText(/The accounts could not be loaded/)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('button', { name: 'Try again' }).first()).toBeVisible();
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { denied: true });
  await page.goto('/#finance-accounts');
  await page.goto('/#finance-connections');
  await expect(page.locator('#fin-denied').first()).toBeVisible({ timeout: 20_000 });
});

const VIEWPORTS = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const locale of ['en', 'ar'] as const) {
  for (const [width, height] of VIEWPORTS) {
    test(`responsive @gate: connections fit with no sideways scroll at ${width}x${height} (${locale})`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      if (locale === 'ar') await useSavedLocale(page, 'ar');
      await mockApi(page);
      await page.goto('/#finance-connections');
      await expect(page.getByRole('heading', { name: locale === 'ar' ? 'الاتصالات والتقاط البيانات' : 'Connections and data capture', level: 1 })).toBeVisible({ timeout: 20_000 });
      await expect(page.locator('.fconnAccount').first()).toBeVisible();
      await expect(page.locator('.fconnTable').first()).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      for (const control of await page.locator('.fconn button:visible').all()) {
        const box = await control.boundingBox();
        if (!box) continue;
        if (await control.evaluate((el) => !!el.closest('.finTabs, .cmTableWrap'))) continue;
        expect(box.x).toBeGreaterThanOrEqual(-0.5);
        expect(box.x + box.width).toBeLessThanOrEqual(width + 0.5);
      }
      const serious = (await new AxeBuilder({ page }).include('main').analyze()).violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
      expect(serious).toEqual([]);
      await page.screenshot({ path: test.info().outputPath(`fconn-${locale}-${width}x${height}.png`), fullPage: true });
    });
  }
}
