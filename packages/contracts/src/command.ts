// ANUM Command (docs/command.md): plan preview, command records, live view, next steps,
// suggestions and quick commands. Client-side shapes (camelCase) of the /api/v1/commands
// and /api/v1/quick-commands routes; clients map the API's snake_case fields onto them.

import type { Approval, RiskLevel, Task } from './index.js';

export type CommandLocale = 'en' | 'ar';
export type CommandAgentId = 'email' | 'whatsapp' | 'commerce' | 'research' | 'action' | 'calendar' | 'tyre' | 'writer' | 'general';
export type ApprovalNeed = 'none' | 'required' | 'blocked';
export type CommandSource = 'typed' | 'voice' | 'quick_command' | 'suggestion' | 'next_step';
export type CommandStatus =
  | 'ready' | 'running' | 'waiting_approval' | 'completed' | 'partial' | 'failed' | 'cancelled';
export type QuickCommandCategory = 'work' | 'personal' | 'custom';

export interface CommandAgent {
  id: CommandAgentId;
  /** In the request's language. */
  name: string;
}

export interface PlanTool {
  name: string;
  description: string;
  category: string | null;
  actionKind: string | null;
  /** Integration host the tool contacts, or null for internal tools. */
  target: string | null;
}

/** One step of `POST /api/v1/commands/preview`. */
export interface PlanStep {
  position: number;
  title: string;
  instruction: string;
  agent: CommandAgent;
  skills: { id: string; name: string }[];
  tool: PlanTool | null;
  riskLevel: RiskLevel;
  /** `required`: the run will pause for approval; `blocked`: the step cannot be confirmed. */
  approval: ApprovalNeed;
  approvalReason: string;
  /** False for agents without a live integration (calendar, tyre); `note` says what happens instead. */
  connected: boolean;
  note: string | null;
}

export interface CommandPlan {
  text: string;
  language: CommandLocale;
  steps: PlanStep[];
  agents: CommandAgent[];
  approvalsRequired: number;
  blocked: number;
}

/** Body of `POST /api/v1/commands`: the confirmed steps, in order. */
export interface CommandCreate {
  text: string;
  steps: { instruction: string }[];
  source?: CommandSource;
  locale?: CommandLocale;
  /** Workspace file ids (at most 5). */
  attachments?: string[];
  quickCommandId?: string | null;
}

export interface CommandStepView {
  position: number;
  title: string;
  instruction: string;
  agent: CommandAgent;
  tool: string | null;
  riskLevel: RiskLevel;
  approval: ApprovalNeed;
  taskId: string;
  task: Task | null;
  /** The task's status, or `missing`. */
  status: string;
  progress: number;
  runId: string | null;
  result: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  approvals: Approval[];
}

export interface CommandTimelineEntry {
  id: string;
  at: string;
  /** Canonical event name (docs/events.md). */
  type: string;
  taskId: string;
  position: number;
  agent: CommandAgent;
  title: string;
}

export interface CommandFile {
  kind: 'attachment' | 'run_log';
  name: string;
  /** API path to download with the caller's headers. */
  path: string;
  fileId: string | null;
  taskId: string | null;
  contentType: string | null;
}

export interface CommandAttachment {
  fileId: string;
  name: string;
  contentType: string | null;
}

/** `GET /api/v1/commands` items. */
export interface CommandSummary {
  id: string;
  text: string;
  source: CommandSource;
  locale: CommandLocale;
  status: CommandStatus;
  progress: number;
  stepCount: number;
  completedSteps: number;
  pendingApprovals: number;
  agents: CommandAgent[];
  createdAt: string;
  updatedAt: string;
}

/** `GET /api/v1/commands/{id}` and the answer of `POST /api/v1/commands`. */
export interface CommandView extends CommandSummary {
  createdBy: string;
  quickCommandId: string | null;
  steps: CommandStepView[];
  /** Pending approvals of the command, for "Approve all" (one request each, with its payload hash). */
  pending: Approval[];
  timeline: CommandTimelineEntry[];
  files: CommandFile[];
  attachments: CommandAttachment[];
  startedAt: string | null;
  finishedAt: string | null;
  durationSeconds: number | null;
}

/** `GET /api/v1/commands/{id}/next-steps`. A `command` item's `text` starts a new command. */
export interface CommandNextStep {
  id: string;
  kind: 'command' | 'review_approvals' | 'save_quick_command';
  label: string;
  text: string | null;
}

/** `GET /api/v1/commands/suggestions`. */
export interface CommandSuggestion {
  kind: 'quick' | 'recent' | 'starter';
  text: string;
  title: string;
  quickCommandId: string | null;
  useCount: number;
  lastUsedAt: string | null;
}

/** `/api/v1/quick-commands`. */
export interface QuickCommand {
  id: string;
  title: string;
  text: string;
  category: QuickCommandCategory;
  position: number;
  useCount: number;
  lastUsedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface QuickCommandInput {
  title: string;
  text: string;
  category?: QuickCommandCategory;
  position?: number;
}
