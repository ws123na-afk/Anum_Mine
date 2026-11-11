import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi } from './support/workspaceApi';
import { createCalendarMock, pinCalendarClock } from './support/calendarApi';

// The November 2026 web depth batch's smaller pieces against mocked APIs: Schedule meeting on Calls &
// Meetings through the calendar (docs/meetings.md#scheduling-a-meeting), an event opened by link
// (docs/calendar.md#web-page) and the search palette's record kinds (docs/design/anum360.md#web-command-center).
type Json = Record<string, unknown>;

async function withCalendar(page: Page) {
  await mockWorkspaceApi(page);
  await pinCalendarClock(page);
  const calendar = createCalendarMock();
  await page.route('http://localhost:8000/api/v1/calendar/**', async (route) => { if (!(await calendar.handle(route))) await route.fallback(); });
  return calendar.state;
}

test('Schedule meeting adds a meeting through the calendar and links to it there', async ({ page }) => {
  const calendar = await withCalendar(page);
  await page.goto('/#meetings');
  await page.getByRole('button', { name: 'Schedule meeting' }).click();
  const form = page.getByRole('dialog', { name: 'New event' });
  await form.getByLabel('Title').fill('Fleet review with Al Noor');
  await expect(form.getByLabel('Type')).toHaveValue('meeting');
  await form.getByRole('button', { name: 'Add event' }).click();
  await expect(page.getByRole('status').filter({ hasText: /Scheduled Fleet review with Al Noor for/ })).toBeVisible();
  expect(calendar.created.at(-1)).toMatchObject({ title: 'Fleet review with Al Noor', kind: 'meeting' });
  await page.getByRole('link', { name: 'Open it in the Calendar' }).click();
  await expect(page).toHaveURL(/#calendar\?event=evt_new_1$/);
  await expect(page.getByRole('article', { name: 'Fleet review with Al Noor' })).toBeVisible();
});

test('a calendar link to an event that is gone says so', async ({ page }) => {
  await withCalendar(page);
  await page.goto('/#calendar?event=evt_missing');
  await expect(page.getByText(/Couldn't open that event/)).toBeVisible();
});

test('the palette finds customers, invoices, documents, vehicles and meetings, and opens one by its link', async ({ page }) => {
  await mockWorkspaceApi(page);
  const searched: string[] = [];
  const hit = (path: string, items: Json[], extra: Json = {}) => ({ path, items, extra });
  const answers = [
    hit('/api/v1/business/accounts', [{ id: 'bacc_1', name: 'Al Noor Fleet', industry: 'Logistics', city: 'Riyadh', country: 'SA' }], { total: 1, limit: 5, offset: 0 }),
    hit('/api/v1/business/invoices', [{ id: 'binv_1', number: 'INV-2026-0007', status: 'sent', account_name: 'Al Noor Fleet' }], { total: 1, limit: 5, offset: 0 }),
    hit('/api/v1/documents', [{ id: 'vdoc_1', title: 'Al Noor lease', category: 'contract' }], { next_cursor: null }),
    hit('/api/v1/vehicles', [{ id: 'veh_1', nickname: 'Al Noor van', make: null, model: null, year: null, registration: 'ABC 123' }], { total: 1, limit: 5, offset: 0 }),
  ];
  await page.route(/http:\/\/localhost:8000\/api\/v1\/(business\/accounts|business\/invoices|documents|vehicles)(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    const answer = answers.find((a) => a.path === url.pathname);
    if (!answer || !url.searchParams.get('q')) return route.fallback();
    searched.push(url.pathname);
    // Invoices are refused for this person: the palette leaves the section out.
    if (answer.path === '/api/v1/business/invoices') return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: { code: 'forbidden', message: 'Only owners and members see invoices.', correlation_id: 'c', details: [] } }) });
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: answer.items, ...answer.extra }) });
  });
  await page.goto('/#home');
  await page.getByRole('button', { name: 'Search anything' }).click();
  await page.getByRole('combobox').fill('Al Noor');
  const results = page.getByRole('listbox', { name: 'Search results' });
  await expect(results.getByRole('group', { name: 'Customers' }).getByRole('option', { name: /Al Noor Fleet/ })).toBeVisible();
  await expect(results.getByRole('group', { name: 'Documents' }).getByRole('option', { name: /Al Noor lease/ })).toBeVisible();
  await expect(results.getByRole('group', { name: 'Vehicles' }).getByRole('option', { name: /Al Noor van/ })).toBeVisible();
  await expect(results.getByRole('group', { name: 'Meetings' })).toBeVisible();
  await expect(results.getByRole('group', { name: 'Invoices' })).toHaveCount(0);
  expect(new Set(searched)).toEqual(new Set(['/api/v1/business/accounts', '/api/v1/business/invoices', '/api/v1/documents', '/api/v1/vehicles']));
  await results.getByRole('option', { name: /Al Noor van/ }).click();
  await expect(page).toHaveURL(/#vehicles\?vehicle=veh_1$/);
});
