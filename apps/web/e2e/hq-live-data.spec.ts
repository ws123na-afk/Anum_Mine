import { expect, test } from '@playwright/test';
import { hqRegistry, mockOfficeSpaces, openHq, TENANT_A, TENANT_B } from './support/hqApi';
import { mockOrgChart } from './support/orgChartApi';

// F03/F14: the headquarters follows the workspace event stream, recovers from a dropped stream, and
// never shows one organisation's records after an in-app switch to another.

const eventStream = (types: string[]) => ({
  status: 200,
  headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
  body: types.map((type, i) => `id: ${i + 1}\ndata: ${JSON.stringify({ id: `evt_${i + 1}`, type, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' })}\n\n`).join(''),
});

test('a dropped event stream reconnects, and a task event refreshes the registry', async ({ page }) => {
  let streamCalls = 0;
  const mock = await openHqWith(page, async () => {
    await page.route('**/api/v1/events/stream', (route) => {
      streamCalls += 1;
      if (streamCalls === 1) return route.abort('failed');
      return route.fulfill(eventStream(['task.updated']));
    });
  });
  await expect(page.locator('.hqLive')).toBeVisible();
  const before = () => mock.requests.filter((r) => r.path.endsWith('/buildings')).length;
  await expect.poll(() => streamCalls, { timeout: 10_000 }).toBeGreaterThanOrEqual(2);
  const base = before();
  // The event (type task.updated) must trigger a debounced refresh of the registry.
  await expect.poll(before, { timeout: 8_000 }).toBeGreaterThan(base);
  await expect(page.locator('.hqLive')).toBeVisible();
});

test('after an in-app organisation switch the stream is re-opened for the new organisation', async ({ page }) => {
  const streamHeaders: (string | undefined)[] = [];
  const mock = await openHqWith(page, async () => {
    await page.route('**/api/v1/events/stream', async (route) => {
      streamHeaders.push((await route.request().allHeaders())['x-tenant-id']);
      return route.fulfill(eventStream(['task.updated']));
    });
  }, true);
  await expect.poll(() => streamHeaders.length, { timeout: 10_000 }).toBeGreaterThanOrEqual(1);
  await switchTenant(page);
  await expect.poll(() => streamHeaders.includes(TENANT_B.tenantId), { timeout: 15_000 }).toBe(true);
  // An event on the new stream must refresh organisation B's registry (no focus event, no timer).
  await expect(page.getByRole('heading', { name: /Bravo Room 1/ })).toBeVisible();
  const countB = () => mock.requests.filter((r) => r.tenant === TENANT_B.tenantId && r.path.endsWith('/buildings')).length;
  const base = countB();
  await expect.poll(countB, { timeout: 15_000 }).toBeGreaterThan(base);
});

test('tenant switch race: a slow response of the old organisation never reaches the screen', async ({ page }) => {
  let release: () => void = () => undefined;
  let holdA: Promise<void> | undefined;
  await mockOrgChart(page);
  await mockOfficeSpaces(page, {
    registries: {
      [TENANT_A.tenantId]: hqRegistry(TENANT_A, { rooms: 2, desks: 0 }),
      [TENANT_B.tenantId]: hqRegistry(TENANT_B, { rooms: 2, desks: 0 }),
    },
    hold: (tenant) => (tenant === TENANT_A.tenantId ? holdA : undefined),
  });
  await page.goto('/#office-preview');
  await expect(page.getByRole('heading', { name: /Alpha Room 1/ })).toBeVisible();

  // A refresh of organisation A starts and its responses are held back ...
  holdA = new Promise<void>((resolve) => { release = resolve; });
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  // ... the user switches organisation while it is in flight ...
  await switchTenant(page);
  await expect(page.getByRole('heading', { name: /Bravo Room 1/ })).toBeVisible({ timeout: 10_000 });
  // ... and only now the old answers arrive.
  release();
  await page.waitForTimeout(1500);
  const text = await page.locator('.hqLive').innerText();
  expect(text).not.toContain('Alpha');
  expect(text).toContain('Bravo');
});

async function switchTenant(page: import('@playwright/test').Page) {
  await page.evaluate(({ tenantId, workspaceId }) => {
    localStorage.setItem('anum.tenant.tenant_local.user_local', tenantId);
    localStorage.setItem(`anum.workspaces.${tenantId}.user_local`, JSON.stringify({ selected: workspaceId, known: [workspaceId] }));
    window.dispatchEvent(new Event('focus'));
  }, TENANT_B);
}

async function openHqWith(page: import('@playwright/test').Page, setup: () => Promise<void>, both = false) {
  await mockOrgChart(page);
  await setup();
  const mock = await mockOfficeSpaces(page, {
    registries: both
      ? { [TENANT_A.tenantId]: hqRegistry(TENANT_A), [TENANT_B.tenantId]: hqRegistry(TENANT_B) }
      : { [TENANT_A.tenantId]: hqRegistry(TENANT_A) },
  });
  await page.goto('/#office-preview');
  return mock;
}
void openHq;
