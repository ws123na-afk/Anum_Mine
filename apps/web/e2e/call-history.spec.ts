import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// "Recent calls" on the Call page (docs/voice.md#recent-calls): the caller's own calls from
// GET /voice/sessions, newest first with Load older, a transcript opened in place, and the states.
const recent = (page: Page) => page.getByRole('region', { name: 'Recent calls' });

test('recent calls list the caller\'s calls and open a transcript', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#call');
  await expect(page.getByRole('heading', { level: 1, name: 'Call' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start call' })).toBeVisible();
  const row = recent(page).getByRole('button', { name: /Which orders ship today\?/ });
  await expect(row).toContainText('Ended');
  await row.click();
  await expect(row).toHaveAttribute('aria-expanded', 'true');
  const lines = recent(page).getByRole('list', { name: 'Call transcript' });
  await expect(lines.getByText('Order 402-0000001 on Amazon.sa.')).toBeVisible();
  await recent(page).getByRole('button', { name: /Transcript erased when the call ended/ }).click();
  await expect(recent(page).getByText('This call was set to delete its transcript when it ended, so there is nothing to read.')).toBeVisible();
});

test('older calls load page by page with the cursor the API gave', async ({ page }) => {
  await mockWorkspaceApi(page);
  const cursors: (string | null)[] = [];
  await page.route((url) => url.host === 'localhost:8000' && url.pathname === '/api/v1/voice/sessions', async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() !== 'GET') return route.fallback();
    cursors.push(url.searchParams.get('cursor'));
    url.searchParams.set('limit', '2');
    const { voiceSessionsReply } = await import('./support/voiceSessions');
    const reply = voiceSessionsReply(url);
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(reply?.body) });
  });
  await page.goto('/#call');
  await expect(recent(page).getByRole('listitem')).toHaveCount(2);
  await recent(page).getByRole('button', { name: 'Load older calls' }).click();
  await expect(recent(page).getByRole('listitem')).toHaveCount(3);
  await expect(recent(page).getByRole('button', { name: 'Load older calls' })).toHaveCount(0);
  expect(cursors).toEqual([null, 'voice_arabic']);
});

test('a failed list says what failed and retries', async ({ page }) => {
  await mockWorkspaceApi(page);
  let fail = true;
  await page.route((url) => url.host === 'localhost:8000' && url.pathname === '/api/v1/voice/sessions', async (route) => {
    if (fail && route.request().method() === 'GET') return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'unavailable', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }) });
    return route.fallback();
  });
  await page.goto('/#call');
  await expect(recent(page).getByRole('alert')).toContainText('Could not load your recent calls: The database is not reachable.');
  fail = false;
  await recent(page).getByRole('button', { name: 'Retry' }).click();
  await expect(recent(page).getByRole('button', { name: /Which orders ship today\?/ })).toBeVisible();
});

test('no calls yet points at the start button; Arabic reads right to left', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.route((url) => url.host === 'localhost:8000' && url.pathname === '/api/v1/voice/sessions', (route) => route.request().method() === 'GET' ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], next_cursor: null }) }) : route.fallback());
  await useSavedLocale(page, 'ar');
  await page.goto('/#call');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'لا مكالمات بعد' })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(overflow).toBe(false);
});
