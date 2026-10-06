export type TaskStatus =
  | 'created'
  | 'queued'
  | 'running'
  | 'waiting_approval'
  | 'completed'
  | 'failed'
  | 'cancelled';

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'expired';
export type RiskLevel = 'low' | 'medium' | 'high' | 'blocked';

export interface TenantContext {
  tenantId: string;
  workspaceId: string;
  userId: string;
  roles: string[];
}

export interface Task {
  id: string;
  title: string;
  prompt: string;
  status: TaskStatus;
  tenantId: string;
  workspaceId: string;
  createdAt: string;
  updatedAt: string;
  /** User id of whoever created the task. */
  createdBy?: string | null;
}

export interface AgentRunStep {
  id: string;
  type: 'queued' | 'retrieval' | 'model_call' | 'tool_proposal' | 'approval_wait' | 'tool_result' | 'final';
  summary: string;
  createdAt: string;
  /** Step details from the API (ids, sources, policy outcome); never retrieved text. */
  metadata?: Record<string, unknown>;
}

/** One retrieved passage a run's prompt used, as recorded on its `retrieval` step. */
export interface RetrievedSourceUsed {
  chunkId: string;
  sourceType: 'memory' | 'file';
  sourceId: string;
  chunkIndex: number;
  score: number;
  truncated: boolean;
}

export interface AgentRun {
  id: string;
  taskId: string;
  status: TaskStatus;
  steps: AgentRunStep[];
  result?: string;
}

export interface Approval {
  id: string;
  taskId: string;
  /** The exact tool the agent will call. */
  action: string;
  riskLevel: RiskLevel;
  status: ApprovalStatus;
  reason: string;
  createdAt: string;
  runId?: string | null;
  stepId?: string | null;
  /** The exact tool arguments; secret-looking values arrive as "[REDACTED]". */
  arguments?: Record<string, unknown>;
  /** SHA-256 of the canonical tool call. Approving sends back the hash that was shown. */
  payloadHash?: string | null;
  /** Pending approvals lapse at this time and can no longer be approved. */
  expiresAt?: string | null;
  decidedAt?: string | null;
  /** User id of whoever approved or rejected. */
  decidedBy?: string | null;
  /** Optional reason the decider gave (at most 500 characters). */
  decisionReason?: string | null;
  /** User whose run proposed the call; under the two-person rule they cannot approve it. */
  requestedBy?: string | null;
  /** Configured integration host the tool will contact (host only), null for internal tools. */
  target?: string | null;
  /**
   * Distinct approvals the matching organization approval rules require (approval chains,
   * docs/approvals-and-risk.md). 1 without a chain; the approval stays pending until reached.
   */
  requiredApprovals?: number;
  /** Who has approved so far, oldest first. */
  approvers?: ApprovalApprover[];
}

/** One recorded approve decision of an approval (one per distinct user). */
export interface ApprovalApprover {
  userId: string;
  approvedAt: string;
  reason?: string | null;
}

/**
 * One of the caller's own active workspace memberships in their tenant
 * (`GET /api/v1/me/workspace-memberships`, docs/identity.md#my-workspaces).
 */
export interface CallerMembership {
  tenantId: string;
  workspaceId: string;
  /** The workspace's display name, or null when it cannot be read. */
  workspaceName: string | null;
  role: string;
  status: 'active';
}

/** Per-workspace approval policy; only owners change it (docs/approvals-and-risk.md). */
export interface WorkspaceApprovalPolicy {
  twoPersonRule: boolean;
  mediumRiskRequiresApproval: boolean;
  updatedBy?: string | null;
  updatedAt?: string | null;
}

export interface DomainEvent<TPayload = Record<string, unknown>> {
  id: string;
  type: string;
  version: number;
  tenantId: string;
  workspaceId?: string;
  subject: string;
  correlationId: string;
  createdAt: string;
  payload: TPayload;
}

export * from './governance.js';
export * from './phase2.js';
export * from './admin.js';
