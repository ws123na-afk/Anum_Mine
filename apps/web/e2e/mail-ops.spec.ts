import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// The platform operator's mail health in the platform console (docs/email.md#operator-view), against a
// mocked API: counts per organisation, the mail server and the optional read-only Mailcow status.
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('mail health lists counts per organisation and the mail server', async ({ page }) => {
  await mockWorkspaceApi(page, { mailcow: 'ok' });
  await page.goto('/#organisations');
  const pane = page.getByRole('region', { name: 'Mail health' });
  await expect(pane.getByRole('rowheader', { name: /^Gulf Tyres/ })).toBeVisible();
  const row = pane.getByRole('row', { name: /^Gulf Tyres/ });
  await expect(row.getByText('Needs attention')).toBeVisible();
  await expect(row.getByRole('cell', { name: '12', exact: true })).toBeVisible();
  await expect(pane.getByText('All organisations: 3 mailboxes, 12 sent, 1 failed, 1 unknown, 3 rate limited')).toBeVisible();
  await expect(pane.getByText(/IMAP \u2068mail\.anum360os\.com\u2069/)).toBeVisible();
  await expect(pane.getByText('17 of 18 containers running')).toBeVisible();
  await expect(pane.getByText('Not running: \u2068sogo-mailcow\u2069')).toBeVisible();
  await expect(pane.getByText('4 messages waiting in the queue')).toBeVisible();
  await pane.getByLabel('Period').selectOption('30');
  await expect(pane.getByRole('rowheader', { name: /^Gulf Tyres/ })).toBeVisible();
  await pane.getByLabel('Search organisations').fill('nobody');
  await expect(pane.getByText('No organisation matches the search.')).toBeVisible();
});

test('Mailcow off says how to turn it on; a failure offers a retry', async ({ page }) => {
  await mockWorkspaceApi(page, { mailHealthFails: true });
  await page.goto('/#organisations');
  const pane = page.getByRole('region', { name: 'Mail health' });
  await expect(pane.getByText(/Mail health could not be loaded: The database did not answer/)).toBeVisible();
  await expect(pane.getByRole('button', { name: 'Retry' })).toBeVisible();
  await expect(pane.getByText(/ANUM_MAILCOW_API_ENABLED/)).toBeVisible();
});

test('a non-operator never sees mail health', async ({ page }) => {
  await mockWorkspaceApi(page, { denyOrganisations: true });
  await page.goto('/#organisations');
  await expect(page.getByRole('heading', { name: 'Platform operators only' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Mail health' })).toHaveCount(0);
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
  for (const locale of ['en', 'ar'] as const) {
    test(`mail health fits ${width}x${height} in ${locale}`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await useSavedLocale(page, locale);
      await mockWorkspaceApi(page, { mailcow: 'ok' });
      await page.goto('/#organisations');
      await expect(page.locator('.mailHealthTable')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.dir)).toBe(locale === 'ar' ? 'rtl' : 'ltr');
      expect(await noHorizontalOverflow(page)).toBe(true);
      const pane = page.locator('.mailHealthPane');
      const box = await pane.boundingBox();
      expect(box && box.x >= -1 && box.x + box.width <= width + 1).toBe(true);
      await pane.screenshot({ path: `test-results/mail-ops-${locale}-${width}.png` });
    });
  }
}
