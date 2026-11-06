// Shared pieces of the Agents pages (docs/agents.md#the-agents-page): the avatar, notices, the
// boundaries list, the spending meter and the owner-only spending caps read.
import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import {
  BarChart3, Bot, Briefcase, Calendar, Check, CircleDot, FileText, Heart, Mail, Phone, Search, ShieldAlert, ShoppingCart, Sparkles, Truck, User, Wrench,
} from 'lucide-react';
import * as api from './lib/api';
import { formatUsd } from './lib/admin';
import { capsPath, type AgentCap, type AgentCapsPage } from './lib/agentControls';
import { agentBoundaries, barWidth } from './lib/agentsPage';
import type { AgentCatalog, AgentProfile } from './lib/agents';
import { budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';

export const AGENT_ICONS: Record<string, typeof Bot> = {
  bot: Bot, truck: Truck, search: Search, mail: Mail, calendar: Calendar, 'file-text': FileText, phone: Phone,
  briefcase: Briefcase, user: User, sparkles: Sparkles, wrench: Wrench, chart: BarChart3, 'shopping-cart': ShoppingCart, heart: Heart,
};

export function AgentAvatar({ icon, color, size = 'md' }: { icon: string; color: string; size?: 'md' | 'lg' | 'xl' }) {
  const Icon = AGENT_ICONS[icon] ?? Bot;
  return <span className={`agentAvatar ${size}`} style={{ '--agent-color': color } as CSSProperties} aria-hidden="true"><Icon /></span>;
}

export function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

/** Why something failed, in words that say who can fix it. */
export function explain(error: unknown, action: MessageKey): string {
  if (isPermissionDenied(error)) return describeError(error);
  if (isBudgetExceeded(error)) return t('common.budgetReached', { message: budgetMessage(error) });
  return t(action, { reason: describeError(error) });
}

export type Role = 'owner' | 'member' | 'viewer' | null;

export function useRole(): Role {
  const [role, setRole] = useState<Role>(null);
  useEffect(() => {
    void api.getCurrentMembership().then((m) => setRole(m.role === 'owner' || m.role === 'member' ? m.role : 'viewer')).catch(() => setRole('viewer'));
  }, []);
  return role;
}

/** The owner-only spending caps (docs/agent-controls.md): `denied` for everyone else, never a fake 0. */
export type CapsLoad = { state: 'loading' } | { state: 'ready'; items: AgentCap[]; resetsOn: string; total: number } | { state: 'denied'; message: string } | { state: 'error'; message: string };

export function useAgentCaps(): { caps: CapsLoad; reload: () => void } {
  const [caps, setCaps] = useState<CapsLoad>({ state: 'loading' });
  const load = useCallback(async () => {
    setCaps({ state: 'loading' });
    try {
      // One page of 100 holds every agent a workspace normally has; the page says so if there are more.
      const page = await api.apiRequest<AgentCapsPage>(capsPath('', 0, 100), { method: 'GET' });
      const items = Array.isArray(page?.items) ? page.items : [];
      setCaps({ state: 'ready', items, resetsOn: page.resets_on, total: typeof page.total === 'number' ? page.total : items.length });
    } catch (error) {
      setCaps(isPermissionDenied(error) ? { state: 'denied', message: describeError(error) } : { state: 'error', message: describeError(error) });
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return { caps, reload: () => void load() };
}

/** A thin meter: the words carry the figure; the bar only helps the eye. */
export function Meter({ percent, label }: { percent: number | null; label: string }) {
  const width = barWidth(percent);
  const tone = percent === null ? '' : percent >= 100 ? 'bad' : percent >= 80 ? 'warn' : 'ok';
  return <span className={`agMeter ${tone}`} role="img" aria-label={label}><span style={{ width: `${width}%` }} /></span>;
}

/** "$3.20 of $10.00" for a cap row, or "$3.20, no cap" when it has none. */
export function capText(cap: AgentCap): string {
  return cap.monthly_cost_limit_usd === null
    ? t('agentsPage.spend.noCap', { used: formatUsd(cap.usage.estimated_cost_usd) })
    : t('agentsPage.spend.ofCap', { used: formatUsd(cap.usage.estimated_cost_usd), limit: formatUsd(cap.monthly_cost_limit_usd) });
}

/** The agent's boundaries, each a check with the words; amber when a change happens without asking. */
export function BoundaryList({ agent, catalog }: { agent: AgentProfile; catalog: AgentCatalog | null }) {
  return <ul className="agBoundaries">
    {agentBoundaries(agent, catalog).map((line) => <li key={line.key} className={line.tone}>
      {line.tone === 'ok' ? <Check aria-hidden="true" /> : <ShieldAlert aria-hidden="true" />}<span>{t(line.key)}</span>
    </li>)}
  </ul>;
}
