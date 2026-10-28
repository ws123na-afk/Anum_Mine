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
