// Members, invitations and model budgets: wire mapping and display logic.
// docs/identity.md (Invitations and Membership Management), docs/model-gateway.md (Monthly budgets).
// Pure functions, so `pnpm --filter @anum/web test:unit` can exercise them without a DOM.
import type {
  AcceptedInvitation,
  BudgetKind,
  CreatedInvitation,
  InvitationRequest,
  ModelBudget,
  ModelBudgetLimits,
  ModelBudgetOverview,
  ModelBudgetScopeView,
  WorkspaceInvitation,
  WorkspaceMember,
  WorkspaceRole,
} from '@anum/contracts';

export const ROLES: readonly WorkspaceRole[] = ['owner', 'member', 'viewer'];
export const INVITATION_PREFIX = 'anum_inv_';
export const DEFAULT_TTL_HOURS = 168;
export const MAX_TTL_HOURS = 720;

// ------------------------------------------------------------------ wire shapes

export interface ApiMember { tenant_id: string; workspace_id: string; user_id: string; role: string; active: boolean; created_at: string; updated_at: string }
export interface ApiInvitation {
  id: string; tenant_id: string; workspace_id: string; role: string;
  invitee_user_id: string | null; invitee_email: string | null; status: string;
  created_by_user_id: string; expires_at: string; accepted_by_user_id: string | null;
  accepted_at: string | null; revoked_at: string | null; created_at: string;
}
export interface ApiInvitationCreated { invitation: ApiInvitation; token: string }
export interface ApiInvitationAccepted { invitation: ApiInvitation; membership: ApiMember }
interface ApiBudget { scope: string; monthly_cost_limit_usd: number | null; monthly_token_limit: number | null; updated_at: string; updated_by: string }
interface ApiScopeView {
  budget: ApiBudget | null;
  usage: { input_tokens: number; output_tokens: number; estimated_cost_usd: number; calls: number; unpriced_calls: number };
  total_tokens: number;
  exceeded: boolean;
}
export interface ApiBudgetOverview { period_start: string; resets_on: string; tenant: ApiScopeView; workspace: ApiScopeView }

const role = (value: string): WorkspaceRole => (ROLES as readonly string[]).includes(value) ? value as WorkspaceRole : 'viewer';

export function mapMember(value: ApiMember): WorkspaceMember {
  return { tenantId: value.tenant_id, workspaceId: value.workspace_id, userId: value.user_id, role: role(value.role), active: value.active, createdAt: value.created_at, updatedAt: value.updated_at };
}

export function mapInvitation(value: ApiInvitation): WorkspaceInvitation {
  const status = ['pending', 'accepted', 'revoked', 'expired'].includes(value.status) ? value.status as WorkspaceInvitation['status'] : 'pending';
  return {
    id: value.id, tenantId: value.tenant_id, workspaceId: value.workspace_id, role: role(value.role),
    inviteeUserId: value.invitee_user_id, inviteeEmail: value.invitee_email, status,
    createdByUserId: value.created_by_user_id, expiresAt: value.expires_at,
    acceptedByUserId: value.accepted_by_user_id, acceptedAt: value.accepted_at, revokedAt: value.revoked_at, createdAt: value.created_at,
  };
}

export const mapCreatedInvitation = (value: ApiInvitationCreated): CreatedInvitation => ({ invitation: mapInvitation(value.invitation), token: value.token });
export const mapAcceptedInvitation = (value: ApiInvitationAccepted): AcceptedInvitation => ({ invitation: mapInvitation(value.invitation), membership: mapMember(value.membership) });

function mapBudget(value: ApiBudget | null): ModelBudget | null {
  if (!value) return null;
  return { scope: value.scope === 'tenant' ? 'tenant' : 'workspace', monthlyCostLimitUsd: value.monthly_cost_limit_usd, monthlyTokenLimit: value.monthly_token_limit, updatedAt: value.updated_at, updatedBy: value.updated_by };
}

function mapScope(value: ApiScopeView): ModelBudgetScopeView {
  return {
    budget: mapBudget(value.budget),
    usage: { inputTokens: value.usage.input_tokens, outputTokens: value.usage.output_tokens, estimatedCostUsd: value.usage.estimated_cost_usd, calls: value.usage.calls, unpricedCalls: value.usage.unpriced_calls },
    totalTokens: value.total_tokens,
    exceeded: value.exceeded,
  };
}

export function mapBudgetOverview(value: ApiBudgetOverview): ModelBudgetOverview {
  return { periodStart: value.period_start, resetsOn: value.resets_on, tenant: mapScope(value.tenant), workspace: mapScope(value.workspace) };
}

export function limitsBody(limits: ModelBudgetLimits): { monthly_cost_limit_usd: number | null; monthly_token_limit: number | null } {
  return { monthly_cost_limit_usd: limits.monthlyCostLimitUsd, monthly_token_limit: limits.monthlyTokenLimit };
}

// ------------------------------------------------------------------ invitations

export type InvitationDraft = { role: WorkspaceRole; userId: string; email: string; ttlHours: string };
export type DraftResult<T> = { ok: true; value: T } | { ok: false; error: string };

/** Validate the invite form the way the API does, so mistakes show before the request. */
export function invitationRequest(draft: InvitationDraft): DraftResult<InvitationRequest> {
  const userId = draft.userId.trim();
  const email = draft.email.trim();
  if (!userId && !email) return { ok: false, error: 'Enter the invitee’s user id, email address, or both.' };
  if (userId.length > 120) return { ok: false, error: 'User ids are at most 120 characters.' };
  if (email) {
    const [local, domain = ''] = email.split(/@(.*)/s);
    if (!email.includes('@') || !local || !domain.includes('.') || /\s/.test(email) || email.length > 320) {
      return { ok: false, error: 'Enter a valid email address.' };
    }
  }
  const ttlText = draft.ttlHours.trim();
  const ttl = ttlText ? Number(ttlText) : DEFAULT_TTL_HOURS;
  if (!Number.isInteger(ttl) || ttl < 1 || ttl > MAX_TTL_HOURS) return { ok: false, error: `Expiry must be a whole number of hours from 1 to ${MAX_TTL_HOURS}.` };
  return { ok: true, value: { role: draft.role, ...(userId ? { inviteeUserId: userId } : {}), ...(email ? { inviteeEmail: email } : {}), ttlHours: ttl } };
}

export function invitationBody(request: InvitationRequest): Record<string, unknown> {
  return {
    role: request.role,
    ...(request.inviteeUserId ? { invitee_user_id: request.inviteeUserId } : {}),
    ...(request.inviteeEmail ? { invitee_email: request.inviteeEmail } : {}),
    ...(request.ttlHours ? { ttl_hours: request.ttlHours } : {}),
  };
}

/**
 * A link that opens the web app on the accept screen. The token travels in the URL fragment,
 * which browsers never send to a server, and the workspace tells the client which
 * `x-workspace-id` to accept it in.
 */
export function invitationLink(appUrl: string, token: string, workspaceId: string): string {
  const base = appUrl.split('#')[0];
  return `${base}#invitation=${encodeURIComponent(token)}&workspace=${encodeURIComponent(workspaceId)}`;
}

/** Read a pasted token or invitation link. Returns null when it holds no ANUM invitation token. */
export function parseInvitationInput(text: string): { token: string; workspaceId: string | null } | null {
  const value = text.trim();
  if (!value) return null;
  if (value.startsWith(INVITATION_PREFIX) && !/[\s&#?]/.test(value)) return { token: value, workspaceId: null };
  const params = new URLSearchParams(value.includes('#') ? value.slice(value.indexOf('#') + 1) : value.includes('?') ? value.slice(value.indexOf('?') + 1) : '');
  const token = params.get('invitation');
  if (!token || !token.startsWith(INVITATION_PREFIX)) return null;
  const workspace = params.get('workspace');
  return { token, workspaceId: workspace && workspace.trim() ? workspace.trim() : null };
}

export function inviteeLabel(invitation: WorkspaceInvitation): string {
  return [invitation.inviteeUserId, invitation.inviteeEmail].filter(Boolean).join(' · ') || 'Anyone with the token';
}

/** "Expires in 3 d", "Expires in 5 h", "Expired 2 h ago". */
export function expiryLabel(expiresAt: string, now: Date = new Date()): string {
  const ms = new Date(expiresAt).getTime() - now.getTime();
  if (Number.isNaN(ms)) return 'Expiry unknown';
  const abs = Math.abs(ms);
  const amount = abs >= 86_400_000 ? `${Math.floor(abs / 86_400_000)} d` : abs >= 3_600_000 ? `${Math.floor(abs / 3_600_000)} h` : `${Math.max(1, Math.floor(abs / 60_000))} min`;
  return ms > 0 ? `Expires in ${amount}` : `Expired ${amount} ago`;
}

export function activeOwnerCount(members: WorkspaceMember[]): number {
  return members.filter((member) => member.active && member.role === 'owner').length;
}

/** The only active owner: the API refuses to demote or deactivate them (409). */
export function isLastActiveOwner(members: WorkspaceMember[], member: WorkspaceMember): boolean {
  return member.active && member.role === 'owner' && activeOwnerCount(members) === 1;
}

/** Active members first, then owners before members before viewers, then by user id. */
export function sortMembers(members: WorkspaceMember[]): WorkspaceMember[] {
  return [...members].sort((a, b) => Number(b.active) - Number(a.active) || ROLES.indexOf(a.role) - ROLES.indexOf(b.role) || a.userId.localeCompare(b.userId));
}

// ------------------------------------------------------------------ budgets

export type MeterTone = 'ok' | 'warn' | 'stop' | 'none';
export interface BudgetMeter {
  kind: BudgetKind;
  label: string;
  used: number;
  limit: number | null;
  /** Share of the limit used, 0 to 100 (capped for the bar); null without a limit. */
  percent: number | null;
  /** Uncapped share, for the text. */
  rawPercent: number | null;
  tone: MeterTone;
  usedText: string;
  limitText: string;
}

/** Used share of a limit in percent. A zero limit blocks everything, so it reads as 100%. */
export function usagePercent(used: number, limit: number | null): number | null {
  if (limit === null) return null;
  if (limit <= 0) return 100;
  return (used / limit) * 100;
}

/** Thresholds match the API's: 80% logs a warning, 100% refuses calls. */
export function meterTone(percent: number | null): MeterTone {
  if (percent === null) return 'none';
  if (percent >= 100) return 'stop';
  if (percent >= 80) return 'warn';
  return 'ok';
}

export function formatUsd(value: number): string {
  if (value > 0 && value < 0.01) return '< $0.01';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: value >= 1000 ? 0 : 2 }).format(value);
}

export function formatTokens(value: number): string {
  return new Intl.NumberFormat('en-US').format(Math.round(value));
}

export function budgetMeters(view: ModelBudgetScopeView): BudgetMeter[] {
  const make = (kind: BudgetKind, used: number, limit: number | null): BudgetMeter => {
    const raw = usagePercent(used, limit);
    const format = kind === 'cost' ? formatUsd : formatTokens;
    return {
      kind,
      label: kind === 'cost' ? 'Estimated cost' : 'Tokens',
      used,
      limit,
      rawPercent: raw,
      percent: raw === null ? null : Math.min(100, Math.max(0, raw)),
      tone: meterTone(raw),
      usedText: format(used),
      limitText: limit === null ? 'No limit' : format(limit),
    };
  };
  return [
    make('cost', view.usage.estimatedCostUsd, view.budget?.monthlyCostLimitUsd ?? null),
    make('tokens', view.totalTokens, view.budget?.monthlyTokenLimit ?? null),
  ];
}

/** "1 November 2026 (UTC)" for an ISO date; the API's months are UTC calendar months. */
export function formatResetDate(isoDate: string): string {
  const date = new Date(`${isoDate}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return isoDate;
  return `${new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(date)} (UTC)`;
}

export type LimitDraft = { cost: string; tokens: string };

export function limitDraft(budget: ModelBudget | null): LimitDraft {
  return {
    cost: budget?.monthlyCostLimitUsd === null || budget?.monthlyCostLimitUsd === undefined ? '' : String(budget.monthlyCostLimitUsd),
    tokens: budget?.monthlyTokenLimit === null || budget?.monthlyTokenLimit === undefined ? '' : String(budget.monthlyTokenLimit),
  };
}

/** Empty means no limit. Cost is USD up to 1e9; tokens a whole number up to 1e15 (the API's bounds). */
export function parseLimits(draft: LimitDraft): DraftResult<ModelBudgetLimits> {
  const costText = draft.cost.trim().replace(/^\$/, '').replaceAll(',', '');
  const tokenText = draft.tokens.trim().replaceAll(',', '').replaceAll('_', '');
  let cost: number | null = null;
  let tokens: number | null = null;
  if (costText) {
    cost = Number(costText);
    if (!Number.isFinite(cost) || cost < 0 || cost > 1_000_000_000) return { ok: false, error: 'Cost limit must be a dollar amount from 0 to 1,000,000,000, or empty for no limit.' };
  }
  if (tokenText) {
    tokens = Number(tokenText);
    if (!Number.isSafeInteger(tokens) || tokens < 0 || tokens > 10 ** 15) return { ok: false, error: 'Token limit must be a whole number from 0 to 10^15, or empty for no limit.' };
  }
  return { ok: true, value: { monthlyCostLimitUsd: cost, monthlyTokenLimit: tokens } };
}
