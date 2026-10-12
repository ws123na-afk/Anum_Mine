// Sign-in options, pending accounts, the kc_idp_hint / prompt=create request, and the
// Keycloak login theme's live password rules (docs/identity.md#google-and-apple-sign-in).
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { describe, test } from 'node:test';
import { canCreateAccount, NO_SIGN_IN_OPTIONS, parseSignInOptions, pendingAccount } from '../src/lib/signIn.ts';
import { OidcClient, OidcError, isIdpHint, type KeyValueStore } from '../src/lib/oidc.ts';
// The theme ships with Keycloak (infra/keycloak/themes/anum); it is plain JavaScript.
import { evaluatePassword } from '../../../infra/keycloak/themes/anum/login/resources/js/password-rules.js';

describe('sign-in options', () => {
  test('keeps known providers in a fixed order and drops unknown ones', () => {
    const options = parseSignInOptions({
      identity_providers: [{ id: 'apple', label: 'x' }, { id: 'facebook' }, { id: 'google' }, { id: 'apple' }],
      self_sign_up: true,
      privacy_policy_url: 'https://example.com/privacy',
    });
    assert.deepEqual(options.identity_providers, [{ id: 'google', label: 'Google' }, { id: 'apple', label: 'Apple' }]);
    assert.equal(options.self_sign_up, true);
    assert.equal(options.privacy_policy_url, 'https://example.com/privacy');
  });

  test('reads junk as nothing offered and refuses a non-http privacy URL', () => {
    assert.deepEqual(parseSignInOptions(null), NO_SIGN_IN_OPTIONS);
    assert.deepEqual(parseSignInOptions('x'), NO_SIGN_IN_OPTIONS);
    const options = parseSignInOptions({ identity_providers: 'google', self_sign_up: 'yes', privacy_policy_url: 'javascript:alert(1)' });
    assert.deepEqual(options, NO_SIGN_IN_OPTIONS);
  });

  test('offers account creation only with a privacy policy and a way to create an account', () => {
    const url = 'https://example.com/privacy';
    assert.equal(canCreateAccount({ identity_providers: [], self_sign_up: true, privacy_policy_url: url }), true);
    assert.equal(canCreateAccount({ identity_providers: [{ id: 'google', label: 'Google' }], self_sign_up: false, privacy_policy_url: url }), true);
    assert.equal(canCreateAccount({ identity_providers: [], self_sign_up: false, privacy_policy_url: url }), false);
    assert.equal(canCreateAccount({ identity_providers: [{ id: 'apple', label: 'Apple' }], self_sign_up: true, privacy_policy_url: null }), false);
  });
});

describe('pending accounts', () => {
  test('a token without tenant_id is pending and shows the email and subject', () => {
    assert.deepEqual(pendingAccount({ sub: 'u-1', email: 'new@example.com' }), { email: 'new@example.com', subject: 'u-1' });
    assert.deepEqual(pendingAccount({ tenant_id: '  ' }), { email: null, subject: null });
  });

  test('a token with a tenant is not pending', () => {
    assert.equal(pendingAccount({ sub: 'u-1', tenant_id: 'tenant_acme' }), null);
  });
});

class MemoryStorage implements KeyValueStore {
  private values = new Map<string, string>();
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}

function client() {
  const fetch = async () => new Response(JSON.stringify({
    issuer: 'https://auth.example.com/realms/anum',
    authorization_endpoint: 'https://auth.example.com/realms/anum/protocol/openid-connect/auth',
    token_endpoint: 'https://auth.example.com/realms/anum/protocol/openid-connect/token',
  }), { status: 200 });
  return new OidcClient(
    { issuer: 'https://auth.example.com/realms/anum', clientId: 'anum-web', redirectUri: 'https://app.example.com/', postLogoutRedirectUri: 'https://app.example.com/', scope: 'openid profile email' },
    { fetch, storage: new MemoryStorage(), crypto: webcrypto as unknown as Crypto },
  );
}

describe('provider and sign-up requests', () => {
  test('kc_idp_hint rides on the same code + PKCE request', async () => {
    const url = new URL(await client().beginSignIn('', undefined, { idpHint: 'google' }));
    assert.equal(url.searchParams.get('kc_idp_hint'), 'google');
    assert.equal(url.searchParams.get('response_type'), 'code');
    assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
    assert.ok(url.searchParams.get('code_challenge'));
    assert.equal(url.searchParams.get('prompt'), null);
  });

  test('create opens the sign-up page with prompt=create', async () => {
    const url = new URL(await client().beginSignIn('', undefined, { create: true }));
    assert.equal(url.searchParams.get('prompt'), 'create');
    assert.equal(url.searchParams.get('kc_idp_hint'), null);
  });

  test('a plain sign-in carries neither', async () => {
    const url = new URL(await client().beginSignIn());
    assert.equal(url.searchParams.get('prompt'), null);
    assert.equal(url.searchParams.get('kc_idp_hint'), null);
  });

  test('refuses a malformed provider hint', async () => {
    assert.equal(isIdpHint('apple'), true);
    assert.equal(isIdpHint('Apple'), false);
    assert.equal(isIdpHint('google&prompt=none'), false);
    await assert.rejects(client().beginSignIn('', undefined, { idpHint: 'x y' }), (error: unknown) => error instanceof OidcError && error.code === 'invalid_idp_hint');
  });
});

describe('Keycloak theme password rules', () => {
  const rules = [
    { name: 'length', value: 12 },
    { name: 'maxLength', value: 128 },
    { name: 'lowerCase', value: 1 },
    { name: 'upperCase', value: 1 },
    { name: 'digits', value: 1 },
    { name: 'specialChars', value: 1 },
    { name: 'notUsername', value: 1 },
    { name: 'notEmail', value: 1 },
  ];
  const met = (value: string, context = {}) => Object.fromEntries(evaluatePassword(value, rules, context).map((r: { name: string; met: boolean }) => [r.name, r.met]));

  test('an empty password meets nothing that needs typing', () => {
    const result = met('');
    assert.equal(result.length, false);
    assert.equal(result.notUsername, false);
    assert.equal(result.maxLength, true);
  });

  test('each rule ticks as the password grows', () => {
    assert.deepEqual(met('Correct-horse-9', { username: 'sam', email: 'sam@example.com' }), {
      length: true, maxLength: true, lowerCase: true, upperCase: true, digits: true, specialChars: true, notUsername: true, notEmail: true,
    });
    assert.equal(met('alllowercase').upperCase, false);
    assert.equal(met('ÉCOLE-école-1').upperCase, true);
  });

  test('the username and email rules ignore case', () => {
    const result = met('Sam@Example.com', { username: 'sam@example.com', email: 'sam@example.com' });
    assert.equal(result.notUsername, false);
    assert.equal(result.notEmail, false);
  });
});
