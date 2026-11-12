import { expect, test, type Page } from '@playwright/test';
import { mockMailApi } from './support/mailApi';
import { sweep } from './support/sweep';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Save as purchase from the Email Center (docs/purchases.md#save-as-purchase-from-the-email-center) against a mocked
// API: the reader offers it, the dialog picks whose purchase and which attachment becomes the receipt, the request
// names the message (never its text), the answer links to the purchase, and a refusal says what failed.

const ATTACHMENTS = [
  { index: 0, filename: 'logo.png', content_type: 'image/png', size: 2048, downloadable: true },
  { index: 1, filename: 'receipt-1042.pdf', content_type: 'application/pdf', size: 40_000, downloadable: true },
];

async function openReader(page: Page, answer: { status: number; body: unknown }) {
  await mockWorkspaceApi(page);
  await mockMailApi(page, { readerAttachments: ATTACHMENTS });
  const sent: unknown[] = [];
  await page.route('http://localhost:8000/api/v1/purchases/from-mail', async (route) => {
    sent.push(route.request().postDataJSON());
    await route.fulfill({ status: answer.status, contentType: 'application/json', body: JSON.stringify(answer.body) });
  });
  await page.goto('/#email');
  await page.locator('.mailRowOpen').first().click();
  await expect(page.getByRole('article').getByRole('heading', { level: 2 })).toBeFocused();
  return sent;
}

test('an email is saved as a purchase draft with its PDF as the receipt', async ({ page }) => {
  const sent = await openReader(page, { status: 201, body: { status: 'created', purchase_id: 'pur_77', message: 'Saved Noon 123-TEST under Purchases. Check it in ANUM.', document_id: 'vdoc_9', attachment_kept: true } });
  await page.getByRole('button', { name: 'Save as purchase' }).click();
  const dialog = page.getByRole('dialog', { name: 'Save this email as a purchase' });
  await expect(dialog.getByRole('radio', { name: 'receipt-1042.pdf' })).toBeChecked();  // the PDF is offered first
  await dialog.getByRole('radio', { name: 'The business' }).check();
  await dialog.getByRole('button', { name: 'Save purchase' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText('Saved Noon 123-TEST under Purchases. Check it in ANUM.')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open the purchase' })).toHaveAttribute('href', '#purchases?open=pur_77');
  expect(sent).toHaveLength(1);
  const body = sent[0] as Record<string, unknown>;
  expect(body).toMatchObject({ uid: expect.any(Number), attachment_index: 1, ownership: 'business' });
  expect(Object.keys(body).sort()).toEqual(['account_id', 'attachment_index', 'folder', 'ownership', 'uid']);
});

test('without an attachment, and a refusal says what failed and keeps the dialog', async ({ page }) => {
  const sent = await openReader(page, { status: 403, body: { detail: 'Keeping the attachment needs the document vault (owners and members). Save it without the attachment, or ask a workspace owner.', code: 'vault_forbidden' } });
  await page.getByRole('button', { name: 'Save as purchase' }).click();
  const dialog = page.getByRole('dialog', { name: 'Save this email as a purchase' });
  await dialog.getByRole('button', { name: 'Save purchase' }).click();
  await expect(dialog.getByRole('alert')).toContainText('Keeping the attachment needs the document vault');
  await dialog.getByRole('radio', { name: 'No attachment' }).check();
  await expect(dialog.getByText('The file is stored privately in your document vault')).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toHaveCount(0);
  expect(sent).toHaveLength(1);
});

test('the dialog passes the layout and axe checks in English and Arabic', async ({ page }) => {
  for (const locale of ['en', 'ar'] as const) {
    const tab = await page.context().newPage();
    await useSavedLocale(tab, locale);
    await openReader(tab, { status: 201, body: { status: 'created', purchase_id: 'pur_1', message: 'Saved.', attachment_kept: false } });
    await tab.getByRole('button', { name: locale === 'ar' ? 'احفظ كمشتريات' : 'Save as purchase' }).click();
    await expect(tab.getByRole('dialog')).toBeVisible();
    await sweep(tab, `${locale} save as purchase`);
    await tab.close();
  }
});
