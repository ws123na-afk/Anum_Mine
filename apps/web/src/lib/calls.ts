// Phone calls (docs/calls.md): API shapes, list paths, the dialpad, tel: links, durations, labels
// and the provider steps. Pure, so `pnpm --filter @anum/web test:unit` covers it without a browser.
import { t, type MessageKey } from './i18n.ts';

export type Direction = 'outbound' | 'inbound';
export type CallKind = 'phone' | 'provider' | 'ai_outbound' | 'ai_inbound' | 'in_app';
export type CallStatus = 'logged' | 'dialing' | 'ringing' | 'in_progress' | 'completed' | 'failed';
export type Outcome = 'answered' | 'no_answer' | 'busy' | 'failed' | 'voicemail' | 'missed' | 'cancelled';
export type ConsentMethod = 'verbal' | 'written' | 'web_form' | 'other';
export type AiStatus = 'pending_approval' | 'calling' | 'completed' | 'failed' | 'cancelled';
export type CallsTab = 'recents' | 'contacts' | 'dial' | 'ai' | 'provider';

export interface ApiAiConsent { granted_at: string; recorded_by: string; method: ConsentMethod; note: string | null }
export interface ApiContact {
  id: string; name: string; phone: string; phone_normalized: string; company: string | null; email: string | null; notes: string | null;
  favourite: boolean; source: 'manual' | 'device'; language: 'auto' | 'en' | 'ar'; ai_consent: ApiAiConsent | null;
  created_by: string; created_at: string; updated_at: string; opted_out: boolean; can_edit: boolean;
}
export interface ApiFollowUp { text: string; due_date: string | null; task_id: string | null; edited: boolean }
export interface ApiProvenance { label: string; source: string; provider: string; model: string; generated_at: string; input_chars: number; truncated: boolean }
export interface ApiRecordingConsent { confirmed_by: string; confirmed_at: string; notice: string; transcription: boolean }
export interface ApiCall {
  id: string; user_id: string; contact_id: string | null; contact_name: string | null; phone: string | null; phone_normalized: string | null;
  peer_user_id: string | null; direction: Direction; kind: CallKind; status: CallStatus; outcome: Outcome | null;
  started_at: string; ended_at: string | null; duration_seconds: number | null; notes: string | null; summary: string | null;
  follow_up: ApiFollowUp | null; summary_provenance: ApiProvenance | null; provider: string | null;
  recording_file_id: string | null; recording_consent: ApiRecordingConsent | null; recording_expires_at: string | null;
  transcript: string | null; ai_request_id: string | null; cost_cents: number | null; created_at: string; updated_at: string; can_edit: boolean;
}
export interface Page<T> { items: T[]; total: number; offset: number; limit: number }
export interface ApiOptOut { id: string; masked: string; source: 'keypad' | 'speech' | 'owner' | 'inbound'; note: string | null; created_by: string; created_at: string }
export interface ApiCapabilities {
  browser_calls: boolean; recording: boolean; transcription: boolean; ai_calls: boolean; inbound_ai: boolean;
  problem: string | null; ai_problem: string | null; cost_per_minute_cents: number | null;
  /** What ANUM says before a recorded call connects ({en, ar}); null while recording is off. */
  recording_notice: { en: string; ar: string } | null;
}
export interface ApiCallsSetup {
  can_read: boolean; can_log: boolean; can_manage: boolean; can_create_tasks: boolean; model_ready: boolean;
  provider_connected: boolean; capabilities: ApiCapabilities; transcription_configured: boolean;
}
export interface ApiProvider {
  connected: boolean; provider: string; account_sid: string | null; api_key_sid: string | null; twiml_app_sid: string | null;
  from_number: string | null; credential_hint: string | null; has_api_key_secret: boolean; business_name: string | null; greeting: string | null;
  recording_enabled: boolean; transcription_enabled: boolean; recording_retention_days: number; ai_calls_enabled: boolean; inbound_ai_enabled: boolean;
  ai_max_seconds: number; ai_max_cost_cents: number; cost_per_minute_cents: number; last_check_status: string | null; last_check_detail: string | null;
  last_checked_at: string | null; voice_url: string | null; status_url: string | null; webhooks_ready: boolean; problem: string | null;
  limits: { ai_max_seconds: number; ai_max_cost_cents: number };
}
export interface ApiApprovalRef { id: string; status: string; payload_hash: string | null }
export interface ApiAiCall {
  id: string; requested_by: string; contact_id: string; contact_name: string; phone_masked: string; purpose: string; script: string;
  language: 'en' | 'ar'; max_seconds: number; max_cost_cents: number; status: AiStatus; approval_id: string | null; task_id: string | null;
  call_id: string | null; error: string | null; decided_by: string | null; created_at: string; approval: ApiApprovalRef | null;
}
export interface ApiBrowserCall { call: ApiCall; token: string; identity: string; expires_in: number }

export const PAGE_SIZE = 25;
export const OUTCOMES: readonly Outcome[] = ['answered', 'no_answer', 'busy', 'voicemail', 'missed', 'failed', 'cancelled'];
/** What a person picks when logging a call they made on their phone. */
export const LOG_OUTCOMES: readonly Outcome[] = ['answered', 'no_answer', 'busy', 'voicemail'];
export const TABS: readonly CallsTab[] = ['recents', 'contacts', 'dial', 'ai', 'provider'];
const LIVE = new Set<CallStatus>(['dialing', 'ringing', 'in_progress']);

/** The tab and the open record a Phone calls hash names: `#calls`, `#calls-contacts?contact=<id>`, `#calls?call=<id>`. */
export function parseCallsHash(hash: string): { tab: CallsTab; call: string | null; contact: string | null } {
  const [id, search = ''] = hash.replace(/^#/, '').split('?');
  const tab = id.startsWith('calls-') ? id.slice(6) : '';
  const params = new URLSearchParams(search);
  return { tab: (TABS as readonly string[]).includes(tab) ? tab as CallsTab : 'recents', call: params.get('call'), contact: params.get('contact') };
}
export const tabFromHash = (hash: string): CallsTab => parseCallsHash(hash).tab;

/** The hash for a tab and, optionally, the record open in it. */
export function callsHash(tab: CallsTab, open: { call?: string | null; contact?: string | null } = {}): string {
  const base = tab === 'recents' ? '#calls' : `#calls-${tab}`;
  if (tab === 'recents' && open.call) return `${base}?call=${encodeURIComponent(open.call)}`;
  if (tab === 'contacts' && open.contact) return `${base}?contact=${encodeURIComponent(open.contact)}`;
  return base;
}
export const tabHash = (tab: CallsTab): string => callsHash(tab);

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '' || value === false) continue;
    search.set(key, String(value));
  }
  return search.toString();
}

export function callsPath(options: { query?: string; direction?: Direction | ''; outcome?: Outcome | ''; contactId?: string; mine?: boolean; offset?: number; limit?: number } = {}): string {
  return `/api/v1/calls/log?${query({ limit: options.limit ?? PAGE_SIZE, offset: options.offset ?? 0, q: options.query?.trim(), direction: options.direction, outcome: options.outcome, contact_id: options.contactId, mine: options.mine })}`;
}
export function contactsPath(options: { query?: string; favourites?: boolean; offset?: number; limit?: number } = {}): string {
  return `/api/v1/calls/contacts?${query({ limit: options.limit ?? PAGE_SIZE, offset: options.offset ?? 0, q: options.query?.trim(), favourites: options.favourites })}`;
}

const SEPARATORS = /[\s\-(). /]/g;
const EASTERN = '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹';

/** Arabic-Indic digits as Western digits (the API reads both). */
export function westernDigits(text: string): string {
  return [...text].map((ch) => { const index = EASTERN.indexOf(ch); return index === -1 ? ch : String(index % 10); }).join('');
}

/** What a phone dials: `+` (only first), digits, `*` and `#`; spaces, dashes and brackets dropped. */
export function dialable(text: string): string {
  const kept = westernDigits(text).replace(SEPARATORS, '').replace(/[^0-9+*#]/g, '');
  return kept.replace(/(?!^)\+/g, '');
}

/** Whether `text` is a number a phone can dial (3 to 20 characters, at least 3 digits). */
export function isDialable(text: string): boolean {
  const value = dialable(text);
  return /^\+?[0-9*#]{3,20}$/.test(value) && value.replace(/[^0-9]/g, '').length >= 3;
}

/** The `tel:` link for a number, or null when it cannot be dialled. `#` is escaped as `%23`. */
export function telHref(text: string): string | null {
  if (!isDialable(text)) return null;
  return `tel:${dialable(text).replace(/#/g, '%23')}`;
}

/** One dialpad key on the current number: digits, `*`, `#`, and `+` only at the start. Max 20. */
export function pressKey(current: string, key: string): string {
  if (current.length >= 20) return current;
  if (key === '+') return current.length === 0 ? '+' : current;
  if (!/^[0-9*#]$/.test(key)) return current;
  return current + key;
}

/** A long press of 0 types `+` when the number is still empty (as phone dialers do), else 0. */
export const longPressZero = (current: string): string => current.length === 0 ? '+' : pressKey(current, '0');
export const backspace = (current: string): string => current.slice(0, -1);
/** Pasted text cleaned to what can be dialled (a copied "+971 (50) 123-4567" works). */
export const pasteNumber = (current: string, pasted: string): string => dialable(current + pasted).slice(0, 20);

/** `1:05`, `12:40`, `1:02:03`; an empty string for no duration. */
export function durationText(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return '';
  const whole = Math.round(seconds);
  const h = Math.floor(whole / 3600), m = Math.floor((whole % 3600) / 60), s = whole % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}

/** Minutes and seconds typed by a person (`2:30`, `150`, `2m`) as seconds, or null. */
export function parseDuration(text: string): number | null {
  const value = westernDigits(text).trim();
  if (!value) return null;
  const clock = /^(\d{1,3}):([0-5]?\d)$/.exec(value);
  if (clock) return Number(clock[1]) * 60 + Number(clock[2]);
  const minutes = /^(\d{1,3})\s*m(in)?$/i.exec(value);
  if (minutes) return Number(minutes[1]) * 60;
  if (/^\d{1,5}$/.test(value)) { const seconds = Number(value); return seconds <= 86400 ? seconds : null; }
  return null;
}

/** Seconds between the moment the dialer opened and the moment the person came back, rounded. */
export function elapsedSeconds(startedAt: number, now: number): number {
  return Math.max(0, Math.min(86400, Math.round((now - startedAt) / 1000)));
}

export const outcomeLabel = (outcome: Outcome | null): string => outcome ? t(`calls.outcome.${outcome}` as MessageKey) : t('calls.outcome.none');
export const kindLabel = (kind: CallKind): string => t(`calls.kind.${kind}` as MessageKey);
export const directionLabel = (direction: Direction): string => t(`calls.direction.${direction}` as MessageKey);
export const aiStatusLabel = (status: AiStatus): string => t(`calls.ai.status.${status}` as MessageKey);
export const isLive = (call: Pick<ApiCall, 'status'>): boolean => LIVE.has(call.status);

export function outcomeTone(outcome: Outcome | null): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  if (outcome === 'answered') return 'success';
  if (outcome === 'failed' || outcome === 'missed') return 'dangerTone';
  if (outcome === 'busy' || outcome === 'no_answer' || outcome === 'voicemail') return 'warning';
  return 'neutral';
}
export function aiTone(status: AiStatus): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  if (status === 'completed') return 'success';
  if (status === 'failed') return 'dangerTone';
  if (status === 'pending_approval' || status === 'calling') return 'warning';
  return 'neutral';
}

/** Who a call was with: the contact, else the number, else (in-app) the other person. */
export function callWith(call: Pick<ApiCall, 'contact_name' | 'phone' | 'peer_user_id'>): string {
  return call.contact_name || call.phone || call.peer_user_id || t('calls.unknownCaller');
}

/** Why an AI call cannot be asked for this contact now, or null. */
export function aiCallBlocker(contact: Pick<ApiContact, 'ai_consent' | 'opted_out'>, setup: Pick<ApiCallsSetup, 'capabilities' | 'can_log'> | null): string | null {
  if (!setup?.can_log) return t('calls.ai.blocked.role');
  if (contact.opted_out) return t('calls.ai.blocked.optedOut');
  if (!contact.ai_consent) return t('calls.ai.blocked.noConsent');
  if (!setup.capabilities.ai_calls) return setup.capabilities.ai_problem ?? t('calls.ai.blocked.off');
  return null;
}

/** The most a call of `seconds` can cost at `cents` per started minute, as text (USD cents). */
export function costText(seconds: number, centsPerMinute: number | null | undefined): string {
  const cents = Math.ceil(Math.max(0, seconds) / 60) * (centsPerMinute ?? 0);
  return `$${(cents / 100).toFixed(2)}`;
}

/** Initials for a contact's avatar (two letters, any script). */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const letters = parts.length > 1 ? [parts[0], parts[parts.length - 1]] : parts;
  return letters.map((part) => [...part][0] ?? '').join('').toUpperCase() || '?';
}

/** The owner's steps to turn on provider calls, in order (shown on the Provider tab). */
export function providerSteps(): string[] {
  return (['account', 'number', 'keys', 'app', 'urls', 'save'] as const).map((step) => t(`calls.provider.step.${step}` as MessageKey));
}

/** The body of PUT /api/v1/calls/provider: empty secrets are left out so the stored ones stay. */
export function providerBody(form: Record<string, string | boolean | number>): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(form)) {
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (!trimmed && ['auth_token', 'api_key_secret', 'api_key_sid', 'twiml_app_sid', 'greeting'].includes(key)) continue;
      body[key] = trimmed;
    } else body[key] = value;
  }
  return body;
}
