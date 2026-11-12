import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { GATE_SIZES } from './support/gatePages';
import { purchasesReply, purchasesState } from './support/purchasesApi';
import { useSavedLocale } from './support/workspaceApi';

// Purchases (docs/purchases.md#web): the overview with what is late and due, a purchase's payment linked from the person's
// own Finance ledger (one payment stays one expense), a return followed to a partial refund linked from Finance (never income),
// a cash payment retried with the same key, the delivery moving along its state machine with shipments and history, adding a
// purchase with its items, Arabic right to left, and the empty, error, permission-denied and paging states. The responsive
// gate renders every page at five sizes in both languages.
async function mockApi(page: Page, options: Parameters<typeof purchasesState>[0] & { role?: 'owner' | 'member' | 'viewer'; failCash?: number } = {}) {
  const role = options.role ?? 'member';
  const state = purchasesState(options);
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
    if (path.startsWith('/api/v1/purchases') || path.startsWith('/api/v1/finance')) sent.push({ method: request.method(), path, body, key: request.headers()['idempotency-key'] ?? null });
    if (path.endsWith('/payment/cash') && failures > 0) { failures -= 1; return route.abort('failed'); }
    if (path.endsWith('/export.csv')) return route.fulfill({ status: 200, contentType: 'text/csv', headers: { 'content-disposition': 'attachment; filename="anum-purchases.csv"', 'access-control-allow-origin': '*', 'access-control-expose-headers': 'Content-Disposition' }, body: 'ordered_on,merchant\n' });
    const reply = purchasesReply(state, request.method(), url, body);
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
      // A control inside a strip or table that scrolls on its own is reachable by scrolling it: only the page must never scroll sideways.
      .filter((el) => { let a = el.parentElement; while (a && a !== document.body) { if (['auto', 'scroll'].includes(getComputedStyle(a).overflowX)) return false; a = a.parentElement; } return true; })
      .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && (r.right > width + 1 || r.left < -1); })
      .map((el) => `${el.tagName.toLowerCase()} ${el.textContent?.trim().slice(0, 30) ?? ''}`);
    return { page: document.documentElement.scrollWidth - width, wide };
  });
  expect(overflow.page, `${where}: horizontal page overflow`).toBeLessThanOrEqual(1);
  expect(overflow.wide, `${where}: clipped controls`).toEqual([]);
}

// A tracker row opens the order drawer; "Open the full record" opens the purchase's own page.
const openStore = async (page: Page) => {
  await page.getByRole('button', { name: /^Headphones and 1 more Example Store/ }).first().click();
  await expect(page.locator('#pur-drawer-title')).toContainText('Example Store');
  await page.getByRole('button', { name: 'Open the full record' }).click();
  await expect(page.locator('.purDetailHead h2')).toContainText('Example Store');
};
const section = (page: Page, name: string) => page.getByRole('navigation', { name: 'Purchase sections' }).getByRole('button', { name });

test('overview: what is late and due, and a payment linked from Finance stays one expense', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#purchases');
  await expect(page.getByRole('heading', { name: 'Purchases and Returns', level: 1 })).toBeVisible();
  await expect(page.getByText(/never counted as income/).first()).toBeVisible();
  await expect(page.getByRole('region', { name: 'Purchases at a glance' })).toContainText('1 is late');
  const tasks = page.getByRole('region', { name: /Delivery and refund tasks/ });
  await expect(tasks.getByText('Delivery late')).toBeVisible();
  await expect(tasks.getByText('Example Market N-77')).toBeVisible();
  await expect(tasks.getByText('Return window closing')).toBeVisible();
  await openStore(page);
  await section(page, 'Details').click();
  await expect(page.getByText('No payment linked').first()).toBeVisible();
  await page.getByRole('button', { name: 'Payment', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Payment' });
  await expect(dialog.getByText('EXAMPLE STORE ONLINE')).toBeVisible();
  await dialog.getByRole('button', { name: /Link EXAMPLE STORE ONLINE/ }).click();
  await expect(page.getByText('Payment linked. No new expense was added.')).toBeVisible();
  const links = sent.filter((r) => r.path.endsWith('/payment') && r.method === 'POST');
  expect(links).toHaveLength(1);
  expect(links[0].body).toEqual({ canonical_id: 'fin_je_store', currency: 'SAR' });
  expect(sent.some((r) => r.path.startsWith('/api/v1/finance/transactions'))).toBe(false);
  await noSeriousAxe(page);
});

test('a return is followed to a partial refund linked from Finance, which is never income', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#purchases-list');
  await openStore(page);
  await section(page, 'Returns and refunds').click();
  await page.getByRole('button', { name: 'Ask for a return' }).click();
  const form = page.getByRole('dialog', { name: 'Ask for a return' });
  await form.getByLabel('Cable (up to 2)').fill('2');
  await form.getByLabel('Reason').selectOption('wrong_item');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Return saved.')).toBeVisible();
  await expect(page.locator('.purReturn')).toContainText('Cable × 2');
  await page.locator('.purReturn').getByLabel('Move to').selectOption('returned');
  await page.locator('.purReturn').getByRole('button', { name: 'Change status' }).click();
  await expect(page.getByText('Return is now Sent back.')).toBeVisible();
  await page.getByRole('button', { name: 'Link refund' }).click();
  const dialog = page.getByRole('dialog', { name: 'Link a refund' });
  await expect(dialog.getByText(/never income/)).toBeVisible();
  await dialog.getByRole('button', { name: /Link EXAMPLE STORE REFUND/ }).click();
  await expect(page.getByText('Refund linked to this purchase. Nothing was added as income.')).toBeVisible();
  await expect(page.locator('.purDetailHead .purChip')).toHaveText('Partly refunded');
  const refunds = sent.filter((r) => r.path.endsWith('/refunds') && r.method === 'POST');
  expect(refunds).toHaveLength(1);
  expect(refunds[0].body).toEqual({ canonical_id: 'fin_je_refund', currency: 'SAR' });
  expect(sent.some((r) => r.path.startsWith('/api/v1/finance/transactions'))).toBe(false);
});

test('a cash payment retried after a failure is sent with the same key and recorded once', async ({ page }) => {
  const { sent, state } = await mockApi(page, { failCash: 1 });
  await page.goto('/#purchases-list');
  await openStore(page);
  await section(page, 'Details').click();
  await page.getByRole('button', { name: 'Payment', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Payment' });
  await dialog.getByRole('radio', { name: 'Paid in cash' }).click();
  await expect(dialog.getByLabel('Finance account')).toHaveValue('fin_acc_cash');
  await expect(dialog.getByLabel('Amount')).toHaveValue('450.00');
  await dialog.getByRole('button', { name: 'Record and link' }).click();
  await expect(dialog.getByRole('alert')).toBeVisible();
  await dialog.getByRole('button', { name: 'Record and link' }).click();
  await expect(page.getByText('Cash payment recorded in Finance and linked.')).toBeVisible();
  const keys = sent.filter((r) => r.path.endsWith('/payment/cash')).map((r) => r.key);
  expect(keys).toHaveLength(2);
  expect(new Set(keys).size).toBe(1);
  expect(state.cashTransactions.size).toBe(1);
});

test('the delivery moves along its steps, with shipments and an unchanging history', async ({ page }) => {
  await mockApi(page, { role: 'owner' });
  await page.goto('/#purchases-list');
  await page.getByRole('button', { name: 'Track purchase' }).click();
  const form = page.getByRole('dialog', { name: 'Add purchase' });
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(form.getByText('Enter the shop or seller.')).toBeVisible();
  await form.getByLabel('Shop or seller').fill('Example Gadgets');
  await form.getByLabel('Order number').fill('GAD-9');
  await form.getByRole('button', { name: 'Add item' }).click();
  await form.getByLabel('Item 1', { exact: true }).fill('Charger');
  await form.getByLabel('Unit price').fill('99');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('#pur-drawer-title')).toContainText('Charger · Example Gadgets');
  await page.getByRole('button', { name: 'Open the full record' }).click();
  await expect(page.getByRole('heading', { name: /Example Gadgets/, level: 2 })).toBeVisible();
  const move = page.locator('#pur-status-title').locator('..');
  const offered = await move.getByLabel('Move to').locator('option').allTextContents();
  expect(offered).toContain('Confirmed');
  expect(offered).not.toContain('Refunded');
  expect(offered).not.toContain('Partly refunded');
  await move.getByLabel('Move to').selectOption('confirmed');
  await move.getByRole('button', { name: 'Change status' }).click();
  await expect(page.getByText('Status changed to Confirmed.')).toBeVisible();
  await page.getByRole('button', { name: 'Add shipment' }).click();
  const ship = page.getByRole('dialog', { name: 'Add shipment' });
  await ship.getByLabel('Carrier').fill('Example Courier');
  await ship.getByLabel('Tracking number').fill('TRK777');
  await ship.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Shipment saved.')).toBeVisible();
  await expect(page.locator('.purDetailHead .purChip')).toHaveText('Shipped');
  await section(page, 'Items').click();
  await expect(page.getByRole('cell', { name: 'Charger' })).toBeVisible();
  await section(page, 'History').click();
  await expect(page.getByText('Confirmed to Shipped')).toBeVisible();
  await expect(page.getByText('Ordered to Confirmed')).toBeVisible();
  await page.getByLabel('Add a note').fill('Called the shop');
  await page.getByRole('button', { name: 'Add note' }).click();
  await expect(page.getByText(/Called the shop/)).toBeVisible();
  await page.getByRole('button', { name: 'Back to purchases' }).click();
  await page.getByRole('tab', { name: 'All purchases' }).click();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  expect((await download).suggestedFilename()).toBe('anum-purchases.csv');
});

test('empty, error, permission denied, filter and paging states', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#purchases');
  await expect(page.getByRole('heading', { name: 'No purchases yet' })).toBeVisible();
  await expect(page.locator('.emptyState').getByRole('button', { name: 'Track purchase' })).toBeVisible();
  await expect(page.locator('.emptyState').getByRole('button', { name: 'Import order' })).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { failOverview: true });
  await page.goto('/#purchases');
  await page.reload();
  await expect(page.getByText(/Couldn't read purchases/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { denied: true, role: 'viewer' });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Purchases are for owners and members' })).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { many: true });
  await page.goto('/#purchases-list');
  await page.reload();
  await expect(page.getByText('Showing 1 to 20 of 25')).toBeVisible();
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('Showing 21 to 25 of 25')).toBeVisible();
  await page.getByRole('combobox', { name: 'Status' }).selectOption('awaiting');
  await expect(page.locator('.purTracker tbody tr')).toHaveCount(20);
  await page.getByRole('searchbox', { name: 'Search purchases' }).fill('N-77');
  await page.getByRole('searchbox', { name: 'Search purchases' }).press('Enter');
  await expect(page.locator('.purTracker tbody tr')).toHaveCount(1);
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await page.getByRole('combobox', { name: 'Source' }).selectOption('iphone');
  await expect(page.getByRole('heading', { name: 'No purchases match' })).toBeVisible();
  await page.getByRole('tab', { name: 'Returns and refunds' }).click();
  await expect(page.getByRole('heading', { name: 'No open returns' })).toBeVisible();
});

test('Arabic reads right to left with the same refund rule', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockApi(page);
  await page.goto('/#purchases');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'المشتريات والمرتجعات', level: 1 })).toBeVisible();
  await expect(page.getByText('لا تُحسب دخلًا أبدًا', { exact: false }).first()).toBeVisible();
  await page.getByRole('button', { name: /Headphones.*Example Store/ }).click();
  await expect(page.locator('.purDrawer')).toBeVisible();
  await noSeriousAxe(page);
});

test('responsive gate: overview, list, the order drawer, a purchase, returns and the payment dialog at five sizes in English and Arabic', async ({ page }, info) => {
  test.skip(info.project.name === 'mobile', 'the desktop project sets every size itself');
  test.setTimeout(240_000);
  for (const locale of ['en', 'ar'] as const) {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await useSavedLocale(page, locale);
    await mockApi(page);
    for (const size of GATE_SIZES) {
      await page.setViewportSize(size);
      const where = `${locale} ${size.width}x${size.height}`;
      await page.goto('/#purchases');
      await page.reload();
      await expect(page.locator('.purTaskList li').first()).toBeVisible();
      await noOverflow(page, `${where} overview`);
      await page.goto('/#purchases-list');
      await expect(page.locator('.purTracker tbody tr').first()).toBeVisible();
      await noOverflow(page, `${where} list`);
      await page.locator('.purTracker tbody tr').first().locator('.purRowButton').click();
      await expect(page.locator('#pur-drawer-title')).toContainText('Example');
      await noOverflow(page, `${where} drawer`);
      await page.locator('.purDrawer').getByRole('button', { name: locale === 'en' ? 'Open the full record' : 'فتح السجل الكامل' }).click();
      await expect(page.locator('.purDetailHead h2')).toBeVisible();
      await noOverflow(page, `${where} purchase`);
      await page.locator('.purTabs button').nth(2).click();
      await expect(page.locator('#pur-returns-title')).toBeVisible();
      await noOverflow(page, `${where} returns`);
      await page.locator('.purTabs button').nth(4).click();
      await page.locator('#pur-actions-title').locator('..').getByRole('button').first().click();
      await expect(page.getByRole('dialog')).toBeVisible();
      await noOverflow(page, `${where} payment dialog`);
      await page.keyboard.press('Escape');
    }
  }
});

test('tracker: seller filter, import order, and the drawer with a carrier update, masked tracking and the carrier link', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#purchases-list');
  await page.getByRole('combobox', { name: 'Seller' }).selectOption('Example Market');
  await expect(page.locator('.purTracker tbody tr')).toHaveCount(1);
  expect(sent.some((r) => r.method === 'GET' && r.path === '/api/v1/purchases')).toBe(true);
  await page.getByRole('button', { name: 'Clear filters' }).click();

  await page.getByRole('button', { name: 'Import order' }).click();
  const dialog = page.getByRole('dialog', { name: 'Import an order' });
  await dialog.getByRole('button', { name: 'Import' }).click();
  await expect(dialog.getByText('Paste the message first.')).toBeVisible();
  await dialog.getByLabel('Order or delivery message').fill('SYNTHETIC. Thank you for your order #IMP-42.');
  await dialog.getByRole('button', { name: 'Import' }).click();
  await expect(page.getByText('Order imported. Check it: it is marked for review.')).toBeVisible();
  const share = sent.find((r) => r.path === '/api/v1/purchases/device/share');
  expect(share?.body).toMatchObject({ action: 'save', ownership: 'personal' });
  expect((share?.body as { idempotency_key: string }).idempotency_key).toMatch(/^import-/);
  await expect(page.locator('#pur-drawer-title')).toContainText('Example Import Shop');
  await page.locator('.purDrawer').getByRole('button', { name: 'Close order details' }).click();

  await page.getByRole('button', { name: /^Example Market Example Market/ }).click();
  const drawer = page.locator('.purDrawer');
  await expect(drawer.getByText('••••5678')).toBeVisible();
  await expect(drawer.getByRole('link', { name: /View on/ })).toHaveCount(0);
  await drawer.getByRole('button', { name: 'Add carrier update' }).click();
  const ship = page.getByRole('dialog', { name: 'Edit shipment' });
  await ship.getByLabel('Carrier').fill('DHL');
  await ship.getByLabel('Tracking number').fill('S3K7Q9AB');
  await ship.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Carrier update saved.')).toBeVisible();
  const carrier = drawer.getByRole('link', { name: 'View on DHL' });
  await expect(carrier).toHaveAttribute('href', /^https:\/\/www\.dhl\.com\/.*S3K7Q9AB$/);
  await expect(carrier).toHaveAttribute('rel', 'noopener noreferrer');
  await expect(drawer.getByText('••••Q9AB')).toBeVisible();
  await noSeriousAxe(page);
});

test('courier tracking: a number several carriers use lists each, and a configured provider gives the status', async ({ page }) => {
  const { state, sent } = await mockApi(page);
  const shipment = (state.purchases.find((p) => p.id === 'pur_noon')!.shipments as Record<string, unknown>[])[0];
  Object.assign(shipment, { carrier: null, tracking_number: '1234567891', tracking: {
    number: '1234567891', carrier: null, carrier_name: null, url: null, detected_from: null, status_provider: 'off',
    candidates: [
      { carrier: 'dhl', carrier_name: 'DHL Express', url: 'https://www.dhl.com/global-en/home/tracking/tracking-express.html?submit=1&tracking-id=1234567891', confidence: 'likely' },
      { carrier: 'aramex', carrier_name: 'Aramex', url: 'https://www.aramex.com/us/en/track/results?ShipmentNumber=1234567891', confidence: 'possible' },
    ] } });
  await page.goto('/#purchases-list');
  await page.getByRole('button', { name: /^Example Market Example Market/ }).click();
  const drawer = page.locator('.purDrawer');
  await expect(drawer.getByText('This number fits more than one carrier. Open the one that sent it:')).toBeVisible();
  await expect(drawer.getByRole('link', { name: 'View on DHL Express' })).toHaveAttribute('href', /tracking-id=1234567891$/);
  await expect(drawer.getByRole('link', { name: 'View on Aramex' })).toHaveAttribute('rel', 'noopener noreferrer');
  await expect(drawer.getByRole('button', { name: 'Check status' })).toHaveCount(0);
  await noSeriousAxe(page);
  await drawer.getByRole('button', { name: 'Close order details' }).click();

  // The operator configured a provider: the named carrier's status is one press away.
  Object.assign(shipment, { carrier: 'UPS', tracking_number: '1Z999AA10123456784', tracking: {
    number: '1Z999AA10123456784', carrier: 'ups', carrier_name: 'UPS', url: 'https://www.ups.com/track?tracknum=1Z999AA10123456784', detected_from: 'name',
    candidates: [], status_provider: 'example' } });
  await page.getByRole('button', { name: /^Example Market Example Market/ }).click();
  await expect(drawer.getByRole('link', { name: 'View on UPS' })).toBeVisible();
  await drawer.getByRole('button', { name: 'Check status' }).click();
  await expect(drawer.getByText('Out for delivery')).toBeVisible();
  expect(sent.some((r) => r.method === 'GET' && r.path === '/api/v1/purchases/shipments/purs_1/tracking')).toBe(true);
  await noOverflow(page, 'tracking drawer');
});

test('Export follows the list: with archived shown it exports the archived purchases', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#purchases-list');
  await expect(page.locator('.purTracker tbody tr').first()).toBeVisible();
  let request = page.waitForRequest((r) => r.url().includes('/api/v1/purchases/export.csv'));
  await page.getByRole('button', { name: 'Export CSV' }).click();
  expect(new URL((await request).url()).searchParams.get('archived')).toBeNull();
  await page.getByLabel('Show archived').check();
  request = page.waitForRequest((r) => r.url().includes('/api/v1/purchases/export.csv'));
  await page.getByRole('button', { name: 'Export CSV' }).click();
  expect(new URL((await request).url()).searchParams.get('archived')).toBe('true');
});
