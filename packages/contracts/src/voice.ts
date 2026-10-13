// Voice actions (docs/voice.md#actions): what a spoken request for work did, as the
// `action` event of `POST /api/v1/voice/sessions/{id}/ask/stream` (and the `action` field
// of `/ask`) sends it (services/api/anum_api/voice_actions.py). Wire shapes are snake_case.

import type { TaskStatus } from './index.js';

/** What was asked for. */
export type VoiceActionKind =
  | 'create_task'
  | 'assign'
  | 'run_task'
  | 'cancel_task'
  | 'task_status'
  | 'run_quick_command';

/**
 * Where it stands: `running` (the task exists and the runtime is working on it), `completed`,
 * `approval` (a tool call waits in Approvals), `failed`, `cancelled`, `created` (saved, not
 * started), `status` (a read-only answer about a task), or nothing done: `not_found`,
 * `ambiguous` (`candidates` lists the matches), `denied` (the role cannot do it) and
 * `needs_detail` (say what to do).
 */
export type VoiceActionState =
  | 'running'
  | 'completed'
  | 'approval'
  | 'failed'
  | 'cancelled'
  | 'created'
  | 'status'
  | 'not_found'
  | 'ambiguous'
  | 'denied'
  | 'needs_detail';

/** The approval a run paused for, as approvers see it; decided only in Approvals. */
export interface VoiceActionApproval {
  id: string;
  action: string;
  risk_level: string;
  status: string;
  reason: string;
  /** SHA-256 of the exact call; approving sends back this hash. */
  payload_hash: string | null;
  arguments: Record<string, unknown>;
  target: string | null;
  expires_at: string | null;
  /** Whether the asking user may decide it (workspace owners). */
  can_decide: boolean;
}

export interface VoiceActionCard {
  kind: VoiceActionKind;
  state: VoiceActionState;
  /** The task's (or quick command's) title. */
  title: string | null;
  task_id: string | null;
  task_status: TaskStatus | null;
  /** The agent the work was given to. */
  agent: { id: string; name: string } | null;
  /** The command a quick command created (Command Center). */
  command_id: string | null;
  /** Steps of a quick command. */
  steps: number;
  approvals_pending: number;
  approval: VoiceActionApproval | null;
  /** What happened, in plain words (the run's result or the reason it stopped). */
  summary: string;
  candidates: string[];
  error_code: string | null;
}

/** The `action` server-sent event: sent when the task exists, then again with the outcome. */
export interface VoiceActionEvent {
  card: VoiceActionCard;
}

/**
 * Where one spoken turn's time went on the server (docs/voice.md#latency): the `timing` event
 * of `/ask/stream`, sent just before `done` and repeated in it, and the `timing` field of
 * `/ask`. Milliseconds from the request to the first reply text (`first_token_ms`, null when
 * no text came), in read-only lookups (`tool_ms`) and in all (`total_ms`); lookup rounds and
 * calls; how the answer was grounded; and the tools it read (names only, never content).
 */
export interface VoiceTurnTiming {
  first_token_ms: number | null;
  tool_ms: number;
  total_ms: number;
  tool_rounds: number;
  tool_calls: number;
  grounding: 'tools' | 'snapshot' | 'none';
  sources: string[];
}
