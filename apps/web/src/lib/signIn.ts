// Sign-in screen logic (docs/identity.md#google-and-apple-sign-in). No imports from the
// browser wiring, so `node --test` runs it (apps/web/test/signIn.test.ts).
import type { SignInOptions, SignInProvider } from '@anum/contracts';

const PROVIDER_ORDER: SignInProvider['id'][] = ['google', 'apple'];
const LABELS: Record<SignInProvider['id'], string> = { google: 'Google', apple: 'Apple' };

/** Nothing offered: the state when the options cannot be loaded (the plain sign-in still works). */
export const NO_SIGN_IN_OPTIONS: SignInOptions = { identity_providers: [], self_sign_up: false, privacy_policy_url: null };

/**
 * Reads `GET /api/v1/auth/sign-in-options` defensively: unknown providers are dropped (the
 * client cannot draw a button it does not know), the order is Google then Apple, and a
 * privacy policy URL that is not http(s) is ignored.
 */
export function parseSignInOptions(body: unknown): SignInOptions {
  if (!body || typeof body !== 'object') return NO_SIGN_IN_OPTIONS;
  const raw = body as Record<string, unknown>;
  const ids = new Set(
    (Array.isArray(raw.identity_providers) ? raw.identity_providers : [])
      .map((item) => (item && typeof item === 'object' ? (item as Record<string, unknown>).id : null))
      .filter((id): id is SignInProvider['id'] => typeof id === 'string' && (PROVIDER_ORDER as string[]).includes(id)),
  );
  const url = typeof raw.privacy_policy_url === 'string' ? raw.privacy_policy_url.trim() : '';
  return {
    identity_providers: PROVIDER_ORDER.filter((id) => ids.has(id)).map((id) => ({ id, label: LABELS[id] })),
    self_sign_up: raw.self_sign_up === true,
    privacy_policy_url: /^https?:\/\/\S+$/i.test(url) ? url : null,
  };
}

/**
 * Whether the "Create account" screen is offered: something can create an account (Keycloak's
 * sign-up page, or a Google or Apple sign-in, which creates a pending account on first use) and
 * there is a privacy policy for the person to accept.
 */
export function canCreateAccount(options: SignInOptions): boolean {
  return options.privacy_policy_url !== null && (options.self_sign_up || options.identity_providers.length > 0);
}

export interface PendingAccount {
  email: string | null;
  subject: string | null;
}

/**
 * A signed-in account that belongs to no organization yet: its token has no `tenant_id`
 * (docs/identity.md#pending-accounts). The API refuses it with 403 `account_pending`, so the
 * app shows the pending screen instead of calling it.
 */
export function pendingAccount(claims: Record<string, unknown>): PendingAccount | null {
  const tenant = claims.tenant_id;
  if (typeof tenant === 'string' && tenant.trim()) return null;
  return {
    email: typeof claims.email === 'string' && claims.email ? claims.email : null,
    subject: typeof claims.sub === 'string' && claims.sub ? claims.sub : null,
  };
}
