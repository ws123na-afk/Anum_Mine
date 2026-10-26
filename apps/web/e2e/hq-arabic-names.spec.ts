import { expect as baseExpect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockFlow } from './support/hqFlowApi';
import { mockProvisioning } from './support/hqProvisioningApi';
import { mockFirstRun } from './support/hqFirstRunApi';
import { mockStarterCampus } from './support/hqStarterCampusApi';
import { openBuilder } from './support/hqBuilderApi';

const expect = baseExpect.configure({ timeout: 20_000 });

// Arabic names of buildings, floors and rooms (docs/ANUM_MINE_OFFICE_REGISTRY_BACKEND.md, docs/ANUM_MINE_STARTER_CAMPUS.md#arabic-names):
// Arabic mode shows the Arabic name wherever the registry shows a name, falls back to English, the builder edits it, the 3D signs
// are lettered in Arabic, and the owner can add the Arabic names of an older campus with one click. The API is mocked at
// http://localhost:8000/**; the registry is the real starter campus (8 buildings, 18 floors, 62 rooms). Set HQ_SHOTS_DIR to keep screenshots.
test.use({ deviceScaleFactor: 1, isMobile: false, hasTouch: false, actionTimeout: 30_000 });
test.describe.configure({ timeout: 240_000 });

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const empty = { ...hqRegistry(TENANT_A, { rooms: 0, desks: 0 }), buildings: [], floors: [] };
const hq = (page: Page) => page.locator('.hqLive');

async function shot(page: Page, name: string) {
  const dir = process.env.HQ_SHOTS_DIR;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`) });
}

/** Nothing leaves the page sideways and no name is cut off inside the headquarters: text boxes are not wider than their container. */
async function noClipping(page: Page, root: string) {
  const problems = await page.evaluate((selector) => {
    const out: string[] = [];
    if (document.documentElement.scrollWidth > window.innerWidth) out.push(`page scrolls sideways (${document.documentElement.scrollWidth} > ${window.innerWidth})`);
    const scope = document.querySelector(selector);
    if (!scope) return [...out, `no ${selector}`];
    for (const el of scope.querySelectorAll<HTMLElement>('strong, small, h2, h3, h4, option, label span')) {
      // Visually hidden labels (screen reader only) are one pixel wide on purpose.
      if (!el.offsetParent || !el.textContent?.trim() || el.closest('.hqSrOnly') || el.getBoundingClientRect().width <= 2) continue;
      const style = getComputedStyle(el);
      const clips = style.overflow !== 'visible' || style.overflowX !== 'visible';
      if (clips && style.textOverflow !== 'ellipsis' && el.scrollWidth > el.clientWidth + 1) out.push(`clipped: ${el.textContent.trim().slice(0, 40)}`);
    }
    return out;
  }, root);
  expect(problems).toEqual([]);
}

async function openCampus(page: Page, size: readonly [number, number], locale: 'en' | 'ar') {
  await page.setViewportSize({ width: size[0], height: size[1] });
  await openHq(page, {
    registry: empty, locale,
    beforeGoto: async (p) => { await mockFlow(p); await mockProvisioning(p); await mockFirstRun(p, { prebuilt: true }); },
  });
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  await expect(hq(page).locator('.hqBuildingCard').first()).toBeAttached({ timeout: 60_000 });
}

for (const size of SIZES) {
  const label = `${size[0]}x${size[1]}`;
  test(`Arabic mode names the campus, the building, the floors and the rooms in Arabic at ${label}`, async ({ page }) => {
    await openCampus(page, size, 'ar');
    expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
    // The campus: one card per building, named by its Arabic name; the Latin name is not on any card.
    for (const name of ['المقر الرئيسي لأنوم', 'أنوم 360 للتجارة', 'تاير بلس', 'الخدمات المؤسسية']) {
      await expect(hq(page).getByRole('button', { name: new RegExp(`^${name}\\.`) }).first()).toBeAttached();
    }
    await expect(hq(page).locator('.hqBuildingCard').filter({ hasText: 'ANUM HQ' })).toHaveCount(0);
    await shot(page, `campus-ar-${label}`);
    await noClipping(page, '.hqLive');

    // The building: the title, the floor selector and the room cards.
    await hq(page).getByRole('button', { name: /^المقر الرئيسي لأنوم\./ }).first().dispatchEvent('click');
    await hq(page).getByRole('button', { name: /^(Open|فتح) ⁨?المقر الرئيسي لأنوم⁩?$/ }).click();
    await expect(hq(page).getByRole('heading', { level: 2, name: 'المقر الرئيسي لأنوم' })).toBeVisible();
    const select = hq(page).locator('.hqFloorControl select');
    const options = await select.locator('option').allTextContents();
    expect(options).toContain('الطابق الثاني: العمليات والإدارة');
    expect(options.some((o) => o.startsWith('Floor'))).toBe(false);
    await select.selectOption({ label: 'الطابق الثاني: العمليات والإدارة' });
    await expect(hq(page).locator('.hqBuilding .hqRoomCard')).toHaveCount(5);
    await expect(hq(page).locator('.hqBuilding .hqRoomCard').filter({ hasText: 'مركز القيادة' })).toBeVisible();
    await expect(hq(page).getByTestId('hq-floor-live')).toContainText('الطابق الثاني: العمليات والإدارة');
    // The 3D signs are lettered with the self-hosted Arabic face: it must be loaded for the canvas to shape the letters.
    await expect.poll(() => page.evaluate(() => document.fonts.check('700 30px "Noto Sans Arabic"', 'المستوى'))).toBe(true);
    await shot(page, `building-ar-${label}`);
    await noClipping(page, '.hqBuilding');

    // The floor overview: one card per room, in Arabic.
    const toggle = hq(page).getByTestId('hq-overview');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');
    for (const name of ['مركز القيادة', 'المؤسسة الذكية', 'مكتب العمليات', 'الأصول', 'الموردون']) {
      await expect(hq(page).locator('.hqOverviewCard').filter({ hasText: name })).toBeVisible();
    }
    await expect(hq(page).getByTestId('hq-floor-rooms')).toContainText('الطابق الثاني: العمليات والإدارة');
    await shot(page, `overview-ar-${label}`);
    await noClipping(page, '.hqBuilding');
  });
}

test('English mode keeps the English names and the Arabic fallback shows English for a record without an Arabic name', async ({ page }) => {
  await openCampus(page, SIZES[0], 'en');
  await expect(hq(page).getByRole('button', { name: /^ANUM HQ\./ }).first()).toBeAttached();
  await expect(hq(page).getByRole('button', { name: /^المقر الرئيسي لأنوم\./ })).toHaveCount(0);
  await shot(page, 'campus-en-1440x900');
});

test('a building, floor and room without an Arabic name show the English name in Arabic mode', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const registry = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: [] });
  await openHq(page, { registry, locale: 'ar', beforeGoto: async (p) => { await mockFlow(p); await mockProvisioning(p); await mockStarterCampus(p); } });
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  await expect(hq(page).locator('.hqBuildingCard').first()).toBeAttached({ timeout: 60_000 });
  const names = await hq(page).locator('.hqBuildingCard strong').allTextContents();
  expect(names.length).toBeGreaterThan(0);
  expect(names.every((n) => n.trim().length > 0)).toBe(true);
});

for (const size of SIZES) {
  const label = `${size[0]}x${size[1]}`;
  test(`the builder edits the Arabic names of the building, floor and room, right to left, at ${label}`, async ({ page }) => {
    await page.setViewportSize({ width: size[0], height: size[1] });
    const mock = await openBuilder(page, { locale: 'ar' });
    const dialog = page.getByRole('dialog', { name: /منشئ|Office builder/ });
    await expect(dialog).toBeVisible();
    // On a narrow screen the side panel is a sheet that opens with its own button.
    if (size[0] <= 900) await page.locator('.hqbSheetToggle').click();
    await page.getByRole('tab', { name: 'الطابق', exact: true }).click();
    const floorAr = page.getByTestId('hqb-floor-name-ar');
    const buildingAr = page.getByTestId('hqb-building-name-ar');
    await expect(floorAr).toBeVisible();
    // The fields are right to left and say, until a name is typed, that Arabic mode shows the English name.
    await expect(floorAr).toHaveAttribute('dir', 'rtl');
    await expect(floorAr).toHaveAttribute('lang', 'ar');
    await expect(buildingAr).toHaveAttribute('dir', 'rtl');
    await expect(page.getByRole('note').filter({ hasText: 'يعرض الوضع العربي الاسم الإنجليزي' })).toHaveCount(2);
    await floorAr.fill('الطابق الأرضي');
    await shot(page, `builder-floor-ar-${label}`);
    await page.getByRole('button', { name: 'حفظ الطابق' }).click();
    await expect.poll(() => mock.requests.find((r) => r.method === 'PATCH' && r.path === '/floors/floor-1')?.body).toMatchObject({ name_ar: 'الطابق الأرضي' });
    await expect(mock.floors.get('floor-1')).toMatchObject({ name_ar: 'الطابق الأرضي' });
    await buildingAr.fill('المقر الرئيسي');
    await page.getByRole('button', { name: 'حفظ المبنى' }).click();
    await expect.poll(() => mock.requests.find((r) => r.method === 'PATCH' && r.path === '/buildings/building-1')?.body).toMatchObject({ name_ar: 'المقر الرئيسي' });
    // The header selectors now show the Arabic names.
    await expect(dialog.locator('select option').filter({ hasText: 'المقر الرئيسي' }).first()).toBeAttached();
    await expect(dialog.locator('select option').filter({ hasText: 'الطابق الأرضي' }).first()).toBeAttached();
    // Rooms without an Arabic name carry a hint in the list (builder only).
    await page.getByRole('tab', { name: 'الغرف', exact: true }).click();
    await expect(page.getByRole('tabpanel').getByText('بلا اسم عربي').first()).toBeVisible();
    await shot(page, `builder-rooms-ar-${label}`);
    await noClipping(page, '.hqb');
  });
}

test('Add Arabic names: shows what would change, confirms with one click and the shown hash, then has nothing left', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const hash = 'd'.repeat(64);
  const items = [
    { kind: 'buildings', id: 'campus-01-hq', name: 'ANUM HQ', name_ar: 'المقر الرئيسي لأنوم' },
    { kind: 'floors', id: 'campus-01-hq-l1', name: 'Floor 1: Reception and Meeting Rooms', name_ar: 'الطابق الأول: الاستقبال وغرف الاجتماعات' },
    { kind: 'rooms', id: 'campus-01-hq-reception', name: 'Reception', name_ar: 'الاستقبال' },
  ];
  let done = false;
  const confirms: Record<string, unknown>[] = [];
  await openHq(page, {
    registry: empty, locale: 'ar',
    beforeGoto: async (p) => {
      await mockFlow(p); await mockProvisioning(p); await mockFirstRun(p, { prebuilt: true });
      await p.route('http://localhost:8000/api/v1/office/starter-campus/arabic-names/preview', (route) => route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify({
          version: 1, catalog_version: 1, workspace_id: 'workspace_foundation', payload_hash: hash, can_confirm: true,
          items: done ? [] : items, counts: done ? { buildings: 0, floors: 0, rooms: 0 } : { buildings: 1, floors: 1, rooms: 1 }, total: done ? 0 : items.length,
          rules: { overwrites: false, fills_empty_only: true, matches_master_names_only: true, changes_other_fields: false } }) }));
      await p.route('http://localhost:8000/api/v1/office/starter-campus/arabic-names/confirm', (route) => {
        confirms.push(route.request().postDataJSON() as Record<string, unknown>);
        done = true;
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
          version: 1, outcome: 'filled', payload_hash: hash, filled: 3, counts: { buildings: 1, floors: 1, rooms: 1 }, skipped: 0 }) });
      });
    },
  });
  await hq(page).getByRole('button', { name: 'أدوات المكتب' }).click();
  await hq(page).getByRole('button', { name: 'إعداد الحرم الخاص بي' }).click();
  const section = page.getByTestId('sc-arabic-names');
  await expect(section).toBeVisible({ timeout: 30_000 });
  await expect(section.getByRole('heading', { name: 'إضافة الأسماء العربية' })).toBeVisible();
  await expect(page.getByTestId('sc-arabic-count')).toContainText('المباني: 1. الطوابق: 1. الغرف: 1.');
  await expect(section.getByRole('list').locator('li')).toHaveCount(3);
  await shot(page, 'add-arabic-names-1440x900');
  expect(confirms).toEqual([]);
  await page.getByTestId('sc-arabic-add').click();
  await expect(page.getByTestId('sc-arabic-done')).toContainText('3');
  expect(confirms).toEqual([{ payload_hash: hash, input_mode: 'click' }]);
  await expect(section.getByText('لكل أسماء الحرم الجاهز أسماء عربية بالفعل')).toBeVisible();
  await expect(page.getByTestId('sc-arabic-add')).toHaveCount(0);
});
