// Local screenshot runner for the reception visitor register and the career building (not part of CI).
//   RECEPTION_OUT=<dir> pnpm exec playwright test -c <a local config with a preview server> e2e/tools/hqReception.shots.ts --project=desktop
// Every name is plainly fake test data; the API is mocked.
import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from '../support/hqApi';
import { fakeVisit, mockReception } from '../support/hqReceptionApi';

const OUT = process.env.RECEPTION_OUT ?? '/tmp/reception';
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const hq = (page: Page) => page.locator('.hqLive');
test.use({ deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 300_000 });

const stamp = '2026-10-01T08:00:00Z';
const rec = (id: string, extra: Record<string, unknown>) => ({ id, revision: 1, tenant_id: TENANT_A.tenantId, workspace_id: TENANT_A.workspaceId, created_at: stamp, updated_at: stamp, ...extra });
function frontOffice() {
  const base = hqRegistry(TENANT_A, { rooms: 0, desks: 0 });
  const floors = [rec('Alpha-floor-0', { building_id: base.buildings[0].id, level: 0, name: 'Ground floor: Front office' })];
  const room = (id: string, name: string, kind: string, x: number) => rec(id, { floor_id: 'Alpha-floor-0', name, kind, capacity: 6, scene_x: x, scene_z: 0, scene_width: 8, scene_depth: 6 });
  return { buildings: base.buildings, floors, rooms: [room('Alpha-finance', 'Alpha Finance', 'office', 0), room('Alpha-lobby', 'Alpha Lobby', 'reception', 10)], desks: [] } as unknown as ReturnType<typeof hqRegistry>;
}
const visits = () => [
  fakeVisit(1, { status: 'checked_in', checked_in_at: new Date().toISOString(), room_id: 'Alpha-lobby', company: 'Example Trading' }),
  fakeVisit(2, { host: { kind: 'user', id: 'owner_example' } }), fakeVisit(3, { status: 'checked_out', checked_in_at: new Date(Date.now() - 7_200_000).toISOString(), checked_out_at: new Date().toISOString() }),
  fakeVisit(4, { status: 'cancelled', cancelled_at: new Date().toISOString() }), ...Array.from({ length: 4 }, (_, i) => fakeVisit(5 + i)),
];

async function shot(page: Page, name: string) {
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  const box = await page.getByTestId('hq-reception').boundingBox().catch(() => null);
  console.log(`${name} overflowX=${overflow} panel=${box ? `${Math.round(box.x)},${Math.round(box.y)},${Math.round(box.width)}x${Math.round(box.height)}` : '-'}`);
}

for (const locale of ['en', 'ar'] as const) {
  for (const [w, h] of SIZES) {
    test(`reception ${locale} ${w}x${h}`, async ({ page }) => {
      await page.setViewportSize({ width: w, height: h });
      await openHq(page, { locale, registry: frontOffice(), beforeGoto: async (p) => { await mockReception(p, { visits: visits() }); } });
      await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
      await hq(page).getByRole('button', { name: /Office tools|أدوات المكتب/ }).click();
      await page.getByRole('button', { name: locale === 'ar' ? 'الاستقبال: الزوار' : 'Reception: visitors' }).click();
      await expect(page.locator('.hqRecRow').first()).toBeVisible();
      await shot(page, `reception-${locale}-list-${w}x${h}`);
      await page.getByTestId('reception-add').click();
      await shot(page, `reception-${locale}-form-${w}x${h}`);
    });
  }
}

for (const locale of ['en', 'ar'] as const) {
  test(`reception ${locale} empty and 3D count 1440x900`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openHq(page, { locale, registry: frontOffice(), beforeGoto: async (p) => { await mockReception(p, { visits: [fakeVisit(1, { status: 'checked_in', checked_in_at: new Date().toISOString(), room_id: 'Alpha-lobby' })] }); } });
    await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible({ timeout: 60_000 });
    await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).dispatchEvent('click', undefined, { timeout: 60_000 });
    await hq(page).getByRole('button', { name: /^(Open|فتح) ⁨?Al Noor Fleet HQ⁩?$/ }).click();
    await expect(hq(page).locator('.hqRoomCard', { hasText: 'Alpha Lobby' })).toBeVisible({ timeout: 60_000 });
    await page.waitForTimeout(2500);
    await shot(page, `reception-${locale}-3d-count-1440x900`);
  });
}

test('campus with the career building 1440x900', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const base = hqRegistry(TENANT_A, { rooms: 0, desks: 0 });
  const b = (id: string, name: string) => rec(id, { name, timezone: 'Asia/Dubai', latitude: null, longitude: null });
  const buildings = [b('campus-01-hq', 'ANUM HQ'), b('campus-07-personal', 'Personal Office'), b('campus-08-career', 'Career & Employment')];
  const floors = buildings.map((x) => rec(`${x.id}-l0`, { building_id: x.id, level: 0, name: 'Ground floor' }));
  const rooms = floors.map((f, i) => rec(`${f.id}-r`, { floor_id: f.id, name: `Room ${i + 1}`, kind: 'office', capacity: 6, scene_x: 0, scene_z: 0, scene_width: 8, scene_depth: 6 }));
  await openHq(page, { registry: { ...base, buildings, floors, rooms, desks: [] } as unknown as ReturnType<typeof hqRegistry> });
  await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible({ timeout: 60_000 });
  await page.waitForTimeout(6000);
  await shot(page, 'career-campus-1440x900');
});
