import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { mockWorkspaceApi } from './support/workspaceApi';
import { mockVault, type VaultMockOptions } from './support/vaultApi';

// The Document vault (docs/documents.md#the-web-page) against a mocked API: overview, list, filters and
// detail, adding with a duplicate, the review of a scanned receipt, trash, settings with the iPhone
// token, the denied, empty and failed states, Arabic, and axe. Runs on desktop and on the phone project.
async function open(page: import('@playwright/test').Page, hash = 'vault', options: VaultMockOptions = {}) {
  await mockWorkspaceApi(page);
  const mock = await mockVault(page, options);
  await page.goto('about:blank');
  await page.goto(`/#${hash}`);
  await expect(page.getByRole('heading', { level: 1, name: 'Document vault' })).toBeVisible();
  return mock;
}

test('the overview shows real counts, what expires next and what waits for review', async ({ page }) => {
  await open(page);
  const summary = page.getByRole('region', { name: 'Document vault figures' });
  await expect(summary.getByText('Documents')).toBeVisible();
  await expect(summary.getByText('4', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Expiring next' })).toBeVisible();
  await expect(page.locator('.vaultMiniList').first().getByRole('button', { name: 'Passport' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Waiting for your review' })).toBeVisible();
  await page.locator('.vaultMiniList').first().getByRole('button', { name: 'Passport' }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'Passport' })).toBeVisible();
});

test('documents: search, filter, open the detail, reveal the number, share and edit', async ({ page }) => {
  const mock = await open(page, 'vault-documents');
  const list = page.getByRole('region', { name: 'Documents' });
  await expect(list.locator('.vaultRow')).toHaveCount(4);
  await expect(list.getByText('•••• 4567').first()).toBeVisible();
  await list.getByRole('searchbox', { name: 'Search' }).fill('iqama');
  await list.getByRole('button', { name: 'Find' }).click();
  await expect(list.locator('.vaultRow')).toHaveCount(1);
  await expect(list.getByText('Expired 3 days ago')).toBeVisible();
  await list.getByRole('button', { name: 'Clear' }).click();
  await list.getByLabel('Category').selectOption('contract');
  await list.getByRole('button', { name: 'Find' }).click();
  await expect(list.locator('.vaultRow')).toHaveCount(1);
  await list.getByRole('button', { name: 'Clear' }).click();
  await list.locator('.vaultRow', { hasText: 'Passport' }).click();
  const detail = page.getByRole('region', { name: 'Passport' });
  await expect(detail.getByText('Ministry of Interior')).toBeVisible();
  await detail.getByRole('button', { name: 'Show number' }).click();
  await expect(detail.getByText('A1234567')).toBeVisible();
  await detail.locator('.vaultSub', { hasText: 'Shared with' }).getByLabel('Person').selectOption('user_amina');
  await detail.getByRole('button', { name: 'Share', exact: true }).click();
  expect(mock.calls.some((c) => c.method === 'POST' && c.path === '/vdoc_passport/access')).toBe(true);
  await detail.getByRole('button', { name: 'Edit details' }).click();
  const form = detail.getByRole('form', { name: 'Edit document details' });
  await form.getByLabel('Title').fill('Passport renewed');
  await form.getByLabel('Country').fill('1A');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(detail.getByText('The country is a two-letter code such as SA or AE.')).toBeVisible();
  await form.getByLabel('Country').fill('AE');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Saved Passport renewed.')).toBeVisible();
  expect(mock.calls.find((c) => c.method === 'PATCH')?.body).toEqual({ row_version: 1, title: 'Passport renewed', country: 'AE' });
});

test('adding a document, and a duplicate offers the existing one', async ({ page }) => {
  const mock = await open(page, 'vault-documents');
  await page.getByRole('button', { name: 'Add document' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add a document' });
  await dialog.getByLabel('File').setInputFiles({ name: 'duplicate.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 sample') });
  await dialog.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(dialog.getByText(/already in the vault as Passport/)).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Open the existing one' })).toBeVisible();
  await dialog.getByLabel('File').setInputFiles({ name: 'lease.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 sample') });
  await dialog.getByLabel('Title').fill('New lease');
  await dialog.getByLabel('Category').selectOption('contract');
  await dialog.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(page.getByText('Added New lease.')).toBeVisible();
  const post = mock.calls.filter((c) => c.method === 'POST' && c.path === '/').at(-1);
  expect(post?.query).toContain('title=New+lease');
  expect(post?.query).toContain('category=contract');
  expect(post?.query).toContain('access_class=private');
});

test('a scanned receipt is reviewed: the warning shows, a wrong value is refused, then confirmed', async ({ page }) => {
  const mock = await open(page, 'vault-review');
  const card = page.locator('.vaultReviewCard', { hasText: 'Fuel receipt' });
  await expect(card.getByText(/reads like instructions to an assistant/)).toBeVisible();
  await expect(card.getByLabel('Total')).toHaveValue('150.00');
  await expect(card.getByText(/read from: Total SAR 150.00/).first()).toBeVisible();
  await card.getByLabel('Tax').fill('19,5x');
  await card.getByRole('button', { name: 'Confirm values' }).click();
  await expect(card.getByText('Use a number such as 150.00.')).toBeVisible();
  await card.getByLabel('Tax').fill('19.56');
  await card.getByRole('button', { name: 'Confirm values' }).click();
  await expect(page.getByText('Confirmed the values of Fuel receipt.')).toBeVisible();
  const confirm = mock.calls.find((c) => c.path === '/extractions/vext_1/confirm');
  expect((confirm?.body as { fields: Record<string, string> }).fields).toMatchObject({ total: '150.00', tax: '19.56', currency: 'SAR' });
  await expect(page.getByRole('heading', { name: 'Nothing to review' })).toBeVisible();
});

test('trash: restore, and delete for good after confirming', async ({ page }) => {
  const mock = await open(page, 'vault-trash');
  await page.getByRole('button', { name: 'Delete Old visa for good' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Delete Old visa for good?' });
  await dialog.getByRole('button', { name: 'Delete for good' }).click();
  await expect(page.getByText('Deleted Old visa for good.')).toBeVisible();
  expect(mock.calls.some((c) => c.method === 'DELETE' && c.query.includes('permanent=true'))).toBe(true);
  await expect(page.getByRole('heading', { name: 'The trash is empty' })).toBeVisible();
});

test('settings: an owner saves scanning switches, adds a category and makes an iPhone token shown once', async ({ page }) => {
  const mock = await open(page, 'vault-settings');
  const form = page.getByRole('form', { name: 'Reminders and scanning' });
  await form.getByLabel('Remind before expiry (days)').fill('400');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(form.getByText('Use a whole number of days from 1 to 365.')).toBeVisible();
  await form.getByLabel('Remind before expiry (days)').fill('14');
  await form.getByLabel(/Read the dates of IDs/).check();
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Saved the vault settings.')).toBeVisible();
  expect(mock.calls.find((c) => c.method === 'PUT')?.body).toEqual({ reminder_days: 14, trash_days: 30, ocr_receipts: true, ocr_identity_dates: true });
  await page.getByLabel('Category name').fill('Medical');
  await page.getByRole('button', { name: 'Add category' }).click();
  await expect(page.getByText('Added the category.')).toBeVisible();
  await page.getByRole('button', { name: 'Make a token' }).click();
  await expect(page.getByText(/Copy this token now/)).toBeVisible();
  await expect(page.locator('.vaultToken code')).toContainText('anumdoc.');
  await expect(page.getByRole('button', { name: 'Disconnect iPhone' })).toBeVisible();
});

test('members see settings read only; viewers are told who can see the vault; failures and empty say what to do', async ({ page }) => {
  await open(page, 'vault-settings', { role: 'member' });
  await expect(page.getByText('Only a workspace owner changes these settings.')).toBeVisible();
  await expect(page.getByLabel('Remind before expiry (days)')).toBeDisabled();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await open(page, 'vault', { role: 'viewer' });
  await expect(page.getByRole('heading', { name: 'The vault is for members and owners' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add document' })).toHaveCount(0);

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await open(page, 'vault', { metaStatus: 500 });
  await expect(page.getByText(/Couldn't load the vault:/)).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await open(page, 'vault', { empty: true });
  await expect(page.getByRole('heading', { name: 'Your vault is empty' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Scan from your iPhone' })).toBeVisible();
});

test('a long list pages with Show more', async ({ page }) => {
  const mock = await open(page, 'vault-documents', { manyDocuments: 60 });
  await expect(page.locator('.vaultRow')).toHaveCount(50);
  await page.getByRole('button', { name: 'Show more' }).click();
  await expect(page.locator('.vaultRow')).toHaveCount(64);
  expect(mock.calls.some((c) => c.query.includes('cursor=50'))).toBe(true);
});

test('Arabic: the vault reads right to left with translated labels', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('anum.locale', 'ar'));
  await mockWorkspaceApi(page);
  await mockVault(page);
  await page.goto('/#vault-documents');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { level: 1, name: 'خزنة المستندات' })).toBeVisible();
  await expect(page.locator('.vaultRow').first()).toBeVisible();
  await expect(page.getByText('انتهى منذ 3 أيام')).toBeVisible();
});

for (const hash of ['vault', 'vault-documents', 'vault-review', 'vault-settings']) {
  test(`axe finds no violations on #${hash}`, async ({ page }) => {
    await open(page, hash);
    await page.waitForLoadState('networkidle');
    if (hash === 'vault-documents') await page.locator('.vaultRow', { hasText: 'Passport' }).click();
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
  });
}
