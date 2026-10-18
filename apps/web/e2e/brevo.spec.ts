import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';
import { brevoHash, brevoKey, mockBrevo } from './support/brevoApi';

// Email campaigns through Brevo (docs/brevo.md): an owner connects Brevo with an API key, reads the
// campaigns and a report, writes a draft, sends a test to the account's own address, asks to send and
// approves with the payload hash shown. Members and Arabic readers see every state.
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('an owner connects Brevo, reads a report, writes a draft and approves the send with the payload hash', async ({ page }) => {
  const sent = await mockBrevo(page, { connected: false });
  await page.goto('/#marketing-email');
  await expect(page.getByRole('heading', { name: 'Email campaigns (Brevo)' })).toBeVisible();
  await expect(page.getByText('Brevo is not connected yet. Paste your Brevo API key')).toBeVisible();
  await page.getByLabel('Brevo API key').fill('xsmtpsib-example-example-example');
  await page.getByRole('button', { name: 'Check and save' }).click();
  await expect(page.getByText('That is an SMTP key.')).toBeVisible();
  expect(sent.keys).toHaveLength(0);
  await page.getByLabel('Brevo API key').fill(brevoKey);
  await page.getByRole('button', { name: 'Check and save' }).click();
  await expect(page.getByText('Brevo is connected (owner@shop.example).')).toBeVisible();
  expect(sent.keys).toEqual([{ api_key: brevoKey }]);

  // Campaigns in Brevo with their numbers; a report opens in a drawer.
  await expect(page.getByRole('table', { name: 'Email campaigns in Brevo' })).toContainText('October offers');
  await expect(page.getByRole('table', { name: 'Email campaigns in Brevo' })).toContainText('50%');
  await page.getByRole('button', { name: 'Report of October offers' }).click();
  const report = page.getByRole('dialog', { name: 'Campaign report' });
  await expect(report.getByText('Spam complaints')).toBeVisible();
  expect(await new AxeBuilder({ page }).include('.drawerPanel').analyze().then((result) => result.violations.filter((item) => item.impact === 'critical' || item.impact === 'serious'))).toEqual([]);
  await report.getByRole('button', { name: 'Close' }).click();

  // A draft: sender and lists from Brevo; nothing is sent by saving.
  await page.getByRole('button', { name: 'Drafts' }).click();
  await expect(page.getByText('No drafts yet')).toBeVisible();
  await page.getByRole('button', { name: 'New campaign' }).first().click();
  const editor = page.getByRole('dialog', { name: 'New email campaign' });
  await editor.getByLabel('Campaign name (only you see it)').fill('Winter tyres');
  await editor.getByLabel('Subject').fill('Fit winter tyres now');
  await editor.getByRole('checkbox', { name: /Newsletter/ }).check();
  await editor.getByLabel('Email HTML').fill('<h1>Winter tyres</h1><p>Book a fitting this week.</p>');
  await expect(editor.getByRole('option', { name: /Old/ })).toHaveCount(0);
  await editor.getByRole('button', { name: 'Create draft' }).click();
  await expect(page.getByText('Draft created. Nothing was sent.')).toBeVisible();
  expect(sent.drafts).toEqual([{ name: 'Winter tyres', subject: 'Fit winter tyres now', preview_text: '', sender_id: 1, html: '<h1>Winter tyres</h1><p>Book a fitting this week.</p>', list_ids: [7] }]);

  const detail = page.getByRole('dialog', { name: 'Campaign draft' });
  await expect(detail.locator('iframe[title="Email preview"]')).toBeVisible();
  await detail.getByRole('button', { name: 'Send me a test' }).click();
  await expect(page.getByText('Test sent to owner@shop.example')).toBeVisible();
  expect(sent.tests).toBe(1);
  await detail.getByRole('button', { name: 'Ask to send' }).click();
  await detail.getByRole('button', { name: 'Ask an owner to send it' }).click();
  const box = detail.getByRole('group', { name: 'Send approval' });
  await expect(box.getByText('Tyre Shop <news@shop.example>')).toBeVisible();
  await expect(box.getByText('1200', { exact: true })).toBeVisible();
  await expect(box.getByText(`${brevoHash.slice(0, 12)}…`)).toBeVisible();
  expect(sent.sends).toEqual([{ scheduled_at: null }]);
  await box.getByRole('button', { name: 'Approve and send' }).click();
  await expect(page.getByText('is being sent now through Brevo')).toBeVisible();
  expect(sent.decisions).toEqual([{ payload_hash: brevoHash }]);
  await expect(detail.getByText('Sent', { exact: true })).toBeVisible();
  expect(await noOverflow(page)).toBe(true);
});

test('members cannot connect or test, and see who can', async ({ page }) => {
  await mockBrevo(page, { role: 'member', connected: false });
  await page.goto('/#marketing-email');
  await expect(page.getByText('Ask a workspace owner to connect it')).toBeVisible();
  await expect(page.getByText('Only a workspace owner connects Brevo or changes its key.')).toBeVisible();
  await expect(page.getByLabel('Brevo API key')).toHaveCount(0);
});

test('email campaigns read in Arabic, right to left, without overflow', async ({ page }) => {
  await mockBrevo(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#marketing-email');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'حملات البريد (Brevo)' })).toBeVisible();
  await expect(page.getByRole('table', { name: 'حملات البريد في Brevo' })).toContainText('October offers');
  expect(await noOverflow(page)).toBe(true);
});
