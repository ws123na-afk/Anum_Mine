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
}

export interface AgentRunStep {
  id: string;
  type: 'model_call' | 'tool_proposal' | 'approval_wait' | 'tool_result' | 'final';
  summary: string;
  createdAt: string;
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
