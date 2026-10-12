// Calls & Meetings (docs/meetings.md): API shapes, list paths, status labels, transcript times,
// recipients, the follow-up mailto link, the OAuth callback page and recent activity. Pure, so
// `pnpm --filter @anum/web test:unit` exercises it without a browser. Text comes from the catalogs.
import { t, type MessageKey } from './i18n.ts';

export type MeetingSource = 'recording' | 'transcript' | 'zoom' | 'teams';
export type MeetingStatus = 'queued' | 'transcribing' | 'analyzing' | 'ready' | 'needs_model' | 'failed';
export type MeetingLanguage = 'auto' | 'en' | 'ar';
export type ConnectorProvider = 'zoom' | 'teams';

export interface ApiActionItem { id: string; text: string; owner: string | null; due_date: string | null; done: boolean; task_id: string | null }
export interface ApiFollowUp {
  to: string[]; subject: string; body: string; edited: boolean; send_task_id: string | null;
  /** Set once handed to the email module: its reply draft, the pending approval and the mailbox. */
  email_draft_id?: string | null; approval_id?: string | null; sent_from?: string | null;
}
export interface ApiEmailAccountChoice { id: string; address: string; provider: string }
export interface ApiProvenance { label: string; source: string; provider: string; model: string; generated_at: string; transcript_chars: number; truncated: boolean }

export interface ApiMeeting {
  id: string; tenant_id: string; workspace_id: string; created_by: string; title: string;
  source: MeetingSource; status: MeetingStatus; language: MeetingLanguage; requested_language: MeetingLanguage;
  occurred_at: string | null; duration_seconds: number | null; agent_skill_id: string | null;
  recording_file_id: string | null; source_file_name: string | null; external_id: string | null;
  segment_count: number; error_code: string | null; error: string | null;
  summary: string | null; decisions: string[]; action_items: ApiActionItem[];
  follow_up: ApiFollowUp | null; provenance: ApiProvenance | null; created_at: string; updated_at: string;
}
export interface ApiMeetingPage { items: ApiMeeting[]; total: number; offset: number; limit: number }
export interface ApiSegment { id: string; meeting_id: string; seq: number; speaker: string | null; start_ms: number | null; end_ms: number | null; text: string }
export interface ApiTranscriptPage { items: ApiSegment[]; total: number; offset: number; limit: number; speakers: string[] }
export interface ApiConnectorStatus { provider: ConnectorProvider; available: boolean; problem: string | null; connected: boolean; account_label: string | null; connected_at: string | null }
export interface ApiMeetingSetup {
  transcription: { configured: boolean; provider: string; model: string | null; target: string | null; problem: string | null; max_recording_bytes: number; accepted_extensions: string[] };
  transcript_extensions: string[]; max_transcript_bytes: number; connectors: ApiConnectorStatus[];
  model_ready: boolean; email_accounts: ApiEmailAccountChoice[]; can_create: boolean; can_manage_all: boolean;
}
export interface ApiExternalMeeting { external_id: string; title: string; started_at: string | null; duration_minutes: number | null; imported_meeting_id: string | null }

export const MEETING_PAGE_SIZE = 20;
export const TRANSCRIPT_PAGE_SIZE = 100;
export const STATUSES: readonly MeetingStatus[] = ['queued', 'transcribing', 'analyzing', 'ready', 'needs_model', 'failed'];
const WORKING = new Set<MeetingStatus>(['queued', 'transcribing', 'analyzing']);

/** `GET /api/v1/meetings` for one page. */
export function meetingsListPath(options: { query?: string; status?: MeetingStatus | ''; offset?: number; limit?: number } = {}): string {
  const params = new URLSearchParams({ limit: String(options.limit ?? MEETING_PAGE_SIZE), offset: String(options.offset ?? 0) });
  const query = options.query?.trim();
  if (query) params.set('q', query);
  if (options.status) params.set('status', options.status);
  return `/api/v1/meetings?${params.toString()}`;
}

/** `GET /api/v1/meetings/{id}/transcript` for one page. */
export function transcriptPath(meetingId: string, options: { query?: string; speaker?: string; offset?: number; limit?: number } = {}): string {
  const params = new URLSearchParams({ limit: String(options.limit ?? TRANSCRIPT_PAGE_SIZE), offset: String(options.offset ?? 0) });
  const query = options.query?.trim();
  if (query) params.set('q', query);
  if (options.speaker) params.set('speaker', options.speaker);
  return `/api/v1/meetings/${encodeURIComponent(meetingId)}/transcript?${params.toString()}`;
}

/** Query string for an upload: the name (URL-encoded, so Arabic names work), title, language, agent. */
export function uploadQuery(fileName: string, options: { title?: string; language?: MeetingLanguage; agentSkillId?: string; occurredAt?: string } = {}): string {
  const params = new URLSearchParams({ name: fileName });
  if (options.title?.trim()) params.set('title', options.title.trim());
  if (options.language && options.language !== 'auto') params.set('language', options.language);
  if (options.agentSkillId) params.set('agent_skill_id', options.agentSkillId);
  if (options.occurredAt) params.set('occurred_at', options.occurredAt);
  return params.toString();
}

export function isWorking(status: MeetingStatus): boolean { return WORKING.has(status); }

export function statusLabel(status: MeetingStatus): string { return t(`meetings.status.${status}` as MessageKey); }
export function sourceLabel(source: MeetingSource): string { return t(`meetings.source.${source}` as MessageKey); }

/** A `.statusBadge` tone class. */
export function statusTone(status: MeetingStatus): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  if (status === 'ready') return 'success';
  if (status === 'failed') return 'dangerTone';
  if (status === 'needs_model') return 'neutral';
  return 'warning';
}

/** "1:02:03" or "02:03" for a transcript line; empty when the line has no time. */
export function clock(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || ms < 0) return '';
  const total = Math.floor(ms / 1000);
  const hours = Math.floor(total / 3600), minutes = Math.floor((total % 3600) / 60), seconds = total % 60;
  const two = (n: number) => String(n).padStart(2, '0');
  return hours ? `${hours}:${two(minutes)}:${two(seconds)}` : `${two(minutes)}:${two(seconds)}`;
}

/** "45 min", "1 h 5 min" from seconds; empty when unknown. */
export function durationText(seconds: number | null | undefined): string {
  if (!seconds || seconds < 0) return '';
  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes < 60) return t('meetings.duration.minutes', { count: minutes });
  return t('meetings.duration.hours', { hours: Math.floor(minutes / 60), minutes: minutes % 60 });
}

/** The writing direction of meeting text: Arabic notes and transcripts read right to left. */
export function textDirection(language: MeetingLanguage): 'rtl' | 'ltr' | 'auto' {
  return language === 'ar' ? 'rtl' : language === 'en' ? 'ltr' : 'auto';
}

const EMAIL = /^[^@\s<>,;]{1,64}@[^@\s<>,;]{1,255}\.[^@\s<>,;]{2,63}$/;

/** Recipients typed as "a@x.com, b@y.com" (commas, semicolons or new lines). */
export function parseRecipients(value: string): { valid: string[]; invalid: string[] } {
  const parts = value.split(/[,;\n]/).map((part) => part.trim()).filter(Boolean);
  const valid = [...new Set(parts.filter((part) => EMAIL.test(part)))];
  return { valid, invalid: parts.filter((part) => !EMAIL.test(part)) };
}

/** A mailto: link that opens the draft in the person's own mail app; null when it would be too long. */
export function mailtoLink(draft: Pick<ApiFollowUp, 'to' | 'subject' | 'body'>): string | null {
  const params = new URLSearchParams();
  if (draft.subject) params.set('subject', draft.subject);
  if (draft.body) params.set('body', draft.body);
  const link = `mailto:${draft.to.map(encodeURIComponent).join(',')}?${params.toString().replace(/\+/g, '%20')}`;
  return link.length > 8000 ? null : link;
}

/** The draft as plain text for the clipboard. */
export function draftText(draft: Pick<ApiFollowUp, 'to' | 'subject' | 'body'>): string {
  return [draft.to.length ? `${t('meetings.email.to')}: ${draft.to.join(', ')}` : '', draft.subject ? `${t('meetings.email.subject')}: ${draft.subject}` : '', '', draft.body]
    .filter((line, index) => index > 1 || line).join('\n');
}

/** Problems with a file before it is sent: wrong kind or over the server's limit. */
export function uploadProblem(file: { name: string; size: number }, kind: 'recording' | 'transcript', setup: ApiMeetingSetup | null): string | null {
  const dot = file.name.lastIndexOf('.');
  const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : '';
  if (!file.size) return t('meetings.import.empty', { name: file.name });
  if (!setup) return null;
  const allowed = kind === 'recording' ? setup.transcription.accepted_extensions : setup.transcript_extensions;
  if (!allowed.includes(extension)) return t('meetings.import.wrongType', { name: file.name, allowed: allowed.join(', ') });
  const limit = kind === 'recording' ? setup.transcription.max_recording_bytes : setup.max_transcript_bytes;
  if (file.size > limit) return t('meetings.import.tooLarge', { name: file.name, limit: Math.floor(limit / (1024 * 1024)) });
  return null;
}

/** The web page the Zoom/Microsoft sign-in returns to (ANUM_MEETINGS_OAUTH_REDIRECT_URI). */
export const OAUTH_CALLBACK_PATH = '/meetings/connected';

/** Code and state when this page load is the meetings OAuth callback; an `error` the provider sent otherwise. */
export function oauthCallback(location: { pathname: string; search: string }): { code: string; state: string } | { error: string } | null {
  if (!location.pathname.endsWith(OAUTH_CALLBACK_PATH)) return null;
  const params = new URLSearchParams(location.search);
  const code = params.get('code'), state = params.get('state');
  if (code && state) return { code, state };
  const error = params.get('error_description') || params.get('error');
  return error ? { error: error.slice(0, 300) } : null;
}

export interface ActivityEntry { id: string; title: string; meetingId: string; at: string; agent: string | null }

/** "Call summary: <title>" entries for the newest ready meetings (the mockup's Recent Activity). */
export function recentActivity(meetings: ApiMeeting[], limit = 5): ActivityEntry[] {
  return meetings
    .filter((meeting) => meeting.status === 'ready' && meeting.summary)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, limit)
    .map((meeting) => ({ id: `activity-${meeting.id}`, title: t('meetings.activity.entry', { title: meeting.title }), meetingId: meeting.id, at: meeting.updated_at, agent: meeting.agent_skill_id }));
}

/** Which page numbers the pager shows: "1 … 4 5 6 … 12". */
export function pageNumbers(current: number, pages: number): (number | null)[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, index) => index + 1);
  const wanted = new Set([1, pages, current - 1, current, current + 1].filter((page) => page >= 1 && page <= pages));
  const sorted = [...wanted].sort((a, b) => a - b);
  const out: (number | null)[] = [];
  sorted.forEach((page, index) => { if (index && page - sorted[index - 1] > 1) out.push(null); out.push(page); });
  return out;
}
