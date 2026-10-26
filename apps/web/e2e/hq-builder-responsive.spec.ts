import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { openBuilder } from './support/hqBuilderApi';

// Responsive gate for the office builder (CLAUDE.md, Responsive UI quality gate): five viewports,
// no horizontal overflow, nothing clipped, the whole builder inside the screen. Set HQ_SHOTS_DIR to
// keep a screenshot of every state.
const SIZES = [
  { name: '1440x900', width: 1440, height: 900 },
  { name: '1366x768', width: 1366, height: 768 },
  { name: '1024x768', width: 1024, height: 768 },
  { name: '768x1024', width: 768, height: 1024 },
  { name: '390x844', width: 390, height: 844 },
] as const;

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.HQ_SHOTS_DIR;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`) });
}

/** Everything interactive that is visible must lie inside the builder, and nothing may scroll sideways. */
async function expectNoOverflow(page: Page): Promise<void> {
  const problems = await page.evaluate(() => {
    const out: string[] = [];
    const root = document.querySelector('.hqb') as HTMLElement;
    const box = root.getBoundingClientRect();
    if (document.documentElement.scrollWidth > window.innerWidth) out.push(`page scrolls sideways (${document.documentElement.scrollWidth} > ${window.innerWidth})`);
    if (root.scrollWidth > root.clientWidth + 1) out.push(`builder scrolls sideways (${root.scrollWidth} > ${root.clientWidth})`);
    if (box.right > window.innerWidth + 1 || box.bottom > window.innerHeight + 1) out.push(`builder leaves the screen (${Math.round(box.right)}x${Math.round(box.bottom)})`);
    for (const el of root.querySelectorAll<HTMLElement>('header button, header select, .hqbToolbar button, .hqbToolbar select, .hqbTabs button, .hqbSaveBar *')) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.left < box.left - 1 || r.right > box.right + 1) out.push(`clipped: ${el.textContent?.trim().slice(0, 30) || el.getAttribute('aria-label') || el.tagName} (${Math.round(r.left)}..${Math.round(r.right)})`);
    }
    return out;
  });
  expect(problems).toEqual([]);
}

for (const size of SIZES) {
  for (const locale of ['en', 'ar'] as const) {
    test(`@gate builder at ${size.name} (${locale})`, async ({ page }) => {
      await page.setViewportSize({ width: size.width, height: size.height });
      await openBuilder(page, { locale });
      await expect(page.locator('.hqb [data-room-id="r-sales"]')).toBeVisible();
      await expectNoOverflow(page);
      await shot(page, `${size.name}-${locale}-plan`);

      await page.locator('.hqb [data-room-id="r-sales"] .hqbRoomBody').click();
      await expect(page.locator('.hqb [data-room-id="r-sales"]')).toHaveAttribute('data-selected', 'true');
      await expectNoOverflow(page);
      await shot(page, `${size.name}-${locale}-room-selected`);

      // A move makes the draft dirty: the save bar must still fit.
      await page.locator('.hqb [data-room-id="r-sales"]').focus();
      await page.keyboard.press('ArrowRight');
      await expect(page.getByTestId('draft-status')).toBeVisible();
      await expectNoOverflow(page);
      await shot(page, `${size.name}-${locale}-draft`);

      await page.locator('.hqb [role=tab]').nth(1).click();
      await expectNoOverflow(page);
      await shot(page, `${size.name}-${locale}-rooms-tab`);
    });
  }
}

test('@gate builder read-only view at 390x844', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openBuilder(page, { role: 'viewer' });
  await expect(page.getByText('View only')).toBeVisible();
  await expectNoOverflow(page);
  await shot(page, '390x844-en-readonly');
});

test('@gate builder error and empty states at 390x844', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openBuilder(page, { empty: true });
  await expect(page.getByText('No rooms on this floor yet').first()).toBeVisible();
  await expectNoOverflow(page);
  await shot(page, '390x844-en-empty');
});

test('touch: a tap selects, then a finger drag moves the room and the details sheet opens', async ({ page }, info) => {
  test.skip(info.project.name !== 'mobile', 'touch emulation runs in the phone project');
  await openBuilder(page);
  const body = page.locator('.hqb [data-room-id="r-meet"] .hqbRoomBody');
  await body.scrollIntoViewIfNeeded();
  await body.tap();
  await expect(page.locator('.hqb [data-room-id="r-meet"]')).toHaveAttribute('data-selected', 'true');
  await expect(page.getByRole('tabpanel')).toBeVisible();
  await page.getByRole('button', { name: 'Hide details' }).click();
  const box = await body.boundingBox();
  if (!box) throw new Error('no room');
  const cdp = await page.context().newCDPSession(page);
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  for (let i = 1; i <= 6; i += 1) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: y + i * 6 }] });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await expect(page.getByTestId('draft-status')).toContainText('Unsaved changes: 1');
  await expectNoOverflow(page);
  await shot(page, 'phone-touch-moved');
});
