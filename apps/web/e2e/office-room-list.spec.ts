import { expect, test } from '@playwright/test';

test('room directory remains usable when the 3D scene is hidden', async ({ page }) => {
  await page.goto('/#office-preview');
  await page.getByRole('button', { name: 'Concept preview' }).click();
  const showList = page.getByRole('button', { name: 'Show room list' });
  if (await showList.isVisible()) await showList.click();

  const directory = page.getByRole('region', { name: /Ground.*rooms/ });
  await expect(directory).toBeVisible();
  await expect(page.locator('.officeScene')).toHaveCount(0);
  await directory.getByRole('button', { name: /ANUM Command/ }).click();
  await expect(page.locator('.officeInfo h2')).toHaveText('ANUM Command');
  await expect(page.getByRole('button', { name: 'Show 3D scene' })).toBeVisible();
});
