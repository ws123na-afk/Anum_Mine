export type TaskStatus =
  | 'created'
  | 'queued'
  | 'running'
  | 'waiting_approval'
  | 'completed'
  | 'failed'
  | 'cancelled';

/** How urgent a task is (0036_task_planning, docs/tasks.md); stored tasks without one read as medium. */
export type TaskPriority = 'low' | 'medium' | 'high';

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
  /** The department assistant the task runs as (docs/team-work.md). */
  assistantId?: string | null;
  /** The agent the task runs as (docs/agents.md), or null for the plain runtime. */
  agentId?: string | null;
  /** Set while the task is archived (hidden from the default list, history kept; docs/tasks.md). */
  archivedAt?: string | null;
  archivedBy?: string | null;
  /** Planning fields (docs/tasks.md): never change what the agent may do. */
  priority?: TaskPriority;
  /** When the result is wanted, UTC; null for no due date. */
  dueAt?: string | null;
}

/** `GET /api/v1/tasks/summary`: per-status counts for the list's other filters (docs/tasks.md). */
export interface TaskSummary {
  total: number;
  byStatus: Partial<Record<TaskStatus, number>>;
  archived: number;
  /** Distinct creator user ids in the workspace (at most 200). */
  creators: string[];
}

export type TaskBulkAction = 'archive' | 'unarchive' | 'cancel';

/** One task's outcome in `POST /api/v1/tasks/bulk`; `statusCode` is what the single-task route answers. */
export interface TaskBulkItemResult {
  taskId: string;
  ok: boolean;
  statusCode: number;
  message: string | null;
  task: Task | null;
}

export interface TaskBulkResult {
  action: TaskBulkAction;
  succeeded: number;
  failed: number;
  results: TaskBulkItemResult[];
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
  createdAt?: string;
  updatedAt?: string;
  /** Checkpoint phase (`planning`, `tool_ready`, `waiting_approval`, `executing`, or a terminal phase). */
  phase?: string;
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
  /**
   * Who may approve this approval and why (approval routing by AED amount,
   * docs/approvals-and-risk.md#approval-routing), and what the signed-in person may do now.
   */
  routing?: ApprovalRouting | null;
  /** Recipients of the call now on the contact suppression list (masked): the send will be refused. */
  suppressed?: SuppressedRecipient[];
}

/** An approver: `owner`, `org_admin`, `role:owner`, `role:member` or `user:<user id>`. */
export type ApproverToken = string;

/** Why a routed approval went to its approvers (structured, so both languages word it). */
export interface ApprovalRouteReason {
  code: 'amount_above' | 'amount_at_most' | 'amount_unknown' | 'percent_at_most' | 'percent_unknown' | 'disputed' | 'not_disputed' | 'no_band' | 'rule';
  rule?: string | null;
  applies: boolean;
  currency: string;
  amount?: string | null;
  total?: string | null;
  threshold?: string | null;
  related: number;
  percent?: string | null;
}

export interface ApprovalRouteStep {
  /** The decider must be one of each list (one list per rule that has this step). */
  approvers: ApproverToken[][];
  approvedBy?: string | null;
}

export type ApprovalRoutingRefusal = 'not_pending' | 'not_listed' | 'own_request' | 'already_approved' | 'viewer' | 'no_hash';

export interface ApprovalRouting {
  /** False: no amount rule applies; workspace owners decide as before. */
  routed: boolean;
  rules: string[];
  steps: ApprovalRouteStep[];
  /** Zero-based step waiting for a decision. */
  currentStep: number;
  reasons: ApprovalRouteReason[];
  /** Rules matched the tool but none of their bands applied: owners decide. */
  fallback: boolean;
  canApprove: boolean;
  canReject: boolean;
  refusal?: ApprovalRoutingRefusal | null;
  /** One English sentence from the API (the clients word it from the fields above). */
  summary: string;
}

export interface SuppressedRecipient {
  /** Masked contact, for example +971•••4567. */
  recipient: string;
  channel: string;
  purpose: string;
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
  /** The workspace is archived (read-only until an owner restores it). */
  archived?: boolean;
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
export * from './account.js';
export * from './autonomy.js';
export * from './learning.js';
export * from './commerce.js';
export * from './command.js';
export * from './library.js';
export * from './team.js';
export * from './agents.js';
export * from './conversations.js';
export * from './setupPlans.js';
export * from './voice.js';
export * from './finance.js';
export * from './calls.js';
export * from './business.js';
export * from './businessPipeline.js';
export * from './officePresence.js';
export * from './officeCampus.js';
export * from './officeWorkstation.js';
export * from './officeFlow.js';
export * from './officeProvisioning.js';
export * from './executive.js';
export * from './officeBuilder.js';
export * from './officeStarterCampus.js';
