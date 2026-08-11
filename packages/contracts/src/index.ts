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
  action: string;
  riskLevel: RiskLevel;
  status: ApprovalStatus;
  reason: string;
  createdAt: string;
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
