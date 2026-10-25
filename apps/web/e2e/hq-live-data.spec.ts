import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, mockOfficeSpaces, TENANT_A, TENANT_B as OTHER_ORG } from './support/hqApi';
import { mockHq } from './support/hqCampusApi';

// These specs test data, not layout: use a desktop viewport in both projects.
test.use({ viewport: { width: 1280, height: 800 } });

// The other organisation keeps the open workspace's id, so the campus building of this workspace stays the open one after the switch.
const TENANT_B = { ...OTHER_ORG, workspaceId: TENANT_A.workspaceId };

// F03/F14: the headquarters follows the shell's workspace event stream, keeps itself fresh when that
// stream drops, and never shows one organisation's records after an in-app switch to another.
// (The shell owns the one event stream; the office re-reads when it moves, and on a 10 second timer.)

const eventStream = (types: string[]) => ({
  status: 200,
  headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
  body: types.map((type, i) => `id: ${i + 1}\ndata: ${JSON.stringify({ id: `evt_${i + 1}`, type, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' })}\n\n`).join(''),
});

const buildingReads = (mock: { requests: { path: string; tenant: string | null }[] }, tenant?: string) =>
  mock.requests.filter((r) => r.path.endsWith('/buildings') && (!tenant || r.tenant === tenant)).length;

async function openWithRegistry(page: Page, registries: Parameters<typeof mockOfficeSpaces>[1]['registries'], before?: () => Promise<void>) {
  await mockHq(page, { campus: 'missing', commerce: 'none', meetings: 'none', presence: 'missing' });
  await before?.();
  // Registered last, so it answers first: the registry of the tenant named by the request headers.
  const mock = await mockOfficeSpaces(page, { registries });
  await page.goto('/#office-preview');
  await expect(page.locator('.hqLive')).toBeVisible();
  return mock;
}

test('a task event on the event stream refreshes the registry', async ({ page }) => {
  const mock = await openWithRegistry(page, { [TENANT_A.tenantId]: hqRegistry(TENANT_A) }, async () => {
    await page.route('**/api/v1/events/stream', (route) => route.fulfill(eventStream(['task.updated'])));
  });
  await expect.poll(() => buildingReads(mock), { timeout: 15_000 }).toBeGreaterThanOrEqual(2);
});

test('a dropped event stream does not freeze the office: the periodic refresh keeps reading the registry', async ({ page }) => {
  let streamCalls = 0;
  const mock = await openWithRegistry(page, { [TENANT_A.tenantId]: hqRegistry(TENANT_A) }, async () => {
    await page.route('**/api/v1/events/stream', (route) => { streamCalls += 1; return route.abort('failed'); });
  });
  await expect.poll(() => streamCalls, { timeout: 10_000 }).toBeGreaterThanOrEqual(1);
  const base = buildingReads(mock);
  await expect.poll(() => buildingReads(mock), { timeout: 30_000 }).toBeGreaterThan(base);
  await expect(page.locator('.hqLive')).toBeVisible();
});

async function switchTenant(page: Page) {
  await page.evaluate(({ tenantId, workspaceId }) => {
    localStorage.setItem('anum.tenant.tenant_local.user_local', tenantId);
    localStorage.setItem(`anum.workspaces.${tenantId}.user_local`, JSON.stringify({ selected: workspaceId, known: [workspaceId] }));
    window.dispatchEvent(new Event('focus'));
  }, TENANT_B);
}

async function openList(page: Page) {
  await page.locator('.hqLive').getByRole('button', { name: 'List' }).click();
}

test('after an in-app organisation switch the registry is read for the new organisation', async ({ page }) => {
  const mock = await openWithRegistry(page, {
    [TENANT_A.tenantId]: hqRegistry(TENANT_A), [TENANT_B.tenantId]: hqRegistry(TENANT_B),
  });
  await openList(page);
  await expect(page.getByRole('button', { name: /Alpha Room 1/ })).toBeVisible({ timeout: 15_000 });
  await switchTenant(page);
  await expect.poll(() => buildingReads(mock, TENANT_B.tenantId), { timeout: 15_000 }).toBeGreaterThan(0);
  await expect(page.getByRole('button', { name: /Bravo Room 1/ })).toBeVisible({ timeout: 15_000 });
});

test('tenant switch race: a slow response of the old organisation never reaches the screen', async ({ page }) => {
  let release: () => void = () => undefined;
  let holdA: Promise<void> | undefined;
  await mockHq(page, { campus: 'missing', commerce: 'none', meetings: 'none', presence: 'missing' });
  await mockOfficeSpaces(page, {
    registries: {
      [TENANT_A.tenantId]: hqRegistry(TENANT_A, { rooms: 2, desks: 0 }),
      [TENANT_B.tenantId]: hqRegistry(TENANT_B, { rooms: 2, desks: 0 }),
    },
    hold: (tenant) => (tenant === TENANT_A.tenantId ? holdA : undefined),
  });
  await page.goto('/#office-preview');
  await openList(page);
  await expect(page.getByRole('button', { name: /Alpha Room 1/ })).toBeVisible({ timeout: 15_000 });

  // A refresh of organisation A starts and its responses are held back ...
  holdA = new Promise<void>((resolve) => { release = resolve; });
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  // ... the user switches organisation while it is in flight ...
  await switchTenant(page);
  await expect(page.getByRole('button', { name: /Bravo Room 1/ })).toBeVisible({ timeout: 15_000 });
  // ... and only now the old answers arrive.
  release();
  await page.waitForTimeout(1500);
  const text = await page.locator('.hqLive').innerText();
  expect(text).not.toContain('Alpha Room');
  expect(text).toContain('Bravo Room');
});
