/**
 * Real OIDC login (Authorization Code + PKCE) via Keycloak's official JS
 * adapter, rather than a hand-rolled implementation. PKCE code-verifier
 * generation, state/nonce handling, and token exchange are exactly the kind
 * of security-critical code better delegated to a maintained library than
 * reimplemented here — the same reasoning the backend uses PyJWT for.
 *
 * This is opt-in, mirroring the backend's ANUM_AUTH_MODE switch: unless
 * VITE_ANUM_AUTH_MODE=oidc is set at build time, none of this runs and the
 * app behaves exactly as it always has (stub tenant/workspace/user/role
 * headers, see lib/api.ts). Existing local dev, tests, and any deployment
 * that hasn't opted in are unaffected.
 */

import Keycloak from 'keycloak-js';
import type { TenantContext } from '@anum/contracts';

export type AuthMode = 'stub_headers' | 'oidc';

export const authMode: AuthMode =
  import.meta.env.VITE_ANUM_AUTH_MODE === 'oidc' ? 'oidc' : 'stub_headers';

export const isOidcEnabled = authMode === 'oidc';

let keycloak: Keycloak | null = null;
let initPromise: Promise<boolean> | null = null;

function getKeycloak(): Keycloak {
  if (!keycloak) {
    keycloak = new Keycloak({
      url: import.meta.env.VITE_ANUM_KEYCLOAK_URL ?? 'http://localhost:8080',
      realm: import.meta.env.VITE_ANUM_KEYCLOAK_REALM ?? 'anum',
      clientId: import.meta.env.VITE_ANUM_KEYCLOAK_CLIENT_ID ?? 'anum-web',
    });
  }
  return keycloak;
}

/**
 * Initializes the Keycloak adapter and redirects to login if the user isn't
 * already authenticated. Call this once, before rendering the app, and wait
 * for it to resolve.
 *
 * `checkLoginIframe` is deliberately disabled: it relies on a hidden iframe
 * polling the IdP for cross-tab SSO-logout detection, which adds real
 * complexity (postMessage, third-party-cookie/iframe restrictions in some
 * browser configurations) for a benefit — near-instant cross-tab logout
 * propagation — this app doesn't need. `getValidToken()` below already
 * catches a session that's gone stale at the next API call, via a normal
 * failed refresh, without needing a background poller.
 */
export async function initAuth(): Promise<boolean> {
  if (!isOidcEnabled) {
    return true;
  }

  if (!initPromise) {
    initPromise = getKeycloak().init({
      onLoad: 'login-required',
      pkceMethod: 'S256',
      checkLoginIframe: false,
    });
  }

  return initPromise;
}

/**
 * Returns a valid access token, refreshing it first if it's within 30
 * seconds of expiry. Call this before every authenticated API request
 * rather than reading `.token` directly — this is what actually keeps the
 * session alive across a long-lived tab, without a background timer.
 *
 * Returns null when OIDC isn't enabled (stub-header mode has no token to
 * send) or if the adapter hasn't been initialized yet.
 */
export async function getValidToken(): Promise<string | null> {
  if (!isOidcEnabled || !keycloak) {
    return null;
  }

  try {
    await keycloak.updateToken(30);
  } catch (error) {
    // Refresh failed (e.g. the refresh token itself expired, or the
    // Keycloak session was revoked server-side) - re-authenticate rather
    // than let the caller send a request with a stale/missing token.
    await keycloak.login();
    return null;
  }

  return keycloak.token ?? null;
}

/**
 * Derives a TenantContext from the current ID/access token's claims. This
 * mirrors, field for field, the mapping services/api/anum_api/oidc_auth.py
 * applies server-side (see that module's docstring) - but this is a
 * DISPLAY-ONLY convenience for the UI (e.g. SettingsView). It is not a
 * trust boundary: the API independently validates the token and derives
 * its own TenantContext server-side; nothing here needs to be trusted by
 * the backend, and nothing here should be used for client-side access
 * control decisions beyond hiding/showing UI affordances.
 */
export function getTenantContext(): TenantContext | null {
  if (!isOidcEnabled || !keycloak?.tokenParsed) {
    return null;
  }

  const claims = keycloak.tokenParsed;
  const tenantId = claims.tenant_id;
  const workspaceId = claims.workspace_id;
  const roles = claims.roles;

  if (typeof tenantId !== 'string' || typeof workspaceId !== 'string') {
    return null;
  }

  return {
    tenantId,
    workspaceId,
    userId: claims.sub ?? 'unknown',
    roles: Array.isArray(roles) ? roles.map(String) : [],
  };
}

export function logout(): void {
  if (!isOidcEnabled || !keycloak) {
    return;
  }
  void keycloak.logout({ redirectUri: window.location.origin });
}
