// The Finance shell's model (docs/finance.md#finance-shell): the section navigation that sits under
// Finance in the sidebar, which sections draw their own page head, the scope and period controls and
// the KPI delta rule. Pure, so `pnpm --filter @anum/web test:unit` covers it without a browser.
//
// Adding a Finance page: add its id to `FinanceTab` and `FINANCE_TABS` (lib/finance.ts), put it in one
// group below, give it a `finance.tab.<id>` label in both catalogs and, when it draws its own head with
// <FinancePageHead>, add it to OWN_HEAD_TABS. An entry never appears without a page: the nav is built
// from FINANCE_TABS, so an id that is not a real tab is dropped.
import { FINANCE_TABS, type FinanceTab } from './finance.ts';

export type FinanceNavGroupId = 'finance' | 'capture' | 'invest' | 'trading' | 'business';

/** A sidebar entry: a Finance tab, or a link to another module (Business Expenses and P&L). */
export type FinanceNavEntry =
  | { kind: 'tab'; tab: FinanceTab }
  | { kind: 'link'; id: 'books'; href: string; badge: 'new' };

export interface FinanceNavGroup { id: FinanceNavGroupId; entries: readonly FinanceNavEntry[] }

const tab = (id: FinanceTab): FinanceNavEntry => ({ kind: 'tab', tab: id });

/**
 * The mockups' order: the Finance group (Overview, Transactions, Accounts, Bills, Budgets, Debts,
 * Goals, Reports, Safe to spend, Guardian), then data capture (Connections, SMS capture, Import,
 * Ledger), Holdings and Research, Trading (Paper trading, Trading intelligence, Execution, Broker
 * connections) and Business Expenses and P&L, which opens the business books.
 */
const ALL_GROUPS: readonly FinanceNavGroup[] = [
  { id: 'finance', entries: [tab('overview'), tab('transactions'), tab('accounts'), tab('bills'), tab('debts'), tab('goals'), tab('reports'), tab('safe'), tab('guardian')] },
  { id: 'capture', entries: [tab('connections'), tab('sms'), tab('import'), tab('ledger')] },
  { id: 'invest', entries: [tab('holdings'), tab('research')] },
  { id: 'trading', entries: [tab('trading'), tab('intel'), tab('execution'), tab('ibkr')] },
  { id: 'business', entries: [{ kind: 'link', id: 'books', href: '#business-books', badge: 'new' }] },
];

export const FINANCE_NAV: readonly FinanceNavGroup[] = ALL_GROUPS
  .map((group) => ({ ...group, entries: group.entries.filter((e) => e.kind === 'link' || FINANCE_TABS.includes(e.tab)) }))
  .filter((group) => group.entries.length > 0);

/** Every tab the nav offers, in order (a test checks it matches FINANCE_TABS one to one). */
export function navTabs(groups: readonly FinanceNavGroup[] = FINANCE_NAV): FinanceTab[] {
  return groups.flatMap((g) => g.entries.flatMap((e) => (e.kind === 'tab' ? [e.tab] : [])));
}

/** The group a tab lives in: the trading group stays open while one of its pages is shown. */
export function groupOf(tabId: FinanceTab, groups: readonly FinanceNavGroup[] = FINANCE_NAV): FinanceNavGroupId | null {
  return groups.find((g) => g.entries.some((e) => e.kind === 'tab' && e.tab === tabId))?.id ?? null;
}

/**
 * Sections that draw their own head with <FinancePageHead> (title, scope, period, tabs). Finance's
 * generic header, summary band and breadcrumbs are left out for them, so the page head is never doubled.
 */
export const OWN_HEAD_TABS: ReadonlySet<FinanceTab> = new Set<FinanceTab>(['reports', 'safe', 'guardian', 'holdings', 'trading']);

/** Personal is the person's own ledger (/api/v1/finance), Business the workspace books (/api/v1/business/books). */
export type FinanceScope = 'personal' | 'business' | 'all';
export const FINANCE_SCOPES: readonly FinanceScope[] = ['personal', 'business', 'all'];

/** "YYYY-MM" for a "YYYY-MM-DD" day. */
export const monthOf = (day: string): string => day.slice(0, 7);

/** The first and last day of "YYYY-MM" (UTC calendar, as the API's month filters use). */
export function monthRange(month: string): { from: string; to: string } {
  const [year, number] = month.split('-').map(Number);
  const last = new Date(Date.UTC(year, number, 0)).getUTCDate();
  return { from: `${month}-01`, to: `${month}-${String(last).padStart(2, '0')}` };
}

/** The `count` months that end with `month`, oldest first. */
export function monthsEnding(month: string, count: number): string[] {
  const [year, number] = month.split('-').map(Number);
  const out: string[] = [];
  for (let back = count - 1; back >= 0; back--) {
    const index = year * 12 + (number - 1) - back;
    out.push(`${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`);
  }
  return out;
}

export type DeltaTone = 'good' | 'bad' | 'flat';
export interface Delta { direction: 'up' | 'down' | 'flat'; percent: number | null; tone: DeltaTone }

/**
 * The change from `previous` to `current` for a KPI chip. `higherIsBetter` decides green or red
 * (income up is good, spending up is bad). A zero or missing previous figure has no percentage: the
 * chip then shows the direction only, never an invented "+100%". Changes under 0.5% read as flat.
 */
export function delta(current: string | number | null | undefined, previous: string | number | null | undefined, higherIsBetter = true): Delta | null {
  const now = Number(current), before = Number(previous);
  if (current === null || current === undefined || previous === null || previous === undefined || !Number.isFinite(now) || !Number.isFinite(before)) return null;
  if (before === 0) {
    if (now === 0) return { direction: 'flat', percent: 0, tone: 'flat' };
    const direction = now > 0 ? 'up' : 'down';
    return { direction, percent: null, tone: (direction === 'up') === higherIsBetter ? 'good' : 'bad' };
  }
  const percent = Math.round(((now - before) / Math.abs(before)) * 1000) / 10;
  if (Math.abs(percent) < 0.5) return { direction: 'flat', percent: 0, tone: 'flat' };
  const direction = percent > 0 ? 'up' : 'down';
  return { direction, percent, tone: (direction === 'up') === higherIsBetter ? 'good' : 'bad' };
}
