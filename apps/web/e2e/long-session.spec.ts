import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi } from './support/workspaceApi';

// A long session (docs/realtime.md#implementation): the owner reported that the web app froze
// after using the Command Center and a few features for a while. The freeze was on the API
// (open event streams held database connections), but the web must not add to it: after many
// navigations there is still one event stream, no request left hanging, no growing set of
// timers, and every click still answers.

const API = 'http://localhost:8000';

async function trackPage(page: Page) {
  await page.addInitScript(() => {
    const live = new Set<number>();
    const set = window.setInterval.bind(window), clear = window.clearInterval.bind(window);
    window.setInterval = ((handler: TimerHandler, ms?: number, ...args: unknown[]) => { const id = set(handler, ms, ...args); live.add(id); return id; }) as typeof window.setInterval;
    window.clearInterval = ((id?: number) => { if (id !== undefined) live.delete(id); clear(id); }) as typeof window.clearInterval;
    (window as unknown as { __liveIntervals: () => number }).__liveIntervals = () => live.size;
  });
  const pending = new Map<object, string>();
  page.on('request', (request) => { if (request.url().startsWith(API)) pending.set(request, new URL(request.url()).pathname); });
  page.on('requestfinished', (request) => pending.delete(request));
  page.on('requestfailed', (request) => pending.delete(request));
  return {
    streams: () => [...pending.values()].filter((path) => path === '/api/v1/events/stream').length,
    others: () => [...pending.values()].filter((path) => path !== '/api/v1/events/stream'),
    intervals: () => page.evaluate(() => (window as unknown as { __liveIntervals: () => number }).__liveIntervals()),
  };
}

test('many navigations keep one event stream, no hanging requests or growing timers, and the app answering', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'the sidebar is the navigation on desktop');
  test.setTimeout(180_000);
  const tracked = await trackPage(page);
  await mockWorkspaceApi(page);
  // Like the real API, the event stream stays open for the life of the page (registered last, so it wins).
  let streamsOpened = 0;
  await page.route(`${API}/api/v1/events/stream`, () => { streamsOpened += 1; });
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1, name: /^Good (morning|afternoon|evening)/ })).toBeVisible();

  // Every collapsed sidebar group open, then every view in the sidebar, three times over.
  for (const toggle of await page.locator('.navGroupToggle[aria-expanded="false"]').all()) await toggle.click();
  const items = page.locator('nav .navList > li > button.navItem').filter({ hasNot: page.locator('xpath=ancestor::*[contains(@class,"navGroup-workspaces")]') });
  const count = await items.count();
  expect(count).toBeGreaterThan(20);
  let navigations = 0;
  let intervalsAfterFirstRound = 0;
  for (let round = 0; round < 3; round += 1) {
    for (let index = 0; index < count; index += 1) {
      const item = items.nth(index);
      await item.click();
      // The app still answers: the clicked entry becomes the current page at once.
      await expect(item).toHaveAttribute('aria-current', 'page', { timeout: 5_000 });
      navigations += 1;
    }
    if (round === 0) intervalsAfterFirstRound = await tracked.intervals();
  }
  expect(navigations).toBeGreaterThanOrEqual(60);

  // On ANUM Command: the composer still answers.
  await page.locator('nav .navList > li > button.navItem', { hasText: 'ANUM Command' }).first().click();
  await expect(page.getByLabel('Your command')).toBeVisible();

  // Nothing piles up: one stream for the page, every other request settled, timers not growing.
  await expect.poll(() => tracked.others(), { timeout: 10_000 }).toEqual([]);
  expect(tracked.streams()).toBe(1);
  expect(streamsOpened).toBe(1);
  expect(await tracked.intervals()).toBeLessThanOrEqual(intervalsAfterFirstRound);
  const started = Date.now();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)));
  expect(Date.now() - started).toBeLessThan(1_000);
});
