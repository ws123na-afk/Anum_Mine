import { expect, test, type Page } from '@playwright/test';
import { openNavMenu } from './support/navMenu';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// The Notifications page (docs/team-work.md#notifications-page) against a stateful mock of the
// notification routes: filters by read state and kind, cursor paging with the shared Pager, open,
// mark read and unread, mark all read and delete; reached from the bell.
type Row = { id: string; user_id: string; kind: string; title: string; body: string; work_item_id: string | null; read_at: string | null; email_status: string; email_attempts: number; email_error: string | null; push_status: string; created_at: string };
const at = (minutes: number) => new Date(Date.UTC(2026, 10, 11, 9, 0) - minutes * 60_000).toISOString();

async function mockNotifications(page: Page, options: { fail?: boolean } = {}) {
  await mockWorkspaceApi(page);
  const calls: { method: string; path: string; query: string }[] = [];
  let rows: Row[] = Array.from({ length: 23 }, (_, n) => ({
    id: `ntf_${String(n).padStart(2, '0')}`, user_id: 'user_local', kind: n % 3 === 0 ? 'calendar_reminder' : 'work_assigned',
    title: n % 3 === 0 ? `Reminder ${n}: supplier call` : `New work ${n}: count tyres`, body: 'Starts at 10:00 in Room 2.',
    work_item_id: n % 3 === 0 ? null : 'work_1', read_at: n % 2 === 0 ? null : at(n), email_status: 'not_requested', email_attempts: 0, email_error: null, push_status: 'not_configured', created_at: at(n),
  }));
  // Registered after mockWorkspaceApi, so these answer first.
  await page.route(/http:\/\/localhost:8000\/api\/v1\/notifications(\/.*)?(\?.*)?$/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname, method = request.method();
    calls.push({ method, path, query: url.search });
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (options.fail && path === '/api/v1/notifications' && method === 'GET') return json({ error: { code: 'service_unavailable', message: 'The database is unavailable', correlation_id: 'corr_e2e', details: [] } }, 503);
    if (path === '/api/v1/notifications' && method === 'GET') {
      const state = url.searchParams.get('state'), kind = url.searchParams.get('kind'), cursor = url.searchParams.get('cursor'), limit = Number(url.searchParams.get('limit') ?? 30);
      let list = rows.filter((n) => (!kind || n.kind === kind) && (state !== 'unread' || !n.read_at) && (state !== 'read' || Boolean(n.read_at)));
      if (cursor) list = list.slice(list.findIndex((n) => n.id === cursor) + 1);
      const page = list.slice(0, limit);
      return json({ items: page, unread: rows.filter((n) => !n.read_at).length, next_cursor: list.length > limit ? page[page.length - 1].id : null });
    }
    if (path === '/api/v1/notifications/kinds') {
      const kinds = [...new Set(rows.map((n) => n.kind))];
      return json({ items: kinds.map((kind) => ({ kind, total: rows.filter((n) => n.kind === kind).length, unread: rows.filter((n) => n.kind === kind && !n.read_at).length })).sort((a, b) => b.total - a.total) });
    }
    if (path === '/api/v1/notifications/read-all') { const updated = rows.filter((n) => !n.read_at).length; rows = rows.map((n) => ({ ...n, read_at: n.read_at ?? at(0) })); return json({ updated }); }
    const one = /^\/api\/v1\/notifications\/([^/]+)(\/(read|unread))?$/.exec(path);
    const row = one ? rows.find((n) => n.id === one[1]) : undefined;
    if (!one || !row) return json({ error: { code: 'not_found', message: 'Notification not found', correlation_id: 'corr_e2e', details: [] } }, 404);
    if (method === 'DELETE') { rows = rows.filter((n) => n.id !== row.id); return route.fulfill({ status: 204 }); }
    row.read_at = one[3] === 'read' ? at(0) : null;
    return json(row);
  });
  return calls;
}

const noHorizontalOverflow = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);

test('the bell links to the page; filter, page, mark, delete and mark all read', async ({ page }) => {
  const calls = await mockNotifications(page);
  await page.goto('/#home');
  await page.getByRole('button', { name: /^Notifications, \d+ waiting$/ }).click();
  await page.getByRole('dialog', { name: 'Notifications' }).getByRole('button', { name: 'See all notifications' }).click();
  await expect(page).toHaveURL(/#notificationCenter$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Notifications' })).toBeVisible();
  const list = page.locator('.ntfList > li');
  await expect(list).toHaveCount(20);
  await expect(page.getByRole('heading', { level: 2, name: '12 unread' })).toBeVisible();
  // Cursor paging with the shared Pager.
  const pager = page.getByRole('navigation', { name: 'Notification pages' });
  await expect(pager).toContainText('1 to 20');
  await pager.getByRole('button', { name: 'Next' }).click();
  await expect(list).toHaveCount(3);
  await expect(pager).toContainText('21 to 23');
  await pager.getByRole('button', { name: 'Previous' }).click();
  await expect(list).toHaveCount(20);
  // Filters: a kind (with its count) and a read state.
  await page.getByRole('group', { name: 'Show one kind of notification' }).getByRole('button', { name: 'Calendar reminders (8)' }).click();
  await expect(list).toHaveCount(8);
  await page.getByRole('group', { name: 'Show read or unread notifications' }).getByRole('button', { name: 'Unread', exact: true }).click();
  await expect(list).toHaveCount(4);
  expect(calls.some((c) => c.query.includes('kind=calendar_reminder') && c.query.includes('state=unread'))).toBe(true);
  // Mark read: it leaves the unread filter and the count drops.
  await page.getByRole('button', { name: 'Mark read: Reminder 0: supplier call' }).click();
  await expect(list).toHaveCount(3);
  await expect(page.getByRole('heading', { level: 2, name: '11 unread' })).toBeVisible();
  // Mark it unread again from the Read filter.
  await page.getByRole('group', { name: 'Show read or unread notifications' }).getByRole('button', { name: 'Read', exact: true }).click();
  await page.getByRole('button', { name: 'Mark unread: Reminder 0: supplier call' }).click();
  await expect(page.getByText('Marked unread. It counts on the bell again.')).toBeVisible();
  // Delete asks first, Cancel keeps it.
  await page.getByRole('button', { name: 'Delete: Reminder 3: supplier call' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Delete this notification?' });
  await expect(confirm.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await confirm.getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText('Notification deleted.')).toBeVisible();
  expect(calls.some((c) => c.method === 'DELETE' && c.path === '/api/v1/notifications/ntf_03')).toBe(true);
  // An empty filter offers the way back.
  await page.getByRole('group', { name: 'Show one kind of notification' }).getByRole('button', { name: /^Calendar reminders/ }).click();
  await page.getByRole('group', { name: 'Show read or unread notifications' }).getByRole('button', { name: 'All', exact: true }).click();
  await page.getByRole('button', { name: 'Mark all read' }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'All read' })).toBeVisible();
  await page.getByRole('group', { name: 'Show read or unread notifications' }).getByRole('button', { name: 'Unread', exact: true }).click();
  await expect(page.getByText('Nothing matches these filters')).toBeVisible();
  await page.getByRole('button', { name: 'Show every notification' }).click();
  await expect(list).toHaveCount(20);
  await noHorizontalOverflow(page);
});

test('opening a notification goes where it points; the page is in the sidebar under More', async ({ page }) => {
  await mockNotifications(page);
  await page.goto('/#notificationCenter');
  await page.getByRole('button', { name: /^Reminder 0: supplier call/ }).click();
  await expect(page).toHaveURL(/#calendar$/);
  await openNavMenu(page);
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Notifications', exact: true }).click();
  await expect(page).toHaveURL(/#notificationCenter$/);
  await page.getByRole('button', { name: /^New work 1: count tyres/ }).click();
  await expect(page).toHaveURL(/#team$/);
});

test('a failed load says what failed and offers a retry', async ({ page }) => {
  await mockNotifications(page, { fail: true });
  await page.goto('/#notificationCenter');
  await expect(page.getByRole('alert').filter({ hasText: "Couldn't load your notifications" })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible();
});

test('Arabic: filters, rows and pager without overflow', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockNotifications(page);
  await page.goto('/#notificationCenter');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { level: 1, name: 'الإشعارات' })).toBeVisible();
  await expect(page.getByRole('group', { name: 'عرض نوع واحد من الإشعارات' }).getByRole('button', { name: 'تذكيرات التقويم (8)' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'صفحات الإشعارات' }).getByRole('button', { name: 'التالي' })).toBeEnabled();
  await noHorizontalOverflow(page);
});
