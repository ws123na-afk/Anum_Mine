// Developer mode (docs/developer-mode.md): pure helpers for the Developer view, the chat and the
// voice view. No browser or Vite APIs, so `pnpm test:unit` exercises them directly.

export type DevStatus = 'queued' | 'coding' | 'ci_running' | 'ci_failed' | 'waiting_approval' | 'merged' | 'failed' | 'cancelled';
export type StageState = 'done' | 'active' | 'failed' | 'pending';

export interface DevStage { id: 'queued' | 'coding' | 'pr_opened' | 'ci' | 'approval' | 'merged'; label: string; state: StageState }
export interface DevChecks { total: number; passed: number; failed: number; pending: number; failing: string[] }

/** `GET /api/v1/developer/requests` row. The approval, when present, is the pending merge approval (API shape). */
export interface DevRequest {
  id: string;
  created_by: string;
  text: string;
  source: 'text' | 'voice';
  status: DevStatus;
  allow_protected_paths: boolean;
  branch: string;
  run_id: number | null;
  run_url: string | null;
  pr_number: number | null;
  pr_url: string | null;
  pr_title: string | null;
  pr_draft: boolean | null;
  head_sha: string | null;
  checks: DevChecks | null;
  failure_reason: string | null;
  merge_error: string | null;
  approval_id: string | null;
  merge_commit_sha: string | null;
  created_at: string;
  updated_at: string;
  merged_at: string | null;
  timeline: DevStage[];
  summary: string;
  approval: Record<string, unknown> | null;
}

export interface DevSetup {
  configured: boolean;
  enabled_here: boolean;
  repository: string | null;
  workflow: string;
  base_branch: string;
  missing: string[];
  message: string;
  max_requests_per_day: number;
  requests_today: number;
}

export const MIN_REQUEST_CHARS = 10;
export const MAX_REQUEST_CHARS = 4000;

export const EXAMPLE_REQUESTS = [
  'Add a dark/light toggle to Settings',
  'Show the number of pending approvals on the Tasks page',
  'Let me rename a conversation in the Assistant',
];

/** Same rule as the API's PROTECTED_REQUEST: requests that touch CI workflows or secret handling. */
const PROTECTED = /\.github|github[ -]actions?|\bci[ -]workflows?\b|workflow[ -]files?|\bsecrets?\b|credentials?|passwords?|api[ _-]?keys?|access[ _-]?tokens?|personal access token|gitleaks|claude\.md/i;

export function mentionsProtected(text: string): boolean {
  return PROTECTED.test(text);
}

/** The request as sent: trimmed, or an error that says what to fix. */
export function requestBody(text: string, allowProtected = false): { ok: true; value: { text: string; allow_protected_paths: boolean } } | { ok: false; error: string } {
  const value = text.replace(/\r\n/g, '\n').trim();
  if (value.length < MIN_REQUEST_CHARS) return { ok: false, error: `Describe the change in at least ${MIN_REQUEST_CHARS} characters.` };
  if (value.length > MAX_REQUEST_CHARS) return { ok: false, error: `Keep the request under ${MAX_REQUEST_CHARS} characters.` };
  if (mentionsProtected(value) && !allowProtected) {
    return { ok: false, error: 'This request mentions CI workflows or secret handling. Tick "Allow changes to workflows and secret handling" if you really mean it.' };
  }
  return { ok: true, value: { text: value, allow_protected_paths: allowProtected } };
}

const TRIGGER = /^\s*(?:(?:hey|ok|okay)[\s,]+)?(?:anum[\s,:]+)?(?:developer|build)\b[\s,:.-]*(.*)$/is;

/**
 * The request text when `text` starts with a developer trigger ("developer, ...", "build ...",
 * "Anum build ..."), otherwise null. "build" alone (nothing to build) is not a request.
 */
export function parseDeveloperCommand(text: string): string | null {
  const match = TRIGGER.exec(text);
  if (!match) return null;
  const rest = match[1].replace(/^(?:to|please)\s+/i, '').trim();
  return rest.length >= MIN_REQUEST_CHARS ? rest : null;
}

const YES = /^\s*(?:yes|yeah|yep|start|start it|go|go ahead|do it|confirm|ok|okay|sure|please do|نعم|ابدأ)[\s.!]*$/i;
const NO = /^\s*(?:no|nope|cancel|stop|not now|never mind|nevermind|don't|do not|لا|إلغاء)[\s.!]*$/i;

/** A spoken or typed answer to "Start?": yes, no, or null when it is neither. */
export function parseConfirmation(text: string): 'yes' | 'no' | null {
  if (YES.test(text)) return 'yes';
  if (NO.test(text)) return 'no';
  return null;
}

/** "approve", "approve it", "approve and merge", "merge it": asks to review the merge (never approves by itself). */
export function isApproveIntent(text: string): boolean {
  return /^\s*(?:(?:anum[\s,]+)?(?:approve(?:\s+(?:it|this|the change|and merge))?|merge(?:\s+it)?))[\s.!]*$/i.test(text);
}

export function confirmPrompt(request: string): string {
  return `I'll ask the developer to: ${request}. Start?`;
}

export const OWNER_ONLY_REPLY = 'Only a workspace owner can ask the developer to change the app. Ask an owner of this workspace.';

/** The sentence spoken and shown when a pull request is ready. */
export function readyAnnouncement(request: DevRequest): string {
  return `The change is ready for review: ${request.pr_title ?? shortText(request.text)}. Say approve to merge.`;
}

export function shortText(text: string, limit = 80): string {
  const words = text.split(/\s+/).join(' ').trim();
  return words.length <= limit ? words : `${words.slice(0, limit - 1).trimEnd()}…`;
}

export const STATUS_LABEL: Record<DevStatus, string> = {
  queued: 'Queued',
  coding: 'Coding',
  ci_running: 'CI running',
  ci_failed: 'CI failing',
  waiting_approval: 'Waiting for approval',
  merged: 'Merged',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

export function statusTone(status: DevStatus): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  if (status === 'merged') return 'success';
  if (status === 'failed' || status === 'ci_failed') return 'dangerTone';
  if (status === 'cancelled') return 'neutral';
  return 'warning';
}

export function isActive(request: DevRequest): boolean {
  return !['merged', 'failed', 'cancelled'].includes(request.status);
}

export function canCancel(request: DevRequest): boolean {
  return isActive(request);
}

export function canRequestMerge(request: DevRequest): boolean {
  return request.status === 'waiting_approval' && Boolean(request.pr_number);
}

/** "2 of 3 checks passed, 1 failing (Web and contracts)". */
export function checksLabel(checks: DevChecks | null): string {
  if (!checks || !checks.total) return 'No CI results yet';
  const parts = [`${checks.passed} of ${checks.total} checks passed`];
  if (checks.pending) parts.push(`${checks.pending} running`);
  if (checks.failed) parts.push(`${checks.failed} failing${checks.failing.length ? ` (${checks.failing.slice(0, 3).join(', ')})` : ''}`);
  return parts.join(', ');
}

/**
 * Requests that just became ready for review, compared with the previous poll: what the voice
 * view says out loud and the chat shows. Requests first seen already ready are announced too.
 */
export function newlyReady(previous: DevRequest[] | null, current: DevRequest[]): DevRequest[] {
  if (previous === null) return [];
  const before = new Map(previous.map((item) => [item.id, item.status]));
  return current.filter((item) => item.status === 'waiting_approval' && before.get(item.id) !== 'waiting_approval');
}

/** How often the views refresh: often while something is moving, never when nothing is. */
export function pollDelay(requests: DevRequest[]): number | null {
  if (requests.some((item) => item.status === 'queued' || item.status === 'coding' || item.status === 'ci_running')) return 20_000;
  if (requests.some((item) => item.status === 'waiting_approval')) return 60_000;
  return null;
}

/** Setup instructions for the missing API settings, by name only; values are never shown. */
export function setupSteps(setup: DevSetup): string[] {
  const steps: Record<string, string> = {
    ANUM_DEVELOPER_GITHUB_REPO: 'ANUM_DEVELOPER_GITHUB_REPO: the repository to build, as owner/repo.',
    ANUM_DEVELOPER_GITHUB_TOKEN: 'ANUM_DEVELOPER_GITHUB_TOKEN: a fine-grained GitHub token for that repository only (Contents, Pull requests and Actions read and write; Commit statuses read).',
    ANUM_DEVELOPER_TENANT_ID: 'ANUM_DEVELOPER_TENANT_ID: the organization allowed to use developer mode.',
    ANUM_DEVELOPER_WORKFLOW_FILE: 'ANUM_DEVELOPER_WORKFLOW_FILE: the workflow file name (anum-developer.yml).',
    ANUM_DEVELOPER_BASE_BRANCH: 'ANUM_DEVELOPER_BASE_BRANCH: the branch to build on (main).',
    ANUM_DEVELOPER_GITHUB_API_URL: 'ANUM_DEVELOPER_GITHUB_API_URL: an https GitHub API address.',
  };
  return setup.missing.map((name) => steps[name] ?? name);
}
