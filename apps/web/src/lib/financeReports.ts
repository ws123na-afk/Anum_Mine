// The post-close daily trading reports (docs/finance-trading-intel.md#daily-report), pure so the unit tests
// cover it. The API writes each report from the Supervisor's own recorded decisions; this file only
// labels its figures and reads the deep link. Recorded text is shown as data, never followed.
import type { FinanceDailyReportBody, FinanceDailyReportSummary } from '@anum/contracts';
import { getLocale, intlLocale, t, type MessageKey } from './i18n.ts';

export const REPORT_PAGE = 10;
/** The deep link the report notice opens: the list, newest report selected. */
export const REPORTS_HASH = '#finance-intel-reports';

/** `''` for the list (`#finance-intel-reports`), the id for `#finance-intel-report=<id>`, null otherwise. */
export function reportFromHash(hash: string): string | null {
  if (hash === REPORTS_HASH) return '';
  const found = /^#finance-intel-report=([A-Za-z0-9_-]{1,80})$/.exec(hash);
  return found ? found[1] : null;
}

export const reportHash = (id: string) => `#finance-intel-report=${encodeURIComponent(id)}`;

const SUMMARY: readonly (keyof FinanceDailyReportSummary)[] = [
  'active_strategies', 'proposed_orders', 'waiting_for_approval', 'refused_signals', 'suspended', 'problems', 'market_closed',
];

/** The summary figures in a fixed order with their labels; missing ones read as 0. */
export function summaryRows(summary: Partial<FinanceDailyReportSummary>): { key: string; label: string; value: number }[] {
  return SUMMARY.map((key) => ({ key, label: t(`finance.reports.summary.${key}` as MessageKey), value: Number(summary[key] ?? 0) || 0 }));
}

/** The one-line summary of a report in the list. */
export function summaryLine(summary: Partial<FinanceDailyReportSummary>): string {
  return t('finance.reports.line', {
    active: Number(summary.active_strategies ?? 0), proposed: Number(summary.proposed_orders ?? 0),
    refused: Number(summary.refused_signals ?? 0), suspended: Number(summary.suspended ?? 0),
  });
}

/** Whether the report asks the person to act: orders waiting, suspensions or problems. */
export function needsAction(summary: Partial<FinanceDailyReportSummary>): boolean {
  return Number(summary.waiting_for_approval ?? 0) > 0 || Number(summary.suspended ?? 0) > 0 || Number(summary.problems ?? 0) > 0;
}

/** A fraction as a signed percentage ("+3.2%"), "-" when missing. */
export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '-';
  return new Intl.NumberFormat(intlLocale(getLocale()), { style: 'percent', maximumFractionDigits: 1, minimumFractionDigits: 1, numberingSystem: 'latn', signDisplay: 'exceptZero' }).format(value);
}

/** How many entries each section has, for the section headings. */
export function sectionCounts(report: FinanceDailyReportBody): Record<'data' | 'orders' | 'refused' | 'portfolios' | 'performance' | 'problems', number> {
  return {
    data: report.data_health.length, orders: report.orders.length, refused: report.refused.length + report.suspended.length,
    portfolios: report.portfolios.length, performance: report.performance.length, problems: report.problems.length,
  };
}

/** The file name the Markdown download gets. */
export const markdownName = (date: string) => `anum-trading-report-${date.replace(/[^0-9-]/g, '') || 'report'}.md`;
