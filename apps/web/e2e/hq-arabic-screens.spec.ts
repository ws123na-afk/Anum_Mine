import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockHq } from './support/hqCampusApi';
import { fakeVisit, mockReception } from './support/hqReceptionApi';
import { useSavedLocale } from './support/workspaceApi';

// Arabic for the office tools that used to show English (docs/web-localization.md): office space management and the
// reception register, right to left, at the five gate sizes. The guided visit has its own Arabic gate in hq-guided-visit.
// The API is mocked at http://localhost:8000; names are the fixtures' own.
test.use({ deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 240_000 });

const hq = (page: Page) => page.locator('.hqLive');
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const LATIN_UI = /\b(Building|Floor|Rooms|Desks|Refresh|Close|Create|Capacity|Vacant|Assign|Unassign|Delete|Return to office|Server|Search|Workspace)\b/;
const noOverflow = async (page: Page) => expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), { timeout: 15_000, intervals: [100, 250, 500, 1000] }).toBeLessThanOrEqual(0);
const clippedButtons = (page: Page, selector: string) => page.locator(`${selector} button`).evaluateAll((els) => els.filter((el) => {
  const r = el.getBoundingClientRect();
  return r.width > 0 && (r.right > window.innerWidth + 1 || r.left < -1);
}).length);
const shot = async (page: Page, name: string) => {
  const dir = process.env.HQ_SHOTS_DIR;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: `${dir}/${name}.png` });
};
/** Visible text without the invisible bidi isolates that keep Latin names in place. */
const plain = async (page: Page, selector: string) => (await page.locator(selector).innerText()).replace(/[⁨⁩]/g, '');

test('@gate Arabic: office space management reads right to left in Arabic at all five sizes', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
  await mockHq(page, { campus: 'api', commerce: 'connected', meetings: 'live' });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/#office-preview');
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  await hq(page).getByRole('button', { name: /^أدوات المكتب/ }).click();
  await hq(page).getByRole('button', { name: 'إدارة المساحات' }).click();
  const manager = page.locator('.liveSpaceManager');
  await expect(manager.getByRole('heading', { level: 2, name: 'إدارة مساحات المكتب' })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  expect(await manager.evaluate((node) => getComputedStyle(node).direction)).toBe('rtl');
  await expect(manager.getByText(/حُمّلت سجلات الخادم/)).toBeVisible();
  await expect(manager.getByRole('button', { name: 'إنشاء المبنى' })).toBeVisible();
  await expect(manager.getByRole('combobox', { name: /^المبنى/ })).toBeVisible();
  await expect(manager.getByRole('button', { name: /العودة إلى المكتب/ })).toBeVisible();
  // Every word the screen writes itself is Arabic: only the stored names, ids and time zones stay as they are.
  expect(await plain(page, '.liveSpaceManager')).not.toMatch(LATIN_UI);
  for (const [width, height] of SIZES) {
    await page.setViewportSize({ width, height });
    await expect(manager).toBeVisible();
    await noOverflow(page);
    expect(await clippedButtons(page, '.liveSpaceManager')).toBe(0);
    await shot(page, `spaces-ar-${width}x${height}`);
  }
});

function frontOffice() {
  const base = hqRegistry(TENANT_A, { rooms: 0, desks: 0 });
  const stamp = '2026-10-01T08:00:00Z';
  const rec = (id: string, extra: Record<string, unknown>) => ({ id, revision: 1, tenant_id: TENANT_A.tenantId, workspace_id: TENANT_A.workspaceId, created_at: stamp, updated_at: stamp, ...extra });
  const floors = [rec('Alpha-floor-0', { building_id: base.buildings[0].id, level: 0, name: 'Ground floor' })];
  const rooms = [rec('Alpha-lobby', { floor_id: 'Alpha-floor-0', name: 'Alpha Lobby', kind: 'reception', capacity: 6, scene_x: 0, scene_z: 0, scene_width: 8, scene_depth: 6 })];
  return { buildings: base.buildings, floors, rooms, desks: [] } as unknown as ReturnType<typeof hqRegistry>;
}

test('@gate Arabic: the reception register and its form fit all five sizes, right to left', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openHq(page, { registry: frontOffice(), locale: 'ar', beforeGoto: async (p) => { await mockReception(p, { visits: [fakeVisit(1), fakeVisit(2, { status: 'checked_in', checked_in_at: new Date().toISOString(), room_id: 'Alpha-lobby' })] }); } });
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  await hq(page).getByRole('button', { name: /^أدوات المكتب/ }).click();
  await page.getByRole('button', { name: 'الاستقبال: الزوار' }).click();
  const panel = page.getByTestId('hq-reception');
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel.locator('.hqRecRow')).toHaveCount(2);
  await expect(panel.getByRole('button', { name: 'تسجيل الخروج' })).toBeVisible();
  // The check in and check out arrows point the reading way.
  const flip = await panel.locator('.hqRecActions svg.flipRtl').first().evaluate((node) => getComputedStyle(node).transform);
  expect(flip).toBe('matrix(-1, 0, 0, 1, 0, 0)');
  expect(await plain(page, '[data-testid="hq-reception"]')).not.toMatch(/\b(Expected|Checked in|Check out|Edit|Delete|Visitor register|Load more)\b/);
  for (const [width, height] of SIZES) {
    await page.setViewportSize({ width, height });
    await expect(panel).toBeVisible();
    await noOverflow(page);
    expect(await clippedButtons(page, '[data-testid="hq-reception"]')).toBe(0);
    await shot(page, `reception-ar-${width}x${height}`);
  }
  await panel.getByTestId('reception-add').click();
  const form = panel.getByTestId('reception-form');
  await form.getByRole('button', { name: 'حفظ الزائر' }).click();
  await expect(form.getByText('راجع الحقول المحددة.')).toBeVisible();
  for (const [width, height] of SIZES) {
    await page.setViewportSize({ width, height });
    await noOverflow(page);
    expect(await clippedButtons(page, '[data-testid="reception-form"]')).toBe(0);
    await shot(page, `reception-form-ar-${width}x${height}`);
  }
});
