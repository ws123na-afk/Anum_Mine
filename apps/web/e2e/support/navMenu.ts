import type { Page } from '@playwright/test';

// Phones (760 px and narrower) keep the primary navigation in a drawer behind the menu button in
// the brand row (src/Shell.tsx Sidebar, e2e/sidebar.spec.ts). Specs that pick a sidebar entry call
// openNavMenu first, so the same steps run in the desktop and the mobile project.

/** Opens the phone navigation drawer when the menu button is showing; does nothing on desktop. */
export async function openNavMenu(page: Page): Promise<void> {
  const button = page.locator('.sidebar > .navMenuButton');
  if (await button.isVisible() && (await button.getAttribute('aria-expanded')) !== 'true') await button.click();
}

/** Closes the drawer again (Escape) when it is open. */
export async function closeNavMenu(page: Page): Promise<void> {
  if (await page.locator('.sidebar.navOpen').count()) await page.keyboard.press('Escape');
}
