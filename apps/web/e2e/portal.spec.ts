import { expect, test } from '@playwright/test';
import { CHANGE_HASH, OFFER_HASH, PORTAL_LINK, mockPortal } from './support/portalApi';
import { GATE_SIZES } from './support/gatePages';
import { sweep } from './support/sweep';
import { useSavedLocale } from './support/workspaceApi';

// The public customer portal (docs/business-portal.md) against a mocked API: the link is read from the
// URL fragment and removed from the address bar, only the bearer link is sent, the customer approves an
// offer and a change order against the version they were shown, reads an invoice and opens a request;
// plus the invalid, expired, turned-off, read-only, archived, empty, failed and Arabic states.

const open = (page: import('@playwright/test').Page) => page.goto(`/portal#access=${PORTAL_LINK}`);

test('the link leaves the address bar, and only the bearer link is sent to the portal routes', async ({ page }) => {
  const mock = await mockPortal(page);
  await open(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Customer portal' })).toBeVisible();
  await expect(page.getByText('Najd Digital Co.', { exact: true })).toBeVisible();
  await expect(page.getByText('For Sara Khan at Acme Trading')).toBeVisible();
  await expect(page).toHaveURL(/\/portal$/);
  expect(page.url()).not.toContain('access=');
  // No ANUM shell: no sidebar and no sign-in screen.
  await expect(page.getByRole('navigation', { name: 'Primary' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /sign in/i })).toHaveCount(0);
  expect(await page.evaluate(() => Object.values(window.sessionStorage).some((value) => value.includes('anum_portal.')))).toBe(true);
  expect(await page.evaluate(() => Object.values(window.localStorage).some((value) => value.includes('anum_portal.')))).toBe(false);
  const first = mock.requests[0];
  expect(first.path).toBe('/me');
  expect(first.headers.authorization).toBe(`Bearer ${PORTAL_LINK}`);
  for (const request of mock.requests) {
    expect(request.headers['x-workspace-id']).toBeUndefined();
    expect(request.headers['x-tenant-id']).toBeUndefined();
    expect(request.headers['x-user-id']).toBeUndefined();
  }
  expect(mock.leaks).toEqual([]);
  // A refresh in the same tab keeps working from the saved link.
  await page.reload();
  await expect(page.getByText('For Sara Khan at Acme Trading')).toBeVisible();
  expect(mock.leaks).toEqual([]);
});

test('the overview says what needs the customer', async ({ page }) => {
  await mockPortal(page);
  await open(page);
  const main = page.getByRole('main');
  await expect(main.getByText('1 offer waits for your decision')).toBeVisible();
  await expect(main.getByText('1 change order waits for your decision')).toBeVisible();
  await expect(main.getByText('1 invoice is open')).toBeVisible();
  await expect(main.getByText('1 invoice is past its due date.')).toBeVisible();
  await expect(main.getByText('No request is open')).toBeVisible();
  await main.getByRole('button', { name: 'See offers' }).click();
  await expect(page.getByRole('heading', { name: 'Offers' })).toBeVisible();
});

test('the customer approves an offer after a confirm step that states the amount', async ({ page }) => {
  const mock = await mockPortal(page);
  await open(page);
  await page.getByRole('navigation', { name: 'Portal sections' }).getByRole('button', { name: /Offers/ }).click();
  await expect(page.getByRole('row').or(page.getByRole('listitem')).filter({ hasText: 'Old maintenance offer' }).getByText('Expired')).toBeVisible();
  await page.getByRole('button', { name: 'Website rebuild' }).click();
  const offer = page.getByRole('article');
  await expect(offer.getByRole('heading', { name: 'Website rebuild' })).toBeFocused();
  await expect(offer.getByText('Design, build and launch of 8 pages.')).toBeVisible();
  await expect(offer.getByRole('row', { name: /Build/ })).toBeVisible();
  await expect(offer.getByRole('row', { name: /Design approved/ })).toBeVisible();
  await expect(offer.locator('dl[aria-label="Totals"]')).toContainText('SAR 5,750.00');
  await expect(offer.getByText(/valid until/)).toBeVisible();
  // Never an internal id.
  await expect(page.locator('body')).not.toContainText('bprop_1');

  await offer.getByRole('button', { name: 'Approve' }).click();
  await expect(offer.getByText('Type your full name, at least two characters.')).toBeVisible();
  await offer.getByLabel('Your full name').fill('Sara Khan');
  await offer.getByLabel('A note for the business (optional)').fill('Please start in November.');
  await offer.getByRole('button', { name: 'Approve' }).click();
  const confirm = offer.getByRole('form', { name: 'Confirm your approval' });
  await expect(confirm).toContainText('You approve the offer "Website rebuild" for a total of SAR 5,750.00.');
  await expect(confirm).toContainText('Signed as Sara Khan.');
  await confirm.getByRole('button', { name: 'Go back' }).click();
  await offer.getByRole('button', { name: 'Approve' }).click();
  await offer.getByRole('button', { name: 'Yes, approve' }).click();
  await expect(offer.getByRole('heading', { name: 'You approved this' })).toBeVisible();
  await expect(offer.getByText(/You approved this offer as Sara Khan on/)).toBeVisible();
  await expect(offer.getByRole('button', { name: 'Approve' })).toHaveCount(0);
  expect(mock.decisions).toEqual([{ kind: 'offer', decision: 'approved', content_hash: OFFER_HASH, signer_name: 'Sara Khan', note: 'Please start in November.' }]);
});

test('an offer that changed since it was opened is refused, reloaded and explained', async ({ page }) => {
  const mock = await mockPortal(page);
  await open(page);
  await page.getByRole('button', { name: /Offers/ }).first().click();
  await page.getByRole('button', { name: 'Website rebuild' }).click();
  const offer = page.getByRole('article');
  await offer.getByLabel('Your full name').fill('Sara Khan');
  await offer.getByRole('button', { name: 'Approve' }).click();
  mock.changeOffer();
  await offer.getByRole('button', { name: 'Yes, approve' }).click();
  await expect(offer.getByText(/This offer changed after you opened it, so nothing was recorded/)).toBeVisible();
  await expect(offer.getByText('The latest version is shown above. Read it again before you decide.')).toBeVisible();
  expect(mock.decisions).toEqual([]);
  // The reloaded offer carries the new hash and can now be decided.
  await offer.getByLabel('Your full name').fill('Sara Khan');
  await offer.getByRole('button', { name: 'Decline' }).click();
  await expect(offer.getByRole('form', { name: 'Confirm your decline' })).toContainText('You decline the offer "Website rebuild"');
  await offer.getByRole('button', { name: 'Yes, decline' }).click();
  await expect(offer.getByRole('heading', { name: 'You declined this' })).toBeVisible();
  expect(mock.decisions).toHaveLength(1);
  expect(mock.decisions[0]).toMatchObject({ decision: 'declined', content_hash: 'b2'.repeat(32) });
});

test('an expired offer cannot be approved', async ({ page }) => {
  await mockPortal(page);
  await open(page);
  await page.getByRole('button', { name: /Offers/ }).first().click();
  await page.getByRole('button', { name: 'Old maintenance offer' }).click();
  const offer = page.getByRole('article');
  await expect(offer.getByText(/can no longer be approved\. Ask the business for a new one/)).toBeVisible();
  await expect(offer.getByRole('button', { name: 'Approve' })).toHaveCount(0);
});

test('the customer approves a change order, and a stale one is explained', async ({ page }) => {
  const mock = await mockPortal(page);
  await open(page);
  await page.getByRole('navigation', { name: 'Portal sections' }).getByRole('button', { name: /Change orders/ }).click();
  await expect(page.getByText('Out of date').first()).toBeVisible();
  await page.getByRole('button', { name: 'Change order 1: Extra pages' }).click();
  const change = page.getByRole('article');
  await expect(change.getByText('Add two more pages for services.')).toBeVisible();
  await expect(change.getByRole('row', { name: /Services page/ })).toContainText('Added');
  await expect(change.locator('dl[aria-label="Totals"]')).toContainText('+SAR 575.00');
  await expect(change.locator('dl[aria-label="Totals"]')).toContainText('+5 days');
  await change.getByLabel('Your full name').fill('Sara Khan');
  await change.getByRole('button', { name: 'Approve' }).click();
  await expect(change.getByRole('form', { name: 'Confirm your approval' })).toContainText('You approve Change order 1: Extra pages, which changes the price by +SAR 575.00.');
  await change.getByRole('button', { name: 'Yes, approve' }).click();
  await expect(change.getByRole('heading', { name: 'You approved this' })).toBeVisible();
  expect(mock.decisions).toEqual([{ kind: 'change', decision: 'approved', content_hash: CHANGE_HASH, signer_name: 'Sara Khan', note: '' }]);
  await change.getByRole('button', { name: 'Back to change orders' }).click();
  await page.getByRole('button', { name: 'Change order 2: Blog section' }).click();
  await expect(page.getByRole('article').getByText(/The contract changed after this change order was sent, so it cannot be approved/)).toBeVisible();
  await expect(page.getByRole('article').getByRole('button', { name: 'Approve' })).toHaveCount(0);
});

test('invoices show the bank details, the payments and a printable copy through a blob URL', async ({ page }) => {
  const mock = await mockPortal(page);
  await open(page);
  await page.getByRole('navigation', { name: 'Portal sections' }).getByRole('button', { name: /Invoices/ }).click();
  await expect(page.getByRole('listitem').filter({ hasText: 'INV-0001' })).toContainText('Overdue, 26 days late');
  await expect(page.getByRole('listitem').filter({ hasText: 'INV-0002' })).toContainText('Paid');
  await expect(page.getByRole('listitem').filter({ hasText: 'INV-0003' })).toContainText('Void');
  await page.getByRole('button', { name: 'INV-0001' }).click();
  const invoice = page.getByRole('article');
  await expect(invoice.getByRole('heading', { name: 'Invoice INV-0001' })).toBeFocused();
  await expect(invoice.getByText(/was due on .* and is 26 days late/)).toBeVisible();
  await expect(invoice.locator('dl[aria-label="Totals"]')).toContainText('SAR 750.00');
  await expect(invoice.getByText('IBAN SA0000000000000000000000')).toBeVisible();
  await expect(invoice.getByText(/There is no online payment here/)).toBeVisible();
  await expect(invoice.getByText('TT-5')).toBeVisible();
  await expect(page.locator('body')).not.toContainText('binv_1');
  await invoice.getByLabel('Language of the copy').selectOption('ar');
  const popup = page.waitForEvent('popup');
  await invoice.getByRole('button', { name: 'Print or save as PDF' }).click();
  const printable = await popup;
  await printable.waitForURL(/^blob:/);
  await expect(printable.getByRole('heading', { name: 'Invoice binv_1' })).toBeVisible();
  expect(mock.requests.some((request) => request.path === '/invoices/binv_1/print' && request.headers.authorization === `Bearer ${PORTAL_LINK}`)).toBe(true);
  await invoice.getByRole('button', { name: 'Back to invoices' }).click();
  await page.getByRole('button', { name: 'INV-0003' }).click();
  await expect(page.getByRole('article').getByText('This invoice was cancelled by the business: Issued twice')).toBeVisible();
  await expect(page.getByRole('article').getByText('How to pay')).toHaveCount(0);
});

test('the customer opens a request and reads the replies they were sent', async ({ page }) => {
  const mock = await mockPortal(page);
  await open(page);
  await page.getByRole('navigation', { name: 'Portal sections' }).getByRole('button', { name: /Requests/ }).click();
  await page.getByRole('button', { name: 'Logo is cut off on mobile' }).click();
  const old = page.getByRole('article');
  await expect(old.getByText('We fixed the header and it is live.')).toBeVisible();
  await expect(old.getByText('Thanks, we have fixed it. Please check on your phone.')).toBeVisible();
  await old.getByRole('button', { name: 'Back to requests' }).click();

  await page.getByRole('button', { name: 'New request' }).click();
  const form = page.getByRole('form', { name: 'New request' });
  await form.getByRole('button', { name: 'Send request' }).click();
  await expect(form.getByText('Give the request a title of at least 3 characters.')).toBeVisible();
  await form.getByLabel('Short title').fill('Add a contact form');
  await form.getByLabel('What happened or what do you need?').fill('Please add a contact form to the footer.');
  await form.getByLabel('How urgent is it?').selectOption('high');
  await form.getByLabel('Project (optional)').selectOption({ label: 'Website rebuild' });
  await form.getByRole('button', { name: 'Send request' }).click();
  await expect(page.getByText('Your request was sent. Its reference is T-1002.')).toBeVisible();
  await expect(page.getByRole('article').getByRole('heading', { name: 'Add a contact form' })).toBeVisible();
  await expect(page.getByRole('article').getByText('No reply yet.')).toBeVisible();
  expect(mock.requests.find((request) => request.method === 'POST' && request.path === '/tickets')?.body).toEqual({ title: 'Add a contact form', description: 'Please add a contact form to the footer.', severity: 'high', project_id: 'bprj_1' });
});

test('a read-only link reads everything but hides the approve buttons and says why', async ({ page }) => {
  await mockPortal(page, { canDecide: false });
  await open(page);
  await expect(page.getByText(/This link is read only\. You can read everything/)).toBeVisible();
  await page.getByRole('button', { name: /Offers/ }).first().click();
  await page.getByRole('button', { name: 'Website rebuild' }).click();
  const offer = page.getByRole('article');
  await expect(offer.getByText(/Your link is read only, so approving and declining are turned off/)).toBeVisible();
  await expect(offer.getByRole('button', { name: 'Approve' })).toHaveCount(0);
  await expect(offer.getByLabel('Your full name')).toHaveCount(0);
});

test('an archived business still shows everything and says so when a decision is refused', async ({ page }) => {
  await mockPortal(page, { archived: true });
  await open(page);
  await page.getByRole('button', { name: /Offers/ }).first().click();
  await page.getByRole('button', { name: 'Website rebuild' }).click();
  const offer = page.getByRole('article');
  await offer.getByLabel('Your full name').fill('Sara Khan');
  await offer.getByRole('button', { name: 'Approve' }).click();
  await offer.getByRole('button', { name: 'Yes, approve' }).click();
  await expect(offer.getByText(/This business has paused its account, so nothing can be changed now/)).toBeVisible();
});

for (const [link, sentence] of [
  ['unknown', 'This link is not valid.'],
  ['expired', 'This link has expired.'],
  ['revoked', 'This link was turned off.'],
] as const) {
  test(`a ${link} link says so, tells the customer what to do, and does not retry`, async ({ page }) => {
    const mock = await mockPortal(page, { link });
    await open(page);
    await expect(page.getByRole('heading', { name: 'This link does not work' })).toBeVisible();
    await expect(page.getByText(sentence, { exact: false }).first()).toBeVisible();
    await expect(page.getByText('Ask the business that sent it for a new link. Reloading will not help.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Try again' })).toHaveCount(0);
    await expect(page).toHaveURL(/\/portal$/);
    await page.waitForTimeout(400);
    expect(mock.requests.length).toBe(1);
    // The saved link is dropped so a refresh does not keep asking with a dead credential.
    expect(await page.evaluate(() => Object.values(window.sessionStorage).some((value) => value.includes('anum_portal.')))).toBe(false);
  });
}

test('opening the portal without a link explains that it needs the link', async ({ page }) => {
  const mock = await mockPortal(page);
  await page.goto('/portal');
  await expect(page.getByText('Open the link you were sent.')).toBeVisible();
  expect(mock.requests).toEqual([]);
});

test('empty sections are friendly and each has its own next step', async ({ page }) => {
  await mockPortal(page, { empty: true });
  await open(page);
  await expect(page.getByText('Nothing needs you right now.')).toBeVisible();
  const nav = page.getByRole('navigation', { name: 'Portal sections' });
  await nav.getByRole('button', { name: 'Offers' }).click();
  await expect(page.getByRole('heading', { name: 'No offers yet' })).toBeVisible();
  await nav.getByRole('button', { name: 'Change orders' }).click();
  await expect(page.getByRole('heading', { name: 'No change orders' })).toBeVisible();
  await nav.getByRole('button', { name: 'Invoices' }).click();
  await expect(page.getByRole('heading', { name: 'No invoices yet' })).toBeVisible();
  await nav.getByRole('button', { name: 'Requests' }).click();
  await expect(page.getByRole('heading', { name: 'No requests yet' })).toBeVisible();
  await page.getByRole('main').getByRole('button', { name: 'New request' }).click();
  await expect(page.getByRole('form', { name: 'New request' })).toBeVisible();
});

test('a failed list shows what failed and offers a retry', async ({ page }) => {
  await mockPortal(page, { failList: 'invoices' });
  await open(page);
  await page.getByRole('navigation', { name: 'Portal sections' }).getByRole('button', { name: /Invoices/ }).click();
  await expect(page.getByRole('alert')).toContainText('The server could not reach its database');
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('the link expiry notice appears in the last week', async ({ page }) => {
  await mockPortal(page, { expiresInDays: 3 });
  await open(page);
  await expect(page.getByText(/This link expires in 3 days, on /)).toBeVisible();
});

test('the portal reads in Arabic, right to left, and the choice switches at once', async ({ page }) => {
  await mockPortal(page);
  await open(page);
  await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
  await page.getByRole('group', { name: 'Language' }).getByRole('button', { name: 'العربية' }).click();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { level: 1, name: 'بوابة العميل' })).toBeVisible();
  await expect(page.getByText('عرض واحد ينتظر قرارك')).toBeVisible();
  await page.getByRole('navigation', { name: 'أقسام البوابة' }).getByRole('button', { name: /العروض/ }).click();
  await page.getByRole('button', { name: 'Website rebuild' }).click();
  await page.getByLabel('اسمك الكامل').fill('سارة خان');
  await page.getByRole('button', { name: 'موافقة' }).click();
  await expect(page.getByRole('form', { name: 'أكّد موافقتك' })).toContainText('أنت توافق على العرض');
});

// The UI quality gate (CLAUDE.md, Responsive UI quality gate) on every portal screen: nothing scrolls
// sideways, no control is cut off, and no serious axe violation, in English and Arabic. The sizes are set
// here, so the mobile project leaves these out (@gate, playwright.config.ts).
for (const size of GATE_SIZES) {
  test.describe(`portal screens at ${size.width}x${size.height} @gate`, () => {
    test.use({ viewport: size });
    test('every screen: layout and axe, in English and Arabic', async ({ page }) => {
      test.setTimeout(180_000);
      for (const locale of ['en', 'ar'] as const) {
        const tab = await page.context().newPage();
        await mockPortal(tab);
        await useSavedLocale(tab, locale);
        await open(tab);
        const ar = locale === 'ar';
        const nav = tab.getByRole('navigation', { name: ar ? 'أقسام البوابة' : 'Portal sections' });
        await expect(tab.getByRole('main').getByRole('list').first()).toBeVisible();
        await sweep(tab, `${locale} overview`);
        await nav.getByRole('button').nth(1).click();
        await expect(tab.getByRole('button', { name: 'Website rebuild' })).toBeVisible();
        await sweep(tab, `${locale} offers`);
        await tab.getByRole('button', { name: 'Website rebuild' }).click();
        await expect(tab.getByRole('article')).toBeVisible();
        await sweep(tab, `${locale} offer`);
        await tab.getByRole('article').getByLabel(ar ? 'اسمك الكامل' : 'Your full name').fill(ar ? 'سارة خان' : 'Sara Khan');
        await tab.getByRole('article').getByRole('button', { name: ar ? 'موافقة' : 'Approve' }).click();
        await expect(tab.getByRole('form', { name: ar ? 'أكّد موافقتك' : 'Confirm your approval' })).toBeVisible();
        await sweep(tab, `${locale} confirm`);
        await nav.getByRole('button').nth(2).click();
        await tab.getByRole('button', { name: ar ? /أمر التغيير 1/ : /Change order 1/ }).click();
        await expect(tab.getByRole('article')).toBeVisible();
        await sweep(tab, `${locale} change order`);
        await nav.getByRole('button').nth(3).click();
        await tab.getByRole('button', { name: 'INV-0001' }).click();
        await expect(tab.getByRole('article')).toBeVisible();
        await sweep(tab, `${locale} invoice`);
        await nav.getByRole('button').nth(4).click();
        await expect(tab.getByRole('button', { name: 'Logo is cut off on mobile' })).toBeVisible();
        await sweep(tab, `${locale} requests`);
        await tab.getByRole('main').getByRole('button').first().click();
        await sweep(tab, `${locale} new request`);
        await tab.close();
        const broken = await page.context().newPage();
        await mockPortal(broken, { link: 'expired' });
        await useSavedLocale(broken, locale);
        await open(broken);
        await expect(broken.getByRole('alert')).toBeVisible();
        await sweep(broken, `${locale} expired link`);
        await broken.close();
      }
    });
  });
}
