import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { GATE_SIZES } from './support/gatePages';
import { vehiclesReply, vehiclesState } from './support/vehiclesApi';
import { useSavedLocale } from './support/workspaceApi';

// Vehicles (docs/vehicles.md#web): the overview with spend per currency and what is due, a vehicle's fuel with its payment
// linked from the person's own Finance ledger (one payment stays one expense), a cash payment retried with the same key,
// the odometer that never goes down without a correction and its reason, adding a vehicle, Arabic right to left, and the
// empty, error, permission-denied and paging states. The responsive gate renders every page at five sizes in both languages.
async function mockApi(page: Page, options: Parameters<typeof vehiclesState>[0] & { role?: 'owner' | 'member' | 'viewer'; failCash?: number } = {}) {
  const role = options.role ?? 'member';
  const state = vehiclesState(options);
  const sent: { method: string; path: string; body: unknown; key: string | null }[] = [];
  let failures = options.failCash ?? 0;
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role, active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role, active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    const body = request.postData() ? request.postDataJSON() : null;
    if (path.startsWith('/api/v1/vehicles')) sent.push({ method: request.method(), path, body, key: request.headers()['idempotency-key'] ?? null });
    if (path.endsWith('/cash') && failures > 0) { failures -= 1; return route.abort('failed'); }
    if (path.endsWith('/export.csv')) return route.fulfill({ status: 200, contentType: 'text/csv', headers: { 'content-disposition': 'attachment; filename="anum-vehicle-ABC1234.csv"', 'access-control-allow-origin': '*', 'access-control-expose-headers': 'Content-Disposition' }, body: 'type,date\n' });
    const reply = vehiclesReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { state, sent };
}

async function noSeriousAxe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  expect(serious).toEqual([]);
}

async function noOverflow(page: Page, where: string) {
  const overflow = await page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const wide = [...document.querySelectorAll<HTMLElement>('main button, main a, main input, main select, main h1, main h2')]
      .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && (r.right > width + 1 || r.left < -1); })
      .map((el) => `${el.tagName.toLowerCase()} ${el.textContent?.trim().slice(0, 30) ?? ''}`);
    return { page: document.documentElement.scrollWidth - width, wide };
  });
  expect(overflow.page, `${where}: horizontal page overflow`).toBeLessThanOrEqual(1);
  expect(overflow.wide, `${where}: clipped controls`).toEqual([]);
}

const openCar = async (page: Page) => {
  await page.getByRole('button', { name: 'Open ABC 1234' }).first().click();
  await expect(page.getByRole('heading', { name: /ABC 1234/, level: 2 })).toBeVisible();
};

test('overview: spend per currency, what is due, and a fuel payment linked from Finance stays one expense', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#vehicles');
  await expect(page.getByRole('heading', { name: 'Vehicles', level: 1 })).toBeVisible();
  await expect(page.getByText('Linking never adds money: one payment stays one expense.')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Vehicles at a glance' })).toContainText('SAR');
  await expect(page.getByRole('heading', { name: 'Coming up' })).toBeVisible();
  await expect(page.getByText(/Insurance · ABC 1234/)).toBeVisible();
  await openCar(page);
  await expect(page.getByText('Example Station North')).toBeVisible();
  await expect(page.getByText('No payment linked').first()).toBeVisible();
  await page.getByRole('button', { name: /Payment for .*Example Station North/ }).click();
  const dialog = page.getByRole('dialog', { name: 'Payment' });
  await expect(dialog.getByText('EXAMPLE FUEL NORTH')).toBeVisible();
  await dialog.getByRole('button', { name: /Link EXAMPLE FUEL NORTH/ }).click();
  await expect(page.getByText('Payment linked. No new expense was added.')).toBeVisible();
  await expect(page.locator('.vehChip.active').first()).toContainText('93.20');
  const links = sent.filter((r) => r.path.endsWith('/payment') && r.method === 'POST');
  expect(links).toHaveLength(1);
  expect(links[0].body).toEqual({ canonical_id: 'fin_je_fuel2', currency: 'SAR' });
  expect(sent.some((r) => r.path.startsWith('/api/v1/finance/transactions'))).toBe(false);
  await noSeriousAxe(page);
});

test('a cash payment retried after a failure is sent with the same key and recorded once', async ({ page }) => {
  const { sent, state } = await mockApi(page, { failCash: 1 });
  await page.goto('/#vehicles-list');
  await openCar(page);
  await page.getByRole('button', { name: /Payment for .*Example Station North/ }).click();
  const dialog = page.getByRole('dialog', { name: 'Payment' });
  await dialog.getByRole('radio', { name: 'Paid in cash' }).click();
  await expect(dialog.getByLabel('Finance account')).toHaveValue('fin_acc_cash');
  await expect(dialog.getByLabel('Amount')).toHaveValue('93.20');
  await dialog.getByRole('button', { name: 'Record and link' }).click();
  await expect(dialog.getByRole('alert')).toBeVisible();
  await dialog.getByRole('button', { name: 'Record and link' }).click();
  await expect(page.getByText('Cash payment recorded in Finance and linked.')).toBeVisible();
  const keys = sent.filter((r) => r.path.endsWith('/cash')).map((r) => r.key);
  expect(keys).toHaveLength(2);
  expect(new Set(keys).size).toBe(1);
  expect(state.cashTransactions.size).toBe(1);
});

test('the odometer refuses a lower reading until it is a correction with a reason', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#vehicles-list');
  await openCar(page);
  await page.getByRole('navigation', { name: 'Vehicle sections' }).getByRole('button', { name: 'Odometer' }).click();
  await expect(page.getByText('The odometer never goes down.')).toBeVisible();
  await page.getByLabel('Reading (km)').fill('9000');
  await page.getByRole('button', { name: 'Add reading' }).click();
  await expect(page.getByText(/The odometer cannot go down: 10400.0 km/)).toBeVisible();
  await page.getByLabel('This is a correction').check();
  await page.getByRole('button', { name: 'Add reading' }).click();
  await expect(page.getByText('Write why the odometer is being corrected.')).toBeVisible();
  await page.getByLabel('Reason for the correction').fill('Cluster replaced');
  await page.getByRole('button', { name: 'Add reading' }).click();
  await expect(page.getByText('Reading saved.')).toBeVisible();
  await expect(page.getByText(/9,000 km · correction/)).toBeVisible();
});

test('add a vehicle, a duplicate registration is refused in plain words, and the report shows depreciation beside running costs', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#vehicles-list');
  await page.getByRole('button', { name: 'Add vehicle' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add vehicle' });
  await dialog.getByLabel('Registration', { exact: true }).fill('ABC 1234');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(dialog.getByText('A vehicle with this registration is already in this workspace.')).toBeVisible();
  await dialog.getByLabel('Registration', { exact: true }).fill('NEW 5');
  await dialog.getByLabel('Business: members of this workspace see it').check();
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('heading', { name: /NEW 5/, level: 2 })).toBeVisible();
  await page.getByRole('button', { name: 'Back to vehicles' }).click();
  await openCar(page);
  await page.getByRole('navigation', { name: 'Vehicle sections' }).getByRole('button', { name: 'Report' }).click();
  await expect(page.getByText(/1,500\.00 a month/)).toBeVisible();
  await expect(page.getByText('Worked out only from one full fill to the next, with the odometer typed at both.')).toBeVisible();
  await page.getByRole('navigation', { name: 'Vehicle sections' }).getByRole('button', { name: 'Details' }).click();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  expect((await download).suggestedFilename()).toBe('anum-vehicle-ABC1234.csv');
});

test('empty, error, permission denied and paging states', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#vehicles');
  await expect(page.getByRole('heading', { name: 'No vehicles yet' })).toBeVisible();
  await expect(page.locator('.emptyState').getByRole('button', { name: 'Add vehicle' })).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { failOverview: true });
  await page.goto('/#vehicles');
  await page.reload();
  await expect(page.getByText(/Couldn't read vehicles/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { denied: true, role: 'viewer' });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Vehicles are for owners and members' })).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { many: true });
  await page.goto('/#vehicles-list');
  await page.reload();
  await expect(page.getByText('Showing 1 to 20 of 25')).toBeVisible();
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('Showing 21 to 25 of 25')).toBeVisible();
  await page.getByLabel('Search registration, make or model').fill('van');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.vehList li')).toHaveCount(1);
});

test('Arabic reads right to left with the same payment rule', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockApi(page);
  await page.goto('/#vehicles');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'المركبات', level: 1 })).toBeVisible();
  await expect(page.getByText('الدفعة الواحدة تبقى مصروفًا واحدًا', { exact: false })).toBeVisible();
  await noSeriousAxe(page);
});

test('responsive gate: overview, list, a vehicle and the payment dialog at five sizes in English and Arabic', async ({ page }, info) => {
  test.skip(info.project.name === 'mobile', 'the desktop project sets every size itself');
  test.setTimeout(180_000);
  for (const locale of ['en', 'ar'] as const) {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await useSavedLocale(page, locale);
    await mockApi(page);
    for (const size of GATE_SIZES) {
      await page.setViewportSize(size);
      const where = `${locale} ${size.width}x${size.height}`;
      await page.goto('/#vehicles');
      await page.reload();
      await expect(page.locator('.vehGrid .cmList li').first()).toBeVisible();
      await noOverflow(page, `${where} overview`);
      await page.goto('/#vehicles-list');
      await expect(page.locator('.vehList li').first()).toBeVisible();
      await noOverflow(page, `${where} list`);
      await page.locator('.vehList li').first().getByRole('button').click();
      await expect(page.locator('.vehRecords li').first()).toBeVisible();
      await noOverflow(page, `${where} vehicle`);
      await page.locator('.vehRecords li').first().getByRole('button').first().click();
      await expect(page.getByRole('dialog')).toBeVisible();
      await noOverflow(page, `${where} payment dialog`);
      await page.keyboard.press('Escape');
    }
  }
});
