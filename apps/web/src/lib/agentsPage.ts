// The Agents page layout (docs/agents.md#the-agents-page): pure helpers behind the overview rail,
// the agent cards, the preview panel and the agent's boundaries. No DOM, so `pnpm test:unit` runs them.
// Every figure comes from the API: agent profiles, and for owners the per-agent spending caps
// (docs/agent-controls.md). Nothing here invents a number the API did not send.
import type { AgentAutonomy, AgentAutonomyLevel, AgentCatalog, AgentProfile, AgentToolCategory } from './agents.ts';
import type { AgentCap } from './agentControls.ts';
import type { MessageKey } from './i18n.ts';

export const TOOL_ORDER: readonly AgentToolCategory[] = ['read', 'write', 'external'];

/** The level that applies: the agent's own, never looser than the workspace floor from the catalog. */
export function appliedLevel(agent: Pick<AgentProfile, 'autonomy'>, category: AgentToolCategory, catalog: Pick<AgentCatalog, 'autonomy_floor'> | null): AgentAutonomyLevel {
  const own = (agent.autonomy as AgentAutonomy)[category];
  const floor = catalog?.autonomy_floor?.[category];
  const rank: Record<AgentAutonomyLevel, number> = { off: 0, ask: 1, auto: 2 };
  if (!floor) return own;
  return rank[floor] < rank[own] ? floor : own;
}

export interface Boundary { key: MessageKey; tone: 'ok' | 'warn' }

/**
 * The agent's boundaries in plain words, from its autonomy levels (after the workspace floor). The
 * first line always holds: an agent uses only the tools it was given. External actions are never
 * automatic (the API refuses `auto` for them), so the last line is either "blocked" or "asks first".
 */
export function agentBoundaries(agent: Pick<AgentProfile, 'autonomy'>, catalog: Pick<AgentCatalog, 'autonomy_floor'> | null): Boundary[] {
  const read = appliedLevel(agent, 'read', catalog);
  const write = appliedLevel(agent, 'write', catalog);
  const external = appliedLevel(agent, 'external', catalog);
  return [
    { key: 'agentsPage.bound.toolsOnly', tone: 'ok' },
    { key: external === 'off' ? 'agentsPage.bound.externalOff' : 'agentsPage.bound.externalAsk', tone: 'ok' },
    { key: write === 'off' ? 'agentsPage.bound.writeOff' : write === 'ask' ? 'agentsPage.bound.writeAsk' : 'agentsPage.bound.writeAuto', tone: write === 'auto' ? 'warn' : 'ok' },
    { key: read === 'off' ? 'agentsPage.bound.readOff' : read === 'ask' ? 'agentsPage.bound.readAsk' : 'agentsPage.bound.readAuto', tone: 'ok' },
  ];
}

/** The one-line approval summary on a card: the strictest thing a person must still approve. */
export function approvalSummary(agent: Pick<AgentProfile, 'autonomy'>, catalog: Pick<AgentCatalog, 'autonomy_floor'> | null): { key: MessageKey; tone: 'ok' | 'warn' | 'muted' } {
  const write = appliedLevel(agent, 'write', catalog);
  const external = appliedLevel(agent, 'external', catalog);
  if (external === 'off' && write === 'off') return { key: 'agentsPage.approval.readOnly', tone: 'muted' };
  if (write === 'ask' || external === 'ask') return { key: 'agentsPage.approval.required', tone: 'warn' };
  return { key: 'agentsPage.approval.policy', tone: 'ok' };
}

/** The tool names a card shows (the always-on reply tool is left out) and how many more there are. */
export function toolSummary(agent: Pick<AgentProfile, 'allowed_tools'>, max = 2): { names: string[]; more: number } {
  const names = agent.allowed_tools.slice(0, max);
  return { names, more: Math.max(0, agent.allowed_tools.length - names.length) };
}

/** An agent's spending cap row, when the owner-only caps list was readable. */
export function capFor(caps: readonly AgentCap[] | null, agentId: string): AgentCap | null {
  return caps?.find((cap) => cap.agent_id === agentId) ?? null;
}

export interface SpendTotals { used: number; limit: number | null; percent: number | null; capped: number; over: number }

/**
 * This month's spending across the listed agents (the default assistant included, as the API lists
 * it). `limit` sums only the agents that have a cap; with none capped it is null, never 0.
 */
export function spendTotals(caps: readonly AgentCap[]): SpendTotals {
  let used = 0, limit = 0, capped = 0, over = 0;
  for (const cap of caps) {
    used += cap.usage.estimated_cost_usd;
    if (cap.monthly_cost_limit_usd !== null) { limit += cap.monthly_cost_limit_usd; capped += 1; }
    if (cap.exceeded) over += 1;
  }
  const total = capped ? limit : null;
  return { used, limit: total, percent: total === null ? null : total <= 0 ? 100 : Math.round((used / total) * 100), capped, over };
}

/** A bar width (0 to 100) for a percent the API may leave null. */
export function barWidth(percent: number | null): number {
  if (percent === null || !Number.isFinite(percent)) return 0;
  return Math.min(100, Math.max(0, percent));
}

/** The most cap rows one `GET /agent-controls/agent-caps` returns (the API's `limit` maximum). */
export const CAPS_PAGE_LIMIT = 100;

/**
 * Every cap row, page by page, so the spending totals never stop at the first page. Stops when the
 * API's `total` is reached or a page comes back empty (never loops forever on a short page).
 */
export async function readAllCaps<T>(readPage: (offset: number, limit: number) => Promise<{ items: T[]; total: number; resets_on: string }>): Promise<{ items: T[]; total: number; resetsOn: string }> {
  const items: T[] = [];
  let offset = 0, total = 0, resetsOn = '';
  for (;;) {
    const page = await readPage(offset, CAPS_PAGE_LIMIT);
    const rows = Array.isArray(page?.items) ? page.items : [];
    items.push(...rows);
    total = typeof page.total === 'number' ? page.total : items.length;
    resetsOn = page.resets_on;
    offset += rows.length;
    if (rows.length === 0 || offset >= total) break;
  }
  return { items, total, resetsOn };
}
