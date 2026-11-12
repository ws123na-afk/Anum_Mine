import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { vehiclesReply, vehiclesState } from './support/vehiclesApi';
import { useSavedLocale } from './support/workspaceApi';

// Running a client fleet (docs/vehicles.md#fleet): Fleet costs per vehicle and per km with the worst vehicles, renewals and
// twelve months, the monthly client report downloaded as PDF or Excel in English or Arabic, and the import of a client's
// fuel file with its preview, row problems, duplicates and the all-or-nothing apply. Also the owners-only import, the
// empty fleet and a costs page that fails to load.
async function mockApi(page: Page, options: { role?: 'owner' | 'member'; empty?: boolean; fail?: boolean } = {}) {
  const role = options.role ?? 'owner';
  const state = vehiclesState({ empty: options.empty });
  const sent: { method: string; path: string; body: unknown }[] = [];
  let failures = options.fail ? 1 : 0;
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
    if (path.startsWith('/api/v1/vehicles/fleet')) sent.push({ method: request.method(), path, body });
    if (path === '/api/v1/vehicles/fleet/costs' && failures > 0) { failures -= 1; return json({ error: { code: 'unavailable', message: 'The database is not answering.', details: [] } }, 503); }
    if (path === '/api/v1/vehicles/fleet/report') {
      const b = body as { format: string; language: string; month: string };
      return route.fulfill({ status: 200, contentType: b.format === 'pdf' ? 'application/pdf' : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers: { 'content-disposition': `attachment; filename="fleet-report-${b.month}-${b.language}.${b.format}"`, 'access-control-allow-origin': '*',
          'access-control-expose-headers': 'Content-Disposition' }, body: b.format === 'pdf' ? '%PDF-1.4\n%%EOF\n' : 'PK' });
    }
    const reply = vehiclesReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { state, sent };
}

async function noSeriousAxe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
}

const FUEL_CSV = 'Plate,Date,Litres,Amount\nVAN 77,2026-11-01,60,150\nVAN 77,2026-11-01,60,150\nZZZ 9,2026-11-02,ten,\n';

test('fleet costs per vehicle and per km, then the monthly client report as a PDF in Arabic', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#vehicles-costs');
  const main = page.locator('#main');
  await expect(main.getByRole('tab', { name: 'Fleet costs' })).toHaveAttribute('aria-selected', 'true');
  await expect(main.getByRole('region', { name: 'The fleet this month' })).toContainText('1,050');
  await expect(main.getByText(/100% of this month's cost comes from the figures on the records/)).toBeVisible();
  const table = main.locator('.vehFleetTable');
  await expect(table.getByRole('row', { name: /VAN 77/ })).toContainText('3,800.00');
  await expect(table.getByRole('row', { name: /VAN 77/ })).toContainText('+533.3%');
  await expect(main.getByRole('heading', { name: 'Highest cost per km' })).toBeVisible();
  await expect(main.getByRole('heading', { name: /^Renewals due/ })).toBeVisible();
  await expect(main.getByText(/Tyre planning is not connected/)).toBeVisible();
  await noSeriousAxe(page);

  const report = main.getByRole('region', { name: 'Monthly client report' });
  await report.getByRole('button', { name: 'Download PDF' }).click();
  await expect(report.getByRole('alert')).toContainText('Type the company name');
  expect(sent.filter((r) => r.path.endsWith('/report'))).toHaveLength(0);
  await report.getByLabel('Company name on the report').fill('Gulf Mixers LLC');
  await report.getByLabel('Report language').selectOption('ar');
  await report.getByLabel('Notes for the client').fill('Two trucks idle; check tyre pressure weekly.');
  const download = page.waitForEvent('download');
  await report.getByRole('button', { name: 'Download PDF' }).click();
  expect((await download).suggestedFilename()).toMatch(/^fleet-report-\d{4}-\d{2}-ar\.pdf$/);
  await expect(report.getByText(/Saved fleet-report-.*-ar\.pdf/)).toBeVisible();
  const asked = sent.find((r) => r.path.endsWith('/report'))?.body as Record<string, string>;
  expect(asked).toMatchObject({ language: 'ar', format: 'pdf', company_name: 'Gulf Mixers LLC', notes: 'Two trucks idle; check tyre pressure weekly.' });
  const excel = page.waitForEvent('download');
  await report.getByRole('button', { name: 'Download Excel' }).click();
  expect((await excel).suggestedFilename()).toMatch(/-ar\.xlsx$/);
});

test('the costs page in Arabic reads right to left', async ({ page }) => {
  await mockApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#vehicles-costs');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.locator('#main').getByRole('heading', { name: 'التكلفة لكل مركبة' })).toBeVisible();
  await expect(page.locator('#main').getByRole('button', { name: 'تنزيل PDF' })).toBeVisible();
  await noSeriousAxe(page);
});

test('importing a client fuel file: preview, row problems, a repeated row added on purpose, all or nothing', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#vehicles-import');
  const main = page.locator('#main');
  await expect(main.getByRole('heading', { name: "Import a client's fleet" })).toBeVisible();
  await main.getByLabel('The file holds').selectOption('fuel');
  await main.getByLabel('Choose a file').setInputFiles({ name: 'fuel.csv', mimeType: 'text/csv', buffer: Buffer.from(FUEL_CSV) });
  await expect(main.getByRole('heading', { name: 'What the file would do: 3 rows' })).toBeVisible();
  const preview = sent.find((r) => r.path.endsWith('/import/preview'))?.body as Record<string, unknown>;
  expect(preview).toMatchObject({ kind: 'fuel', format: 'csv', mapping: null, country: 'AE', date_order: 'dmy' });
  await expect(main.getByRole('combobox', { name: 'Registration (required)' })).toHaveValue('Plate');
  const rows = main.getByRole('region', { name: 'Rows of the file' });
  await expect(rows.getByRole('row', { name: /ZZZ 9/ })).toContainText('Litres: "ten" is not a number.');
  await expect(rows.getByRole('row', { name: /^3 / })).toContainText('Repeats row 2');
  await expect(main.getByText('1 to add, 0 to update, 2 to skip')).toBeVisible();
  await noSeriousAxe(page);
  await main.getByLabel('Action for row 3').selectOption('create');
  await expect(main.getByText('2 to add, 0 to update, 1 to skip')).toBeVisible();
  await main.getByRole('button', { name: 'Import', exact: true }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Import this file?' });
  await expect(dialog).toContainText('Fuel entries: 2 rows will be added');
  await dialog.getByRole('button', { name: 'Import' }).click();
  await expect(main.getByText('Imported: 2 added, 0 updated, 1 skipped, 2 odometer readings.')).toBeVisible();
  const applied = sent.find((r) => r.path.endsWith('/import/apply'))?.body as Record<string, unknown>;
  expect(applied).toMatchObject({ kind: 'fuel', fingerprint: 'f'.repeat(64), decisions: [{ row: 3, action: 'create' }], mapping: { registration: 'Plate', date: 'Date', litres: 'Litres' } });
});

test('a refused row stops the whole import and says which row', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#vehicles-import');
  const main = page.locator('#main');
  await main.getByLabel('The file holds').selectOption('fuel');
  await main.getByLabel('Choose a file').setInputFiles({ name: 'fuel.csv', mimeType: 'text/csv', buffer: Buffer.from(FUEL_CSV) });
  await expect(main.getByRole('heading', { name: 'What the file would do: 3 rows' })).toBeVisible();
  await expect(main.getByLabel('Action for row 4')).toBeDisabled();
  await main.getByRole('button', { name: 'Choose another file' }).click();
  await main.getByLabel('Choose a file').setInputFiles({ name: 'old.xls', mimeType: 'application/vnd.ms-excel', buffer: Buffer.from('x') });
  await expect(main.getByRole('alert')).toContainText('Choose an Excel (.xlsx) or CSV file.');
  expect(sent.filter((r) => r.path.endsWith('/import/apply'))).toHaveLength(0);
});

test('only workspace owners import; a member is told who can', async ({ page }) => {
  await mockApi(page, { role: 'member' });
  await page.goto('/#vehicles-import');
  await expect(page.locator('#main').getByRole('heading', { name: 'Importing a fleet is for workspace owners' })).toBeVisible();
  await expect(page.locator('#main').getByLabel('Choose a file')).toHaveCount(0);
});

test('an empty fleet offers the import', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#vehicles-costs');
  const main = page.locator('#main');
  await expect(main.getByRole('heading', { name: 'No business vehicles yet' })).toBeVisible();
  await main.getByRole('button', { name: 'Import a fleet' }).click();
  await expect(main.getByRole('tab', { name: 'Import' })).toHaveAttribute('aria-selected', 'true');
});

test('a costs page that fails to load can be retried', async ({ page }) => {
  await mockApi(page, { fail: true });
  await page.goto('/#vehicles-costs');
  const main = page.locator('#main');
  await expect(main.getByRole('alert')).toContainText('The fleet costs could not be loaded');
  await main.getByRole('button', { name: 'Try again' }).click();
  await expect(main.locator('.vehFleetTable')).toBeVisible();
});
