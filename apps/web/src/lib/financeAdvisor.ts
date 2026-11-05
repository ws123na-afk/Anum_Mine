// Finance, Money advisor (docs/finance.md#money-advisor): pure helpers for the page, so
// `pnpm --filter @anum/web test:unit` covers them without a browser. The figures come from the API's weekly
// note; nothing here works out money, and ANUM only advises: it never moves money.
import type {
  FinanceAdvisorAction, FinanceAdvisorCard, FinanceAdvisorCurrency, FinanceAdvisorNote, FinanceAdvisorRunway, FinanceAdvisorScenario,
  FinanceAdvisorScenarioKind,
} from '@anum/contracts';
import type { FinanceTab } from './finance.ts';
import { FINANCE_TABS } from './finance.ts';

export const NOTES_PAGE = 8;
/** Monday is 0, as the API counts weekdays. */
export const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6] as const;
const SEVERITY_ORDER = { urgent: 0, high: 1, medium: 2, info: 3 } as const;

/** An action's words in the page's language: the API writes both from fixed templates. */
export function actionText(action: Pick<FinanceAdvisorAction, 'text_en' | 'text_ar'>, locale: 'en' | 'ar'): string {
  return locale === 'ar' ? action.text_ar : action.text_en;
}

/** The Finance page an action opens, or null when it names none (or a page this build does not have). */
export function actionTab(action: Pick<FinanceAdvisorAction, 'tab'>): FinanceTab | null {
  return action.tab && (FINANCE_TABS as readonly string[]).includes(action.tab) ? action.tab as FinanceTab : null;
}

export function sortActions(actions: readonly FinanceAdvisorAction[]): FinanceAdvisorAction[] {
  return actions.map((a, i) => ({ a, i })).sort((x, y) => SEVERITY_ORDER[x.a.severity] - SEVERITY_ORDER[y.a.severity] || x.i - y.i).map(({ a }) => a);
}

export function scenario(runway: FinanceAdvisorRunway, kind: FinanceAdvisorScenarioKind): FinanceAdvisorScenario | null {
  return runway.scenarios.find((s) => s.kind === kind) ?? null;
}

/** The survival figure the KPI shows: essentials when there is enough history, else the bare minimum. */
export function headlineScenario(runway: FinanceAdvisorRunway): FinanceAdvisorScenario | null {
  return scenario(runway, 'essential') ?? scenario(runway, 'bare');
}

/** Under 30 days is bad, under 60 a warning (the API's "runway low" step uses the same lines). */
export function runwayTone(days: number | null | undefined): '' | 'good' | 'warn' | 'bad' {
  if (days === null || days === undefined) return '';
  if (days < 30) return 'bad';
  if (days < 60) return 'warn';
  return 'good';
}

export function severityTone(severity: FinanceAdvisorAction['severity']): 'bad' | 'warn' | 'info' | 'muted' {
  return severity === 'urgent' ? 'bad' : severity === 'high' ? 'warn' : severity === 'medium' ? 'info' : 'muted';
}

/** The currency block to show: the chosen one when present, else the base currency, else the first. */
export function pickCurrency(note: FinanceAdvisorNote, chosen: string | null): FinanceAdvisorCurrency | null {
  return note.currencies.find((c) => c.currency === chosen) ?? note.currencies.find((c) => c.currency === note.base_currency) ?? note.currencies[0] ?? null;
}

/** Cards that owe money and have a due date within 30 days, in one currency. */
export function cardsDue(cards: readonly FinanceAdvisorCard[], currency: string): FinanceAdvisorCard[] {
  return cards.filter((c) => c.currency === currency && Number(c.owed) > 0 && c.days_until !== null && c.days_until <= 30);
}

/** Exact sum of decimal strings with the currency's digits (no binary float drift). */
export function sumMoney(values: readonly string[], digits = 2): string {
  const scale = 10n ** BigInt(digits);
  const toMinor = (v: string) => {
    const negative = v.trim().startsWith('-');
    const [whole, frac = ''] = v.replace('-', '').split('.');
    const minor = BigInt(whole || '0') * scale + BigInt((frac + '0'.repeat(digits)).slice(0, digits) || '0');
    return negative ? -minor : minor;
  };
  const total = values.reduce((acc, v) => acc + toMinor(v), 0n);
  const negative = total < 0n, abs = negative ? -total : total;
  const text = digits ? `${abs / scale}.${String(abs % scale).padStart(digits, '0')}` : String(abs);
  return negative ? `-${text}` : text;
}

/** The weekday's name in the page's language (Monday is 0; 1 January 2024 was a Monday). */
export function weekdayName(day: number, intlLocale: string): string {
  return new Date(Date.UTC(2024, 0, 1 + day)).toLocaleDateString(intlLocale, { weekday: 'long', timeZone: 'UTC' });
}

export function pageCount(total: number, size = NOTES_PAGE): number {
  return Math.max(1, Math.ceil(total / size));
}

/** Full months the runway averaged, "YYYY-MM" as short month names joined for a sentence. */
export function monthsList(months: readonly string[], intlLocale: string): string {
  return months.map((m) => new Date(`${m}-01T12:00:00Z`).toLocaleDateString(intlLocale, { month: 'short', year: 'numeric', timeZone: 'UTC' })).join(', ');
}
