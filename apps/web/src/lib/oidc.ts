// OpenID Connect authorization code + PKCE (RFC 7636) for public browser clients.
//
// This module has no imports and no Vite globals so `node --test` can exercise it directly
// (see apps/web/test/oidc.test.ts). Browser wiring lives in ./auth.ts.
//
// Storage rules (docs/identity.md, "Client Sign-In"): the access and ID tokens live only in
// memory. The refresh token and the one-time sign-in request (state, PKCE verifier, nonce) may
// live in sessionStorage, never in localStorage.

export interface OidcConfig {
  issuer: string;
  clientId: string;
  redirectUri: string;
  postLogoutRedirectUri: string;
  scope: string;
}

export interface OidcMetadata {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
}

export interface TokenSet {
  accessToken: string;
  refreshToken?: string;
  idToken?: string;
  /** Access-token expiry in epoch milliseconds. */
  expiresAt: number;
  /** When to refresh, in epoch milliseconds: a minute early, or halfway through a short lifetime. */
  refreshAt: number;
  claims: Record<string, unknown>;
}

export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface OidcDependencies {
  fetch: (input: string, init?: RequestInit) => Promise<Response>;
  /** sessionStorage in the browser. */
  storage: KeyValueStore;
  crypto: Pick<Crypto, 'getRandomValues' | 'subtle'>;
  now?: () => number;
}

export interface PendingSignIn {
  state: string;
  codeVerifier: string;
  nonce: string;
  returnTo: string;
  createdAt: number;
  /** Redirect URI of this request when it differs from the configured one (desktop loopback). */
  redirectUri?: string;
}

export class OidcError extends Error {
  readonly code: string;

  constructor(message: string, code = 'oidc_error') {
    super(message);
    this.name = 'OidcError';
    this.code = code;
  }
}

export const PENDING_KEY = 'anum.oidc.pending';
export const REFRESH_KEY = 'anum.oidc.refresh_token';
/** A sign-in that has not come back within this window is discarded. */
export const PENDING_TTL_MS = 10 * 60 * 1000;
/** Refresh this long before the access token expires. */
export const REFRESH_SKEW_MS = 60 * 1000;

export function base64UrlEncode(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function base64UrlDecode(value: string): string {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(value.length / 4) * 4, '=');
  const binary = atob(padded);
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

/** 32 random bytes give a 43-character verifier, the RFC 7636 minimum length. */
export function randomToken(crypto: OidcDependencies['crypto'], byteLength = 32): string {
  return base64UrlEncode(crypto.getRandomValues(new Uint8Array(byteLength)));
}

export async function pkceChallenge(verifier: string, crypto: OidcDependencies['crypto']): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return base64UrlEncode(new Uint8Array(digest));
}

/** True for an RFC 8252 section 7.3 loopback redirect: plain HTTP to a loopback IP literal, never `localhost`. */
export function isLoopbackRedirect(value: string): boolean {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  return url.protocol === 'http:' && (url.hostname === '127.0.0.1' || url.hostname === '[::1]') && url.port !== '' && !url.username && !url.password && !url.hash;
}

/** Reads a JWT payload without verifying it. The API verifies tokens; clients only read claims. */
export function decodeJwtClaims(token: string): Record<string, unknown> {
  const parts = token.split('.');
  if (parts.length !== 3) return {};
  try {
    const claims: unknown = JSON.parse(base64UrlDecode(parts[1]));
    return claims && typeof claims === 'object' && !Array.isArray(claims) ? (claims as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/**
 * Builds the client configuration, or returns null when OIDC is not configured so the app keeps
 * its local development session.
 */
export function oidcConfigFromEnv(
  env: Record<string, string | boolean | undefined>,
  location: { origin: string; pathname: string },
): OidcConfig | null {
  const issuer = typeof env.VITE_ANUM_OIDC_ISSUER === 'string' ? env.VITE_ANUM_OIDC_ISSUER.trim().replace(/\/+$/, '') : '';
  if (!issuer) return null;
  const clientId = typeof env.VITE_ANUM_OIDC_CLIENT_ID === 'string' && env.VITE_ANUM_OIDC_CLIENT_ID.trim() ? env.VITE_ANUM_OIDC_CLIENT_ID.trim() : 'anum-web';
  const appUrl = `${location.origin}${location.pathname}`;
  const redirectUri = typeof env.VITE_ANUM_OIDC_REDIRECT_URI === 'string' && env.VITE_ANUM_OIDC_REDIRECT_URI ? env.VITE_ANUM_OIDC_REDIRECT_URI : appUrl;
  const scope = typeof env.VITE_ANUM_OIDC_SCOPE === 'string' && env.VITE_ANUM_OIDC_SCOPE ? env.VITE_ANUM_OIDC_SCOPE : 'openid profile email';
  return { issuer, clientId, redirectUri, postLogoutRedirectUri: appUrl, scope };
}

export class OidcClient {
  readonly config: OidcConfig;
  private readonly deps: OidcDependencies;
  private readonly now: () => number;
  private metadata: Promise<OidcMetadata> | null = null;

  // Plain fields instead of parameter properties keep the module runnable with Node's type stripping.
  constructor(config: OidcConfig, deps: OidcDependencies) {
    this.config = config;
    this.deps = deps;
    this.now = deps.now ?? (() => Date.now());
  }

  discover(): Promise<OidcMetadata> {
    this.metadata ??= (async () => {
      const response = await this.deps.fetch(`${this.config.issuer}/.well-known/openid-configuration`);
      if (!response.ok) throw new OidcError(`OpenID discovery failed: ${response.status}`, 'discovery_failed');
      const metadata = (await response.json()) as OidcMetadata;
      if (metadata.issuer !== this.config.issuer) throw new OidcError('OpenID discovery returned a different issuer', 'issuer_mismatch');
      if (!metadata.authorization_endpoint || !metadata.token_endpoint) throw new OidcError('OpenID discovery is missing endpoints', 'discovery_failed');
      return metadata;
    })().catch((error: unknown) => {
      this.metadata = null;
      throw error;
    });
    return this.metadata;
  }

  /**
   * Starts a sign-in and returns the authorization URL to navigate to. `redirectUri` overrides the
   * configured redirect for this one request; the desktop shell passes its RFC 8252 loopback URI
   * (`http://127.0.0.1:<port>/callback`), which must be a loopback IP literal.
   */
  async beginSignIn(returnTo = '', redirectUri?: string): Promise<string> {
    if (redirectUri !== undefined && !isLoopbackRedirect(redirectUri)) {
      throw new OidcError('A redirect override must be an http://127.0.0.1 or http://[::1] loopback URI', 'invalid_redirect');
    }
    const metadata = await this.discover();
    const pending: PendingSignIn = {
      state: randomToken(this.deps.crypto),
      codeVerifier: randomToken(this.deps.crypto),
      nonce: randomToken(this.deps.crypto),
      returnTo,
      createdAt: this.now(),
      ...(redirectUri ? { redirectUri } : {}),
    };
    this.deps.storage.setItem(PENDING_KEY, JSON.stringify(pending));
    const url = new URL(metadata.authorization_endpoint);
    url.search = new URLSearchParams({
      response_type: 'code',
      client_id: this.config.clientId,
      redirect_uri: redirectUri ?? this.config.redirectUri,
      scope: this.config.scope,
      state: pending.state,
      nonce: pending.nonce,
      code_challenge: await pkceChallenge(pending.codeVerifier, this.deps.crypto),
      code_challenge_method: 'S256',
    }).toString();
    return url.toString();
  }

  /** True when the URL is the provider's redirect back to this app. */
  isRedirectCallback(url: URL): boolean {
    return url.searchParams.has('state') && (url.searchParams.has('code') || url.searchParams.has('error'));
  }

  /** Validates the redirect against the stored request and exchanges the code for tokens. */
  async completeSignIn(url: URL): Promise<{ tokens: TokenSet; returnTo: string }> {
    const raw = this.deps.storage.getItem(PENDING_KEY);
    // The request is single-use: a replayed or second callback cannot reuse the verifier.
    this.deps.storage.removeItem(PENDING_KEY);
    let pending: PendingSignIn | null = null;
    try {
      pending = raw ? (JSON.parse(raw) as PendingSignIn) : null;
    } catch {
      pending = null;
    }
    if (!pending || typeof pending.state !== 'string' || typeof pending.codeVerifier !== 'string') {
      throw new OidcError('No sign-in is in progress', 'no_pending_request');
    }
    const state = url.searchParams.get('state');
    if (!state || state !== pending.state) throw new OidcError('Sign-in state does not match', 'state_mismatch');
    if (this.now() - pending.createdAt > PENDING_TTL_MS) throw new OidcError('Sign-in request expired', 'request_expired');
    const error = url.searchParams.get('error');
    if (error) throw new OidcError(url.searchParams.get('error_description') ?? error, error);
    const code = url.searchParams.get('code');
    if (!code) throw new OidcError('Authorization code missing', 'invalid_response');

    const tokens = await this.tokenRequest({
      grant_type: 'authorization_code',
      code,
      // The token request must repeat the redirect URI of this request (RFC 6749 section 4.1.3).
      redirect_uri: pending.redirectUri ?? this.config.redirectUri,
      client_id: this.config.clientId,
      code_verifier: pending.codeVerifier,
    });
    if (tokens.idToken) {
      const idClaims = decodeJwtClaims(tokens.idToken);
      if (idClaims.nonce !== pending.nonce) {
        this.forget();
        throw new OidcError('ID token nonce does not match', 'nonce_mismatch');
      }
    }
    return { tokens, returnTo: pending.returnTo };
  }

  /** Uses the stored refresh token. Returns null (and forgets it) when it is missing or rejected. */
  async refresh(): Promise<TokenSet | null> {
    const refreshToken = this.deps.storage.getItem(REFRESH_KEY);
    if (!refreshToken) return null;
    try {
      return await this.tokenRequest({ grant_type: 'refresh_token', refresh_token: refreshToken, client_id: this.config.clientId });
    } catch (error) {
      if (error instanceof OidcError && error.code === 'invalid_grant') {
        this.forget();
        return null;
      }
      throw error;
    }
  }

  /**
   * Forgets local tokens and returns the provider logout URL (RP-initiated logout), if any.
   * `postLogoutRedirect: false` leaves out the post-logout redirect: the desktop shell opens the
   * URL in the system browser, which has no app page to return to.
   */
  async signOutUrl(idToken?: string, options: { postLogoutRedirect?: boolean } = {}): Promise<string | null> {
    this.forget();
    const metadata = await this.discover().catch(() => null);
    if (!metadata?.end_session_endpoint) return null;
    const url = new URL(metadata.end_session_endpoint);
    url.search = new URLSearchParams({
      client_id: this.config.clientId,
      ...(options.postLogoutRedirect === false ? {} : { post_logout_redirect_uri: this.config.postLogoutRedirectUri }),
      ...(idToken ? { id_token_hint: idToken } : {}),
    }).toString();
    return url.toString();
  }

  forget(): void {
    this.deps.storage.removeItem(REFRESH_KEY);
    this.deps.storage.removeItem(PENDING_KEY);
  }

  /** Milliseconds until the token should be refreshed (never negative). */
  refreshDelay(tokens: TokenSet): number {
    return Math.max(0, tokens.refreshAt - this.now());
  }

  private async tokenRequest(body: Record<string, string>): Promise<TokenSet> {
    const metadata = await this.discover();
    const response = await this.deps.fetch(metadata.token_endpoint, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(body).toString(),
    });
    const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    if (!response.ok) {
      const code = typeof payload.error === 'string' ? payload.error : 'token_request_failed';
      const description = typeof payload.error_description === 'string' ? payload.error_description : `Token request failed: ${response.status}`;
      throw new OidcError(description, code);
    }
    if (typeof payload.access_token !== 'string' || !payload.access_token) {
      throw new OidcError('Token response has no access token', 'invalid_response');
    }
    const claims = decodeJwtClaims(payload.access_token);
    const expiresIn = typeof payload.expires_in === 'number' ? payload.expires_in : Number(payload.expires_in);
    const expiresAt = Number.isFinite(expiresIn) && expiresIn > 0
      ? this.now() + expiresIn * 1000
      : typeof claims.exp === 'number' ? claims.exp * 1000 : this.now() + 5 * 60 * 1000;
    const lifetime = Math.max(0, expiresAt - this.now());
    const refreshAt = this.now() + Math.max(lifetime - REFRESH_SKEW_MS, lifetime / 2);
    const refreshToken = typeof payload.refresh_token === 'string' && payload.refresh_token ? payload.refresh_token : body.refresh_token;
    if (refreshToken) this.deps.storage.setItem(REFRESH_KEY, refreshToken);
    else this.deps.storage.removeItem(REFRESH_KEY);
    return {
      accessToken: payload.access_token,
      refreshToken,
      idToken: typeof payload.id_token === 'string' ? payload.id_token : undefined,
      expiresAt,
      refreshAt,
      claims,
    };
  }
}
