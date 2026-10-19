import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Mail domains in the platform console (docs/email.md#mail-domains), against a mocked API: the
// Mailcow domains with aliases, a live DNS check with the records to set, and changes that wait
// for an approval of exactly that change.
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('domains show their DNS, flag a dangerous SPF and list the records to set', async ({ page }) => {
  await mockWorkspaceApi(page, { mailDomains: 'ok' });
  await page.goto('/#organisations');
  const pane = page.getByRole('region', { name: 'Mail domains' });
  const main = pane.getByRole('listitem').filter({ has: page.getByRole('heading', { name: 'anum360os.com' }) });
  await expect(main.getByText('In Mailcow', { exact: true })).toBeVisible();
  await expect(main.getByText('3 mailboxes')).toBeVisible();
  // Allowed domains in Mailcow are checked on load.
  await expect(main.getByText(/^MX points to \u2068mail\.anum360os\.com\u2069\.$/)).toBeVisible();
  await expect(main.getByRole('cell', { name: 'dkim._domainkey.anum360os.com' })).toBeVisible();
  const risky = pane.getByRole('listitem').filter({ has: page.getByRole('heading', { name: 'easyvoicequote.com' }) });
  await expect(risky.getByText('Not in Mailcow yet')).toBeVisible();
  await risky.getByRole('button', { name: 'Check DNS' }).click();
  await expect(risky.getByText('Dangerous', { exact: true })).toBeVisible();
  await expect(risky.getByText(/'\+all' lets any server/)).toBeVisible();
  await expect(risky.getByRole('cell', { name: 'v=spf1 mx ~all' })).toBeVisible();
  const legacy = pane.getByRole('listitem').filter({ has: page.getByRole('heading', { name: 'legacy-shop.example' }) });
  await expect(legacy.getByText('Not allowed', { exact: true })).toBeVisible();
  await expect(legacy.getByRole('button', { name: 'Remove from Mailcow' })).toHaveCount(0);
  await pane.getByLabel('Search domains and aliases').fill('sales@');
  await expect(pane.getByRole('heading', { name: 'easyvoicequote.com' })).toHaveCount(0);
  await pane.getByLabel('Search domains and aliases').fill('nothing-here');
  await expect(pane.getByText('No domain or alias matches the search.')).toBeVisible();
});

test('adding a domain and an alias waits for approval, then runs', async ({ page }) => {
  await mockWorkspaceApi(page, { mailDomains: 'ok' });
  await page.goto('/#organisations');
  const pane = page.getByRole('region', { name: 'Mail domains' });
  const risky = pane.getByRole('listitem').filter({ has: page.getByRole('heading', { name: 'easyvoicequote.com' }) });
  await risky.getByRole('button', { name: 'Add to Mailcow' }).click();
  await expect(pane.getByText(/Asked: Add the domain .*easyvoicequote\.com.* to Mailcow/)).toBeVisible();
  const waiting = pane.getByRole('group', { name: 'Changes waiting for approval' });
  await expect(waiting.getByText(/Add the domain .*easyvoicequote\.com/)).toBeVisible();
  await expect(risky.getByText('Waiting for approval')).toBeVisible();
  await waiting.getByRole('button', { name: 'Approve' }).click();
  await expect(pane.getByText(/Done: Add the domain/)).toBeVisible();
  await expect(risky.getByText('In Mailcow', { exact: true })).toBeVisible();

  const main = pane.getByRole('listitem').filter({ has: page.getByRole('heading', { name: 'anum360os.com' }) }).first();
  const form = main.getByRole('form', { name: 'Add an alias' });
  await form.getByRole('button', { name: 'Ask to add the alias' }).click();
  await expect(form.getByRole('alert')).toHaveText(/Enter the part before the @/);
  await form.getByLabel('Alias name').fill('support');
  await form.getByLabel('Delivers to (one or more addresses)').fill('omar@anum360os.com, ahmed@example.com');
  await form.getByRole('button', { name: 'Ask to add the alias' }).click();
  await expect(waiting.getByText(/support@anum360os\.com/)).toBeVisible();
  await waiting.getByRole('button', { name: 'Withdraw' }).click();
  await expect(pane.getByText('The change was withdrawn. Nothing changed.')).toBeVisible();
  await expect(pane.getByRole('group', { name: 'Changes waiting for approval' })).toHaveCount(0);
});

test('changes off, a failure and a non-operator are each explained', async ({ page }) => {
  await mockWorkspaceApi(page, { mailDomains: 'readOnly' });
  await page.goto('/#organisations');
  const pane = page.getByRole('region', { name: 'Mail domains' });
  await expect(pane.getByText(/Changes are off: .*ANUM_MAILCOW_WRITE_ENABLED/)).toBeVisible();
  await expect(pane.getByRole('button', { name: 'Add to Mailcow' })).toHaveCount(0);
  await expect(pane.getByRole('form', { name: 'Add an alias' })).toHaveCount(0);
});

test('a failed load offers a retry', async ({ page }) => {
  await mockWorkspaceApi(page, { mailDomains: 'fails' });
  await page.goto('/#organisations');
  const pane = page.getByRole('region', { name: 'Mail domains' });
  await expect(pane.getByText(/Mail domains could not be loaded: The database did not answer/)).toBeVisible();
  await expect(pane.getByRole('button', { name: 'Retry' })).toBeVisible();
});

test('Mailcow off still lists the allowed domains and says how to turn it on', async ({ page }) => {
  await mockWorkspaceApi(page, {});
  await page.goto('/#organisations');
  const pane = page.getByRole('region', { name: 'Mail domains' });
  await expect(pane.getByText(/The Mailcow API is off/)).toBeVisible();
  await expect(pane.getByRole('heading', { name: 'anum360os.com' })).toBeVisible();
});

test('a non-operator never sees mail domains', async ({ page }) => {
  await mockWorkspaceApi(page, { denyOrganisations: true, mailDomains: 'ok' });
  await page.goto('/#organisations');
  await expect(page.getByRole('heading', { name: 'Platform operators only' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Mail domains' })).toHaveCount(0);
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
  for (const locale of ['en', 'ar'] as const) {
    test(`mail domains fit ${width}x${height} in ${locale}`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await useSavedLocale(page, locale);
      await mockWorkspaceApi(page, { mailDomains: 'ok' });
      await page.goto('/#organisations');
      await expect(page.locator('.mailDnsRecords').first()).toBeVisible();
      // DNS judgements come as codes and are shown in the reader's language; the records stay as they are.
      await expect(page.locator('.mailDnsMessage').first()).toHaveText(locale === 'ar' ? /يشير MX إلى/ : /MX points to/);
      await expect(page.getByText('v=spf1 mx ~all').first()).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.dir)).toBe(locale === 'ar' ? 'rtl' : 'ltr');
      expect(await noHorizontalOverflow(page)).toBe(true);
      const pane = page.locator('.mailDomainsPane');
      const box = await pane.boundingBox();
      expect(box && box.x >= -1 && box.x + box.width <= width + 1).toBe(true);
      await pane.screenshot({ path: `test-results/mail-domains-${locale}-${width}.png` });
    });
  }
}
