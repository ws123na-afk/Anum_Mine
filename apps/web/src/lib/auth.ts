// Browser session for the web app and the Tauri desktop shell.
//
// With VITE_ANUM_OIDC_ISSUER set, the app signs in through Keycloak (authorization code + PKCE)
// and sends the access token to the API. Without it, the app keeps the local development
// session from /api/v1/auth/local/session (ANUM_AUTH_MODE=headers only).
//
// Inside the Tauri desktop shell the login runs in the system browser and returns through an
// RFC 8252 loopback redirect (http://127.0.0.1:<port>/callback) served by the shell, instead of
// loading the identity provider inside the webview. See docs/desktop.md, "Sign-In".
import { desktopShell } from './desktop';
import { OidcClient, OidcError, oidcConfigFromEnv, type TokenSet } from './oidc';

export type AuthStatus =
  | { kind: 'local' }
  | { kind: 'signedIn'; claims: Record<string, unknown> }
  | { kind: 'signedOut'; message?: string };

const config = oidcConfigFromEnv(import.meta.env as Record<string, string | boolean | undefined>, window.location);
const client = config ? new OidcClient(config, { fetch: (input, init) => fetch(input, init), storage: window.sessionStorage, crypto: window.crypto }) : null;

const desktop = desktopShell(window);

export const oidcEnabled = client !== null;
/** True when sign-in opens the system browser (desktop shell) and the page stays where it is. */
export const browserSignIn = client !== null && desktop !== null;

let tokens: TokenSet | null = null;
let refreshing: Promise<TokenSet | null> | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<(status: AuthStatus) => void>();

export function onAuthChange(listener: (status: AuthStatus) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function emit(status: AuthStatus): void {
  for (const listener of listeners) listener(status);
}

function accept(next: TokenSet): void {
  // A refresh response may omit the ID token; keep the last one for the end-session hint.
  tokens = { ...next, idToken: next.idToken ?? tokens?.idToken };
  clearTimeout(refreshTimer);
  if (!client) return;
  refreshTimer = setTimeout(() => {
    void refreshTokens().catch(() => undefined);
  }, Math.max(5_000, client.refreshDelay(next)));
}

function drop(message?: string): void {
  tokens = null;
  clearTimeout(refreshTimer);
  emit({ kind: 'signedOut', message });
}

async function refreshTokens(): Promise<TokenSet | null> {
  if (!client) return null;
  refreshing ??= client
    .refresh()
    .then((next) => {
      if (next) accept(next);
      else if (tokens) drop('Your session ended. Sign in again.');
      return next;
    })
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

/**
 * Resolves the session on start-up: finishes a provider redirect, or restores the session from
 * the refresh token kept in sessionStorage for this tab.
 */
export async function initAuth(): Promise<AuthStatus> {
  if (!client) return { kind: 'local' };
  const url = new URL(window.location.href);
  if (client.isRedirectCallback(url)) {
    // Code and state are single-use; drop them from the address bar and history either way.
    const clean = `${url.origin}${url.pathname}`;
    try {
      const result = await client.completeSignIn(url);
      accept(result.tokens);
      window.history.replaceState(null, '', `${clean}${result.returnTo}`);
      return { kind: 'signedIn', claims: result.tokens.claims };
    } catch (error) {
      window.history.replaceState(null, '', clean);
      return { kind: 'signedOut', message: error instanceof Error ? error.message : 'Sign-in failed.' };
    }
  }
  try {
    const restored = await refreshTokens();
    return restored ? { kind: 'signedIn', claims: restored.claims } : { kind: 'signedOut' };
  } catch (error) {
    return { kind: 'signedOut', message: error instanceof OidcError ? error.message : 'The identity provider is unreachable.' };
  }
}

/**
 * Starts a sign-in. In the browser this navigates to the provider. In the desktop shell it opens
 * the system browser and returns; the session arrives through onAuthChange when the loopback
 * callback completes. Calling it again restarts the desktop sign-in.
 */
export async function signIn(): Promise<void> {
  if (!client) return;
  if (!desktop) {
    window.location.assign(await client.beginSignIn(window.location.hash));
    return;
  }
  const port = await desktop.listenForSignIn();
  const authorizationUrl = await client.beginSignIn(window.location.hash, `http://127.0.0.1:${port}/callback`);
  const callback = desktop.authorizeInBrowser(authorizationUrl);
  void callback
    .then(async (url) => {
      const result = await client.completeSignIn(new URL(url));
      accept(result.tokens);
      if (result.returnTo) window.history.replaceState(null, '', `${window.location.pathname}${result.returnTo}`);
      emit({ kind: 'signedIn', claims: result.tokens.claims });
    })
    .catch((error: unknown) => {
      // A restarted sign-in supersedes this one; its own result is reported instead.
      if (String(error).includes('Sign-in was restarted')) return;
      emit({ kind: 'signedOut', message: error instanceof Error ? error.message : String(error) });
    });
}

export async function signOut(): Promise<void> {
  if (!client) return;
  const idToken = tokens?.idToken;
  tokens = null;
  clearTimeout(refreshTimer);
  if (desktop) {
    // End the provider session in the system browser, where the sign-in happened.
    const url = await client.signOutUrl(idToken, { postLogoutRedirect: false });
    drop();
    if (url) await desktop.openInBrowser(url).catch(() => undefined);
    return;
  }
  const url = await client.signOutUrl(idToken);
  if (url) window.location.assign(url);
  else drop();
}

/** A current access token, refreshed first when it is about to expire. */
export async function accessToken(): Promise<string | null> {
  if (!client) return null;
  if (tokens && client.refreshDelay(tokens) > 0) return tokens.accessToken;
  const next = await refreshTokens().catch(() => null);
  if (next) return next.accessToken;
  // A transient refresh failure keeps using the current token until it actually expires.
  return tokens && tokens.expiresAt > Date.now() ? tokens.accessToken : null;
}

export function currentClaims(): Record<string, unknown> | null {
  return tokens?.claims ?? null;
}
