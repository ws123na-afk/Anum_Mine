import { expect, test, type Page } from '@playwright/test';

// Contact opt-outs (docs/privacy.md#contact-suppression): owners and organization administrators
// view, search, add and remove entries with a reason; only owners export. API mocked.
const now = new Date().toISOString();
type Json = Record<string, unknown>;
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });

const entry = (id: string, masked: string, extra: Json = {}): Json => ({
  id, masked, kind: masked.includes('@') ? 'email' : 'phone', channel: 'whatsapp', purpose: 'all', source: 'whatsapp_keyword',
  reason: 'The contact asked to stop (WhatsApp message wam_1).', created_by: 'system:inbound', created_at: now, ...extra,
});

interface Options { status?: 403 | 500; canExport?: boolean; total?: number; empty?: boolean }

async function mockApi(page: Page, options: Options = {}) {
  const calls: { method: string; path: string; query: string; body: unknown }[] = [];
  let items: Json[] = options.empty ? [] : [
    entry('supp_1', '+971•••4567'),
    entry('supp_2', 'a•••@example.com', { channel: 'email', purpose: 'marketing', source: 'manual', reason: 'Asked by phone', created_by: 'owner_a' }),
  ];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path.startsWith('/api/v1/contact-suppressions')) {
      calls.push({ method, path, query: url.search, body: request.postData() ? request.postDataJSON() : null });
      if (options.status === 403) return json(envelope('forbidden', 'Only workspace owners and organization administrators can see and change the contact suppression list.'), 403);
      if (options.status === 500) return json(envelope('internal_error', 'Database unavailable'), 500);
      if (path === '/api/v1/contact-suppressions/export') {
        return route.fulfill({ status: 200, contentType: 'text/csv', headers: { 'content-disposition': 'attachment; filename="contact-suppressions.csv"' }, body: 'contact,kind\n' });
      }
      if (method === 'GET') {
        const q = url.searchParams.get('q');
        const offset = Number(url.searchParams.get('offset') ?? 0);
        const shown = q ? items.filter((i) => String(i.masked).includes('4567') && q.includes('4567')) : items;
        const total = options.total ?? shown.length;
        const page = offset > 0 ? [entry(`supp_more_${offset}`, '+966•••0001')] : shown;
        return json({ items: page, total, can_manage: true, can_export: options.canExport ?? true });
      }
      if (method === 'POST') {
        const body = request.postDataJSON() as Json;
        const saved = entry('supp_new', '+971•••0000', { channel: body.channel, purpose: body.purpose, source: 'manual', reason: body.reason, created_by: 'user_local' });
        items = [saved, ...items];
        return json(saved, 201);
      }
      if (method === 'DELETE') { items = items.filter((i) => !path.endsWith(String(i.id))); return route.fulfill({ status: 204 }); }
    }
    return route.abort();
  });
  return calls;
}

test('an owner sees who opted out, adds one with a reason and searches', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#suppressions');
  await expect(page.getByRole('heading', { level: 1, name: 'Contact opt-outs' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'People ANUM must not message' })).toBeVisible();
  const list = page.getByRole('region', { name: 'Opted-out contacts' });
  await expect(list.getByRole('heading', { name: '2 contacts' })).toBeVisible();
  await expect(list.getByText('+971•••4567', { exact: true })).toBeVisible();
  await expect(list.getByText('WhatsApp STOP')).toBeVisible();
  await expect(list.getByText(/Added automatically/)).toBeVisible();
  await expect(list.getByText('Marketing only')).toBeVisible();

  const form = page.getByRole('form', { name: 'Add an opt-out' });
  await form.getByLabel('Phone number or e-mail').fill('ahmed@');
  await form.getByLabel('Reason').fill('Asked by phone');
  await form.getByRole('button', { name: 'Add to the list' }).click();
  await expect(form.getByText('Enter a phone number with its country code or an e-mail address.')).toBeVisible();
  await form.getByLabel('Phone number or e-mail').fill('+971 50 000 0000');
  await form.getByLabel('Channel').selectOption('whatsapp');
  await form.getByRole('combobox', { name: /^Stop/ }).selectOption('marketing');
  await form.getByRole('button', { name: 'Add to the list' }).click();
  await expect(page.getByText('+971•••0000 is on the list.')).toBeVisible();
  expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ recipient: '+971 50 000 0000', channel: 'whatsapp', purpose: 'marketing', reason: 'Asked by phone' });

  await page.getByRole('searchbox', { name: 'Search' }).fill('+971 50 123 4567');
  await page.getByRole('button', { name: 'Find' }).click();
  await expect(list.getByRole('heading', { name: '1 contact matches' })).toBeVisible();
  expect(calls.some((c) => c.method === 'GET' && c.query.includes('q=%2B971+50+123+4567'))).toBe(true);
  await page.getByRole('button', { name: 'Clear' }).first().click();
  await expect(list.getByRole('heading', { name: '3 contacts' })).toBeVisible();
});

test('removing an entry needs a reason and is confirmed', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#suppressions');
  await page.getByRole('button', { name: 'Remove +971•••4567 from the list' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Remove +971•••4567 from the list?' });
  await expect(dialog).toContainText('ANUM may message this contact again on WhatsApp');
  await dialog.getByRole('button', { name: 'Remove' }).click();
  await expect(dialog.getByText('Give a reason of at least 3 characters.')).toBeVisible();
  await dialog.getByLabel('Reason for removing').fill('Customer asked to start again');
  await dialog.getByRole('button', { name: 'Remove' }).click();
  await expect(page.getByText('+971•••4567 was removed from the list.')).toBeVisible();
  expect(calls.find((c) => c.method === 'DELETE')).toEqual({ method: 'DELETE', path: '/api/v1/contact-suppressions/supp_1', query: '', body: { reason: 'Customer asked to start again' } });
});

test('owners export the list; an organization administrator who is not an owner is told why not', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#suppressions');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  expect((await download).suggestedFilename()).toBe('contact-suppressions.csv');
  await expect(page.getByText('Downloaded contact-suppressions.csv.')).toBeVisible();
  expect(calls.some((c) => c.path === '/api/v1/contact-suppressions/export')).toBe(true);

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { canExport: false });
  await page.reload();
  await expect(page.getByText('Only workspace owners can export the list.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Export CSV' })).toHaveCount(0);
});

test('a long list pages', async ({ page }) => {
  const calls = await mockApi(page, { total: 51 });
  await page.goto('/#suppressions');
  await page.getByRole('button', { name: 'Show more' }).click();
  await expect(page.getByText('+966•••0001')).toBeVisible();
  expect(calls.some((c) => c.query.includes('offset=2'))).toBe(true);
});

test('empty, denied and failed states explain themselves', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#suppressions');
  await expect(page.getByRole('heading', { name: 'Nobody has opted out yet' })).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { status: 403 });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Owners and organization administrators only' })).toBeVisible();
  await expect(page.getByText(/The API said: Only workspace owners/)).toBeVisible();
  await expect(page.getByRole('form', { name: 'Add an opt-out' })).toHaveCount(0);

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { status: 500 });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'The list could not be loaded' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('the page reads right to left in Arabic', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('anum.locale', 'ar'));
  await mockApi(page);
  await page.goto('/#suppressions');
  await expect(page.getByRole('heading', { level: 1, name: 'إلغاء اشتراك جهات الاتصال' })).toBeVisible();
  await expect(page.getByText('STOP على واتساب')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

for (const size of [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
]) {
  test(`contact opt-outs fit at ${size.width}x${size.height}`, async ({ page }, testInfo) => {
    await page.setViewportSize(size);
    await mockApi(page);
    await page.goto('/#suppressions');
    await expect(page.getByText('+971•••4567', { exact: true })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    for (const name of ['Add to the list', 'Remove +971•••4567 from the list', 'Find']) {
      const box = (await page.getByRole('button', { name }).boundingBox())!;
      expect(box.x, name).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, name).toBeLessThanOrEqual(size.width);
    }
    if (process.env.ANUM_SCREENSHOTS) await page.screenshot({ path: testInfo.outputPath(`suppressions-${size.width}.png`), fullPage: true });
  });
}
