// Workspace membership, invitations and monthly model budgets.
// Wire routes: docs/identity.md (Invitations and Membership Management) and
// docs/model-gateway.md (Monthly budgets). Clients map the API's snake_case JSON to these.

export type WorkspaceRole = 'owner' | 'member' | 'viewer';

/** `expired` is derived by the API for a pending invitation past `expiresAt`. */
export type InvitationStatus = 'pending' | 'accepted' | 'revoked' | 'expired';

export interface WorkspaceMember {
  tenantId: string;
  workspaceId: string;
  userId: string;
  role: WorkspaceRole;
  active: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface WorkspaceInvitation {
  id: string;
  tenantId: string;
  workspaceId: string;
  role: WorkspaceRole;
  inviteeUserId: string | null;
  inviteeEmail: string | null;
  status: InvitationStatus;
  createdByUserId: string;
  expiresAt: string;
  acceptedByUserId: string | null;
  acceptedAt: string | null;
  revokedAt: string | null;
  createdAt: string;
}

export interface InvitationRequest {
  role: WorkspaceRole;
  inviteeUserId?: string;
  inviteeEmail?: string;
  /** 1 to 720; the API defaults to 168 (7 days). */
  ttlHours?: number;
}

/** The token is returned exactly once; only its hash is stored. */
export interface CreatedInvitation {
  invitation: WorkspaceInvitation;
  token: string;
}

export interface AcceptedInvitation {
  invitation: WorkspaceInvitation;
  membership: WorkspaceMember;
}

export type BudgetScope = 'tenant' | 'workspace';
export type BudgetKind = 'cost' | 'tokens';

/** Null means no limit; 0 blocks every model call. */
export interface ModelBudgetLimits {
  monthlyCostLimitUsd: number | null;
  monthlyTokenLimit: number | null;
}

export interface ModelBudget extends ModelBudgetLimits {
  scope: BudgetScope;
  updatedAt: string;
  updatedBy: string;
}

export interface ModelUsageTotals {
  inputTokens: number;
  outputTokens: number;
  estimatedCostUsd: number;
  calls: number;
  /** Calls whose model has no price estimate: they count tokens but no cost. */
  unpricedCalls: number;
}

export interface ModelBudgetScopeView {
  budget: ModelBudget | null;
  usage: ModelUsageTotals;
  totalTokens: number;
  exceeded: boolean;
}

/** This UTC calendar month: `periodStart` and `resetsOn` are ISO dates (YYYY-MM-DD). */
export interface ModelBudgetOverview {
  periodStart: string;
  resetsOn: string;
  tenant: ModelBudgetScopeView;
  workspace: ModelBudgetScopeView;
}

/** Stable error codes of the API's error envelope `{ error: { code, message, correlation_id } }`. */
export type ApiErrorCode =
  | 'validation_error'
  | 'bad_request'
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'conflict'
  | 'model_budget_exceeded'
  | 'gone'
  | 'payload_too_large'
  | 'rate_limited'
  | 'service_unavailable'
  | 'internal_error';
