// The customer portal's pure helpers (docs/business-portal.md): reading the access link from the URL
// fragment and keeping it out of the address bar, status and expiry labels, the approve/decline
// confirmation text and the owner's side of the link (building the shareable address). No browser
// or Vite APIs, so `pnpm --filter @anum/web test:unit` covers it.
import type { BusinessCurrency, PortalDecisionSummary } from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';
import { formatMoney, type Tone } from './business.ts';

const label = (key: string) => t(key as MessageKey);

export const PORTAL_PATH = '/portal';
/** Per-tab storage key: a refresh in the same tab keeps the link; a new tab asks for it again. */
export const PORTAL_STORAGE_KEY = 'anum.portal.access';
const TOKEN = /^anum_portal\.[A-Za-z0-9_-]{1,80}\.[A-Za-z0-9_-]{1,80}\.[A-Za-z0-9_-]{1,80}\.[A-Za-z0-9_-]{20,100}$/;

/** True for the public portal page (a trailing slash is accepted). */
export function isPortalPath(pathname: string): boolean {
  return (pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname) === PORTAL_PATH;
}

/** The token in a `#access=<token>` fragment, or null when there is none or it is not shaped like one. */
export function parseAccessFragment(hash: string): string | null {
  const body = hash.replace(/^#/, '');
  for (const part of body.split('&')) {
    if (!part.startsWith('access=')) continue;
    let value = part.slice('access='.length);
    try { value = decodeURIComponent(value); } catch { return null; }
    value = value.trim();
    return TOKEN.test(value) ? value : null;
  }
  return null;
}

/** The address with the fragment removed, so the credential is not left in the bar, history or a screenshot. */
export function withoutFragment(location: { pathname: string; search: string }): string {
  return `${location.pathname}${location.search}`;
}

type TokenStore = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export function readStoredToken(store: TokenStore | null): string | null {
  try {
    const value = store?.getItem(PORTAL_STORAGE_KEY) ?? null;
    return value && TOKEN.test(value) ? value : null;
  } catch { return null; }
}

export function storeToken(store: TokenStore | null, token: string | null): void {
  try {
    if (token) store?.setItem(PORTAL_STORAGE_KEY, token);
    else store?.removeItem(PORTAL_STORAGE_KEY);
  } catch { /* the link then lasts until the page is closed or reloaded */ }
}

/** The link in this tab: a fragment wins over the saved one. `fromFragment` says the address bar must be cleaned. */
export function resolveToken(hash: string, store: TokenStore | null): { token: string | null; fromFragment: boolean } {
  const fresh = parseAccessFragment(hash);
  if (fresh) return { token: fresh, fromFragment: true };
  return { token: readStoredToken(store), fromFragment: hash.replace(/^#/, '').split('&').some((part) => part.startsWith('access=')) };
}

/** The owner's shareable address from the API's `portal_path` (`/portal#access=<token>`). */
export function buildPortalLink(origin: string, portalPath: string): string {
  return `${origin.replace(/\/+$/, '')}${portalPath.startsWith('/') ? portalPath : `/${portalPath}`}`;
}

// ---- what the API's refusals mean to the page --------------------------------------------------

export type PortalProblem = 'link' | 'archived' | 'forbidden' | 'missing' | 'changed' | 'limit' | 'invalid' | 'network' | 'error';

/** 401 is the link itself (no retry loop); 403 is a read-only link or a paused business; 409 is a changed record. */
export function problemKind(status: number | null, detail: string | null): PortalProblem {
  if (status === null || status === 0) return 'network';
  if (status === 401) return 'link';
  if (status === 403) return /paused/i.test(detail ?? '') ? 'archived' : 'forbidden';
  if (status === 404) return 'missing';
  if (status === 409) return 'changed';
  if (status === 422) return 'invalid';
  if (status === 429) return 'limit';
  return 'error';
}

// ---- labels ------------------------------------------------------------------------------------

export const offerStatusLabel = (status: 'waiting' | 'accepted') => label(`pt.offer.status.${status}`);
export const changeStatusLabel = (status: 'waiting' | 'approved') => label(`pt.change.status.${status}`);
export const ticketStatusLabel = (status: string) => label(`pt.ticket.status.${status}`);
export const severityLabel = (severity: string) => label(`pt.severity.${severity}`);
export const methodLabel = (method: string) => label(`inv.method.${method}`);
export const REQUEST_SEVERITIES = ['low', 'normal', 'high'] as const;

export function invoiceStatusText(item: { status: string; overdue: boolean; payment_state: string }): string {
  if (item.status === 'void') return t('pt.invoice.void');
  if (item.status === 'paid' || item.payment_state === 'paid') return t('pt.invoice.paid');
  if (item.overdue) return t('pt.invoice.overdue');
  return item.payment_state === 'partially_paid' ? t('pt.invoice.partial') : t('pt.invoice.open');
}

export function invoiceStatusTone(item: { status: string; overdue: boolean; payment_state: string }): Tone {
  if (item.status === 'void') return 'dangerTone';
  if (item.status === 'paid' || item.payment_state === 'paid') return 'success';
  return item.overdue ? 'dangerTone' : 'warning';
}

export function ticketTone(status: string): Tone {
  return status === 'resolved' || status === 'closed' ? 'success' : status === 'waiting_on_customer' ? 'warning' : 'neutral';
}

// ---- decisions ---------------------------------------------------------------------------------

export type Signer = { ok: true; name: string } | { ok: false; error: string };

/** The typed full name that signs a decision: two to 160 characters, at least one letter. */
export function checkSigner(input: string): Signer {
  const name = input.replace(/\s+/g, ' ').trim();
  if (name.length < 2 || name.length > 160 || !/\p{L}/u.test(name)) return { ok: false, error: t('pt.decide.nameInvalid') };
  return { ok: true, name };
}

/** The sentence the confirm step shows: exactly what is approved or declined, and for how much. */
export function confirmText(kind: 'offer' | 'change', decision: 'approved' | 'declined', title: string, amount: number | null, currency: BusinessCurrency): string {
  // A change order moves the price up or down, so its amount keeps its sign.
  const money = amount === null ? t('pt.dash') : kind === 'change' ? `${amount > 0 ? '+' : amount < 0 ? '-' : ''}${formatMoney(Math.abs(amount), currency)}` : formatMoney(amount, currency);
  return t(`pt.decide.confirm.${kind}.${decision}` as MessageKey, { title, amount: money });
}

export type Expiry = { kind: 'expired' } | { kind: 'soon'; days: number } | { kind: 'ok'; days: number };

/** How long the link has left: a notice shows at seven days or fewer. */
export function expiryState(expiresAt: string, now: number = Date.now()): Expiry {
  const left = new Date(expiresAt).getTime() - now;
  if (!Number.isFinite(left) || left <= 0) return { kind: 'expired' };
  const days = Math.ceil(left / 86_400_000);
  return days <= 7 ? { kind: 'soon', days } : { kind: 'ok', days };
}

/** What waits for the customer: offers and change orders to decide, invoices open, requests open. */
export function waitingTotal(counts: { offers_waiting: number; change_orders_waiting: number }): number {
  return counts.offers_waiting + counts.change_orders_waiting;
}

// ---- the owner's view of a customer's decision (proposal and change order drawers) -------------

/** "Approved by Sara Khan in the portal on 3 Nov 2026" with, when it is about an older version, a hint. */
export function decisionLine(decision: PortalDecisionSummary, when: (value: string) => string): string {
  return t(decision.decision === 'approved' ? 'inv.portal.decisionApproved' : 'inv.portal.decisionDeclined', { name: decision.signer_name, when: when(decision.decided_at) });
}

/** Access links: who can use the link now. */
export function accessState(item: { status: 'active' | 'revoked'; expired: boolean }): 'active' | 'expired' | 'revoked' {
  return item.status === 'revoked' ? 'revoked' : item.expired ? 'expired' : 'active';
}
export const accessTone = (state: 'active' | 'expired' | 'revoked'): Tone => (state === 'active' ? 'success' : 'dangerTone');
export const accessLabel = (state: 'active' | 'expired' | 'revoked') => label(`inv.portal.state.${state}`);
