import { expect, type Page } from '@playwright/test';

/**
 * Waits for the headquarters scene to be drawn by the real renderer, not just for the canvas element. The first draw builds the whole 3D
 * scene, which on a loaded software-GL runner takes far longer than the 5 s default of `expect`.
 */
export async function sceneReady(page: Page, timeout = 90_000): Promise<void> {
  const canvas = page.locator('.hqLive canvas.hqCanvas');
  await expect(canvas).toBeVisible({ timeout });
  await expect.poll(() => canvas.evaluate((el) => (el as HTMLCanvasElement).dataset.engine ?? ''), { timeout }).not.toBe('');
}

/**
 * A reload that lets go of the page's WebGL context first. `page.reload()` builds the new scene while the old context is still alive,
 * so two full 3D scenes are held at once; leaving through about:blank frees the first (the saved localStorage and the routes stay).
 */
export async function reloadReleasingGl(page: Page): Promise<void> {
  const url = page.url();
  await page.goto('about:blank');
  await page.goto(url);
}

/**
 * Waits for the settled scene (`data-scene-settled="true"` on the canvas): no lazy part that changes the meshes is pending in the view (the
 * campus plan kit, the billboards), the scene effect chunks have loaded and the scene was built again with them, and a frame of that final
 * scene was drawn. The first builds are temporary, so a triangle count read before this is not the scene's own.
 */
export async function sceneSettled(page: Page, timeout = 90_000): Promise<void> {
  await sceneReady(page, timeout);
  await expect(page.locator('.hqLive canvas.hqCanvas')).toHaveAttribute('data-scene-settled', 'true', { timeout });
}

/* The decluttered headquarters chrome (docs/ANUM_MINE_HQ_PAGE_LAYOUT.md, "Header"; docs/ANUM_MINE_HQ_LABELS.md, "Declutter"): rarely used
   header items are in the Office tools menu, the status chips are one chip with a panel, Face north and the light are under More camera
   options, and the floor list with thumbnails is docked by the Floors button. These helpers reach them the way a person does. */
const live = (page: Page) => page.locator('.hqLive');

/** Opens Office tools and returns the menu item named `name` (the menu closes again when an item is chosen). */
export async function officeMenuItem(page: Page, name: string | RegExp) {
  const tools = live(page).getByRole('button', { name: /^(Office tools|أدوات المكتب)$/ });
  if ((await tools.getAttribute('aria-expanded')) !== 'true') await tools.click();
  return live(page).locator('.hqMenu').getByRole('button', { name });
}

/** Gives the scene the page's whole width: Hide side panels when something is docked beside it, else nothing to do. */
export async function widenScene(page: Page): Promise<void> {
  const hide = await officeMenuItem(page, /^(Hide side panels|إخفاء اللوحات الجانبية)$/);
  if (await hide.count()) { await hide.click(); return; }
  await page.keyboard.press('Escape');
}

/** Opens More camera options (Face north, Day, Blue hour, Night). */
export async function cameraOptions(page: Page): Promise<void> {
  const more = live(page).getByTestId('hq-cam-more').first();
  if ((await more.getAttribute('aria-expanded')) !== 'true') await more.click();
}

/** Opens the status chip's panel (the people link, the office data read and the graphics tier). */
export async function openStatus(page: Page): Promise<void> {
  const chip = live(page).getByTestId('hq-status-chip');
  if ((await chip.getAttribute('aria-expanded')) !== 'true') await chip.click();
}

/** Docks the floor list with thumbnails beside the scene (900 px and wider; narrower it opens as a drawer). */
export async function showFloorList(page: Page): Promise<void> {
  const floors = live(page).getByTestId('hq-floors-toggle');
  await expect(floors).toBeVisible({ timeout: 30_000 });
  if ((await floors.getAttribute('aria-expanded')) !== 'true') await floors.click();
}
