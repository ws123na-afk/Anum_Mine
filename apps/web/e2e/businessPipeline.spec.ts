import { expect, test, type Page } from '@playwright/test';
import { SEND_HASH, mockPipeline } from './support/businessPipelineApi';

// Business Prospecting and Outreach (docs/business.md#prospecting-pipeline, #outreach) against a mocked API:
// a target profile per product, confirmed signals, a discovery run (estimate, confirm, results), a profile
// run, the lead's product fit and score suggestion, then the outbox: queue, approve with the payload hash,
// held because sending is off, and a reply that stops it.

const sections = (page: Page) => page.getByRole('navigation', { name: 'Business sections' });

test('target profile, signals, discover, profile, fit, queue, approve, held, reply', async ({ page }) => {
  const errors: string[] = [];
  // Shell endpoints outside Business are not mocked here; only Business errors and script errors count.
  page.on('console', (message) => { if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) errors.push(message.text()); });
  page.on('response', (response) => { if (response.status() >= 400 && response.url().includes('/api/v1/business')) errors.push(`${response.status()} ${response.url()}`); });
  const mock = await mockPipeline(page);
  await page.goto('/#business-prospecting');
  await expect(page.getByRole('heading', { name: 'Who each product is for' })).toBeVisible();

  // Each product has its own target customer profile.
  await page.getByRole('row', { name: /TyrePulse/ }).getByRole('button', { name: 'Edit' }).click();
  const target = page.getByRole('form', { name: 'Target profile: TyrePulse' });
  await target.getByLabel('Industries').fill('concrete, fleet, logistics');
  await target.getByLabel('Disqualifiers').fill('precast');
  await target.getByLabel('Large').check();
  await target.getByRole('button', { name: 'Save profile' }).click();
  await expect(page.getByRole('row', { name: /TyrePulse/ })).toContainText('concrete, fleet, logistics');
  const saved = mock.calls.find((call) => call.path === '/pipeline/products/bprod_tyre/target');
  expect(saved?.body).toMatchObject({ industries: ['concrete', 'fleet', 'logistics'], disqualifiers: ['precast'], company_sizes: ['large'] });

  // Nothing is collected until the owner confirms signals.
  await expect(page.getByText('No signal is confirmed yet, so profile runs cannot start.')).toBeVisible();
  await page.getByLabel(/Found in a market search/).check();
  await page.getByLabel(/Business address published on the site/).check();
  await page.getByRole('button', { name: 'Save choices' }).click();
  await expect(page.getByText('Signal choices saved.')).toBeVisible();

  // Discovery is for one product: estimate first, then confirm.
  const form = page.getByRole('form', { name: 'New run' });
  await form.getByRole('button', { name: 'Estimate' }).click();
  await expect(form.getByText('Choose the product this run is for.')).toBeVisible();
  await form.getByLabel('Product').selectOption({ label: 'TyrePulse' });
  await form.getByRole('button', { name: 'Estimate' }).click();
  const estimate = page.getByRole('group', { name: 'What this run will use at most' });
  await expect(estimate).toContainText('2 search(es), 0 page read(s), 0 model call(s)');
  await expect(estimate).toContainText('concrete UAE');
  await estimate.getByRole('button', { name: 'Confirm and run' }).click();
  await expect(page.getByText('3 result(s): 1 new lead(s), 0 already known.')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Gulf Mixers | Concrete fleet' })).toBeVisible();

  // Profile the new lead.
  await form.getByLabel('Profile leads').check();
  await form.getByLabel(/Gulf Mixers/).check();
  await form.getByRole('button', { name: 'Estimate' }).click();
  await page.getByRole('group', { name: 'What this run will use at most' }).getByRole('button', { name: 'Confirm and run' }).click();
  await expect(page.getByText(/2 page\(s\) read, 1 signal\(s\) recorded/)).toBeVisible();
  await expect(page.getByRole('cell', { name: 'info@gulfmixers.example', exact: true })).toBeVisible();

  // The best product fit and a deterministic score suggestion a person applies.
  await page.getByRole('button', { name: 'Profile and fit' }).click();
  const profile = page.getByRole('region', { name: 'Lead profile' });
  await expect(profile.getByText('Best fit: TyrePulse (4 of 5)')).toBeVisible();
  await expect(profile.getByText('Disqualified', { exact: true })).toBeVisible();
  await expect(profile.getByRole('row', { name: /Urgency/ })).toContainText('Only a conversation confirms this');
  await expect(profile.getByRole('row', { name: /Official channel/ })).toContainText('publishes a business address');

  // Outreach: sending is off; queue the approved draft, approve it with the payload hash, it is held.
  await sections(page).getByRole('button', { name: 'Outreach' }).click();
  await expect(page.getByText(/Sending is off\. Approved messages are held, not sent\./)).toBeVisible();
  await page.getByRole('button', { name: 'Queue for approval' }).click();
  await expect(page.getByText('Queued: waiting for an owner\'s approval.')).toBeVisible();
  const item = page.getByRole('group', { name: 'Send approval waiting for an owner' }).first();
  await expect(page.getByText('Waiting for approval of this exact message')).toBeVisible();
  await expect(page.locator(`code[title="${SEND_HASH}"]`)).toBeVisible();
  await item.getByRole('button', { name: 'Approve sending' }).click();
  await expect(page.getByText('Approved, but sending is turned off (Business, Outreach settings). Nothing was sent.')).toBeVisible();

  // A reply stops the lead's waiting messages.
  await page.getByRole('button', { name: 'Record reply' }).click();
  const reply = page.getByRole('form', { name: 'Record reply' });
  await reply.getByLabel(/Reply text/).fill('Interesting, call me on Sunday.');
  await reply.getByRole('button', { name: 'Record reply' }).click();
  await expect(page.getByText('Reply recorded: 1 waiting message(s) cancelled.')).toBeVisible();
  await expect(page.getByText('The lead replied (stop-on-reply).')).toBeVisible();
  expect(mock.calls.some((call) => call.path.endsWith('/send'))).toBe(false);
  expect(errors).toEqual([]);
});

test('a member reads target profiles and signals but cannot change them', async ({ page }) => {
  await mockPipeline(page, { role: 'member' });
  await page.goto('/#business-prospecting');
  await expect(page.getByText('Only workspace owners change target profiles.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Edit' })).toHaveCount(0);
  await expect(page.getByText('Only workspace owners choose what is collected.')).toBeVisible();
  await sections(page).getByRole('button', { name: 'Outreach' }).click();
  await expect(page.getByText('Only workspace owners change outreach settings.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Turn sending on' })).toHaveCount(0);
});

test('a failed load says what failed and offers a retry', async ({ page }) => {
  await mockPipeline(page, { status: 500 });
  await page.goto('/#business-prospecting');
  await expect(page.getByRole('heading', { name: 'Business could not be loaded' })).toBeVisible();
  await expect(page.getByText(/Database unavailable/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
  test(`prospecting and outreach fit at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockPipeline(page);
    for (const hash of ['#business-prospecting', '#business-outreach']) {
      await page.goto(`/${hash}`);
      await expect(page.getByRole('heading', { level: 1, name: 'Business' })).toBeVisible();
      await page.waitForLoadState('networkidle');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    }
  });
}

test('Prospecting works in Arabic, right to left', async ({ page }) => {
  await mockPipeline(page);
  await page.addInitScript(() => localStorage.setItem('anum.locale', 'ar'));
  await page.goto('/#business-prospecting');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'لمن كل منتج' })).toBeVisible();
});
