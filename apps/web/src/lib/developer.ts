// Developer mode (docs/developer-mode.md): pure helpers for the Developer view, its Projects
// section, the chat and the voice view. No browser or Vite APIs, so `pnpm test:unit` exercises
// them directly. Every sentence goes through the catalogs (docs/web-localization.md).
import { joinList, t, type MessageKey } from './i18n.ts';

export type DevStatus = 'queued' | 'coding' | 'ci_running' | 'ci_failed' | 'waiting_approval' | 'merged' | 'failed' | 'cancelled';
export type StageState = 'done' | 'active' | 'failed' | 'pending';
export type StageId = 'queued' | 'coding' | 'pr_opened' | 'ci' | 'approval' | 'merged';

export interface DevStage { id: StageId; label: string; state: StageState }
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
  /** The project it went to (null for requests from before projects) and its name and repository then. */
  project_id?: string | null;
  project_name?: string | null;
  repository?: string | null;
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
  projects?: number;
  ready_projects?: number;
  max_projects?: number;
  environment_token_configured?: boolean;
}

export interface ProjectCheck {
  ok: boolean;
  checked_at: string;
  repository_reachable: boolean;
  default_branch_exists: boolean;
  workflow_present: boolean;
  actions_readable: boolean;
  github_default_branch: string | null;
  message: string;
}

/** `GET /api/v1/developer/projects` row. Never a token: only whether one is configured. */
export interface DevProject {
  id: string;
  name: string;
  github_owner: string;
  github_repo: string;
  repository: string;
  default_branch: string;
  description: string;
  enabled: boolean;
  protected_paths: string[];
  workflow_file: string;
  token_source: 'project' | 'environment';
  token_configured: boolean;
  token_updated_at: string | null;
  last_check: ProjectCheck | null;
  install_pr_number: number | null;
  install_pr_url: string | null;
  ready: boolean;
  problems: string[];
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectRef { id: string; name: string; repository: string; description: string }

/** `POST /api/v1/developer/resolve`: which project a chat or voice request is for. */
export interface Resolution {
  status: 'resolved' | 'ambiguous' | 'none';
  text: string;
  project: ProjectRef | null;
  candidates: ProjectRef[];
  matched_by: 'name' | 'description' | 'only_project' | null;
}

export const MIN_REQUEST_CHARS = 10;
export const MAX_REQUEST_CHARS = 4000;

export function exampleRequests(): string[] {
  return [t('dev.example.1'), t('dev.example.2'), t('dev.example.3')];
}

/** Same rule as the API's PROTECTED_REQUEST: requests that touch CI workflows or secret handling. */
const PROTECTED = /\.github|github[ -]actions?|\bci[ -]workflows?\b|workflow[ -]files?|\bsecrets?\b|credentials?|passwords?|api[ _-]?keys?|access[ _-]?tokens?|personal access token|gitleaks|claude\.md/i;

export function mentionsProtected(text: string): boolean {
  return PROTECTED.test(text);
}

export type RequestBody = { text: string; allow_protected_paths: boolean; project_id?: string };

/** The request as sent: trimmed, or an error that says what to fix. */
export function requestBody(text: string, allowProtected = false, projectId?: string | null): { ok: true; value: RequestBody } | { ok: false; error: string } {
  const value = text.replace(/\r\n/g, '\n').trim();
  if (projectId === '') return { ok: false, error: t('dev.validate.project') };
  if (value.length < MIN_REQUEST_CHARS) return { ok: false, error: t('dev.validate.min', { min: MIN_REQUEST_CHARS }) };
  if (value.length > MAX_REQUEST_CHARS) return { ok: false, error: t('dev.validate.max', { max: MAX_REQUEST_CHARS }) };
  if (mentionsProtected(value) && !allowProtected) return { ok: false, error: t('dev.validate.protected') };
  return { ok: true, value: { text: value, allow_protected_paths: allowProtected, ...(projectId ? { project_id: projectId } : {}) } };
}

const TRIGGER = /^\s*(?:(?:hey|ok|okay)[\s,]+)?(?:anum[\s,:]+)?(?:developer|build)\b[\s,:.-]*(.*)$/is;
// "ANUM, in TyrePulse add a login page": the project named first, without "build".
const IN_PROJECT = /^\s*(?:(?:hey|ok|okay)[\s,]+)?anum[\s,:]+((?:in|for|on)\s+\S.*)$/is;

/**
 * The request text when `text` starts with a developer trigger ("developer, ...", "build ...",
 * "Anum build ...", "ANUM, in <project> ..."), otherwise null. "build" alone is not a request.
 * The project phrase stays in the text: the API resolves and strips it (POST /developer/resolve).
 */
export function parseDeveloperCommand(text: string): string | null {
  const match = TRIGGER.exec(text) ?? IN_PROJECT.exec(text);
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

const normalize = (value: string) => value.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();

/**
 * The project a spoken or typed answer names among `candidates` ("TyrePulse", "the tyre pulse
 * one", "acme/tyrepulse"), or null when it names none or more than one.
 */
export function matchProjectAnswer(answer: string, candidates: ProjectRef[]): ProjectRef | null {
  const said = normalize(answer);
  if (!said) return null;
  const squashed = said.replace(/\s+/g, '');
  const hits = candidates.filter((candidate) => {
    const names = [candidate.name, candidate.repository.split('/').pop() ?? '', candidate.repository].map(normalize).filter(Boolean);
    return names.some((name) => said === name || squashed === name.replace(/\s+/g, '') || ` ${said} `.includes(` ${name} `) || squashed.includes(name.replace(/\s+/g, '')));
  });
  return hits.length === 1 ? hits[0] : null;
}

export function confirmPrompt(request: string, project: string): string {
  return t('dev.assist.confirm', { request, project });
}

/** Names joined as alternatives: "a", "a or b", "a, b or c". */
export function orList(names: string[]): string {
  if (names.length <= 1) return names[0] ?? '';
  return t('dev.listOr', { head: names.slice(0, -1).join(t('common.listSeparator')), last: names[names.length - 1] });
}

export function whichProjectPrompt(candidates: ProjectRef[]): string {
  return t('dev.assist.which', { names: orList(candidates.map((candidate) => candidate.name)) });
}

/** "ANUM and TyrePulse": for sentences that list every project. */
export function projectNames(projects: { name: string }[]): string {
  return joinList(projects.map((project) => project.name));
}

/** The sentence spoken and shown when a pull request is ready. */
export function readyAnnouncement(request: DevRequest): string {
  const title = request.pr_title ?? shortText(request.text);
  return request.project_name
    ? t('dev.assist.ready', { project: request.project_name, title })
    : t('dev.assist.readyNoProject', { title });
}

export function shortText(text: string, limit = 80): string {
  const words = text.split(/\s+/).join(' ').trim();
  return words.length <= limit ? words : `${words.slice(0, limit - 1).trimEnd()}…`;
}

export function statusLabel(status: DevStatus): string {
  return t(`dev.status.${status}` as MessageKey);
}

export function stageLabel(stage: DevStage): string {
  return t(`dev.stage.${stage.id}` as MessageKey);
}

export function stageStateLabel(state: StageState): string {
  return t(`dev.stageState.${state}` as MessageKey);
}

export function statusTone(status: DevStatus): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  if (status === 'merged') return 'success';
  if (status === 'failed' || status === 'ci_failed') return 'dangerTone';
  if (status === 'cancelled') return 'neutral';
  return 'warning';
}

/** The one-line summary in the current language (the API's `summary` is English). */
export function summaryText(request: DevRequest): string {
  const title = request.pr_title ?? shortText(request.text);
  switch (request.status) {
    case 'queued': return t('dev.summary.queued');
    case 'coding': return t('dev.summary.coding');
    case 'ci_running': return t('dev.summary.ciRunning', { title });
    case 'ci_failed': {
      const failing = request.checks?.failing.slice(0, 3) ?? [];
      return failing.length ? t('dev.summary.ciFailedNames', { title, names: failing.join(', ') }) : t('dev.summary.ciFailed', { title });
    }
    case 'waiting_approval': return t('dev.summary.ready', { title });
    case 'merged': return t('dev.summary.merged', { title });
    case 'cancelled': return t('dev.summary.cancelled');
    default: return request.failure_reason ?? t('dev.summary.failed');
  }
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
  if (!checks || !checks.total) return t('dev.checks.none');
  const parts = [t('dev.checks.passed', { passed: checks.passed, total: checks.total })];
  if (checks.pending) parts.push(t('dev.checks.running', { count: checks.pending }));
  if (checks.failed) {
    parts.push(checks.failing.length
      ? t('dev.checks.failingNames', { count: checks.failed, names: checks.failing.slice(0, 3).join(', ') })
      : t('dev.checks.failing', { count: checks.failed }));
  }
  return parts.join(t('common.listSeparator'));
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

/** The project ids that have a queued or coding request (one at a time per project). */
export function busyProjects(requests: DevRequest[]): Set<string> {
  return new Set(requests.filter((item) => item.status === 'queued' || item.status === 'coding').map((item) => item.project_id ?? ''));
}

const SETUP_STEPS = new Set(['ANUM_DEVELOPER_TENANT_ID', 'ANUM_DEVELOPER_GITHUB_REPO', 'ANUM_DEVELOPER_WORKFLOW_FILE', 'ANUM_DEVELOPER_BASE_BRANCH', 'ANUM_DEVELOPER_GITHUB_API_URL']);

/** Setup instructions for the missing API settings, by name only; values are never shown. */
export function setupSteps(setup: DevSetup): { name: string; purpose: string }[] {
  return setup.missing.map((name) => ({
    name,
    purpose: SETUP_STEPS.has(name) ? t(`dev.setupStep.${name}` as MessageKey) : t('dev.setupStep.other'),
  }));
}

// ---- Projects ----

export const SECRET_NAMES = ['ANTHROPIC_API_KEY', 'ANUM_DEV_PUSH_TOKEN'];
export const VARIABLE_NAMES = ['ANUM_DEV_PROTECTED_PATHS', 'ANUM_DEV_ALLOW_PROTECTED_PATHS'];

/** "acme/tyrepulse", "https://github.com/acme/tyrepulse(.git)" or "git@github.com:acme/tyrepulse.git". */
export function parseRepository(value: string): { owner: string; repo: string } | null {
  const text = value.trim().replace(/^git@github\.com:/i, '').replace(/^https?:\/\/(?:www\.)?github\.com\//i, '').replace(/\.git$/i, '').replace(/\/+$/, '');
  const match = /^([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9_.-]{1,100})$/.exec(text);
  if (!match || match[2] === '.' || match[2] === '..') return null;
  return { owner: match[1], repo: match[2] };
}

const BRANCH = /^[A-Za-z0-9._][A-Za-z0-9._/-]{0,99}$/;
const PROTECTED_PATH = /^[A-Za-z0-9._-][A-Za-z0-9._/-]{0,199}$/;

/** One path per line (or comma-separated), trimmed, "./" removed, duplicates dropped. */
export function parseProtectedPaths(value: string): string[] {
  const paths: string[] = [];
  for (const raw of value.split(/[\n,]/)) {
    const path = raw.trim().replace(/^\.\//, '');
    if (path && !paths.includes(path)) paths.push(path);
  }
  return paths;
}

export interface ProjectDraft {
  name: string;
  repository: string;
  default_branch: string;
  description: string;
  protected_paths: string;
  enabled: boolean;
  token_source: 'project' | 'environment';
  token: string;
  clear_token: boolean;
  workflow_file: string;
}

export function emptyProjectDraft(): ProjectDraft {
  return { name: '', repository: '', default_branch: 'main', description: '', protected_paths: '', enabled: true, token_source: 'project', token: '', clear_token: false, workflow_file: 'anum-developer.yml' };
}

export function draftFromProject(project: DevProject): ProjectDraft {
  return {
    name: project.name, repository: project.repository, default_branch: project.default_branch, description: project.description,
    protected_paths: project.protected_paths.join('\n'), enabled: project.enabled, token_source: project.token_source, token: '',
    clear_token: false, workflow_file: project.workflow_file,
  };
}

export type ProjectPayload = {
  name: string; github_owner: string; github_repo: string; default_branch: string; description: string; protected_paths: string[];
  enabled: boolean; token_source: 'project' | 'environment'; workflow_file: string; token?: string; clear_token?: boolean;
};

/** The body for POST/PATCH /developer/projects, or the first field error in the current language. */
export function projectPayload(draft: ProjectDraft): { ok: true; value: ProjectPayload } | { ok: false; field: keyof ProjectDraft; error: string } {
  const name = draft.name.trim().replace(/\s+/g, ' ');
  if (!name) return { ok: false, field: 'name', error: t('dev.projectForm.err.name') };
  if (name.length > 60) return { ok: false, field: 'name', error: t('dev.projectForm.err.nameLong') };
  const repository = parseRepository(draft.repository);
  if (!repository) return { ok: false, field: 'repository', error: t('dev.projectForm.err.repository') };
  const branch = draft.default_branch.trim();
  if (!BRANCH.test(branch) || branch.includes('..') || branch.endsWith('/')) return { ok: false, field: 'default_branch', error: t('dev.projectForm.err.branch') };
  const paths = parseProtectedPaths(draft.protected_paths);
  const bad = paths.find((path) => !PROTECTED_PATH.test(path) || path.split('/').includes('..') || path.includes('//'));
  if (bad !== undefined) return { ok: false, field: 'protected_paths', error: t('dev.projectForm.err.protected', { path: bad.slice(0, 60) }) };
  const workflow = draft.workflow_file.trim() || 'anum-developer.yml';
  if (!/^[A-Za-z0-9_.-]{1,100}\.ya?ml$/.test(workflow)) return { ok: false, field: 'workflow_file', error: t('dev.projectForm.err.workflow') };
  const token = draft.token.trim();
  if (token && (token.length < 20 || /\s/.test(token))) return { ok: false, field: 'token', error: t('dev.projectForm.err.token') };
  return {
    ok: true,
    value: {
      name, github_owner: repository.owner, github_repo: repository.repo, default_branch: branch, description: draft.description.trim().replace(/\s+/g, ' ').slice(0, 500),
      protected_paths: paths, enabled: draft.enabled, token_source: draft.token_source, workflow_file: workflow,
      ...(draft.token_source === 'project' && token ? { token } : {}),
      ...(draft.clear_token && !token ? { clear_token: true } : {}),
    },
  };
}

export type ProjectState = 'ready' | 'off' | 'attention' | 'untested';

/** The badge a project card shows. */
export function projectState(project: DevProject): ProjectState {
  if (!project.enabled) return 'off';
  if (!project.ready) return 'attention';
  return project.last_check ? 'ready' : 'untested';
}

/** Install workflow is offered while the workflow is not known to be on the branch. */
export function canInstallWorkflow(project: DevProject): boolean {
  return project.token_configured && !(project.last_check?.workflow_present ?? false);
}

/** Projects requests can be sent to (the API re-checks on send). */
export function sendableProjects(projects: DevProject[]): DevProject[] {
  return projects.filter((project) => project.enabled && project.token_configured);
}

/** The project the request form starts with: the only sendable one, else the one used last. */
export function defaultProjectId(projects: DevProject[], lastUsed: string | null): string {
  const sendable = sendableProjects(projects);
  if (sendable.length === 1) return sendable[0].id;
  return sendable.some((project) => project.id === lastUsed) ? (lastUsed as string) : '';
}

/** Client-side filter for the Projects list (name, repository, description). */
export function filterProjects(projects: DevProject[], query: string): DevProject[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return projects;
  return projects.filter((project) => [project.name, project.repository, project.description].some((value) => value.toLowerCase().includes(needle)));
}
