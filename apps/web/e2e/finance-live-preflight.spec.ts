import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// Live eligibility check (docs/finance.md#live-eligibility-preflight), on Finance, Execution: the person
// ticks what is true for their broker and the API answers what is still missing before live could ever be
// considered. Read only: live trading stays off and zero orders are sent, whatever is ticked. Plus the idle,
// loading, error with retry, permission denied, Arabic and responsive states.
const GROUPS = {
  data: ['regulated_provider_verified', 'account_in_owner_name', 'broker_country_eligibility_verified', 'asset_permissions_verified',
    'funding_only_at_broker', 'market_data_terms_accepted', 'data_freshness_verified', 'read_only_access_verified'],
  research: ['historical_data_tests_passed', 'paper_testing_passed', 'guardian_regression_passed'],
  live_review: ['reconciliation_tests_passed', 'independent_security_review_passed', 'written_live_approval', 'withdrawal_scope_absent',
    'credentials_in_secret_store', 'order_idempotency_verified', 'emergency_stop_verified'],
};
const SYSTEM = [
  { code: 'deployment_flag', met: false, message: 'x' }, { code: 'live_executor', met: false, message: 'x' },
  { code: 'write_connector', met: false, message: 'x' }, { code: 'opt_in', met: false, message: 'x' },
  { code: 'limits', met: false, message: 'x' }, { code: 'kill_switch_global', met: true, message: 'x' },
  { code: 'kill_switch_person', met: true, message: 'x' },
];
type Body = { facts: Record<string, boolean>; profile: { broker: string; country_of_residence: string; assets: string[] } | null };

/** The server's answer (finance/broker_readiness.py), computed from what was sent. */
function preflight(body: Body) {
  const missing = (group: keyof typeof GROUPS) => GROUPS[group].filter((f) => !body.facts[f]);
  const [data, research, review] = [missing('data'), missing('research'), missing('live_review')];
  const sa = body.profile?.country_of_residence === 'SA' && body.profile.assets.includes('crypto');
  return {
    stage: data.length ? 'not_connected' : research.length ? 'read_only' : 'live_blocked',
    missing_data_requirements: data, missing_research_requirements: research, missing_live_review_requirements: review,
    system_preconditions: SYSTEM,
    notes: sa ? [{ code: 'sa_crypto_not_licensed', source: 'https://cma.gov.sa/en/MediaCenter/PR/Pages/Virtual_Currencies.aspx' }] : [],
    broker: body.profile?.broker ?? null, assets: body.profile?.assets ?? [],
    eligibility_confirmed: false, live_trading_enabled: false, credentials_connected: false, deposits_into_anum_supported: false,
    orders_sent: 0, explanation: 'Live trading is off and this check cannot turn it on.',
  };
}

async function mockApi(page: Page, options: { fail?: number; denied?: boolean; slow?: boolean } = {}) {
  const state = financeState({});
  const sent: Body[] = [];
  let failures = options.fail ?? 0;
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'member', active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'member', active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    const body = request.postData() ? request.postDataJSON() : null;
    if (path === '/api/v1/finance/broker/readiness') {
      expect(request.method()).toBe('POST');
      sent.push(body as Body);
      if (options.slow) await new Promise((resolve) => setTimeout(resolve, 400));
      if (options.denied) return json({ error: { code: 'forbidden', message: 'Finance is for workspace owners and members. Ask a workspace owner to change your role from viewer to member.' } }, 403);
      if (failures > 0) { failures -= 1; return json({ error: { code: 'unavailable', message: 'The service is busy.' } }, 503); }
      return json(preflight(body as Body));
    }
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { sent };
}

const region = (page: Page, name = 'Live eligibility check') => page.getByRole('region', { name, exact: true });

test('the check lists what is missing, keeps live off and adds the Saudi crypto note', async ({ page }) => {
  const { sent } = await mockApi(page, { slow: true });
  await page.goto('/#finance-execution');
  const check = region(page);
  await expect(check).toContainText('Nothing checked yet.');
  await expect(check).toContainText('Live trading stays off.');
  await check.getByLabel('Broker', { exact: true }).selectOption('alpaca');
  await check.getByLabel(/Country you live in/).fill('s');
  await check.getByRole('button', { name: 'Check what is missing' }).click();
  await expect(check.getByRole('alert')).toHaveText('Enter the two-letter code of the country you live in, for example SA.');
  expect(sent).toHaveLength(0);
  await check.getByLabel(/Country you live in/).fill('sa');
  await check.getByRole('button', { name: 'Check what is missing' }).click();
  await expect(check.getByRole('alert')).toHaveText('Choose at least one thing you would trade.');
  await check.getByLabel('Stocks and funds').check();
  await check.getByLabel('Crypto').check();
  await check.getByLabel(/The broker is regulated/).check();
  await check.getByLabel(/written approval/).check();
  await check.getByRole('button', { name: 'Check what is missing' }).click();
  await expect(check.getByRole('button', { name: 'Checking...' })).toBeDisabled();
  const answer = check.getByRole('region', { name: 'Result' });
  await expect(answer).toContainText('Live trading: off');
  await expect(answer).toContainText('Orders sent: 0');
  await expect(answer).toContainText('Stage: not connected');
  await expect(answer).toContainText('16 items still missing on your list');
  await expect(answer.getByRole('listitem').filter({ hasText: 'A real account in your own name' })).toContainText('(not met)');
  await expect(answer.getByRole('listitem').filter({ hasText: 'The broker is regulated' })).toHaveCount(0);
  await expect(answer.getByRole('listitem').filter({ hasText: 'A live executor for a licensed provider' })).toContainText('(not met)');
  await expect(answer.getByRole('listitem').filter({ hasText: 'Your kill switch is off.' })).toContainText('(met)');
  await expect(answer).toContainText('Saudi residents and crypto');
  await expect(answer.getByRole('link', { name: 'Source' })).toHaveAttribute('href', 'https://cma.gov.sa/en/MediaCenter/PR/Pages/Virtual_Currencies.aspx');
  expect(sent).toEqual([{ facts: { regulated_provider_verified: true, written_live_approval: true },
    profile: { broker: 'alpaca', country_of_residence: 'SA', assets: ['stock', 'crypto'] } }]);

  // Every item ticked: still off, and ANUM's own preconditions still unmet.
  for (const box of await check.locator('.finPreflightFacts input[type=checkbox]').all()) await box.check();
  await check.getByRole('button', { name: 'Check what is missing' }).click();
  await expect(answer).toContainText('Every item on your list is ticked. Live trading still stays off');
  await expect(answer).toContainText('Stage: checklist done, live still blocked');
  await expect(answer).toContainText('Orders sent: 0');

  await check.getByRole('button', { name: 'Clear' }).click();
  await expect(check).toContainText('Nothing checked yet.');
  await expect(check.getByLabel('Broker', { exact: true })).toHaveValue('');
});

test('read-only access before the broker is checked is caught before sending', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-execution');
  const check = region(page);
  await check.getByLabel(/Read-only access to the account works/).check();
  await check.getByRole('button', { name: 'Check what is missing' }).click();
  await expect(check.getByRole('alert')).toContainText('once the broker itself is checked as regulated');
  expect(sent).toHaveLength(0);
});

test('an error says what failed and the check can be tried again', async ({ page }) => {
  await mockApi(page, { fail: 1 });
  await page.goto('/#finance-execution');
  const check = region(page);
  await check.getByRole('button', { name: 'Check what is missing' }).click();
  await expect(check.getByRole('alert')).toContainText('The check could not run');
  await check.getByRole('button', { name: 'Try again' }).click();
  await expect(check.getByRole('region', { name: 'Result' })).toContainText('Live trading: off');
});

test('permission denied explains who can give access', async ({ page }) => {
  await mockApi(page, { denied: true });
  await page.goto('/#finance-execution');
  const check = region(page);
  await check.getByRole('button', { name: 'Check what is missing' }).click();
  await expect(check.getByRole('alert')).toContainText('viewer to member');
});

test('Arabic: the check reads right to left', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockApi(page);
  await page.goto('/#finance-execution');
  const check = region(page, 'فحص الأهلية للتداول الحي');
  await expect(check).toContainText('يبقى التداول الحي متوقفًا');
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
  await check.getByRole('button', { name: 'افحص ما ينقص' }).click();
  await expect(check.getByRole('region', { name: 'النتيجة' })).toContainText('التداول الحي: متوقف');
});

const VIEWPORTS = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const [width, height] of VIEWPORTS) {
  test(`responsive: the check fits with no sideways scroll at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockApi(page);
    await page.goto('/#finance-execution');
    const check = region(page);
    await check.getByLabel('Broker', { exact: true }).selectOption('interactive_brokers');
    await check.getByLabel(/Country you live in/).fill('SA');
    await check.getByLabel('Crypto').check();
    await check.getByRole('button', { name: 'Check what is missing' }).click();
    await expect(check.getByRole('region', { name: 'Result' })).toContainText('Saudi residents and crypto');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    const sticking = await check.evaluate((root) => [...root.querySelectorAll('*')].filter((e) => {
      if (e.getBoundingClientRect().right <= document.documentElement.clientWidth + 1) return false;
      for (let p = e.parentElement; p && p !== root.parentElement; p = p.parentElement) if (getComputedStyle(p).overflowX !== 'visible') return false;
      return true;
    }).map((e) => `${e.tagName}.${e.className}`));
    expect(sticking).toEqual([]);
    for (const control of [check.getByRole('button', { name: 'Check what is missing' }), check.getByRole('button', { name: 'Clear' }), check.getByLabel('Broker', { exact: true })]) {
      const box = await control.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width + 0.5);
      expect(box!.height).toBeGreaterThanOrEqual(32);
    }
    await check.screenshot({ path: test.info().outputPath(`preflight-${width}x${height}.png`) });
  });
}
