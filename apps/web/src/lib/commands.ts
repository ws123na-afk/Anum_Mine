// ANUM Command on the web (docs/command.md#the-web-command-center): mapping the /api/v1/commands and
// /api/v1/quick-commands answers onto the contract types, and the pure rules of the page (plan
// editing, status tones, what is still running). Pure, so the unit tests run it without a browser.
import type {
  Approval, CommandAgent, CommandFile, CommandNextStep, CommandPlan, CommandStatus, CommandSuggestion, CommandSummary, CommandView,
  PlanStep, QuickCommand, QuickCommandCategory, Task,
} from '@anum/contracts';

type Raw = Record<string, unknown>;
const str = (value: unknown): string => (typeof value === 'string' ? value : '');
const opt = (value: unknown): string | null => (typeof value === 'string' ? value : null);
const num = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) ? value : 0);
const list = (value: unknown): Raw[] => (Array.isArray(value) ? (value as Raw[]) : []);
const agent = (value: unknown): CommandAgent => { const raw = (value ?? {}) as Raw; return { id: str(raw.id) as CommandAgent['id'], name: str(raw.name) }; };

export function mapPlan(raw: Raw): CommandPlan {
  return {
    text: str(raw.text),
    language: raw.language === 'ar' ? 'ar' : 'en',
    steps: list(raw.steps).map((step): PlanStep => {
      const tool = step.tool as Raw | null;
      return {
        position: num(step.position), title: str(step.title), instruction: str(step.instruction), agent: agent(step.agent),
        skills: list(step.skills).map((s) => ({ id: str(s.id), name: str(s.name) })),
        tool: tool ? { name: str(tool.name), description: str(tool.description), category: opt(tool.category), actionKind: opt(tool.action_kind), target: opt(tool.target) } : null,
        riskLevel: str(step.risk_level) as PlanStep['riskLevel'], approval: str(step.approval) as PlanStep['approval'],
        approvalReason: str(step.approval_reason), connected: step.connected !== false, note: opt(step.note),
      };
    }),
    agents: list(raw.agents).map(agent),
    approvalsRequired: num(raw.approvals_required),
    blocked: num(raw.blocked),
  };
}

export function mapSummary(raw: Raw): CommandSummary {
  return {
    id: str(raw.id), text: str(raw.text), source: str(raw.source) as CommandSummary['source'], locale: raw.locale === 'ar' ? 'ar' : 'en',
    status: str(raw.status) as CommandStatus, progress: num(raw.progress), stepCount: num(raw.step_count), completedSteps: num(raw.completed_steps),
    pendingApprovals: num(raw.pending_approvals), agents: list(raw.agents).map(agent), createdAt: str(raw.created_at), updatedAt: str(raw.updated_at),
  };
}

/** `mapTask` and `mapApproval` come from api.ts; passed in so this file stays free of browser code. */
export function mapView(raw: Raw, mapTask: (t: never) => Task, mapApproval: (a: never) => Approval): CommandView {
  return {
    ...mapSummary(raw),
    createdBy: str(raw.created_by), quickCommandId: opt(raw.quick_command_id),
    steps: list(raw.steps).map((step) => ({
      position: num(step.position), title: str(step.title), instruction: str(step.instruction), agent: agent(step.agent), tool: opt(step.tool),
      riskLevel: str(step.risk_level) as PlanStep['riskLevel'], approval: str(step.approval) as PlanStep['approval'], taskId: str(step.task_id),
      task: step.task ? mapTask(step.task as never) : null, status: str(step.status), progress: num(step.progress), runId: opt(step.run_id),
      result: opt(step.result), startedAt: opt(step.started_at), finishedAt: opt(step.finished_at),
      approvals: list(step.approvals).map((a) => mapApproval(a as never)),
    })),
    pending: list(raw.pending).map((a) => mapApproval(a as never)),
    timeline: list(raw.timeline).map((e) => ({ id: str(e.id), at: str(e.at), type: str(e.type), taskId: str(e.task_id), position: num(e.position), agent: agent(e.agent), title: str(e.title) })),
    files: list(raw.files).map((f): CommandFile => ({ kind: f.kind === 'run_log' ? 'run_log' : 'attachment', name: str(f.name), path: str(f.path), fileId: opt(f.file_id), taskId: opt(f.task_id), contentType: opt(f.content_type) })),
    attachments: list(raw.attachments).map((a) => ({ fileId: str(a.file_id), name: str(a.name), contentType: opt(a.content_type) })),
    startedAt: opt(raw.started_at), finishedAt: opt(raw.finished_at), durationSeconds: typeof raw.duration_seconds === 'number' ? raw.duration_seconds : null,
  };
}

export const mapSuggestion = (raw: Raw): CommandSuggestion => ({
  kind: raw.kind === 'quick' || raw.kind === 'recent' ? raw.kind : 'starter', text: str(raw.text), title: str(raw.title),
  quickCommandId: opt(raw.quick_command_id), useCount: num(raw.use_count), lastUsedAt: opt(raw.last_used_at),
});
export const mapNextStep = (raw: Raw): CommandNextStep => ({
  id: str(raw.id), kind: raw.kind === 'review_approvals' || raw.kind === 'save_quick_command' ? raw.kind : 'command', label: str(raw.label), text: opt(raw.text),
});
export const mapQuick = (raw: Raw): QuickCommand => ({
  id: str(raw.id), title: str(raw.title), text: str(raw.text), category: (['work', 'personal', 'custom'].includes(str(raw.category)) ? raw.category : 'custom') as QuickCommandCategory,
  position: num(raw.position), useCount: num(raw.use_count), lastUsedAt: opt(raw.last_used_at), createdAt: str(raw.created_at), updatedAt: str(raw.updated_at),
});

export const QUICK_CATEGORIES: readonly QuickCommandCategory[] = ['work', 'personal', 'custom'];
export const MAX_STEPS = 8;
export const MAX_ATTACHMENTS = 5;
export const MAX_REQUEST_CHARS = 4000;
export const HISTORY_PAGE = 10;

/** Statuses during which a command still changes, so the page keeps refreshing it. */
const LIVE: ReadonlySet<CommandStatus> = new Set(['ready', 'running']);
export const isLive = (status: CommandStatus): boolean => LIVE.has(status);
/** A command can be removed from history only once nothing runs or waits (the API answers 409 otherwise). */
export const canDelete = (status: CommandStatus): boolean => !LIVE.has(status) && status !== 'waiting_approval';

export function commandTone(status: string): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  if (status === 'completed') return 'success';
  if (status === 'failed' || status === 'cancelled' || status === 'partial' || status === 'missing') return 'dangerTone';
  if (status === 'running' || status === 'queued' || status === 'waiting_approval' || status === 'ready') return 'warning';
  return 'neutral';
}

/** Step ids the plan editor works with: the preview's steps keep their planned order until edited. */
export function moveStep<T>(steps: readonly T[], index: number, by: -1 | 1): T[] {
  const to = index + by;
  if (index < 0 || to < 0 || to >= steps.length) return [...steps];
  const next = [...steps];
  [next[index], next[to]] = [next[to], next[index]];
  return next;
}
export const removeStep = <T,>(steps: readonly T[], index: number): T[] => steps.filter((_, i) => i !== index);

/** Whether the edited plan can start: at least one step and none refused by policy. */
export const canStart = (steps: readonly Pick<PlanStep, 'approval'>[]): boolean => steps.length > 0 && steps.every((s) => s.approval !== 'blocked');

/** The task ids of a command that have not started yet (each is run with `POST /tasks/{id}/run`). */
export const unstartedTasks = (view: Pick<CommandView, 'steps'>): string[] =>
  view.steps.filter((step) => step.status === 'created' && !step.runId).map((step) => step.taskId);
/** The task ids that a Stop cancels: the ones still queued, running or waiting. */
export const stoppableTasks = (view: Pick<CommandView, 'steps'>): string[] =>
  view.steps.filter((step) => ['created', 'queued', 'running', 'waiting_approval'].includes(step.status)).map((step) => step.taskId);

/** The language a command is planned in: Arabic when the text is mostly Arabic, else the interface's. */
export function commandLocale(text: string, interfaceLocale: string): 'en' | 'ar' {
  const arabic = (text.match(/[؀-ۿ]/g) ?? []).length, latin = (text.match(/[A-Za-z]/g) ?? []).length;
  if (arabic > latin) return 'ar';
  if (latin > arabic) return 'en';
  return interfaceLocale === 'ar' ? 'ar' : 'en';
}

/** A short title for a quick command saved from a request. */
export function quickTitle(text: string): string {
  const line = text.trim().split(/\n/)[0].replace(/\s+/g, ' ');
  return line.length > 60 ? `${line.slice(0, 59).trimEnd()}…` : line;
}
