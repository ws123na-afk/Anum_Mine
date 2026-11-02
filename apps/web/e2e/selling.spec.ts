import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';
import { mockBilling } from './support/billingApi';
import { mockSelling } from './support/sellingApi';

// Ready to sell (docs/selling-readiness.md): the platform owner's checklist read from the API, the SPF
// lookup, company details and legal facts, Moyasar and Stripe settings, refunds of Moyasar and Stripe payments, and the public
// legal pages filled from the company details with the data rights wired to the app. Five sizes, English
// and Arabic, axe on each.
const sizes = [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
];
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
const serious = async (page: Page, selector?: string) => {
  const builder = new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']);
  if (selector) builder.include(selector);
  return (await builder.analyze()).violations.filter((v) => v.impact === 'critical' || v.impact === 'serious').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
};

async function openConsoleTab(page: Page, tab: string) {
  await page.goto('/#platformBilling');
  await expect(page.getByRole('heading', { name: 'Billing console', level: 1 })).toBeVisible();
  await page.getByRole('navigation', { name: 'Billing console sections' }).getByRole('button', { name: tab }).click();
}

test('the Ready to sell checklist reads real state, links to fixes and checks SPF', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockBilling(page);
  const sent = await mockSelling(page);
  await openConsoleTab(page, 'Ready to sell');
  const list = page.getByRole('heading', { name: 'Ready to sell', level: 2 });
  await expect(list).toBeVisible();
  await expect(page.getByText('3 of 8 done')).toBeVisible();
  const payment = page.locator('.blReadyItem').filter({ hasText: 'Card payment provider' });
  await expect(payment).toContainText('Missing');
  await expect(payment).toContainText('Moyasar: off');
  await expect(page.locator('.blReadyItem').filter({ hasText: 'Off-site backups' })).toContainText('It has never succeeded.');
  // Missing items come first.
  await expect(page.locator('.blReadyItem').first()).toHaveClass(/missing/);
  expect(await serious(page, '.blConsole')).toEqual([]);

  // The SPF lookup runs on the server and explains the result.
  await expect(page.getByRole('textbox', { name: 'Domain' })).toHaveValue('example.com');
  await page.getByRole('textbox', { name: 'Domain' }).fill('not a domain');
  await page.getByRole('button', { name: 'Check DNS' }).click();
  await expect(page.getByText('Enter a domain such as example.com.')).toBeVisible();
  expect(sent.dns).toEqual([]);
  await page.getByRole('textbox', { name: 'Domain' }).fill('anum360os.com');
  await page.getByRole('button', { name: 'Check DNS' }).click();
  await expect(page.getByText('Allows no sender')).toBeVisible();
  await expect(page.getByText('v=spf1 ~all')).toBeVisible();
  await expect(page.getByText('Monitoring only')).toBeVisible();
  expect(sent.dns).toEqual(['anum360os.com']);

  // Each item links to where it is fixed.
  await payment.getByRole('button', { name: 'Open payment settings' }).click();
  await expect(page.getByRole('heading', { name: 'Card payments in Saudi Arabia (Moyasar, optional)' })).toBeVisible();
});

test('company details and legal facts are saved, and publishing needs the lawyer confirmation', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockBilling(page);
  const sent = await mockSelling(page);
  await openConsoleTab(page, 'Company and legal');
  await expect(page.getByRole('heading', { name: 'Company details' })).toBeVisible();
  await expect(page.getByText('7 needed fields are empty')).toBeVisible();
  await expect(page.getByText('These documents are templates.')).toBeVisible();
  await page.getByLabel(/^Legal name/).fill('Example Business Group LLC');
  await page.getByLabel(/^Privacy contact email/).fill('not an email');
  await page.getByRole('button', { name: 'Save company and legal details' }).click();
  await expect(page.getByText('Privacy contact email is not an email address.')).toBeVisible();
  expect(sent.legal).toEqual([]);
  await page.getByLabel(/^Privacy contact email/).fill('privacy@example.com');
  await page.getByLabel(/^Registration country/).fill('sa');
  await expect(page.getByLabel(/^Registration country/)).toHaveValue('SA');
  await page.getByLabel('Effective date').fill('1 December 2026');
  await page.getByLabel('Find a fact').fill('breach');
  await expect(page.getByLabel('Effective date')).toHaveCount(0);
  await expect(page.getByLabel('Hours to tell a customer about a breach')).toBeVisible();
  await page.getByLabel('Find a fact').fill('');
  expect(await serious(page, '.blConsole')).toEqual([]);
  await page.getByRole('button', { name: 'Save company and legal details' }).click();
  await expect(page.getByText('Saved. The legal pages show the new details within a minute.')).toBeVisible();
  expect(sent.legal[0]).toMatchObject({ legal_name: 'Example Business Group LLC', dpo_email: 'privacy@example.com', registration_country: 'SA',
    legal_terms: { EFFECTIVE_DATE: '1 December 2026' }, published_docs: [], lawyer_reviewed: false });
  await page.getByText(/Facts ANUM fills in from this server/).click();
  await expect(page.getByText('https://app.example').first()).toBeVisible();
});

test('the platform owner turns Moyasar on with write-only keys', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockBilling(page);
  const sent = await mockSelling(page);
  await openConsoleTab(page, 'Settings');
  const card = page.locator('form').filter({ hasText: 'Card payments in Saudi Arabia (Moyasar, optional)' });
  await expect(card.getByText('http://localhost:8000/api/v1/billing/webhooks/moyasar/anum')).toBeVisible();
  await card.getByLabel('Let customers pay open invoices by card through Moyasar').check();
  await card.getByRole('button', { name: 'Save Moyasar settings' }).click();
  await expect(card.getByText('Enter the Moyasar secret key before turning Moyasar on.')).toBeVisible();
  await card.getByLabel('Secret key').fill('pk_test_example');
  await card.getByRole('button', { name: 'Save Moyasar settings' }).click();
  await expect(card.getByText(/starts with sk_test_ or sk_live_/)).toBeVisible();
  await card.getByLabel('Secret key').fill('sk_test_example');
  await card.getByLabel('Webhook secret token').fill('example-webhook-token-value');
  await card.getByRole('button', { name: 'Save Moyasar settings' }).click();
  await expect(card.getByText('Webhook token saved')).toBeVisible();
  await expect(card.getByText('Test mode')).toBeVisible();
  expect(sent.moyasar).toEqual([{ enabled: true, secret_key: 'sk_test_example', webhook_secret: 'example-webhook-token-value' }]);
  await expect(card.getByLabel('Replace the secret key')).toHaveValue('');
});

test('a Moyasar payment is refunded with one idempotency key and a credit note', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockBilling(page);
  const sent = await mockSelling(page);
  await page.goto('/#platformBilling');
  await page.getByRole('button', { name: 'Open the billing of Acme Trading' }).click();
  await page.getByRole('button', { name: 'Open invoice INV-000001' }).click();
  const drawer = page.getByRole('dialog', { name: 'Invoice' });
  await expect(drawer.getByRole('button', { name: /Download the receipt for/ })).toBeVisible();
  await drawer.getByRole('button', { name: /Refund the payment of/ }).click();
  const form = page.getByRole('dialog', { name: 'Refund a payment on INV-000001' });
  await expect(form.getByLabel('Moyasar refunds the card now')).toBeChecked();
  await form.getByLabel('Amount (SAR)').fill('400');
  await form.getByRole('button', { name: 'Refund', exact: true }).click();
  await expect(form.getByText(/Enter an amount from 0.01 to/)).toBeVisible();
  await form.getByLabel('Amount (SAR)').fill('100');
  await form.getByLabel('Reason').fill('Goodwill credit');
  expect(await serious(page, '.dialogPanel')).toEqual([]);
  await form.getByRole('button', { name: 'Refund', exact: true }).click();
  await expect(page.getByText('The refund on INV-000001 is done and a credit note was issued.')).toBeVisible();
  await expect(drawer.getByText('CN-000001')).toBeVisible();
  await expect(drawer.getByText('Through Moyasar')).toBeVisible();
  expect(sent.refunds).toHaveLength(1);
  expect(sent.refunds[0]).toMatchObject({ amount_minor: 10000, reason: 'Goodwill credit', mode: 'provider' });
  expect((sent.refunds[0] as { idempotency_key: string }).idempotency_key).toMatch(/^refund-/);
});

test('with a UAE company, Stripe is the active provider and Moyasar is optional', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockBilling(page);
  await mockSelling(page, { stripeOn: true });
  await openConsoleTab(page, 'Ready to sell');
  const payment = page.locator('.blReadyItem').filter({ hasText: 'Card payment provider' });
  await expect(payment).toContainText('Check');
  await expect(payment).toContainText('Card payments go through Stripe (AED, SAR, USD, EUR, GBP).');
  await expect(payment).toContainText('Stripe: ready');
  await expect(payment).toContainText('Moyasar: off');
  await expect(payment).toContainText('Moyasar is optional: it needs a company registered in Saudi Arabia.');
  await expect(payment).toContainText('With a UAE trade licence, open a Stripe account');
  expect(await serious(page, '.blConsole')).toEqual([]);
});

test('the platform owner sets up Stripe from the steps, with the webhook address to copy', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockBilling(page);
  const sent = await mockSelling(page);
  await openConsoleTab(page, 'Settings');
  const stripe = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Card payments (Stripe)' }) });
  await expect(stripe.getByText('Open a Stripe account for your UAE company (trade licence)', { exact: false })).toBeVisible();
  await expect(stripe.getByText('http://localhost:8000/api/v1/billing/webhooks/stripe/anum')).toBeVisible();
  await expect(stripe.getByText(/charge\.refunded/)).toBeVisible();
  await stripe.getByLabel('Let customers pay open invoices by card').check();
  await stripe.getByLabel('Secret key', { exact: true }).fill('sk_test_example');
  await stripe.getByLabel('Webhook signing secret').fill('whsec_example');
  expect(await serious(page, '.blConsole')).toEqual([]);
  await page.getByRole('button', { name: 'Save settings' }).click();
  await expect(stripe.getByText('Webhook secret saved')).toBeVisible();
  expect(sent.settings).toHaveLength(1);
  expect(sent.settings[0]).toMatchObject({ stripe_enabled: true, stripe_secret_key: 'sk_test_example', stripe_webhook_secret: 'whsec_example' });
});

test('a Stripe payment is refunded through Stripe with one idempotency key and a credit note', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockBilling(page);
  const sent = await mockSelling(page, { provider: 'stripe', stripeOn: true });
  await page.goto('/#platformBilling');
  await page.getByRole('button', { name: 'Open the billing of Acme Trading' }).click();
  await page.getByRole('button', { name: 'Open invoice INV-000001' }).click();
  const drawer = page.getByRole('dialog', { name: 'Invoice' });
  await drawer.getByRole('button', { name: /Refund the payment of/ }).click();
  const form = page.getByRole('dialog', { name: 'Refund a payment on INV-000001' });
  await expect(form.getByLabel('Stripe refunds the card now')).toBeChecked();
  await expect(form.getByLabel('I returned it myself (a bank transfer back); record it only')).not.toBeChecked();
  await form.getByLabel('Amount (AED)').fill('150.50');
  await form.getByLabel('Reason').fill('Partial refund');
  expect(await serious(page, '.dialogPanel')).toEqual([]);
  await form.getByRole('button', { name: 'Refund', exact: true }).click();
  await expect(page.getByText('The refund on INV-000001 is done and a credit note was issued.')).toBeVisible();
  await expect(drawer.getByText('Through Stripe')).toBeVisible();
  // AED in fils: 150.50 AED is 15050.
  expect(sent.refunds).toHaveLength(1);
  expect(sent.refunds[0]).toMatchObject({ amount_minor: 15050, reason: 'Partial refund', mode: 'provider' });
  expect((sent.refunds[0] as { idempotency_key: string }).idempotency_key).toMatch(/^refund-/);
});

for (const locale of ['en', 'ar'] as const) {
  test(`the console's new tabs fit every size in ${locale}`, async ({ page }) => {
    await mockWorkspaceApi(page);
    await mockBilling(page);
    await mockSelling(page, { companySet: true, provider: 'stripe', stripeOn: true });
    if (locale === 'ar') await useSavedLocale(page, 'ar');
    for (const size of sizes) {
      await page.setViewportSize(size);
      // A fresh load per size: a hash-only goto keeps the previous size's shell state.
      await page.goto('about:blank');
      await page.goto('/#platformBilling');
      const nav = page.locator('.blTabs');
      await nav.locator('button').nth(4).click();
      await expect(page.locator('.blReady')).toBeVisible();
      expect(await noOverflow(page), `ready ${size.width}`).toBe(true);
      expect(await serious(page, '.blConsole'), `ready ${size.width}`).toEqual([]);
      await nav.locator('button').nth(3).click();
      await expect(page.locator('.blTerms')).toBeVisible();
      expect(await noOverflow(page), `company ${size.width}`).toBe(true);
      expect(await serious(page, '.blConsole'), `company ${size.width}`).toEqual([]);
      // The Stripe setup steps with the webhook address.
      await nav.locator('button').nth(2).click();
      await expect(page.locator('.blSteps code').filter({ hasText: '/webhooks/stripe/' })).toBeVisible();
      expect(await noOverflow(page), `settings ${size.width}`).toBe(true);
      expect(await serious(page, '.blConsole'), `settings ${size.width}`).toEqual([]);
      // The refund dialog of a Stripe payment.
      await nav.locator('button').nth(0).click();
      await page.locator('button[aria-label*="Acme Trading"]').first().click();
      await page.locator('button[aria-label*="INV-000001"]').first().click();
      await page.getByRole('dialog').getByRole('button', { name: locale === 'ar' ? /استرد الدفعة/ : /Refund the payment of/ }).click();
      await expect(page.locator('input[type=radio]').first()).toBeChecked();
      expect(await noOverflow(page), `refund ${size.width}`).toBe(true);
      expect(await serious(page, '.dialogPanel'), `refund ${size.width}`).toEqual([]);
    }
    if (locale === 'ar') await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  });

  test(`the privacy notice is filled from the company details, with the rights wired to the app, in ${locale}`, async ({ page }) => {
    const sent = await mockSelling(page, { companySet: true });
    if (locale === 'ar') await useSavedLocale(page, 'ar');
    for (const size of sizes) {
      await page.setViewportSize(size);
      await page.goto('/legal/privacy');
      await expect(page.locator('.companyCard')).toContainText('Example Business Group LLC');
      await expect(page.locator('.legalBody').getByText('Example Business Group LLC').first()).toBeVisible();
      await expect(page.locator('.legalBody mark.placeholder').first()).toBeVisible();
      const rights = page.locator('.rightsPanel');
      await expect(rights.locator('a[href="/#privacy"]')).toHaveCount(2);
      await expect(rights.locator('a[href="/#settings"]')).toHaveCount(2);
      expect(await noOverflow(page), `${size.width}`).toBe(true);
      expect(await serious(page), `${size.width}`).toEqual([]);
    }
    await expect(page.getByText(locale === 'en' ? 'This is a template.' : 'هذا نموذج.', { exact: false })).toBeVisible();
    // The public read carries no credentials.
    expect(sent.publicAuth.every((value) => value === null)).toBe(true);
  });
}
