import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// iPhone SMS banking and the ledger (docs/finance-sms.md): the safety check, the switch, a device token
// shown once with the Shortcut steps, the review queue with a correction, taught formats, the banks
// list and the Ledger dashboard, plus loading, empty, error and permission-denied states. Messages are
// SYNTHETIC; the API is mocked at localhost:8000.
interface SmsMock { ready?: boolean; empty?: boolean; denied?: boolean; failLedger?: boolean; learned?: boolean }

async function mockApi(page: Page, options: SmsMock = {}) {
  const state = financeState({ denied: options.denied });
  const sent: { method: string; path: string; body: unknown }[] = [];
  let settings = {
    enabled: false, ledger_kind: 'personal', auto_post: true, body_retention_days: 30, verified_senders: [], extra_senders: [], account_map: [], wallet_cards: [],
    default_account_id: null, cash_account_id: null, ai: { enabled: false, opted_in_at: null, daily_cap: 20, used_on: null, used: 0 },
    self_test_passed_at: options.ready ? '2026-11-04T08:00:00Z' : null, version: 1, updated_at: null,
  };
  const devices: Record<string, unknown>[] = [];
  const message = {
    id: 'fin_sms_1', device_id: 'dev1', source: 'device', received_at: '2026-11-03T10:00:00Z', sender: 'testbank', bank_key: 'test', status: 'review',
    body_masked: 'SYNTHETIC. Purchase of SAR 45.50 at TEST SHOP with card ***1234 on 03/11/2026', body_kept_until: '2026-12-03T10:00:00Z',
    reason: 'format_unverified', parsed: { kind: 'card_purchase', amount: '45.50', currency: 'SAR', merchant: 'TEST SHOP', last4: '1234' }, rule: 'generic',
    entry_id: null, duplicate_of: null, observed_balance: null, observed_currency: null, attempts: 1,
    fix: { action: 'trust_sender', sender: 'testbank', last4: null, account_id: null, account_name: null, party: null, kind: null, ref: null },
  };
  const messages = options.empty ? [] : [message];
  // What ANUM learned (docs/finance-sms.md#learning): one notice when the mock says so.
  let notices = options.learned ? [{ id: 'n1', kind: 'sender_trusted', sender: 'testbank', party: null, value: null, last4: null, account_id: null, account_name: null, ref: null, created_at: '2026-11-04T09:00:00Z' }] : [];
  const learning = () => ({ enabled: true, confirm_after: 2, auto_post: settings.auto_post, auto_post_limits: [], senders: [], kinds: [], categories: [], card_proposals: [], notices, version: settings.version });
  const summary = () => {
    const waiting = messages.filter((m) => m.status === 'review');
    return { total: waiting.length, scanned: waiting.length, complete: true,
      groups: waiting.length ? [{ reason: 'format_unverified', count: waiting.length, items: [{ key: 'testbank', count: waiting.length, fix: message.fix }] }] : [] };
  };
  const status = () => ({
    enabled: settings.enabled, ledger_kind: settings.ledger_kind, self_test_passed_at: settings.self_test_passed_at, ready: Boolean(settings.self_test_passed_at),
    ingest_path: settings.self_test_passed_at ? '/api/v1/finance/sms/ingest' : null, devices_active: devices.length, last_received_at: messages.length ? message.received_at : null,
    last_ok_at: null, last_error: null, last_error_at: null, counts: { review: messages.filter((m) => m.status === 'review').length }, review_count: messages.filter((m) => m.status === 'review').length,
  });
  const money = (amount: string) => [{ currency: 'SAR', amount }];
  const dashboard = {
    month: '2026-11', income: options.empty ? [] : money('12000.00'), expenses: options.empty ? [] : money('45.50'), surplus: options.empty ? [] : money('11954.50'),
    assets: money('12000.00'), liabilities: money('45.50'), net_worth: money('11954.50'), in_transit: [],
    accounts: [{ id: 'acc_bank', name: 'Current', kind: 'bank', currency: 'SAR', last4: '5678', balance: '12000.00', owed: null, credit_limit: null, utilization_percent: null, observed_balance: '14000.00', observed_at: '2026-11-01T08:00:00Z', difference: '2000.00' }],
    cards: [{ id: 'acc_card', name: 'Card', kind: 'card', currency: 'SAR', last4: '1234', balance: '-45.50', owed: '45.50', credit_limit: '5000', utilization_percent: 1, observed_balance: null, observed_at: null, difference: null }],
    loans: [], categories: options.empty ? [] : [{ category: 'groceries', currency: 'SAR', amount: '45.50', count: 1 }],
    merchants: options.empty ? [] : [{ merchant: 'TEST SHOP', currency: 'SAR', amount: '45.50', count: 1 }],
    trend: options.empty ? [] : [{ month: '2026-11', currency: 'SAR', income: '12000.00', expenses: '45.50', net: '11954.50' }],
    year: [], currencies: ['SAR'], entry_count: options.empty ? 0 : 2, ledger_kind: 'personal', base_currency: 'SAR', bills_due: [], goals: [], debts: [],
    sync: { enabled: true, devices_active: 1, last_received_at: '2026-11-03T10:00:00Z', last_ok_at: '2026-11-03T10:00:00Z', last_error: null, last_error_at: null, review_count: 1 }, filters: {},
  };
  const entries = options.empty ? [] : [{
    id: 'je1', canonical_id: 'je1', posted_on: '2026-11-03', kind: 'card_purchase', category: 'groceries', merchant: 'TEST SHOP', description: 'TEST SHOP (card purchase)', source: 'sms',
    source_ref: 'fin_sms_1', status: 'posted', reverses_id: null, reversed_by_id: null, original_amount: null, original_currency: null, fx_rate: null, reconciled_at: null,
    sources: [{ kind: 'wallet', message_id: 'w1', status: 'posted', received_at: '2026-11-03T10:00:00Z' }, { kind: 'sms', message_id: 'fin_sms_1', status: 'attached', received_at: '2026-11-03T10:01:00Z' }],
    postings: [{ ledger_account: 'expense:groceries', account_id: null, amount: '45.50', currency: 'SAR' }, { ledger_account: 'liability:acc_card', account_id: 'acc_card', amount: '-45.50', currency: 'SAR' }],
  }];
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
    if (path.startsWith('/api/v1/finance')) sent.push({ method: request.method(), path, body });
    const sms = path.replace('/api/v1/finance', '');
    if (options.denied && (sms.startsWith('/sms') || sms.startsWith('/ledger'))) return json({ detail: 'Finance is for workspace owners and members.' }, 403);
    if (sms === '/sms/status') return json(status());
    if (sms === '/sms/settings' && request.method() === 'GET') return json(settings);
    if (sms === '/sms/settings') { settings = { ...settings, ...(body as object), version: settings.version + 1 }; return json(settings); }
    if (sms === '/sms/self-test') {
      settings = { ...settings, self_test_passed_at: '2026-11-04T09:00:00Z', version: settings.version + 1 };
      return json({ passed: true, passed_at: settings.self_test_passed_at, checks: ['parse_purchase', 'refuse_one_time_codes', 'mask_numbers', 'token_round_trip', 'double_entry_balances', 'storage_reachable'].map((name) => ({ name, passed: true })) });
    }
    if (sms === '/sms/devices' && request.method() === 'GET') return json(devices);
    if (sms === '/sms/devices') {
      const device = { id: 'dev1', name: (body as { name: string }).name, status: 'active', token_hint: 'wxyz', scopes: (body as { scopes: string[] }).scopes, created_at: '2026-11-04T09:00:00Z', rotated_at: null, revoked_at: null, last_seen_at: null, last_ok_at: null, last_error: null, last_error_at: null, message_count: 0 };
      devices.push(device);
      return json({ device, token: 'anum_sms.example.example.example.dev1.exampleexampleexampleexample', ingest_path: '/api/v1/finance/sms/ingest', headers: { Authorization: 'Bearer <token>', 'Content-Type': 'application/json' }, json_fields: { message: 'Shortcut Input, Content', sender: 'Shortcut Input, Sender' } }, 201);
    }
    if (sms === '/sms/messages') {
      const wanted = url.searchParams.get('status');
      const items = messages.filter((m) => !wanted || m.status === wanted);
      return json({ items, total: items.length, limit: 20, offset: 0 });
    }
    if (sms === '/sms/messages/fin_sms_1/post') { message.status = 'posted'; message.reason = null as unknown as string; return json({ message, entry_id: 'je1', reversed_entry_id: null, proposed_rule: null, rule_error: null }); }
    if (sms === '/sms/rules') return json([]);
    if (sms === '/sms/learning') return json(learning());
    if (sms === '/sms/review/summary') return json(summary());
    if (sms === '/sms/learning/senders/trust') { settings = { ...settings, verified_senders: ['testbank'] as never[] }; return json(learning()); }
    if (sms === '/sms/learning/senders/untrust') { notices = []; return json(learning()); }
    if (sms === '/sms/learning/recheck') {
      message.status = 'posted';
      return json({ ...learning(), recheck: { recorded: 1, still_waiting: 0, more: false, message_ids: [message.id] } });
    }
    if (sms === '/sms/banks') return json([{ key: 'al_rajhi', name: 'Al Rajhi Bank', country: 'SA', currency: 'SAR', senders: ['alrajhibank'], verified_by_anum: false, confirmed_by_you: false }]);
    if (sms === '/ledger/dashboard') return options.failLedger ? json({ detail: 'The database is not reachable' }, 503) : json(dashboard);
    if (sms === '/ledger/entries') return json({ items: entries, total: entries.length, limit: 20, offset: 0 });
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { sent };
}

test('safety check, switch on, connect a device: the token is shown once with the Shortcut steps', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-sms');
  await expect(page.getByRole('heading', { name: 'iPhone SMS forwarding' })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText('The address appears once the safety check passes.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Turn forwarding on' })).toBeDisabled();
  await page.getByRole('button', { name: 'Run the safety check' }).click();
  await expect(page.getByText('Ledger entries balance: passed')).toBeVisible();
  await page.getByRole('button', { name: 'Turn forwarding on' }).click();
  await expect(page.getByText('/api/v1/finance/sms/ingest').first()).toBeVisible();
  await page.getByLabel('Device name').fill('My iPhone');
  await page.getByRole('button', { name: 'Connect a device' }).click();
  const dialog = page.getByRole('dialog', { name: 'Your device token' });
  await expect(dialog.getByText('ANUM shows it only once')).toBeVisible();
  await expect(dialog.getByRole('textbox', { name: 'Token' })).toHaveValue(/^anum_sms\./);
  await expect(dialog.getByText(/Shortcuts, Automation, New Automation, Message/)).toBeVisible();
  await expect(dialog.getByText('Authorization: Bearer <token>')).toBeVisible();
  await expect(dialog.getByText('source = apple_wallet', { exact: true })).toBeVisible();
  await expect(dialog.getByText(/\/api\/v1\/finance\/wallet\/ingest/).first()).toBeVisible();
  await dialog.getByRole('button', { name: 'I saved it' }).click();
  await expect(page.getByText('My iPhone')).toBeVisible();
  expect(sent.find((s) => s.path.endsWith('/sms/devices') && s.method === 'POST')?.body).toMatchObject({ scopes: ['finance.sms', 'finance.wallet'] });
  expect(sent.some((s) => s.path.endsWith('/sms/settings') && (s.body as { enabled?: boolean } | null)?.enabled === true)).toBe(true);
});

test('the review queue: a message is checked, its format confirmed and recorded', async ({ page }) => {
  const { sent } = await mockApi(page, { ready: true });
  await page.goto('/#finance-sms');
  await page.getByRole('navigation', { name: 'iPhone SMS sections' }).getByRole('button', { name: /Review/ }).click();
  await expect(page.locator('.finSmsMessage').getByText(/Format not confirmed yet/)).toBeVisible({ timeout: 20_000 });
  await page.getByRole('button', { name: 'Check', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Check this message' });
  await expect(dialog.getByLabel('Amount', { exact: true })).toHaveValue('45.50');
  await dialog.getByLabel(/This reading is right/).check();
  await dialog.getByRole('button', { name: 'Record', exact: true }).click();
  await expect(dialog).toBeHidden();
  const post = sent.find((s) => s.path.endsWith('/sms/messages/fin_sms_1/post'));
  expect(post?.body).toMatchObject({ kind: 'card_purchase', amount: '45.50', currency: 'SAR', confirm_format: true });
  await page.getByRole('navigation', { name: 'iPhone SMS sections' }).getByRole('button', { name: 'Banks' }).click();
  await expect(page.getByRole('cell', { name: 'Unverified' })).toBeVisible();
});

test('why messages wait: one tap trusts the sender, and what ANUM learned can be undone', async ({ page }) => {
  const { sent } = await mockApi(page, { ready: true, learned: true });
  await page.goto('/#finance-sms');
  await page.getByRole('navigation', { name: 'iPhone SMS sections' }).getByRole('button', { name: /Review/ }).click();
  const why = page.getByRole('region', { name: 'Why is this waiting?' });
  await expect(why).toBeVisible({ timeout: 20_000 });
  await expect(why.getByText('Format not confirmed yet')).toBeVisible();
  await expect(why.getByText('1 waiting')).toBeVisible();
  // The same fix is on the message itself.
  await expect(page.locator('.finSmsMessage').getByRole('button', { name: 'Always record messages from testbank' })).toBeVisible();
  await why.getByRole('button', { name: 'Always record messages from testbank' }).click();
  await expect.poll(() => sent.find((s) => s.path.endsWith('/sms/learning/senders/trust'))?.body).toEqual({ sender: 'testbank' });
  const learned = page.getByRole('region', { name: 'What ANUM learned' });
  await expect(learned.getByText('ANUM will now record messages from testbank on its own.')).toBeVisible();
  await learned.getByRole('button', { name: /^Undo: ANUM will now record messages from testbank/ }).click();
  await expect.poll(() => sent.some((s) => s.path.endsWith('/sms/learning/senders/untrust'))).toBe(true);
  await expect(learned).toBeHidden();
  const serious = (await new AxeBuilder({ page }).include('main').analyze()).violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
  expect(serious).toEqual([]);
});

test('re-check waiting messages records what now passes and says how many', async ({ page }) => {
  const { sent } = await mockApi(page, { ready: true });
  await page.goto('/#finance-sms');
  await page.getByRole('navigation', { name: 'iPhone SMS sections' }).getByRole('button', { name: /Review/ }).click();
  const why = page.getByRole('region', { name: 'Why is this waiting?' });
  await why.getByRole('button', { name: 'Re-check waiting messages' }).click({ timeout: 20_000 });
  await expect(page.getByText('Recorded: 1. Still waiting: 0.')).toBeVisible();
  expect(sent.filter((s) => s.path.endsWith('/sms/learning/recheck') && s.method === 'POST')).toHaveLength(1);
});

test('learning settings and limits are saved with the form', async ({ page }) => {
  const { sent } = await mockApi(page, { ready: true });
  await page.goto('/#finance-sms');
  await expect(page.getByRole('group', { name: 'Learning from your reviews' })).toBeVisible({ timeout: 20_000 });
  await page.getByLabel('Trust a sender after this many messages recorded unchanged').selectOption('3');
  await page.getByRole('button', { name: 'Add a limit' }).click();
  await page.getByLabel('Currency', { exact: true }).fill('sar');
  await page.getByLabel('Largest amount').fill('500');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(() => sent.find((s) => s.path.endsWith('/sms/settings') && s.method === 'PUT')?.body).toMatchObject({
    learning_enabled: true, learning_confirm_after: 3, auto_post_limits: [{ currency: 'SAR', amount: '500' }],
  });
});

test('the ledger dashboard shows the journal figures per currency and the entries balance', async ({ page }) => {
  await mockApi(page, { ready: true });
  await page.goto('/#finance-ledger');
  await expect(page.getByRole('heading', { name: 'Ledger', level: 2 })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('region', { name: 'Cards' }).getByText('1% of the limit used')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Balances' }).getByText(/Bank said SAR 14000.00/)).toBeVisible();
  const journal = page.getByRole('region', { name: 'Journal' });
  await expect(journal.getByText('Expense: groceries')).toBeVisible();
  await expect(journal.locator('.finSmsPostings li')).toHaveCount(2);
  // One payment reported by Apple Pay and the bank SMS: one entry, two sources.
  await expect(journal.getByText('Sources: Apple Pay, bank message')).toBeVisible();
});

test('empty, error and permission-denied states', async ({ page }) => {
  await mockApi(page, { ready: true, empty: true });
  await page.goto('/#finance-ledger');
  await expect(page.getByText('Nothing recorded this month')).toBeVisible({ timeout: 20_000 });
  await page.goto('/#finance-sms');
  await page.getByRole('navigation', { name: 'iPhone SMS sections' }).getByRole('button', { name: /Review/ }).click();
  await expect(page.getByText('Nothing here')).toBeVisible();
});

test('a ledger load error says what failed and offers a retry', async ({ page }) => {
  await mockApi(page, { failLedger: true });
  await page.goto('/#finance-ledger');
  await expect(page.getByText(/The ledger could not be loaded/)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('permission denied on iPhone SMS', async ({ page }) => {
  await mockApi(page, { denied: true });
  await page.goto('/#finance-sms');
  await expect(page.locator('#fin-denied')).toBeVisible({ timeout: 20_000 });
});

const VIEWPORTS = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const locale of ['en', 'ar'] as const) {
  for (const [width, height] of VIEWPORTS) {
    for (const tab of ['sms', 'review', 'ledger'] as const) {
      test(`responsive @gate: the ${tab} tab fits with no sideways scroll at ${width}x${height} (${locale})`, async ({ page }) => {
        await page.setViewportSize({ width, height });
        if (locale === 'ar') await useSavedLocale(page, 'ar');
        await mockApi(page, { ready: true, learned: tab === 'review' });
        await page.goto(`/#finance-${tab === 'review' ? 'sms' : tab}`);
        const heading = tab === 'ledger' ? (locale === 'ar' ? 'السجل المحاسبي' : 'Ledger') : (locale === 'ar' ? 'تحويل رسائل الآيفون' : 'iPhone SMS forwarding');
        await expect(page.getByRole('heading', { name: heading, level: 2 })).toBeVisible({ timeout: 20_000 });
        if (tab === 'review') {
          await page.getByRole('navigation', { name: locale === 'ar' ? 'أقسام رسائل الآيفون' : 'iPhone SMS sections' }).getByRole('button').nth(1).click();
          await expect(page.locator('#fsms-why')).toBeVisible();
        }
        if (tab === 'ledger') await expect(page.locator('.finSmsEntry').first()).toBeVisible();
        if (tab === 'sms') await expect(page.locator('#fsms-devices')).toBeVisible();
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow).toBeLessThanOrEqual(0);
        for (const control of await page.locator('.finSms button:visible').all()) {
          const box = await control.boundingBox();
          if (!box) continue;
          expect(box.x).toBeGreaterThanOrEqual(-0.5);
          expect(box.x + box.width).toBeLessThanOrEqual(width + 0.5);
        }
        const serious = (await new AxeBuilder({ page }).include('main').analyze()).violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
        expect(serious).toEqual([]);
        await page.screenshot({ path: test.info().outputPath(`fsms-${tab}-${locale}-${width}x${height}.png`), fullPage: true });
      });
    }
  }
}
