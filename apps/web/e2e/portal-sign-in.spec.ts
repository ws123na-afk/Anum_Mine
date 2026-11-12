import { expect, test, type Page } from '@playwright/test';
import { mockPortal } from './support/portalApi';
import { GATE_SIZES } from './support/gatePages';
import { sweep } from './support/sweep';
import { useSavedLocale } from './support/workspaceApi';

// Signing in to the customer portal with an emailed code (docs/business-portal.md#signing-in-with-an-emailed-code)
// against a mocked API: the business's sign-in address, the email step, the code step, the portal after it, signing
// out, the wrong-code, limit and turned-off answers, and the refunds a customer sees on an invoice.

const SCOPE = 'tenant_local.workspace_foundation';
const SESSION = 'anum_portal.tenant_local.workspace_foundation.bpsign_0123456789abcdef0123456789abcdef.SeSsIoNsEcReT0123456789abcdefghijklmnop';
const CHALLENGE = 'bpsign_0123456789abcdef0123456789abcdef';
const SENT = 'If this address may use this business\'s portal, a code is on its way. It works for 10 minutes.';

interface SignInMock { asked: unknown[]; verified: unknown[]; ended: number }

async function mockSignIn(page: Page, answer: { code?: 'off' | 'limit'; wrongFirst?: boolean } = {}): Promise<SignInMock> {
  const seen: SignInMock = { asked: [], verified: [], ended: 0 };
  let tries = 0;
  // Registered after mockPortal, so these routes answer first.
  await page.route('http://localhost:8000/api/v1/portal/sign-in/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    expect(request.headers()['x-tenant-id']).toBeUndefined();
    if (path.endsWith('/code')) {
      seen.asked.push(request.postDataJSON());
      if (answer.code === 'off') return json({ detail: 'This business does not offer signing in with a code. Open the link the business sent you, or ask it for one.' }, 403);
      if (answer.code === 'limit') return json({ detail: 'Too many codes were asked for. Wait an hour and try again, or open the link the business sent you.' }, 429);
      return json({ challenge_id: CHALLENGE, expires_in_seconds: 600, message: SENT }, 202);
    }
    if (path.endsWith('/verify')) {
      seen.verified.push(request.postDataJSON());
      tries += 1;
      if (answer.wrongFirst && tries === 1) return json({ detail: 'This code is not right, or it has expired. Check the latest email, or ask for a new code.' }, 401);
      return json({ token: SESSION, expires_at: new Date(Date.now() + 12 * 3_600_000).toISOString() });
    }
    if (path.endsWith('/end')) { seen.ended += 1; return route.fulfill({ status: 204, body: '' }); }
    return json({ detail: 'Not found' }, 404);
  });
  return seen;
}

const openSignIn = (page: Page) => page.goto(`/portal#signin=${SCOPE}`);

test('a customer signs in with an emailed code, uses the portal and signs out', async ({ page }) => {
  const portal = await mockPortal(page, { token: SESSION, signedInWith: 'code' });
  const signIn = await mockSignIn(page, { wrongFirst: true });
  await openSignIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Sign in with an emailed code' })).toBeFocused();
  await page.getByRole('button', { name: 'Email me a code' }).click();
  await expect(page.getByText('Enter your email address in full, like name@company.com.')).toBeVisible();
  expect(signIn.asked).toEqual([]);
  await page.getByLabel('Email address').fill('sara@acme.example');
  await page.getByRole('button', { name: 'Email me a code' }).click();
  await expect(page.getByText(SENT)).toBeVisible();
  expect(signIn.asked).toEqual([{ workspace: SCOPE, email: 'sara@acme.example' }]);
  const code = page.getByLabel('Six-digit code');
  await expect(code).toBeFocused();
  await expect(code).toHaveAttribute('autocomplete', 'one-time-code');
  await code.fill('12345');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByText('Enter the six digits from the email.')).toBeVisible();
  await code.fill('123 456');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('This code is not right, or it has expired.');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByText('For Sara Khan at Acme Trading')).toBeVisible();
  expect(signIn.verified.at(-1)).toEqual({ workspace: SCOPE, challenge_id: CHALLENGE, code: '123456' });
  expect(portal.requests.every((request) => request.headers.authorization === `Bearer ${SESSION}`)).toBe(true);
  expect(portal.leaks).toEqual([]);
  // The session is a credential like a link: kept only for this tab.
  expect(await page.evaluate(() => Object.values(window.localStorage).some((value) => value.includes('anum_portal.')))).toBe(false);
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Sign in with an emailed code' })).toBeVisible();
  expect(signIn.ended).toBe(1);
  expect(await page.evaluate(() => Object.values(window.sessionStorage).some((value) => value.includes('anum_portal.')))).toBe(false);
});

test('another address, a new code, the limit and a business that turned it off', async ({ page }) => {
  await mockPortal(page, { token: SESSION });
  await mockSignIn(page, { code: 'limit' });
  await openSignIn(page);
  await page.getByLabel('Email address').fill('sara@acme.example');
  await page.getByRole('button', { name: 'Email me a code' }).click();
  await expect(page.getByRole('alert')).toContainText('Too many codes were asked for.');

  const other = await page.context().newPage();
  await mockPortal(other, { token: SESSION });
  await mockSignIn(other, { code: 'off' });
  await openSignIn(other);
  await other.getByLabel('Email address').fill('sara@acme.example');
  await other.getByRole('button', { name: 'Email me a code' }).click();
  await expect(other.getByRole('alert')).toContainText('This business does not offer signing in with a code.');
});

test('a link sign-in has no Sign out, and an invoice shows a card refund', async ({ page }) => {
  await mockPortal(page, { refunded: true });
  await page.goto('/portal#access=anum_portal.tenant_local.workspace_foundation.bpacc_9.AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd');
  await expect(page.getByText('For Sara Khan at Acme Trading')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sign out' })).toHaveCount(0);
  await page.getByRole('navigation', { name: 'Portal sections' }).getByRole('button', { name: /Invoices/ }).click();
  await page.getByRole('button', { name: /INV-0002/ }).click();
  await expect(page.getByRole('heading', { name: 'Refunded to your card' })).toBeVisible();
  await expect(page.getByText(/SAR\s?200\.00/).first()).toBeVisible();
});

for (const size of GATE_SIZES) {
  test.describe(`portal sign-in at ${size.width}x${size.height} @gate`, () => {
    test.use({ viewport: size });
    test('both steps: layout and axe, in English and Arabic', async ({ page }) => {
      test.setTimeout(120_000);
      for (const locale of ['en', 'ar'] as const) {
        const tab = await page.context().newPage();
        await mockPortal(tab, { token: SESSION, signedInWith: 'code' });
        await mockSignIn(tab);
        await useSavedLocale(tab, locale);
        await openSignIn(tab);
        const ar = locale === 'ar';
        await expect(tab.getByRole('heading', { level: 1, name: ar ? 'الدخول برمز يصلك بالبريد' : 'Sign in with an emailed code' })).toBeVisible();
        await sweep(tab, `${locale} email step`);
        await tab.getByLabel(ar ? 'البريد الإلكتروني' : 'Email address').fill('sara@acme.example');
        await tab.getByRole('button', { name: ar ? 'أرسل لي رمزًا' : 'Email me a code' }).click();
        await expect(tab.getByLabel(ar ? 'الرمز المكوّن من ستة أرقام' : 'Six-digit code')).toBeVisible();
        await sweep(tab, `${locale} code step`);
        await tab.getByLabel(ar ? 'الرمز المكوّن من ستة أرقام' : 'Six-digit code').fill('123456');
        await tab.getByRole('button', { name: ar ? 'دخول' : 'Sign in', exact: true }).click();
        await expect(tab.getByRole('button', { name: ar ? 'خروج' : 'Sign out' })).toBeVisible();
        await sweep(tab, `${locale} signed in`);
        await tab.close();
      }
    });
  });
}
