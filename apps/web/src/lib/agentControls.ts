// Owner agent controls (docs/agent-controls.md): the organization's emergency stop and per-agent
// monthly cost caps. Pure helpers and the API's shapes, so `pnpm --filter @anum/web test:unit`
// covers them without a browser. Who may do what is the API's answer (`can_engage`, `can_release`,
// `can_manage`), never guessed from the token here.
import { formatUsd, meterTone, usagePercent, type MeterTone } from './admin.ts';
import { t } from './i18n.ts';

export interface EmergencyStopView {
  engaged: boolean;
  reason: string | null;
  changed_by: string | null;
  changed_by_you: boolean;
  changed_at: string | null;
  engaged_at: string | null;
}

export interface AgentControls {
  emergency_stop: EmergencyStopView;
  can_engage: boolean;
  can_release: boolean;
  cancelled_tasks: number;
}

export interface UsageTotals {
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
  calls: number;
  unpriced_calls: number;
}

export interface AgentCap {
  agent_id: string;
  name: string;
  status: string;
  sharing: string;
  /** The default assistant: chat, voice and every call no custom agent makes. */
  is_default?: boolean;
  monthly_cost_limit_usd: number | null;
  monthly_token_limit?: number | null;
  total_tokens?: number;
  token_percent_used?: number | null;
  usage: UsageTotals;
  percent_used: number | null;
  exceeded: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

export interface AgentCapsPage {
  period_start: string;
  resets_on: string;
  can_manage: boolean;
  total: number;
  items: AgentCap[];
}

export const CAPS_PAGE_SIZE = 20;
export const REASON_MIN = 3;
export const REASON_MAX = 300;

/** The query string of one page of caps (search and offset paging). */
export function capsPath(query: string, offset: number, limit = CAPS_PAGE_SIZE): string {
  const params = new URLSearchParams({ limit: String(limit), offset: String(Math.max(0, offset)) });
  const q = query.trim();
  if (q) params.set('q', q.slice(0, 120));
  return `/api/v1/agent-controls/agent-caps?${params.toString()}`;
}

/** The reason typed for a change of the switch: trimmed and checked like the API (3 to 300 characters). */
export function parseReason(text: string): { ok: true; value: string } | { ok: false; error: string } {
  const value = text.split(/\s+/).filter(Boolean).join(' ');
  if (value.length < REASON_MIN) return { ok: false, error: t('agentControls.reasonShort', { min: REASON_MIN }) };
  if (value.length > REASON_MAX) return { ok: false, error: t('agentControls.reasonLong', { max: REASON_MAX }) };
  return { ok: true, value };
}

/** A cap typed in USD: empty clears it; otherwise a number from 0 up. */
export function parseCap(text: string): { ok: true; value: number | null } | { ok: false; error: string } {
  const trimmed = text.trim().replace(/^\$/, '').replace(/,/g, '');
  if (!trimmed) return { ok: true, value: null };
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < 0) return { ok: false, error: t('agentControls.capInvalid') };
  if (value > 1_000_000_000) return { ok: false, error: t('agentControls.capTooLarge') };
  return { ok: true, value: Math.round(value * 100) / 100 };
}

export const capDraft = (cap: AgentCap): string => (cap.monthly_cost_limit_usd === null ? '' : String(cap.monthly_cost_limit_usd));
export const tokenDraft = (cap: AgentCap): string => (cap.monthly_token_limit == null ? '' : String(cap.monthly_token_limit));

/** A token cap: empty clears it; otherwise a whole number from 0 up. */
export function parseTokenCap(text: string): { ok: true; value: number | null } | { ok: false; error: string } {
  const trimmed = text.trim().replace(/[,\s]/g, '');
  if (!trimmed) return { ok: true, value: null };
  if (!/^\d+$/.test(trimmed)) return { ok: false, error: t('agentControls.tokenInvalid') };
  const value = Number(trimmed);
  if (!Number.isSafeInteger(value) || value > 1e15) return { ok: false, error: t('agentControls.capTooLarge') };
  return { ok: true, value };
}

/** Usage against the token cap; null without one. */
export function tokenMeter(cap: AgentCap): CapMeter | null {
  const limit = cap.monthly_token_limit ?? null;
  if (limit === null) return null;
  const used = cap.total_tokens ?? cap.usage.input_tokens + cap.usage.output_tokens;
  const raw = usagePercent(used, limit);
  const count = (value: number) => new Intl.NumberFormat('en-US').format(value);
  return { percent: raw, barPercent: raw === null ? 0 : Math.min(100, Math.max(0, raw)), tone: meterTone(raw), usedText: count(used), limitText: count(limit) };
}

export type HeldKind = 'approval' | 'mail' | 'post';
export interface HeldItem { kind: HeldKind | string; id: string; title: string; detail: string; at: string | null; by: string | null }
export interface HeldPage { engaged: boolean; total: number; limit: number; offset: number; items: HeldItem[] }
export const HELD_PAGE_SIZE = 20;

export function heldPath(offset: number, limit = HELD_PAGE_SIZE): string {
  return `/api/v1/agent-controls/held?limit=${limit}&offset=${Math.max(0, offset)}`;
}

/** "Approval", "Scheduled email", "Scheduled post" in the page language. */
export function heldKindLabel(kind: string): string {
  if (kind === 'approval') return t('agentControls.held.approval');
  if (kind === 'mail') return t('agentControls.held.mail');
  if (kind === 'post') return t('agentControls.held.post');
  return kind;
}

export interface CapMeter { percent: number | null; barPercent: number; tone: MeterTone; usedText: string; limitText: string }

/** Usage against the cap, with the API's thresholds (80% warns, 100% refuses). */
export function capMeter(cap: AgentCap): CapMeter {
  const raw = usagePercent(cap.usage.estimated_cost_usd, cap.monthly_cost_limit_usd);
  return {
    percent: raw,
    barPercent: raw === null ? 0 : Math.min(100, Math.max(0, raw)),
    tone: meterTone(raw),
    usedText: formatUsd(cap.usage.estimated_cost_usd),
    limitText: cap.monthly_cost_limit_usd === null ? t('agentControls.noCap') : formatUsd(cap.monthly_cost_limit_usd),
  };
}

/** What the switch's state means for this person, in one sentence. */
export function stopSummary(controls: AgentControls): string {
  if (!controls.emergency_stop.engaged) return t('agentControls.offSummary');
  return controls.can_release ? t('agentControls.onSummaryRelease') : t('agentControls.onSummaryWait');
}
