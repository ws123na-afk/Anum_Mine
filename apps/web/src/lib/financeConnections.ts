// Finance, Connections and data capture (docs/finance.md#connections-and-data-capture): pure helpers
// for the page. ANUM has no live bank link (Open Finance is an owner action), so an account's
// "source" is what really feeds it: forwarded SMS or Apple Pay, statement imports, or the person's own
// entries. Nothing here invents a sync count, a consent date or a connection state.
import type { FinanceAccount, FinanceConnection, FinanceImport, FinanceSmsSettings, FinanceSmsStatusView, IphoneOverview } from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

export type ConnectionsTab = 'accounts' | 'sms' | 'shortcuts' | 'imports' | 'permissions';
export const CONNECTIONS_TABS: readonly ConnectionsTab[] = ['accounts', 'sms', 'shortcuts', 'imports', 'permissions'];

/** What feeds an account. `sms` covers forwarded bank messages and Apple Pay (both arrive from the iPhone). */
export type AccountSource = 'sms' | 'statement' | 'manual';

/** The sources that really feed an account, from the SMS settings (mapped last four digits, Wallet cards,
 *  default and cash accounts) and the statement imports. An account with neither is kept by hand. */
export function accountSources(account: Pick<FinanceAccount, 'id'>, settings: Pick<FinanceSmsSettings, 'account_map' | 'wallet_cards' | 'default_account_id' | 'cash_account_id'> | null,
  imports: readonly Pick<FinanceImport, 'account_id'>[]): AccountSource[] {
  const sources: AccountSource[] = [];
  if (settings && (settings.default_account_id === account.id || settings.cash_account_id === account.id
    || settings.account_map.some((m) => m.account_id === account.id) || (settings.wallet_cards ?? []).some((c) => c.account_id === account.id))) sources.push('sms');
  if (imports.some((i) => i.account_id === account.id)) sources.push('statement');
  return sources.length ? sources : ['manual'];
}

/** The newest statement import per account (imports come newest first from the API, but do not rely on it). */
export function lastImportByAccount(imports: readonly Pick<FinanceImport, 'account_id' | 'created_at'>[]): Record<string, string> {
  const last: Record<string, string> = {};
  for (const item of imports) if (!last[item.account_id] || item.created_at > last[item.account_id]) last[item.account_id] = item.created_at;
  return last;
}

export interface LastActivity { at: string; source: 'sms' | 'statement' | 'rates' }

/** The newest data ANUM really received: an SMS or Apple Pay report, a statement import, or an exchange-rate sync. */
export function lastActivity(status: Pick<FinanceSmsStatusView, 'last_received_at'> | null, imports: readonly Pick<FinanceImport, 'created_at'>[],
  connections: readonly Pick<FinanceConnection, 'last_success_at'>[]): LastActivity | null {
  const found: LastActivity[] = [];
  if (status?.last_received_at) found.push({ at: status.last_received_at, source: 'sms' });
  for (const item of imports) found.push({ at: item.created_at, source: 'statement' });
  for (const c of connections) if (c.last_success_at) found.push({ at: c.last_success_at, source: 'rates' });
  return found.reduce<LastActivity | null>((best, item) => (!best || Date.parse(item.at) > Date.parse(best.at) ? item : best), null);
}

/** iPhone Shortcut routes that can carry data now: switched on and carried by at least one active device. */
export function shortcutRoutes(overview: Pick<IphoneOverview, 'integrations' | 'devices'> | null): { live: number; total: number } {
  if (!overview) return { live: 0, total: 0 };
  const active = overview.devices.filter((d) => d.status === 'active');
  const live = overview.integrations.filter((i) => i.enabled && active.some((d) => d.scopes.includes(i.scope))).length;
  return { live, total: overview.integrations.length };
}

/** Messages received and recorded, from the per-status counts of `GET /sms/status`. */
export function smsCounts(status: Pick<FinanceSmsStatusView, 'counts' | 'review_count'>): { received: number; recorded: number; review: number; ignored: number } {
  const c = status.counts ?? {};
  const n = (key: string) => c[key] ?? 0;
  const received = Object.entries(c).filter(([key]) => key !== 'test_ok').reduce((sum, [, value]) => sum + value, 0);
  return { received, recorded: n('posted') + n('attached'), review: status.review_count, ignored: n('rejected') + n('dismissed') + n('duplicate') };
}

/** A device token is shown once; afterwards only its hint (the last characters) is known. */
export function maskedToken(hint: string): string {
  return `${'•'.repeat(12)}${hint}`;
}

/** The import's format as a person reads it: a CSV layout, a PDF statement, or a scanned PDF read by OCR. */
export function importFormat(preset: string): string {
  if (preset === 'pdf') return t('fconn.import.format.pdf');
  if (preset === 'pdf_scan') return t('fconn.import.format.pdfScan');
  return t('fconn.import.format.csv');
}

export function sourceLabel(source: AccountSource): string {
  return t(`fconn.source.${source}` as MessageKey);
}

/** Accounts that match a search over name, institution and last four digits. */
export function searchAccounts<T extends Pick<FinanceAccount, 'name' | 'institution' | 'last4'>>(accounts: readonly T[], text: string): T[] {
  const q = text.trim().toLowerCase();
  if (!q) return [...accounts];
  return accounts.filter((a) => [a.name, a.institution ?? '', a.last4 ?? ''].some((v) => v.toLowerCase().includes(q)));
}

/** The connections tab a Finance hash opens on: `#finance-sms` opens SMS capture, everything else the accounts. */
export function connectionsTabFor(financeTab: string): ConnectionsTab {
  return financeTab === 'sms' ? 'sms' : 'accounts';
}
