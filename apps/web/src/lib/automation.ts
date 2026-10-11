// Pure helpers for the automation workbench: step drafts, workflow bodies, schedule cron presets and labels.
export interface StepDraft { id: string; name: string; action: string; input: string; maxAttempts: number; }
export interface WorkflowStepBody { id: string; name: string; action: string; input: Record<string, unknown>; max_attempts: number; }
export interface WorkflowBody { name: string; description: string; steps: WorkflowStepBody[]; }

/** Actions the engine interprets: `pause` waits for resume, `fail` fails with `input.message`, anything else completes. */
export const STEP_ACTIONS = [
  { value: 'task.execute', label: 'Execute agent task' },
  { value: 'notify', label: 'Send notification' },
  { value: 'pause', label: 'Wait for external signal' },
  { value: 'fail', label: 'Fail with message' },
] as const;

export function newStep(existing: StepDraft[], action = 'task.execute'): StepDraft {
  const ids = new Set(existing.map((step) => step.id));
  let index = existing.length + 1;
  while (ids.has(`step-${index}`)) index += 1;
  return { id: `step-${index}`, name: `Step ${index}`, action, input: '{}', maxAttempts: 3 };
}

export function moveStep<T>(steps: T[], index: number, delta: number): T[] {
  const target = index + delta;
  if (index < 0 || index >= steps.length || target < 0 || target >= steps.length) return steps;
  const next = [...steps];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export function removeStep<T>(steps: T[], index: number): T[] { return steps.length <= 1 ? steps : steps.filter((_, i) => i !== index); }

export function parseStepInput(text: string): Record<string, unknown> {
  const trimmed = text.trim();
  if (!trimmed) return {};
  const value: unknown = JSON.parse(trimmed);
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('Step input must be a JSON object.');
  return value as Record<string, unknown>;
}

/** Validates drafts against the API's rules (1-100 steps, unique non-empty ids, attempts 1-10) and builds the POST body. */
export function workflowBody(name: string, description: string, drafts: StepDraft[]): WorkflowBody {
  if (!name.trim()) throw new Error('Workflow name is required.');
  if (drafts.length < 1 || drafts.length > 100) throw new Error('A workflow needs between 1 and 100 steps.');
  const seen = new Set<string>();
  const steps = drafts.map((draft, index) => {
    const id = draft.id.trim();
    if (!id || !draft.name.trim() || !draft.action.trim()) throw new Error(`Step ${index + 1} needs an id, name and action.`);
    if (seen.has(id)) throw new Error(`Step id "${id}" is used twice.`);
    seen.add(id);
    let input: Record<string, unknown>;
    try { input = parseStepInput(draft.input); } catch (error) { throw new Error(`Step ${index + 1} input: ${error instanceof Error ? error.message : 'invalid JSON'}`); }
    return { id, name: draft.name.trim(), action: draft.action.trim(), input, max_attempts: Math.min(10, Math.max(1, Math.round(draft.maxAttempts) || 1)) };
  });
  return { name: name.trim(), description: description.trim(), steps };
}

export function draftsFromSteps(steps: WorkflowStepBody[]): StepDraft[] {
  return steps.map((step) => ({ id: step.id, name: step.name, action: step.action, input: JSON.stringify(step.input ?? {}), maxAttempts: step.max_attempts }));
}

/** The API accepts five-field cron only; intervals are offered as presets that expand to cron. */
export const SCHEDULE_PRESETS = [
  { value: '*/15 * * * *', label: 'Every 15 minutes' },
  { value: '0 * * * *', label: 'Every hour' },
  { value: '0 9 * * *', label: 'Daily at 09:00' },
  { value: '0 9 * * 1-5', label: 'Weekdays at 09:00' },
  { value: '0 9 * * 1', label: 'Mondays at 09:00' },
] as const;

export function intervalCron(minutes: number): string {
  const value = Math.round(minutes);
  if (!Number.isFinite(value) || value < 1) throw new Error('Interval must be at least one minute.');
  if (value < 60) return `*/${value} * * * *`;
  if (value === 60) return '0 * * * *';
  if (value === 1440) return '0 0 * * *';
  if (value % 60 === 0 && 24 % (value / 60) === 0) return `0 */${value / 60} * * *`;
  throw new Error('Intervals of an hour or more must divide a day evenly.');
}

export function cronLabel(cron: string): string { return SCHEDULE_PRESETS.find((preset) => preset.value === cron.trim())?.label ?? cron; }

export function nextRunLabel(nextRunAt: string | null | undefined, enabled: boolean, now: Date = new Date()): string {
  if (!enabled) return 'Paused';
  if (!nextRunAt) return 'Not scheduled';
  const at = new Date(nextRunAt);
  if (Number.isNaN(at.getTime())) return 'Not scheduled';
  const minutes = Math.round((at.getTime() - now.getTime()) / 60000);
  const when = `${at.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
  if (minutes <= 0) return `Due now (${when})`;
  if (minutes < 60) return `In ${minutes} min (${when})`;
  if (minutes < 48 * 60) return `In ${Math.round(minutes / 60)} h (${when})`;
  return `In ${Math.round(minutes / 1440)} days (${when})`;
}
