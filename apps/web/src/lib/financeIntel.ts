// Trading intelligence dashboard logic (docs/finance-trading-intel.md#dashboard), pure so the unit
// tests cover it. Everything shown is SIMULATED or recorded research: the view formats figures the
// API sent and never computes a return, a price or a forecast of its own.
import type { FinanceIntelAgent, FinanceIntelPhase, FinanceIntelRule, FinanceIntelVenue, FinanceStrategyFold, FinanceStrategyRun, FinanceStrategyState } from '@anum/contracts';
import { getLocale, intlLocale, t, type MessageKey } from './i18n.ts';

/** Who can decide: the person, the Trading Supervisor and the specialist agents (intel/records.py `Agent`). */
export const AGENTS: readonly FinanceIntelAgent[] = ['trading_supervisor', 'market_research', 'technical_analysis', 'strategy_research', 'portfolio',
  'risk_guardian', 'execution_coordinator', 'performance_reviewer', 'person'];

export const STRATEGY_STATES: readonly FinanceStrategyState[] = ['DRAFT', 'BACKTESTING', 'VALIDATION', 'PAPER_ACTIVE', 'PAPER_SUSPENDED', 'RETIRED'];

export type MetricKind = 'percent' | 'ratio' | 'count' | 'amount';

/** The metrics shown first, in this order; any other number the API sends follows, by name. */
export const METRICS: readonly { key: string; kind: MetricKind }[] = [
  { key: 'total_return', kind: 'percent' }, { key: 'cagr', kind: 'percent' }, { key: 'max_drawdown', kind: 'percent' },
  { key: 'sharpe', kind: 'ratio' }, { key: 'sortino', kind: 'ratio' }, { key: 'volatility', kind: 'percent' },
  { key: 'profit_factor', kind: 'ratio' }, { key: 'expectancy', kind: 'amount' }, { key: 'win_rate', kind: 'percent' },
  { key: 'turnover', kind: 'ratio' }, { key: 'exposure', kind: 'percent' }, { key: 'trades', kind: 'count' },
  { key: 'closed_trades', kind: 'count' }, { key: 'fees', kind: 'amount' }, { key: 'bars', kind: 'count' },
  { key: 'out_of_sample_return', kind: 'percent' }, { key: 'out_of_sample_sharpe', kind: 'ratio' }, { key: 'in_sample_sharpe', kind: 'ratio' },
];
const KINDS = new Map(METRICS.map((m) => [m.key, m.kind]));

export function stateTone(state: FinanceStrategyState): 'success' | 'neutral' | 'warning' {
  if (state === 'PAPER_ACTIVE') return 'success';
  if (state === 'PAPER_SUSPENDED' || state === 'RETIRED') return 'warning';
  return 'neutral';
}

export const stateLabel = (state: FinanceStrategyState) => t(`finance.intel.state.${state}` as MessageKey);

/** A metric the API sent, for reading: fractions as percentages, ratios to 2 decimals, counts whole. Missing is "-". */
export function formatMetric(key: string, value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '-';
  const locale = intlLocale(getLocale());
  const kind = KINDS.get(key) ?? 'ratio';
  if (kind === 'percent') return new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 2, minimumFractionDigits: 2, numberingSystem: 'latn', signDisplay: 'exceptZero' }).format(value);
  if (kind === 'count') return new Intl.NumberFormat(locale, { maximumFractionDigits: 0, numberingSystem: 'latn' }).format(value);
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 2, minimumFractionDigits: 2, numberingSystem: 'latn' }).format(value);
}

/** A metric's name in the person's language (the API's key when it is one the view does not know). */
export function metricLabel(key: string): string {
  const message = `finance.intel.metric.${key}` as MessageKey;
  const text = t(message);
  return text === message ? key.replace(/_/g, ' ') : text;
}

/** The metrics of a run in display order: known ones first, then the rest by name. */
export function orderedMetrics(metrics: Record<string, number | null>): { key: string; value: number | null }[] {
  const known = METRICS.filter((m) => m.key in metrics).map((m) => ({ key: m.key, value: metrics[m.key] ?? null }));
  const rest = Object.keys(metrics).filter((k) => !KINDS.has(k)).sort().map((k) => ({ key: k, value: metrics[k] ?? null }));
  return [...known, ...rest];
}

/** A run's verdict as a tone and label. A run is never labelled profitable or approved: it passed or failed the configured checks. */
export function verdictTone(verdict: FinanceStrategyRun['verdict'] | null): 'success' | 'warning' | 'neutral' {
  if (verdict === 'passed') return 'success';
  if (verdict === 'rejected' || verdict === 'insufficient_data') return 'warning';
  return 'neutral';
}

export function verdictLabel(verdict: FinanceStrategyRun['verdict'] | string | null): string {
  if (!verdict) return t('finance.intel.verdict.none');
  const key = `finance.intel.verdict.${verdict}` as MessageKey;
  const text = t(key);
  return text === key ? verdict : text;
}

export function agentLabel(agent: string): string {
  const key = `finance.intel.agent.${agent}` as MessageKey;
  const text = t(key);
  return text === key ? agent.replace(/_/g, ' ') : text;
}

export function outcomeTone(outcome: string | null): 'success' | 'warning' | 'neutral' {
  if (!outcome) return 'neutral';
  if (['ok', 'accepted', 'allowed', 'allow', 'executed', 'passed'].includes(outcome)) return 'success';
  if (['rejected', 'denied', 'deny', 'refused', 'blocked', 'suspended', 'failed'].includes(outcome)) return 'warning';
  return 'neutral';
}

/** A decision's outcome in the person's language (the recorded word otherwise). */
export function outcomeLabel(outcome: string): string {
  const key = `finance.intel.outcome.${outcome}` as MessageKey;
  const text = t(key);
  return text === key ? outcome.replace(/_/g, ' ') : text;
}

/** The metrics shown per fold: those the first fold has, known ones first, at most four. */
export function foldMetricKeys(folds: FinanceStrategyFold[]): string[] {
  if (folds.length === 0) return [];
  return orderedMetrics(folds[0].metrics).map((m) => m.key).slice(0, 4);
}

/** "fee_bps 10, slippage_bps 5": the costs a run assumed, as one line. */
export function costsLine(costs: Record<string, string>): string {
  return Object.entries(costs).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(', ');
}

/** Venue sessions: open ones first, then by name, so the person sees what trades now. */
export function sortVenues(venues: FinanceIntelVenue[]): FinanceIntelVenue[] {
  const rank = { open: 0, closed: 1, holiday: 2 } as const;
  return [...venues].sort((a, b) => rank[a.state] - rank[b.state] || a.name.localeCompare(b.name));
}

/** The venue's local clock, in the venue's own zone, for "Tadawul: 10:32 Sunday". */
export function venueClock(venue: Pick<FinanceIntelVenue, 'local_time' | 'timezone'>): string {
  const when = new Date(venue.local_time);
  if (Number.isNaN(when.getTime())) return '';
  return new Intl.DateTimeFormat(intlLocale(getLocale()), { timeZone: venue.timezone, weekday: 'short', hour: '2-digit', minute: '2-digit', numberingSystem: 'latn' }).format(when);
}

/** "Fast 20, slow 50": a strategy's parameters as one line. */
export function paramsLine(params: Record<string, string | number | boolean | null>): string {
  const entries = Object.entries(params);
  if (entries.length === 0) return t('finance.intel.noParams');
  return entries.map(([k, v]) => `${k.replace(/_/g, ' ')} ${v === null ? '-' : String(v)}`).join(', ');
}


// ---- actions (finance/intel_control_api.py) -----------------------------------------------------

/** The moves the person may make from each state (intel/lifecycle.py TRANSITIONS); the server checks again. */
const MOVES: Record<FinanceStrategyState, readonly FinanceStrategyState[]> = {
  DRAFT: ['BACKTESTING', 'RETIRED'],
  BACKTESTING: ['VALIDATION', 'DRAFT', 'RETIRED'],
  VALIDATION: ['PAPER_ACTIVE', 'BACKTESTING', 'RETIRED'],
  PAPER_ACTIVE: ['PAPER_SUSPENDED', 'RETIRED'],
  PAPER_SUSPENDED: ['PAPER_ACTIVE', 'RETIRED'],
  RETIRED: [],
};

export function nextStates(state: FinanceStrategyState): FinanceStrategyState[] {
  return [...MOVES[state]];
}

/** Moving into these needs a stronger confirmation: paper trading starts, or the version is closed for good. */
export function isWeightyMove(target: FinanceStrategyState): boolean {
  return target === 'PAPER_ACTIVE' || target === 'RETIRED';
}

export const PHASES: readonly FinanceIntelPhase[] = ['pre_open', 'intraday', 'post_close', 'weekly', 'monthly'];

export const phaseLabel = (phase: FinanceIntelPhase) => t(`finance.intel.phase.${phase}` as MessageKey);

/** Rules grouped by research candidate, the benchmark (0) first, for the "New strategy" picker. */
export function rulesByCandidate(rules: FinanceIntelRule[]): { candidate: number; name: string; rules: FinanceIntelRule[] }[] {
  const groups = new Map<number, { candidate: number; name: string; rules: FinanceIntelRule[] }>();
  for (const rule of rules) {
    const group = groups.get(rule.candidate) ?? { candidate: rule.candidate, name: rule.candidate_name, rules: [] };
    group.rules.push(rule);
    groups.set(rule.candidate, group);
  }
  return [...groups.values()].sort((a, b) => a.candidate - b.candidate);
}

export const MAX_BARS_FILE = 2_000_000;

/** Why a chosen bars file cannot be sent, or null. The server checks every bar again. */
export function barsFileProblem(file: { name: string; size: number }): MessageKey | null {
  if (file.size === 0) return 'finance.intel.data.fileEmpty';
  if (file.size > MAX_BARS_FILE) return 'finance.intel.data.fileTooBig';
  if (!/\.(csv|txt|json)$/i.test(file.name)) return 'finance.intel.data.fileType';
  return null;
}
