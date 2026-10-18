import { createHash } from 'node:crypto';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';
import { mockWorkspaceApi } from './support/workspaceApi';
import { mockBilling, mockPublicPlans, plans } from './support/billingApi';

// Self-serve sign-up (docs/billing.md#self-serve-sign-up): the pricing page without sign-in, Start
// free trial into Keycloak's registration (prompt=create, mocked), a pending account creating its
// organisation, the refreshed token entering the new workspace, and the onboarding checklist on Home.
const ISSUER = 'http://oidc.anum.test/realms/anum';
const OIDC_APP = 'http://127.0.0.1:4174';
const cors = { 'access-control-allow-origin': OIDC_APP, 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' };
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

function jwt(claims: Record<string, unknown>): string {
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${part({ alg: 'RS256', typ: 'JWT', kid: 'e2e' })}.${part(claims)}.e2e-signature`;
}

interface Oidc { authorize: URLSearchParams[]; claims: Record<string, unknown>; created: unknown[]; status: string }

async function mockOidc(page: Page): Promise<Oidc> {
  const state: Oidc = { authorize: [], claims: {}, created: [], status: 'ready' };
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', headers: cors, body: JSON.stringify(body) });
  const tokens = (nonce?: string | null) => ({
    access_token: jwt({ iss: ISSUER, aud: 'anum-api', sub: 'new-user-1', email: 'founder@riyadh-tyres.example', email_verified: true, exp: Math.floor(Date.now() / 1000) + 300, ...state.claims }),
    refresh_token: 'refresh-e2e', id_token: jwt({ iss: ISSUER, aud: 'anum-web', sub: 'new-user-1', ...(nonce ? { nonce } : {}) }), token_type: 'Bearer', expires_in: 300,
  });
  await page.route(`${ISSUER}/**`, async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    const url = new URL(request.url());
    if (url.pathname.endsWith('/.well-known/openid-configuration')) {
      return json(route, { issuer: ISSUER, authorization_endpoint: `${ISSUER}/protocol/openid-connect/auth`, token_endpoint: `${ISSUER}/protocol/openid-connect/token`, end_session_endpoint: `${ISSUER}/protocol/openid-connect/logout` });
    }
    if (url.pathname.endsWith('/protocol/openid-connect/auth')) {
      state.authorize.push(url.searchParams);
      const back = new URL(url.searchParams.get('redirect_uri')!);
      back.searchParams.set('code', 'code-e2e');
      back.searchParams.set('state', url.searchParams.get('state')!);
      return route.fulfill({ status: 302, headers: { location: back.toString() } });
    }
    if (url.pathname.endsWith('/protocol/openid-connect/token')) {
      const body = new URLSearchParams(request.postData() ?? '');
      if (body.get('grant_type') === 'refresh_token') return json(route, tokens());
      const authorize = state.authorize.at(-1);
      const challenge = createHash('sha256').update(body.get('code_verifier') ?? '').digest('base64url');
      if (challenge !== authorize?.get('code_challenge')) return json(route, { error: 'invalid_grant' }, 400);
      return json(route, tokens(authorize?.get('nonce')));
    }
    return route.fulfill({ status: 404, headers: cors });
  });
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    const path = new URL(request.url()).pathname;
    if (path === '/api/v1/auth/sign-in-options') return json(route, { identity_providers: [], self_sign_up: true, privacy_policy_url: 'https://example.com/privacy' });
    if (path === '/api/v1/signup/status') return json(route, { email: 'founder@riyadh-tyres.example', email_verified: state.status !== 'verify_email', signup_enabled: true, state: state.status });
    if (path === '/api/v1/signup/organization') {
      state.created.push(request.postDataJSON());
      // Keycloak now carries the new organisation on the account; the next token names it.
      state.claims = { tenant_id: 'riyadh-tyres-a1b2c3', workspace_id: 'riyadh-tyres-a1b2c3_main' };
      return json(route, { tenant_id: 'riyadh-tyres-a1b2c3', workspace_id: 'riyadh-tyres-a1b2c3_main', trial_end: new Date(Date.now() + 14 * 86400_000).toISOString(), identity_linked: true, plan_id: 'plan_business' }, 201);
    }
    if (path === '/api/v1/billing/onboarding') return json(route, { items: [{ key: 'invite_team', done: false }, { key: 'connect_email', done: false }, { key: 'choose_plan', done: true }, { key: 'add_payment_details', done: false }], complete: false, dismissed: false });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204, headers: cors });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json(route, []);
    return json(route, { error: { message: 'not mocked' } }, 404);
  });
  return state;
}

test.describe('with Keycloak sign-in', () => {
  test.use({ baseURL: OIDC_APP });

  test('pricing renders without sign-in, and a new account starts a trial and creates its organisation', async ({ page }) => {
    const oidc = await mockOidc(page);
    const pub = { publicAuth: [] as (string | null)[] };
    await mockPublicPlans(page, pub);

    // The sign-in screen links to pricing; pricing needs no sign-in and sends no auth header.
    await page.goto('/');
    await page.getByRole('link', { name: 'See plans and pricing' }).click();
    await expect(page.getByRole('heading', { name: 'Plans and pricing' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Business' })).toBeVisible();
    await expect(page.getByText('SAR 799.00 per month')).toBeVisible();
    await expect(page.getByText('Prices exclude VAT. 15% VAT applies in Saudi Arabia.')).toBeVisible();
    expect(pub.publicAuth.length).toBeGreaterThan(0);
    expect(pub.publicAuth.every((value) => value === null)).toBe(true);
    expect((await new AxeBuilder({ page }).analyze()).violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);
    expect(await noOverflow(page)).toBe(true);

    // Start free trial: the steps, the privacy policy first, then Keycloak's registration page.
    await page.getByRole('link', { name: 'Start a free trial of Business' }).click();
    await expect(page).toHaveURL(/#signup\?plan=plan_business$/);
    await expect(page.getByRole('heading', { name: 'Start your free trial' })).toBeVisible();
    await expect(page.getByText(/You chose Business/)).toBeVisible();
    const create = page.getByRole('button', { name: 'Create account' });
    await expect(create).toBeDisabled();
    await page.getByRole('checkbox', { name: /I have read and accept the privacy policy/ }).check();
    await create.click();

    // Back as a verified account without an organisation: name it and create it.
    await expect(page.getByRole('heading', { name: 'Create your organisation' })).toBeVisible();
    expect(oidc.authorize[0].get('prompt')).toBe('create');
    await expect(page.getByLabel('Plan')).toHaveValue('plan_business');
    await expect(page.getByLabel('Country')).toHaveValue('SA');
    await page.getByLabel('Organisation name').fill('Riyadh Tyres');
    await page.getByRole('button', { name: 'Create organisation' }).click();
    await expect(page.getByText('Accept the terms to create the organisation.')).toBeVisible();
    expect(oidc.created).toEqual([]);
    await page.getByRole('checkbox', { name: /I accept the/ }).check();
    await page.getByRole('button', { name: 'Create organisation' }).click();

    // The refreshed token names the organisation: Home opens with the first-run checklist.
    await expect(page.getByRole('heading', { name: /^Good (morning|afternoon|evening)/, level: 1 })).toBeVisible();
    expect(oidc.created).toEqual([{ organization_name: 'Riyadh Tyres', plan_id: 'plan_business', country: 'SA', accept_terms: true }]);
    const checklist = page.getByRole('region', { name: 'Set up your organisation' });
    await expect(checklist).toBeVisible();
    await expect(checklist.getByText('1 of 4 done')).toBeVisible();
    await expect(checklist.getByRole('link', { name: 'Members' })).toHaveAttribute('href', '#members');
  });

  test('an account that has not verified its email is asked to, with Check again', async ({ page }) => {
    const oidc = await mockOidc(page);
    oidc.status = 'verify_email';
    await mockPublicPlans(page, { publicAuth: [] });
    await page.goto('/#signup');
    await page.getByRole('checkbox', { name: /I have read and accept the privacy policy/ }).check();
    await page.getByRole('button', { name: 'Create account' }).click();
    await expect(page.getByRole('heading', { name: 'Verify your email address' })).toBeVisible();
    await expect(page.getByText('founder@riyadh-tyres.example')).toBeVisible();
    oidc.status = 'ready';
    await page.getByRole('button', { name: 'Check again' }).click();
    await expect(page.getByRole('heading', { name: 'Create your organisation' })).toBeVisible();
  });
});

test('pricing says sign-up is not open when it is off', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockPublicPlans(page, { publicAuth: [] }, { signupEnabled: false });
  await page.goto('/#pricing');
  await expect(page.getByText('Sign-up is not open yet.')).toBeVisible();
  await expect(page.getByRole('heading', { name: plans[0].name })).toBeVisible();
  await expect(page.getByRole('link', { name: /Start a free trial/ })).toHaveCount(0);
});

test('the onboarding checklist on Home links onward and can be dismissed', async ({ page }) => {
  await mockWorkspaceApi(page);
  const sent = await mockBilling(page);
  await page.goto('/#home');
  const checklist = page.getByRole('region', { name: 'Set up your organisation' });
  await expect(checklist).toBeVisible();
  await expect(checklist.getByRole('link', { name: 'Billing contact' })).toHaveAttribute('href', '#billing-contact');
  await expect(checklist.getByRole('link', { name: 'Setup plans' })).toBeVisible();
  expect(await noOverflow(page)).toBe(true);
  await checklist.getByRole('button', { name: 'Dismiss' }).click();
  await expect(checklist).toHaveCount(0);
  expect(sent.dismissed).toBe(1);
});
