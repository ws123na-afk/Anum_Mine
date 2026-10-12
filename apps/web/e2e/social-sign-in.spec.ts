import { createHash } from 'node:crypto';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';

// "Continue with Google / Apple", "Create account" and the pending-account screen
// (docs/identity.md#google-and-apple-sign-in), against the OIDC build on :4174. Keycloak, and
// the Google or Apple page behind it, are replaced by route interception: the mocked
// authorization endpoint answers as Keycloak would after the provider sent the person back.
const ISSUER = 'http://oidc.anum.test/realms/anum';
const appOrigin = 'http://127.0.0.1:4174';
const cors = { 'access-control-allow-origin': appOrigin, 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' };
const PRIVACY = 'https://example.com/privacy';

test.use({ baseURL: appOrigin });

function jwt(claims: Record<string, unknown>): string {
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${part({ alg: 'RS256', typ: 'JWT', kid: 'e2e' })}.${part(claims)}.e2e-signature`;
}

interface Mock {
  authorize: URLSearchParams[];
  apiPaths: string[];
  /** Claims the next token response carries; tests change it to simulate a tenant assignment. */
  claims: Record<string, unknown>;
}

const assigned = { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' };

async function mock(page: Page, options: unknown, claims: Record<string, unknown>): Promise<Mock> {
  const state: Mock = { authorize: [], apiPaths: [], claims };
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', headers: cors, body: JSON.stringify(body) });
  const tokens = (nonce?: string | null) => ({
    access_token: jwt({ iss: ISSUER, aud: 'anum-api', sub: 'google-user-1', email: 'new.person@example.com', email_verified: true, exp: Math.floor(Date.now() / 1000) + 300, ...state.claims }),
    refresh_token: 'refresh-e2e',
    id_token: jwt({ iss: ISSUER, aud: 'anum-web', sub: 'google-user-1', ...(nonce ? { nonce } : {}) }),
    token_type: 'Bearer',
    expires_in: 300,
  });
  await page.route(`${ISSUER}/**`, async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    const url = new URL(request.url());
    if (url.pathname.endsWith('/.well-known/openid-configuration')) {
      return json(route, {
        issuer: ISSUER,
        authorization_endpoint: `${ISSUER}/protocol/openid-connect/auth`,
        token_endpoint: `${ISSUER}/protocol/openid-connect/token`,
        end_session_endpoint: `${ISSUER}/protocol/openid-connect/logout`,
      });
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
    if (url.pathname.endsWith('/protocol/openid-connect/logout')) {
      return route.fulfill({ status: 302, headers: { location: url.searchParams.get('post_logout_redirect_uri')! } });
    }
    return route.fulfill({ status: 404, headers: cors });
  });
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    const path = new URL(request.url()).pathname;
    state.apiPaths.push(path);
    if (path === '/api/v1/auth/sign-in-options') {
      return options === 'fail' ? json(route, { error: { code: 'service_unavailable', message: 'API is restarting' } }, 503) : json(route, options);
    }
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204, headers: cors });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json(route, []);
    return json(route, { error: { message: 'not mocked' } }, 404);
  });
  return state;
}

const everything = { identity_providers: [{ id: 'google', label: 'Google' }, { id: 'apple', label: 'Apple' }], self_sign_up: true, privacy_policy_url: PRIVACY };

test('Continue with Google sends kc_idp_hint on the code + PKCE request and signs in', async ({ page }) => {
  const state = await mock(page, everything, assigned);
  await page.goto('/#tasks');
  await expect(page.getByRole('button', { name: 'Continue with Google' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue with Apple' })).toBeVisible();
  await page.getByRole('button', { name: 'Continue with Google' }).click();
  await expect(page.getByRole('heading', { name: 'Tasks', exact: true })).toBeVisible();
  const authorize = state.authorize[0];
  expect(authorize.get('kc_idp_hint')).toBe('google');
  expect(authorize.get('code_challenge_method')).toBe('S256');
  expect(authorize.get('client_id')).toBe('anum-web');
});

test('only configured providers get a button, and no create-account without a privacy policy', async ({ page }) => {
  await mock(page, { identity_providers: [{ id: 'apple', label: 'Apple' }], self_sign_up: true, privacy_policy_url: null }, assigned);
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Continue with Apple' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue with Google' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Sign in with email or username' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create account' })).toHaveCount(0);
});

test('create account needs the privacy policy accepted, then opens the sign-up page', async ({ page }) => {
  const state = await mock(page, everything, assigned);
  await page.goto('/');
  await page.getByRole('button', { name: 'Create account' }).click();
  await expect(page.getByRole('heading', { name: 'Create your ANUM account' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'privacy policy' })).toHaveAttribute('href', PRIVACY);
  const email = page.getByRole('button', { name: 'Sign up with email' });
  await expect(email).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Sign up with Google' })).toBeDisabled();
  await expect(page.getByText('Accept the privacy policy to continue.')).toBeVisible();
  await page.getByRole('checkbox', { name: /I have read and accept the privacy policy/ }).check();
  await expect(email).toBeEnabled();
  await email.click();
  await expect(page.getByRole('heading', { name: 'ANUM Command Center', level: 1 })).toBeVisible();
  await expect(page).toHaveURL(`${appOrigin}/`);
  expect(state.authorize[0].get('prompt')).toBe('create');
  expect(state.authorize[0].get('kc_idp_hint')).toBeNull();
});

test('a new account without a tenant waits on the pending screen until it is assigned', async ({ page }) => {
  const state = await mock(page, everything, {});
  await page.goto('/');
  await page.getByRole('button', { name: 'Continue with Apple' }).click();
  await expect(page.getByRole('heading', { name: 'Your account is waiting for access' })).toBeVisible();
  await expect(page.getByText('new.person@example.com')).toBeVisible();
  await expect(page.getByText('google-user-1')).toBeVisible();
  // Nothing tenant-scoped is called for a pending account.
  expect(state.apiPaths.filter((path) => path !== '/api/v1/auth/sign-in-options')).toEqual([]);

  await page.getByRole('button', { name: 'Check again' }).click();
  await expect(page.getByText('Still waiting: no organization has been assigned to this account yet.')).toBeVisible();

  // An administrator assigns the tenant; the next token carries it.
  state.claims = assigned;
  await page.getByRole('button', { name: 'Check again' }).click();
  await expect(page.getByRole('heading', { name: 'ANUM Command Center', level: 1 })).toBeVisible();
});

test('the pending screen signs out', async ({ page }) => {
  await mock(page, everything, {});
  await page.goto('/');
  await page.getByRole('button', { name: 'Continue with Google' }).click();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in to ANUM' })).toBeVisible();
});

test('when the options cannot be loaded the plain sign-in still works', async ({ page }) => {
  const state = await mock(page, 'fail', assigned);
  await page.goto('/#tasks');
  await expect(page.getByText(/Other sign-in options could not be loaded/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue with Google' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Tasks', exact: true })).toBeVisible();
  expect(state.authorize[0].get('kc_idp_hint')).toBeNull();
});

async function seriousViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  return results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
}

test('the sign-in, create-account and pending screens pass axe and work from the keyboard', async ({ page }) => {
  await mock(page, everything, {});
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Continue with Google' })).toBeVisible();
  expect(await seriousViolations(page)).toEqual([]);
  await page.getByRole('button', { name: 'Create account' }).focus();
  await page.keyboard.press('Enter');
  const consent = page.getByRole('checkbox', { name: /I have read and accept the privacy policy/ });
  await expect(consent).toBeVisible();
  expect(await seriousViolations(page)).toEqual([]);
  await consent.focus();
  await page.keyboard.press('Space');
  await expect(page.getByRole('button', { name: 'Sign up with Apple' })).toBeEnabled();
  await page.getByRole('button', { name: 'Sign up with Apple' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Your account is waiting for access' })).toBeVisible();
  expect(await seriousViolations(page)).toEqual([]);
});
