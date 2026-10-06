// Unit tests for the PKCE and state handling in src/lib/oidc.ts.
// Run with `pnpm --filter @anum/web test:unit` (Node's test runner with type stripping).
import assert from 'node:assert/strict';
import { createHash, webcrypto } from 'node:crypto';
import { beforeEach, describe, test } from 'node:test';
import {
  OidcClient,
  OidcError,
  PENDING_KEY,
  PENDING_TTL_MS,
  REFRESH_KEY,
  base64UrlEncode,
  decodeJwtClaims,
  isLoopbackRedirect,
  oidcConfigFromEnv,
  pkceChallenge,
  randomToken,
  type KeyValueStore,
  type OidcConfig,
} from '../src/lib/oidc.ts';
import { desktopShell } from '../src/lib/desktop.ts';

const issuer = 'https://id.example.test/realms/anum';
const config: OidcConfig = {
  issuer,
  clientId: 'anum-web',
  redirectUri: 'http://localhost:5173/',
  postLogoutRedirectUri: 'http://localhost:5173/',
  scope: 'openid profile email',
};
const metadata = {
  issuer,
  authorization_endpoint: `${issuer}/protocol/openid-connect/auth`,
  token_endpoint: `${issuer}/protocol/openid-connect/token`,
  end_session_endpoint: `${issuer}/protocol/openid-connect/logout`,
};

class MemoryStore implements KeyValueStore {
  readonly values = new Map<string, string>();
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}

function jwt(claims: Record<string, unknown>): string {
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${part({ alg: 'RS256', typ: 'JWT' })}.${part(claims)}.signature`;
}

interface Recorded { url: string; body: URLSearchParams | null }

function harness(tokenResponse: (body: URLSearchParams) => { status: number; body: unknown }) {
  const store = new MemoryStore();
  const calls: Recorded[] = [];
  let now = 1_700_000_000_000;
  const fetch = async (url: string, init?: RequestInit) => {
    const body = typeof init?.body === 'string' ? new URLSearchParams(init.body) : null;
    calls.push({ url, body });
    if (url === `${issuer}/.well-known/openid-configuration`) return Response.json(metadata);
    if (url === metadata.token_endpoint && body) {
      const result = tokenResponse(body);
      return Response.json(result.body, { status: result.status });
    }
    return new Response('not found', { status: 404 });
  };
  const client = new OidcClient(config, { fetch, storage: store, crypto: webcrypto as unknown as Crypto, now: () => now });
  return { client, store, calls, advance: (ms: number) => { now += ms; } };
}

async function startSignIn(client: OidcClient, store: MemoryStore, returnTo = '#tasks') {
  const authorizeUrl = new URL(await client.beginSignIn(returnTo));
  const pending = JSON.parse(store.getItem(PENDING_KEY) ?? 'null') as { state: string; nonce: string; codeVerifier: string };
  return { authorizeUrl, pending };
}

describe('PKCE primitives', () => {
  test('base64url encoding has no padding or unsafe characters', () => {
    assert.equal(base64UrlEncode(new Uint8Array([251, 255, 191])), '-_-_');
    assert.equal(base64UrlEncode(new Uint8Array([1])), 'AQ');
  });

  test('verifiers are 43+ characters from the unreserved set and unique', () => {
    const a = randomToken(webcrypto as unknown as Crypto);
    const b = randomToken(webcrypto as unknown as Crypto);
    assert.match(a, /^[A-Za-z0-9_-]{43,128}$/);
    assert.notEqual(a, b);
  });

  test('S256 challenge matches RFC 7636 Appendix B', async () => {
    const verifier = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk';
    assert.equal(await pkceChallenge(verifier, webcrypto as unknown as Crypto), 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM');
    assert.equal(await pkceChallenge(verifier, webcrypto as unknown as Crypto), createHash('sha256').update(verifier).digest('base64url'));
  });

  test('JWT claims decode without verification and tolerate garbage', () => {
    assert.deepEqual(decodeJwtClaims(jwt({ tenant_id: 'tenant_local' })), { tenant_id: 'tenant_local' });
    assert.deepEqual(decodeJwtClaims('not-a-jwt'), {});
    assert.deepEqual(decodeJwtClaims('a.!!!.c'), {});
  });
});

describe('configuration', () => {
  const location = { origin: 'http://localhost:5173', pathname: '/' };

  test('unset issuer keeps the local session', () => {
    assert.equal(oidcConfigFromEnv({}, location), null);
    assert.equal(oidcConfigFromEnv({ VITE_ANUM_OIDC_ISSUER: '  ' }, location), null);
  });

  test('issuer and client come from the environment, redirect from the page', () => {
    const value = oidcConfigFromEnv({ VITE_ANUM_OIDC_ISSUER: `${issuer}/`, VITE_ANUM_OIDC_CLIENT_ID: 'anum-desktop' }, location);
    assert.deepEqual(value, { issuer, clientId: 'anum-desktop', redirectUri: 'http://localhost:5173/', postLogoutRedirectUri: 'http://localhost:5173/', scope: 'openid profile email' });
    assert.equal(oidcConfigFromEnv({ VITE_ANUM_OIDC_ISSUER: issuer }, location)?.clientId, 'anum-web');
  });
});

describe('authorization code flow', () => {
  let h: ReturnType<typeof harness>;
  // The nonce the fake provider puts in the ID token; tests set it to the one the client sent.
  let providerNonce = '';
  beforeEach(() => {
    providerNonce = '';
    h = harness((body) => body.get('grant_type') === 'authorization_code'
      ? { status: 200, body: { access_token: jwt({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' }), refresh_token: 'refresh-1', id_token: jwt({ nonce: providerNonce }), expires_in: 300, token_type: 'Bearer' } }
      : { status: 400, body: { error: 'unsupported_grant_type' } });
  });

  test('authorization request carries S256 challenge, state and nonce', async () => {
    const { authorizeUrl, pending } = await startSignIn(h.client, h.store);
    assert.equal(authorizeUrl.origin + authorizeUrl.pathname, metadata.authorization_endpoint);
    const params = authorizeUrl.searchParams;
    assert.equal(params.get('response_type'), 'code');
    assert.equal(params.get('client_id'), 'anum-web');
    assert.equal(params.get('redirect_uri'), config.redirectUri);
    assert.equal(params.get('code_challenge_method'), 'S256');
    assert.equal(params.get('state'), pending.state);
    assert.equal(params.get('nonce'), pending.nonce);
    assert.equal(params.get('code_challenge'), createHash('sha256').update(pending.codeVerifier).digest('base64url'));
    assert.equal(params.get('code_verifier'), null, 'the verifier never leaves the browser in the redirect');
  });

  test('callback exchanges the code with the verifier and keeps only the refresh token', async () => {
    const { pending } = await startSignIn(h.client, h.store);
    providerNonce = pending.nonce;
    const result = await h.client.completeSignIn(new URL(`${config.redirectUri}?code=abc&state=${pending.state}`));
    const tokenCall = h.calls.find((call) => call.url === metadata.token_endpoint);
    assert.equal(tokenCall?.body?.get('code'), 'abc');
    assert.equal(tokenCall?.body?.get('code_verifier'), pending.codeVerifier);
    assert.equal(tokenCall?.body?.get('client_id'), 'anum-web');
    assert.equal(result.returnTo, '#tasks');
    assert.equal(result.tokens.claims.tenant_id, 'tenant_local');
    assert.equal(h.store.getItem(PENDING_KEY), null);
    assert.deepEqual([...h.store.values.keys()], [REFRESH_KEY]);
    assert.equal(h.store.getItem(REFRESH_KEY), 'refresh-1');
  });

  test('a state mismatch is rejected before any token request', async () => {
    await startSignIn(h.client, h.store);
    await assert.rejects(h.client.completeSignIn(new URL(`${config.redirectUri}?code=abc&state=forged`)), (error: unknown) => error instanceof OidcError && error.code === 'state_mismatch');
    assert.equal(h.calls.some((call) => call.url === metadata.token_endpoint), false);
  });

  test('the pending request is single use', async () => {
    const { pending } = await startSignIn(h.client, h.store);
    providerNonce = pending.nonce;
    await h.client.completeSignIn(new URL(`${config.redirectUri}?code=abc&state=${pending.state}`));
    await assert.rejects(h.client.completeSignIn(new URL(`${config.redirectUri}?code=abc&state=${pending.state}`)), (error: unknown) => error instanceof OidcError && error.code === 'no_pending_request');
  });

  test('a callback without a started sign-in is rejected', async () => {
    await assert.rejects(h.client.completeSignIn(new URL(`${config.redirectUri}?code=abc&state=x`)), (error: unknown) => error instanceof OidcError && error.code === 'no_pending_request');
  });

  test('an expired pending request is rejected', async () => {
    const { pending } = await startSignIn(h.client, h.store);
    h.advance(PENDING_TTL_MS + 1);
    await assert.rejects(h.client.completeSignIn(new URL(`${config.redirectUri}?code=abc&state=${pending.state}`)), (error: unknown) => error instanceof OidcError && error.code === 'request_expired');
  });

  test('provider errors surface after the state check', async () => {
    const { pending } = await startSignIn(h.client, h.store);
    await assert.rejects(h.client.completeSignIn(new URL(`${config.redirectUri}?error=access_denied&error_description=Denied&state=${pending.state}`)), (error: unknown) => error instanceof OidcError && error.code === 'access_denied' && error.message === 'Denied');
  });

  test('an ID token with the wrong nonce is rejected and nothing is kept', async () => {
    const { pending } = await startSignIn(h.client, h.store);
    providerNonce = 'replayed-nonce';
    await assert.rejects(h.client.completeSignIn(new URL(`${config.redirectUri}?code=abc&state=${pending.state}`)), (error: unknown) => error instanceof OidcError && error.code === 'nonce_mismatch');
    assert.equal(h.store.getItem(REFRESH_KEY), null);
  });

  test('redirect detection needs state plus code or error', () => {
    assert.equal(h.client.isRedirectCallback(new URL('http://localhost:5173/?code=a&state=b')), true);
    assert.equal(h.client.isRedirectCallback(new URL('http://localhost:5173/?error=x&state=b')), true);
    assert.equal(h.client.isRedirectCallback(new URL('http://localhost:5173/?code=a')), false);
    assert.equal(h.client.isRedirectCallback(new URL('http://localhost:5173/#tasks')), false);
  });
});

describe('refresh and sign-out', () => {
  test('refresh rotates the stored token and schedules before expiry', async () => {
    const h = harness((body) => ({ status: 200, body: { access_token: jwt({ grant: body.get('grant_type') }), refresh_token: 'refresh-2', expires_in: 300 } }));
    h.store.setItem(REFRESH_KEY, 'refresh-1');
    const tokens = await h.client.refresh();
    assert.equal(tokens?.claims.grant, 'refresh_token');
    assert.equal(h.calls.at(-1)?.body?.get('refresh_token'), 'refresh-1');
    assert.equal(h.store.getItem(REFRESH_KEY), 'refresh-2');
    assert.equal(h.client.refreshDelay(tokens!), 240_000);
    h.advance(250_000);
    assert.equal(h.client.refreshDelay(tokens!), 0);
  });

  test('short-lived tokens refresh halfway through their lifetime', async () => {
    const h = harness(() => ({ status: 200, body: { access_token: jwt({}), expires_in: 40 } }));
    h.store.setItem(REFRESH_KEY, 'refresh-1');
    const tokens = await h.client.refresh();
    assert.equal(h.client.refreshDelay(tokens!), 20_000);
    assert.equal(h.store.getItem(REFRESH_KEY), 'refresh-1', 'a response without rotation keeps the current refresh token');
  });

  test('a rejected refresh token is forgotten', async () => {
    const h = harness(() => ({ status: 400, body: { error: 'invalid_grant', error_description: 'Token is not active' } }));
    h.store.setItem(REFRESH_KEY, 'refresh-1');
    assert.equal(await h.client.refresh(), null);
    assert.equal(h.store.getItem(REFRESH_KEY), null);
  });

  test('no refresh token means no request', async () => {
    const h = harness(() => ({ status: 500, body: {} }));
    assert.equal(await h.client.refresh(), null);
    assert.equal(h.calls.length, 0);
  });

  test('sign-out clears storage and builds the end-session URL', async () => {
    const h = harness(() => ({ status: 500, body: {} }));
    h.store.setItem(REFRESH_KEY, 'refresh-1');
    const url = new URL((await h.client.signOutUrl('id-token'))!);
    assert.equal(url.origin + url.pathname, metadata.end_session_endpoint);
    assert.equal(url.searchParams.get('client_id'), 'anum-web');
    assert.equal(url.searchParams.get('id_token_hint'), 'id-token');
    assert.equal(url.searchParams.get('post_logout_redirect_uri'), config.postLogoutRedirectUri);
    assert.equal(h.store.values.size, 0);
  });

  test('discovery must name the configured issuer', async () => {
    const store = new MemoryStore();
    const client = new OidcClient(config, { fetch: async () => Response.json({ ...metadata, issuer: 'https://evil.test' }), storage: store, crypto: webcrypto as unknown as Crypto });
    await assert.rejects(client.beginSignIn(), (error: unknown) => error instanceof OidcError && error.code === 'issuer_mismatch');
    assert.equal(store.getItem(PENDING_KEY), null);
  });
});

describe('desktop loopback sign-in (RFC 8252)', () => {
  const loopback = 'http://127.0.0.1:49152/callback';

  test('only loopback IP literals with a port are accepted as overrides', () => {
    assert.equal(isLoopbackRedirect(loopback), true);
    assert.equal(isLoopbackRedirect('http://[::1]:49152/callback'), true);
    assert.equal(isLoopbackRedirect('http://localhost:49152/callback'), false);
    assert.equal(isLoopbackRedirect('https://127.0.0.1:49152/callback'), false);
    assert.equal(isLoopbackRedirect('http://127.0.0.1/callback'), false);
    assert.equal(isLoopbackRedirect('http://evil.test:49152/callback'), false);
    assert.equal(isLoopbackRedirect('not a url'), false);
  });

  test('a non-loopback override is refused before anything is stored', async () => {
    const h = harness(() => ({ status: 500, body: {} }));
    await assert.rejects(h.client.beginSignIn('', 'https://evil.test/callback'), (error: unknown) => error instanceof OidcError && error.code === 'invalid_redirect');
    assert.equal(h.store.getItem(PENDING_KEY), null);
  });

  test('the override is used for the authorization and token requests', async () => {
    const h = harness((body) => {
      assert.equal(body.get('redirect_uri'), loopback);
      return { status: 200, body: { access_token: jwt({ tenant_id: 't' }), expires_in: 300 } };
    });
    const authorizeUrl = new URL(await h.client.beginSignIn('#tasks', loopback));
    assert.equal(authorizeUrl.searchParams.get('redirect_uri'), loopback);
    const pending = JSON.parse(h.store.getItem(PENDING_KEY)!) as { state: string };
    const result = await h.client.completeSignIn(new URL(`${loopback}?code=abc&state=${pending.state}`));
    assert.equal(result.returnTo, '#tasks');
    assert.deepEqual(result.tokens.claims, { tenant_id: 't' });
  });

  test('desktop sign-out can leave out the post-logout redirect', async () => {
    const h = harness(() => ({ status: 500, body: {} }));
    const url = new URL((await h.client.signOutUrl('id-token', { postLogoutRedirect: false }))!);
    assert.equal(url.searchParams.get('post_logout_redirect_uri'), null);
    assert.equal(url.searchParams.get('id_token_hint'), 'id-token');
  });

  test('the shell bridge exists only inside Tauri and calls the shell commands', async () => {
    assert.equal(desktopShell({}), null);
    const calls: Array<[string, unknown]> = [];
    const shell = desktopShell({ __TAURI_INTERNALS__: { invoke: async (command: string, args?: unknown) => { calls.push([command, args]); return command === 'oidc_loopback_listen' ? 49152 : loopback; } } });
    assert.ok(shell);
    assert.equal(await shell.listenForSignIn(), 49152);
    assert.equal(await shell.authorizeInBrowser('https://id.example.test/auth'), loopback);
    await shell.openInBrowser('https://id.example.test/logout');
    assert.deepEqual(calls, [
      ['oidc_loopback_listen', undefined],
      ['oidc_loopback_authorize', { authorizationUrl: 'https://id.example.test/auth' }],
      ['oidc_open_browser', { url: 'https://id.example.test/logout' }],
    ]);
  });
});
