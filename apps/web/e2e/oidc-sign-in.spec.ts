import { createHash } from 'node:crypto';
import { expect, test, type Page, type Route } from '@playwright/test';

// Runs against the OIDC build served on :4174 (see playwright.config.ts). Keycloak is replaced by
// route interception, so this checks the browser side of the contract in docs/identity.md:
// authorization code + PKCE, state and nonce checks, bearer + x-workspace-id on API calls,
// token storage, and RP-initiated sign-out.
const OIDC_E2E_ISSUER = 'http://oidc.anum.test/realms/anum';
const appOrigin = 'http://127.0.0.1:4174';
const cors = { 'access-control-allow-origin': appOrigin, 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' };

test.use({ baseURL: appOrigin });

function jwt(claims: Record<string, unknown>): string {
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${part({ alg: 'RS256', typ: 'JWT', kid: 'e2e' })}.${part(claims)}.e2e-signature`;
}

interface Provider {
  authorize?: URLSearchParams;
  tokenRequests: URLSearchParams[];
  logout?: URLSearchParams;
  apiHeaders: Record<string, string>[];
  accessToken: string;
}

async function mockProviderAndApi(page: Page): Promise<Provider> {
  const accessToken = jwt({ iss: OIDC_E2E_ISSUER, aud: 'anum-api', sub: 'user-dev', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', exp: Math.floor(Date.now() / 1000) + 300 });
  const provider: Provider = { tokenRequests: [], apiHeaders: [], accessToken };
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', headers: cors, body: JSON.stringify(body) });

  await page.route(`${OIDC_E2E_ISSUER}/**`, async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    const url = new URL(request.url());
    if (url.pathname.endsWith('/.well-known/openid-configuration')) {
      return json(route, {
        issuer: OIDC_E2E_ISSUER,
        authorization_endpoint: `${OIDC_E2E_ISSUER}/protocol/openid-connect/auth`,
        token_endpoint: `${OIDC_E2E_ISSUER}/protocol/openid-connect/token`,
        end_session_endpoint: `${OIDC_E2E_ISSUER}/protocol/openid-connect/logout`,
      });
    }
    if (url.pathname.endsWith('/protocol/openid-connect/auth')) {
      provider.authorize = url.searchParams;
      const back = new URL(url.searchParams.get('redirect_uri')!);
      back.searchParams.set('code', 'code-e2e');
      back.searchParams.set('state', url.searchParams.get('state')!);
      back.searchParams.set('session_state', 'kc-session');
      return route.fulfill({ status: 302, headers: { location: back.toString() } });
    }
    if (url.pathname.endsWith('/protocol/openid-connect/token')) {
      const body = new URLSearchParams(request.postData() ?? '');
      provider.tokenRequests.push(body);
      if (body.get('grant_type') === 'refresh_token') {
        if (body.get('refresh_token') !== 'refresh-e2e') return json(route, { error: 'invalid_grant' }, 400);
        return json(route, { access_token: accessToken, refresh_token: 'refresh-e2e', id_token: jwt({ iss: OIDC_E2E_ISSUER, aud: 'anum-web', sub: 'user-dev' }), token_type: 'Bearer', expires_in: 300 });
      }
      const verifier = body.get('code_verifier') ?? '';
      const challenge = createHash('sha256').update(verifier).digest('base64url');
      if (body.get('code') !== 'code-e2e' || challenge !== provider.authorize?.get('code_challenge')) {
        return json(route, { error: 'invalid_grant', error_description: 'PKCE verification failed' }, 400);
      }
      return json(route, {
        access_token: accessToken,
        refresh_token: 'refresh-e2e',
        id_token: jwt({ iss: OIDC_E2E_ISSUER, aud: 'anum-web', sub: 'user-dev', nonce: provider.authorize?.get('nonce') }),
        token_type: 'Bearer',
        expires_in: 300,
      });
    }
    if (url.pathname.endsWith('/protocol/openid-connect/logout')) {
      provider.logout = url.searchParams;
      return route.fulfill({ status: 302, headers: { location: url.searchParams.get('post_logout_redirect_uri')! } });
    }
    return route.fulfill({ status: 404, headers: cors });
  });

  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    provider.apiHeaders.push(await request.allHeaders());
    const path = new URL(request.url()).pathname;
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204, headers: cors });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json(route, []);
    return json(route, { error: { message: 'not mocked' } }, 404);
  });
  return provider;
}

test('signs in with authorization code + PKCE, calls the API with the bearer token, and signs out', async ({ page }) => {
  const provider = await mockProviderAndApi(page);

  await page.goto('/#tasks');
  await expect(page.getByRole('heading', { name: 'Sign in to ANUM' })).toBeVisible();
  expect(provider.apiHeaders).toHaveLength(0);

  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Tasks', exact: true })).toBeVisible();

  const authorize = provider.authorize!;
  expect(authorize.get('client_id')).toBe('anum-web');
  expect(authorize.get('response_type')).toBe('code');
  expect(authorize.get('code_challenge_method')).toBe('S256');
  expect(authorize.get('redirect_uri')).toBe(`${appOrigin}/`);
  expect(authorize.get('scope')).toContain('openid');
  expect(provider.tokenRequests).toHaveLength(1);
  expect(provider.tokenRequests[0].get('grant_type')).toBe('authorization_code');

  // The code and state leave the address bar; the original route comes back.
  await expect(page).toHaveURL(`${appOrigin}/#tasks`);
  await expect(page.locator('.tenant')).toHaveText('tenant_local / workspace_foundation');

  await expect.poll(() => provider.apiHeaders.length).toBeGreaterThan(0);
  for (const headers of provider.apiHeaders) {
    expect(headers.authorization).toBe(`Bearer ${provider.accessToken}`);
    expect(headers['x-workspace-id']).toBe('workspace_foundation');
    expect(headers['x-user-id']).toBeUndefined();
    expect(headers['x-user-roles']).toBeUndefined();
  }

  const stored = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } }));
  expect(JSON.stringify(stored.local)).not.toContain(provider.accessToken);
  expect(JSON.stringify(stored.local)).not.toContain('refresh-e2e');
  expect(stored.session).toEqual({ 'anum.oidc.refresh_token': 'refresh-e2e' });

  // A reload restores the session from the tab's refresh token.
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Tasks', exact: true })).toBeVisible();
  expect(provider.tokenRequests.at(-1)?.get('grant_type')).toBe('refresh_token');
  expect(provider.tokenRequests.at(-1)?.get('refresh_token')).toBe('refresh-e2e');

  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in to ANUM' })).toBeVisible();
  expect(provider.logout?.get('client_id')).toBe('anum-web');
  expect(provider.logout?.get('post_logout_redirect_uri')).toBe(`${appOrigin}/`);
  expect(provider.logout?.get('id_token_hint')).toBeTruthy();
  expect(await page.evaluate(() => sessionStorage.length)).toBe(0);
});

test('a callback with a forged state is rejected without a token request', async ({ page }) => {
  const provider = await mockProviderAndApi(page);
  await page.goto('/?code=code-e2e&state=forged#tasks');
  await expect(page.getByRole('heading', { name: 'Sign in to ANUM' })).toBeVisible();
  await expect(page.getByText('No sign-in is in progress')).toBeVisible();
  expect(provider.tokenRequests).toHaveLength(0);
  await expect(page).toHaveURL(`${appOrigin}/`);
});
