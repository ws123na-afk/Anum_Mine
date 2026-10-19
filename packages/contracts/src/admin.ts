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
  /** Verified email of the invitation the member accepted, when there was one. */
  email?: string | null;
}

/** `GET /api/v1/workspace-members/summary`: counts independent of search and paging. */
export interface MembersSummary {
  total: number;
  active: number;
  deactivated: number;
  activeOwners: number;
  pendingInvitations: number;
}

/** One page of `GET /api/v1/workspace-members?limit=` (the cursor comes from `X-Next-Cursor`). */
export interface MemberPage {
  items: WorkspaceMember[];
  nextCursor: string | null;
}

/** A workspace (docs/identity.md#workspace-lifecycle). Archived workspaces are read-only. */
export interface WorkspaceInfo {
  id: string;
  tenantId: string;
  name: string;
  createdAt: string;
  updatedAt: string;
  archivedAt: string | null;
  archivedBy: string | null;
}

/** `GET /api/v1/workspaces/current/permissions`: the API's answer for the caller. */
export interface WorkspacePermissions {
  workspaceId: string;
  role: WorkspaceRole | null;
  organizationAdmin: boolean;
  canManageWorkspace: boolean;
  canCreateWorkspace: boolean;
  canManageMembers: boolean;
  canReadAudit: boolean;
  canExportAudit: boolean;
}

/** One append-only audit record (docs/governance-and-scale.md#audit-log). */
export interface AuditRecord {
  id: string;
  tenantId: string;
  workspaceId: string;
  actor: string;
  action: string;
  target: string;
  outcome: string;
  correlationId: string;
  createdAt: string;
  /** Ids, counts and settings; secret-looking keys arrive as "[REDACTED]". */
  metadata: Record<string, unknown>;
}

/** Audit log filters. `action` ending in `*` matches a prefix; `until` is exclusive. */
export interface AuditFilters {
  actor?: string;
  action?: string;
  target?: string;
  outcome?: string;
  correlationId?: string;
  since?: string;
  until?: string;
  q?: string;
}

/** `GET /api/v1/audit-records`: newest first, keyset paged. */
export interface AuditPage {
  items: AuditRecord[];
  nextCursor: string | null;
  retention: string;
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
  /** Email the invitee the accept link (default true; only with inviteeEmail). */
  sendEmail?: boolean;
  /** The address this app is open at, for the link in the email (the server checks it against its allowed origins). */
  appUrl?: string;
}

/** What happened to the invitation email (docs/identity.md#invitation-email). */
export type InvitationEmailStatus = 'sent' | 'failed' | 'not_configured' | 'not_requested';

export interface InvitationEmail {
  status: InvitationEmailStatus;
  to: string | null;
  attempts: number;
  error: string | null;
  /** False when the email carried the token to paste instead of a link. */
  linkIncluded: boolean;
}

/** Whether this server can email invitations at all, and where their links point. */
export interface InvitationEmailSetup {
  configured: boolean;
  linkBase: string | null;
}

/** The token is returned exactly once; only its hash is stored. */
export interface CreatedInvitation {
  invitation: WorkspaceInvitation;
  token: string;
  email: InvitationEmail;
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
  /**
   * Whether the caller may change the organization (tenant) budget: the organization
   * administrator role `org_admin` (docs/governance-and-scale.md#who-may-change-what).
   */
  canManageTenantBudget: boolean;
}

/** Stable error codes of the API's error envelope `{ error: { code, message, correlation_id } }`. */
export type ApiErrorCode =
  | 'validation_error'
  | 'bad_request'
  | 'unauthorized'
  | 'forbidden'
  /** A valid token without a tenant: a self sign-up nobody has assigned to an organization yet (403). */
  | 'account_pending'
  | 'not_found'
  | 'conflict'
  | 'model_budget_exceeded'
  | 'gone'
  | 'payload_too_large'
  | 'rate_limited'
  | 'service_unavailable'
  | 'internal_error';
