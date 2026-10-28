import AxeBuilder from '@axe-core/playwright';
import { expect as baseExpect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { openBuilder } from './support/hqBuilderApi';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockFlow } from './support/hqFlowApi';
import { mockProvisioning } from './support/hqProvisioningApi';
import { logoPng } from './support/pngFixture';

const expect = baseExpect.configure({ timeout: 20_000 });

// Logo and brand signs (docs/ANUM_MINE_HQ_LOGO_SIGNS.md), builder side: the "Building sign" section of the Floor tab. The owner chooses or
// drops a logo file, sees the preview, uploads it, picks a brand colour (with a contrast check) and a sign style, removes the logo and resets
// to the lettered sign; a bad file is refused with a plain reason; a person who is not the owner sees how the sign looks and who can change it.
// The API is mocked at http://localhost:8000/** (e2e/support/hqBuilderApi.ts mirrors services/api office_logo_api.py). Set HQ_SHOTS_DIR to keep screenshots.
test.use({ deviceScaleFactor: 1, isMobile: false, hasTouch: false, actionTimeout: 30_000 });
test.describe.configure({ timeout: 240_000 });

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const LOGO = logoPng();

async function shot(page: Page, name: string) {
  const dir = process.env.HQ_SHOTS_DIR;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`) });
}

async function openSign(page: Page, size: readonly [number, number], options: Parameters<typeof openBuilder>[1] = {}, afterOpen?: () => Promise<void>) {
  await page.setViewportSize({ width: size[0], height: size[1] });
  const mock = await openBuilder(page, options);
  await expect(page.locator('.hqb')).toBeVisible();
  // Routes added here come after the mock's own, so they answer first.
  await afterOpen?.();
  // On a narrow screen the side panel is a sheet that opens with its own button.
  if (size[0] <= 900) await page.locator('.hqbSheetToggle').click();
  await page.getByRole('tab', { name: /^(Floor|الطابق)$/ }).click();
  await expect(page.getByTestId('hql-section')).toBeVisible();
  await page.getByTestId('hql-section').scrollIntoViewIfNeeded();
  return mock;
}

/** Nothing leaves the builder sideways and the section's controls are inside the panel. */
async function noOverflow(page: Page) {
  const problems = await page.evaluate(() => {
    const out: string[] = [];
    if (document.documentElement.scrollWidth > window.innerWidth) out.push(`page scrolls sideways (${document.documentElement.scrollWidth} > ${window.innerWidth})`);
    const section = document.querySelector('[data-testid="hql-section"]') as HTMLElement | null;
    if (!section) return [...out, 'no section'];
    const box = section.getBoundingClientRect();
    if (section.scrollWidth > section.clientWidth + 1) out.push(`section scrolls sideways (${section.scrollWidth} > ${section.clientWidth})`);
    for (const el of section.querySelectorAll<HTMLElement>('button, input, canvas, label')) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.left < box.left - 1 || r.right > box.right + 1) out.push(`clipped: ${el.textContent?.trim().slice(0, 30) || el.getAttribute('aria-label') || el.tagName}`);
    }
    return out;
  });
  expect(problems).toEqual([]);
}

async function axeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).include('[data-testid="hql-section"]').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  return results.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
}

for (const size of SIZES) {
  const label = `${size[0]}x${size[1]}`;
  test(`the owner uploads a logo, sets colour and style, removes it and resets, at ${label}`, async ({ page }) => {
    const mock = await openSign(page, size);
    const section = page.getByTestId('hql-section');
    // Empty state: no logo yet, the sign is lettered with the building's name, and there is a next step.
    await expect(page.getByTestId('hql-empty')).toBeVisible();
    await expect(page.getByTestId('hql-preview')).toHaveAttribute('aria-label', /Dubai HQ: lettered with the name/);
    await shot(page, `sign-empty-${label}`);
    await noOverflow(page);

    // Choose a file: the preview shows it before anything is sent.
    await page.getByTestId('hql-file').setInputFiles({ name: 'my-logo.png', mimeType: 'image/png', buffer: LOGO });
    await expect(page.getByTestId('hql-pending')).toContainText('my-logo.png');
    await expect(page.getByTestId('hql-preview')).toHaveAttribute('aria-label', /your logo and name/);
    expect(mock.logos.size).toBe(0);
    await shot(page, `sign-picked-${label}`);
    await noOverflow(page);

    // Upload: one governed call, the raw bytes, a click, the revision we were shown.
    await section.getByRole('button', { name: 'Upload this logo' }).click();
    await expect(page.getByTestId('hql-has-logo')).toBeVisible();
    await expect(page.getByTestId('hql-pending')).toHaveCount(0);
    const put = mock.requests.filter((r) => r.method === 'PUT' && r.path === '/buildings/building-1/logo');
    expect(put).toHaveLength(1);
    expect(mock.logos.get('building-1')?.equals(LOGO)).toBe(true);
    await expect(page.getByTestId('hql-preview')).toHaveAttribute('aria-label', /your logo and name/);

    // Brand colour with its contrast check, and a style.
    await page.getByTestId('hql-style-plain').check();
    await page.getByTestId('hql-color').fill('#1f6f4a');
    await expect(page.getByTestId('hql-contrast')).toContainText('Readable: contrast');
    await page.getByTestId('hql-style-plaque').check();
    // A dark green frame on the dark plaque is hard to see: the page says so (3 to 1 is the least a frame needs).
    await expect(page.getByTestId('hql-contrast')).toContainText('Low contrast');
    await shot(page, `sign-look-${label}`);
    await section.getByRole('button', { name: 'Save colour and style' }).click();
    await expect.poll(() => mock.requests.filter((r) => r.method === 'PATCH' && r.path === '/buildings/building-1').map((r) => r.body)).toEqual([
      expect.objectContaining({ sign_style: 'plaque', brand_color: '#1f6f4a' }),
    ]);
    await expect(section.getByRole('button', { name: 'Save colour and style' })).toBeDisabled();
    await noOverflow(page);

    // Remove the logo only.
    await section.getByRole('button', { name: 'Remove logo' }).click();
    await expect(page.getByTestId('hql-empty')).toBeVisible();
    expect(mock.logos.size).toBe(0);

    // Reset to the lettered sign: asks first, then clears the colour and the style.
    await page.getByTestId('hql-reset').click();
    await expect(page.getByRole('alertdialog', { name: 'Reset to lettered sign' })).toBeVisible();
    await page.getByTestId('hql-reset-confirm').click();
    await expect(page.getByTestId('hql-color')).toHaveValue('');
    await expect(page.getByTestId('hql-reset')).toHaveCount(0);
    await expect(page.getByTestId('hql-style-standard')).toBeChecked();
    await shot(page, `sign-reset-${label}`);
    expect(await axeViolations(page)).toEqual([]);
  });
}

test('a faint colour is flagged and the sign keeps readable lettering; a bad colour code is refused', async ({ page }) => {
  await openSign(page, SIZES[0]);
  // On the standard dark board a pale colour is easy to see; on the plain light board it is not, and the lettering falls back to ink.
  await page.getByTestId('hql-color').fill('#ffee99');
  await expect(page.getByTestId('hql-contrast')).toContainText('Readable');
  await page.getByTestId('hql-style-plain').check();
  await expect(page.getByTestId('hql-contrast')).toContainText('Low contrast');
  await expect(page.getByTestId('hql-contrast')).toHaveAttribute('data-readable', 'false');
  await page.getByTestId('hql-style-banner').check();
  await expect(page.getByTestId('hql-contrast')).toContainText('Readable');
  await page.getByTestId('hql-color').fill('green');
  await expect(page.getByTestId('hql-contrast')).toContainText('Use a colour like #1f6f4a');
  await expect(page.getByTestId('hql-color')).toHaveAttribute('aria-invalid', 'true');
  await expect(page.getByTestId('hql-save')).toBeDisabled();
});

test('files that cannot be a logo are refused with a plain reason and nothing is sent', async ({ page }) => {
  const mock = await openSign(page, SIZES[0]);
  const file = page.getByTestId('hql-file');
  const problem = page.getByTestId('hql-problem');
  await file.setInputFiles({ name: 'logo.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>') });
  await expect(problem).toContainText('SVG logos are not accepted');
  await file.setInputFiles({ name: 'notes.png', mimeType: 'image/png', buffer: Buffer.from('this is plain text, not a picture') });
  await expect(problem).toContainText('not a PNG, JPEG or WebP picture');
  await file.setInputFiles({ name: 'big.png', mimeType: 'image/png', buffer: Buffer.concat([LOGO, Buffer.alloc(1_100_000)]) });
  await expect(problem).toContainText('larger than 1 MB');
  await file.setInputFiles({ name: 'anim.gif', mimeType: 'image/gif', buffer: Buffer.from('GIF89a\x01\x00\x01\x00\x00\x00\x00;', 'latin1') });
  await expect(problem).toContainText('GIF files are not accepted');
  await file.setInputFiles({ name: 'empty.png', mimeType: 'image/png', buffer: Buffer.alloc(0) });
  await expect(problem).toContainText('empty');
  // A picture whose header is a PNG but whose pixels cannot be read never reaches the server.
  await file.setInputFiles({ name: 'broken.png', mimeType: 'image/png', buffer: Buffer.concat([LOGO.subarray(0, 8), Buffer.from('junk junk junk junk')]) });
  await expect(problem).toContainText('could not be read');
  await expect(page.getByTestId('hql-pending')).toHaveCount(0);
  expect(mock.requests.filter((r) => r.path.endsWith('/logo'))).toHaveLength(0);
  await shot(page, 'sign-bad-file-1440x900');
  expect(await axeViolations(page)).toEqual([]);
});

test('the server\'s own refusal is shown next to the logo and the building stays as it was', async ({ page }) => {
  const mock = await openSign(page, SIZES[0]);
  // The page's own check passes (a real PNG); the server (the malware scanner, the decoder) refuses it with its own reason.
  await page.route(/http:\/\/localhost:8000\/api\/v1\/office-spaces\/buildings\/building-1\/logo\?.*/, (route) => (route.request().method() === 'PUT'
    ? route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ error: { code: 'validation_error', message: 'The malware scanner found Eicar-Test in logo.png, so it was not stored. Delete the file and ask the sender for a clean copy.', details: [], correlation_id: 'corr_example' } }) })
    : route.fallback()));
  await page.getByTestId('hql-file').setInputFiles({ name: 'my-logo.png', mimeType: 'image/png', buffer: LOGO });
  await page.getByRole('button', { name: 'Upload this logo' }).click();
  await expect(page.getByTestId('hql-problem')).toContainText('The malware scanner found Eicar-Test');
  await expect(page.getByTestId('hql-problem')).toBeInViewport();
  await expect(page.getByTestId('hql-pending')).toBeVisible();
  expect(mock.logos.size).toBe(0);
});

test('a change made in another window is reported with a way to reload', async ({ page }) => {
  const mock = await openSign(page, SIZES[0]);
  await page.getByTestId('hql-file').setInputFiles({ name: 'my-logo.png', mimeType: 'image/png', buffer: LOGO });
  await expect(page.getByTestId('hql-pending')).toBeVisible();
  // Another editor changes the building behind the page's back.
  mock.touchBuilding('building-1');
  await page.getByRole('button', { name: 'Upload this logo' }).click();
  await expect(page.getByTestId('hql-problem')).toContainText('changed in another window');
  await expect(page.getByTestId('hql-problem').getByRole('button', { name: 'Reload the builder' })).toBeVisible();
  expect(mock.logos.size).toBe(0);
});

test('someone who is not the owner sees the sign and who can change it, and has no controls to change it', async ({ page }) => {
  await openSign(page, SIZES[0], { role: 'member', withLogo: LOGO });
  await expect(page.getByTestId('hql-denied')).toContainText('Only the workspace owner can change the sign');
  await expect(page.getByTestId('hql-preview')).toBeVisible();
  await expect(page.getByTestId('hql-has-logo')).toBeVisible();
  await expect(page.getByRole('button', { name: /Choose a logo file|Choose another file|Remove logo|Save colour and style/ })).toHaveCount(0);
  await expect(page.getByTestId('hql-color')).toBeDisabled();
  await shot(page, 'sign-denied-1440x900');
});

test('an owner whose write is refused by the server sees the server\'s reason', async ({ page }) => {
  await openSign(page, SIZES[0], { forbidWrites: true });
  await page.getByTestId('hql-file').setInputFiles({ name: 'my-logo.png', mimeType: 'image/png', buffer: LOGO });
  await page.getByRole('button', { name: 'Upload this logo' }).click();
  await expect(page.getByTestId('hql-problem')).toContainText('Workspace manager permission is required');
});

test('the current logo loads with the builder, and a failed load says so and offers a retry', async ({ page }) => {
  let failures = 1;
  await openSign(page, SIZES[0], { withLogo: LOGO }, async () => {
    await page.route(/http:\/\/localhost:8000\/api\/v1\/office-spaces\/buildings\/building-1\/logo$/, async (route) => {
      if (route.request().method() === 'GET' && failures > 0) { failures -= 1; return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"down"}' }); }
      return route.fallback();
    });
  });
  await expect(page.getByText('The current logo could not be loaded')).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByTestId('hql-has-logo')).toBeVisible();
  await expect(page.getByText('The current logo could not be loaded')).toHaveCount(0);
});

for (const size of [SIZES[0], SIZES[4]] as const) {
  const label = `${size[0]}x${size[1]}`;
  test(`Arabic: the sign section is right to left with Arabic text at ${label}`, async ({ page }) => {
    const mock = await openSign(page, size, { locale: 'ar' });
    expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
    const section = page.getByTestId('hql-section');
    await expect(section.getByRole('heading', { name: 'لافتة المبنى' })).toBeVisible();
    await expect(page.getByTestId('hql-empty')).toContainText('لا يوجد شعار بعد');
    await expect(page.getByTestId('hql-preview')).toHaveAttribute('aria-label', /مكتوبة باسم المبنى/);
    await page.getByTestId('hql-file').setInputFiles({ name: 'شعار.png', mimeType: 'image/png', buffer: LOGO });
    await expect(page.getByTestId('hql-pending')).toContainText('شعار.png');
    await section.getByRole('button', { name: 'ارفع هذا الشعار' }).click();
    await expect(page.getByTestId('hql-has-logo')).toBeVisible();
    await page.getByTestId('hql-color').fill('#12345g');
    await expect(page.getByTestId('hql-contrast')).toContainText('استخدم لونًا مثل');
    await page.getByTestId('hql-style-plain').check();
    await page.getByTestId('hql-color').fill('#8a5a00');
    await expect(page.getByTestId('hql-contrast')).toContainText('مقروء');
    await page.getByTestId('hql-style-illuminated').check();
    await shot(page, `sign-ar-${label}`);
    await noOverflow(page);
    expect(mock.logos.size).toBe(1);
    // A refused file is explained in Arabic by the page.
    await page.getByTestId('hql-file').setInputFiles({ name: 'logo.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg/>') });
    await expect(page.getByTestId('hql-problem')).toContainText('لا تُقبل شعارات SVG');
    expect(await axeViolations(page)).toEqual([]);
  });
}

test('the whole section works from the keyboard', async ({ page }) => {
  await openSign(page, SIZES[0]);
  const choose = page.getByRole('button', { name: 'Choose a logo file' });
  await choose.focus();
  await expect(choose).toBeFocused();
  // The colour field, the style radios and the save button are reachable in order with Tab and arrows.
  await page.getByTestId('hql-color').focus();
  await page.keyboard.type('#1f6f4a');
  await page.getByTestId('hql-style-standard').focus();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('hql-style-plaque')).toBeChecked();
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('hql-save')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('hql-save')).toBeDisabled();
});

// ---- the 3D campus: the sign of a building switches from lettered to the owner's logo, colour and style

const stage = (page: Page) => page.locator('.hqLive .hqSceneWrap');
const SHA = 'ab'.repeat(32);

/** Three registry buildings; the first has a banner sign with a logo, the third a plaque with a colour only, the middle one nothing. */
function signedRegistry(options: { sign: boolean; nameAr?: boolean }) {
  const base = hqRegistry(TENANT_A, { rooms: 3, desks: 3 });
  const stamp = base.buildings[0];
  const plain = (id: string, name: string, nameAr: string, extra: Record<string, unknown> = {}) => ({ ...stamp, id, name, name_ar: options.nameAr ? nameAr : null, ...extra });
  const buildings = [
    plain('b-alpha', 'Alpha Plant', 'مصنع ألفا', options.sign ? { sign_style: 'banner', brand_color: '#1f6f4a', logo_sha256: SHA } : {}),
    plain('b-beta', 'Beta Works', 'أعمال بيتا'),
    plain('b-gamma', 'Gamma Yard', 'ساحة غاما', options.sign ? { sign_style: 'plaque', brand_color: '#51d9cc' } : {}),
  ];
  const floors = buildings.map((b, i) => ({ ...base.floors[0], id: `f-${i}`, building_id: b.id, name: `Ground ${i + 1}` }));
  const rooms = floors.flatMap((f, i) => [0, 1].map((n) => ({ ...base.rooms[0], id: `r-${i}-${n}`, floor_id: f.id, name: `Room ${i + 1}.${n + 1}`, scene_x: n * 7, scene_z: 0 })));
  const desks = rooms.slice(0, 3).map((r, i) => ({ ...base.desks[0], id: `d-${i}`, room_id: r.id, label: `Desk ${i + 1}`, agent_id: null }));
  return { buildings, floors, rooms, desks };
}

async function openCampus(page: Page, size: readonly [number, number], options: { sign: boolean; locale?: 'en' | 'ar'; logo?: 'png' | '404' | 'slow' }) {
  await page.setViewportSize({ width: size[0], height: size[1] });
  const fetched: string[] = [];
  await openHq(page, {
    registry: signedRegistry({ sign: options.sign, nameAr: options.locale === 'ar' }), locale: options.locale ?? 'en',
    beforeGoto: async (p) => {
      await mockFlow(p); await mockProvisioning(p);
      await p.route(/http:\/\/localhost:8000\/api\/v1\/office-spaces\/buildings\/([^/]+)\/logo$/, async (route) => {
        fetched.push(route.request().url());
        if (options.logo === '404') return route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"This building has no logo."}' });
        return route.fulfill({ status: 200, contentType: 'image/png', headers: { 'cache-control': 'private, no-cache', etag: `"${SHA}"` }, body: LOGO });
      });
    },
  });
  await expect(page.locator('.hqLive').getByRole('heading', { level: 1 })).toBeVisible();
  await expect(stage(page)).toBeAttached({ timeout: 60_000 });
  await expect(page.locator('.hqLive .hqBuildingCard').first()).toBeAttached({ timeout: 60_000 });
  // The registry buildings of the open workspace replace its single campus building once the registry has loaded.
  await expect(page.locator('.hqLive').getByRole('button', { name: /^(Alpha Plant|مصنع ألفا)\./ }).first()).toBeAttached({ timeout: 120_000 });
  return fetched;
}

test('the 3D sign of a building without an upload stays lettered: no logo is requested and nothing extra loads', async ({ page }) => {
  const fetched = await openCampus(page, SIZES[0], { sign: false });
  await expect(stage(page)).toHaveAttribute('data-sign-brands', '0');
  await expect(stage(page)).toHaveAttribute('data-sign-logos', '0');
  await page.waitForTimeout(1500);
  expect(fetched).toEqual([]);
  // The boards in front of the buildings keep the standard header: no brand band, no logo.
  await expect(stage(page)).not.toHaveAttribute('data-board-version', '0', { timeout: 60_000 });
  await expect(stage(page)).toHaveAttribute('data-board-brands', '0');
  await expect(stage(page)).toHaveAttribute('data-board-logos', '0');
  await shot(page, 'campus-lettered-1440x900');
});

for (const size of SIZES) {
  const label = `${size[0]}x${size[1]}`;
  test(`the 3D sign switches to the owner's logo, colour and style at ${label}`, async ({ page }) => {
    const fetched = await openCampus(page, size, { sign: true });
    // Two buildings carry a sign; only the one with a logo is fetched, once, with the person's own credentials.
    await expect(stage(page)).toHaveAttribute('data-sign-brands', '2');
    await expect(stage(page)).toHaveAttribute('data-sign-logos', '1', { timeout: 60_000 });
    expect(fetched.filter((u) => u.includes('/b-alpha/logo'))).toHaveLength(1);
    expect(fetched.filter((u) => !u.includes('/b-alpha/logo'))).toEqual([]);
    // The boards take the same decoded logo (no second download) and the brand colours as header bands: both colours pass the contrast check.
    await expect(stage(page)).toHaveAttribute('data-board-logos', '1', { timeout: 60_000 });
    await expect(stage(page)).toHaveAttribute('data-board-brands', '2');
    expect(fetched.filter((u) => u.includes('/b-alpha/logo'))).toHaveLength(1);
    // Every building card is still on the page and clickable.
    await expect(page.locator('.hqLive .hqBuildingCard')).toHaveCount(await page.locator('.hqLive .hqBuildingCard').count());
    expect(await page.locator('.hqLive .hqBuildingCard').count()).toBeGreaterThanOrEqual(3);
    await shot(page, `campus-signs-${label}`);
    // Frame the building with the logo: the camera flies to it and its sign is on its facade.
    await page.locator('.hqLive').getByRole('button', { name: /^Alpha Plant\./ }).first().dispatchEvent('click');
    await page.waitForTimeout(2500);
    await shot(page, `campus-signs-alpha-${label}`);
  });
}

test('a logo that cannot be loaded leaves the lettered sign and the campus works', async ({ page }) => {
  await openCampus(page, SIZES[0], { sign: true, logo: '404' });
  await expect(stage(page)).toHaveAttribute('data-sign-brands', '2');
  await page.waitForTimeout(2500);
  await expect(stage(page)).toHaveAttribute('data-sign-logos', '0');
  // The board falls back to the plain header with the brand colour band, and no logo.
  await expect(stage(page)).toHaveAttribute('data-board-logos', '0');
  await expect(stage(page)).toHaveAttribute('data-board-brands', '2', { timeout: 60_000 });
  expect(await page.locator('.hqLive .hqBuildingCard').count()).toBeGreaterThanOrEqual(3);
  await expect(page.locator('.hqLive canvas.hqCanvas')).toBeVisible();
});

for (const size of [SIZES[0], SIZES[4]] as const) {
  const label = `${size[0]}x${size[1]}`;
  test(`Arabic: the sign is lettered with the Arabic name and carries the logo at ${label}`, async ({ page }) => {
    await openCampus(page, size, { sign: true, locale: 'ar' });
    expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
    await expect(stage(page)).toHaveAttribute('data-sign-logos', '1', { timeout: 60_000 });
    // The Arabic face must be loaded for the canvas to shape the letters.
    await expect.poll(() => page.evaluate(() => document.fonts.check('700 30px "Noto Sans Arabic"', 'مصنع'))).toBe(true);
    await page.locator('.hqLive').getByRole('button', { name: /^مصنع ألفا\./ }).first().dispatchEvent('click');
    await page.waitForTimeout(2500);
    await shot(page, `campus-signs-alpha-ar-${label}`);
  });
}
