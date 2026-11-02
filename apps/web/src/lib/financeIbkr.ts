// IBKR trading view logic (docs/finance-live-trading.md), pure so the unit suite covers it. The view shows
// IBKR's own figures and ANUM's checks; it never predicts earnings and never decides what the server decides.
import type { IbkrConnection, IbkrEnvironment, IbkrOrderState, IbkrOverview } from '@anum/contracts';


const DECIMAL = /^\d{1,10}(\.\d{1,4})?$/;

/** A decimal text as an integer count of 1/10000, or null. */
function scaled(text: string): bigint | null {
  const value = text.trim();
  if (!DECIMAL.test(value)) return null;
  const [whole, fraction = ''] = value.split('.');
  return BigInt(whole) * 10000n + BigInt((fraction + '0000').slice(0, 4));
}

/** quantity times price, rounded half up to cents, as "12.34". Exact (no floating point). */
export function notional(quantity: string, price: string): string | null {
  const q = scaled(quantity), p = scaled(price);
  if (q === null || p === null) return null;
  const product = q * p; // scale 10^8
  const cents = (product + 500000n) / 1000000n;
  const whole = cents / 100n, rest = cents % 100n;
  return `${whole}.${rest.toString().padStart(2, '0')}`;
}

export type TicketError = 'symbol' | 'quantity' | 'wholeShares' | 'price' | 'overOrder';

/** The ticket's own checks before anything is sent to the server (the server checks everything again). */
export function checkTicket(draft: { symbol: string; quantity: string; limit_price: string }, allowed: string[], fractionalOk: boolean, maxOrder: string): TicketError | null {
  if (!allowed.includes(draft.symbol)) return 'symbol';
  const q = scaled(draft.quantity);
  if (q === null || q <= 0n) return 'quantity';
  if (!fractionalOk && q % 10000n !== 0n) return 'wholeShares';
  const p = scaled(draft.limit_price);
  if (p === null || p <= 0n) return 'price';
  const total = notional(draft.quantity, draft.limit_price), cap = scaled(maxOrder);
  if (total !== null && cap !== null && (scaled(total) ?? 0n) > cap) return 'overOrder';
  return null;
}

/** A SHA-256 fingerprint as 64 lower-case hex digits; accepts openssl's AB:CD:... form. */
export function normalizeFingerprint(text: string): string | null {
  let value = text.trim().toLowerCase().replace(/[:\s]/g, '');
  if (value.startsWith('sha256fingerprint=')) value = value.slice('sha256fingerprint='.length);
  return /^[0-9a-f]{64}$/.test(value) ? value : null;
}

export function connectionFor(overview: IbkrOverview, environment: IbkrEnvironment): IbkrConnection | null {
  return overview.connections.find((c) => c.environment === environment && c.status === 'active') ?? null;
}

/** True when the owner must log in to the IBKR gateway again (ANUM never stores the IBKR password). */
export function needsLogin(connection: IbkrConnection): boolean {
  return connection.session === 'expired';
}

export function orderTone(state: IbkrOrderState): 'success' | 'warning' | 'danger' | 'neutral' {
  if (state === 'filled') return 'success';
  if (state === 'rejected' || state === 'unknown') return 'danger';
  if (state === 'cancelled') return 'neutral';
  return 'warning';
}

export const OPEN_ORDER_STATES: readonly IbkrOrderState[] = ['submitting', 'submitted', 'partially_filled', 'cancel_requested', 'unknown'];

/** -1, 0 or 1 for a signed decimal text (for colour only; no arithmetic on money). */
export function sign(text: string | null | undefined): -1 | 0 | 1 {
  if (!text) return 0;
  const value = text.trim();
  if (/^-0*(\.0*)?$/.test(value) || /^0*(\.0*)?$/.test(value)) return 0;
  return value.startsWith('-') ? -1 : 1;
}

/** Seconds left before an approval expires (0 when expired). */
export function secondsLeft(expiresAt: string, now: number): number {
  return Math.max(0, Math.floor((Date.parse(expiresAt) - now) / 1000));
}

export function shortHash(hash: string): string {
  return `${hash.slice(0, 8)}...${hash.slice(-6)}`;
}
