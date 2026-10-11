// "What ANUM learned" helpers (docs/learning.md): state wording, labels, scopes, paging and the
// style form. Pure, so `pnpm --filter @anum/web test:unit` runs them without a browser; the
// requests live in learningApi.ts.
import type {
  ImprovementNote, ImprovementSuggestion, LearnedRoutine, LearningLanguage, LearningLength, LearningProfile,
  LearningSignal, LearningSignalKind, LearningState, LearningStyleOverrides, LearningSurface, LearningTone,
} from '@anum/contracts';
import { formatNumber, t, type MessageKey } from './i18n.ts';

export type {
  ImprovementNote, ImprovementNoteView, ImprovementNotePage, ImprovementSuggestion, LearnedPreference, LearnedPreferencePage,
  LearnedRoutine, LearnedRoutineList, LearningFeedback, LearningFeedbackResult, LearningOverview, LearningProfile,
  LearningRatings, LearningSignal, LearningSignalKind, LearningSignalPage, LearningState, LearningStyleOverrides,
} from '@anum/contracts';

export const PAGE_SIZE = 10;
export const TONES: LearningTone[] = ['formal', 'neutral', 'casual'];
export const LENGTHS: LearningLength[] = ['short', 'medium', 'long'];
export const LANGUAGES: LearningLanguage[] = ['ar', 'en', 'mixed'];
export const SIGNAL_KINDS: LearningSignalKind[] = [
  'rating_up', 'rating_down', 'draft_accepted', 'draft_edited', 'approval_approved', 'approval_rejected', 'retry',
];
export const SURFACE_SCOPES = ['chat', 'email', 'whatsapp', 'task'] as const;
export type ScopeKind = 'all' | `surface:${(typeof SURFACE_SCOPES)[number]}` | 'contact' | 'tool' | 'skill';

export interface StateStatus { badge: string; tone: 'success' | 'neutral' | 'warning'; text: string; offerChatMemory: boolean }

/** Whether ANUM learns from the caller now, in words that say who can change it. */
export function stateStatus(state: LearningState | string | undefined): StateStatus {
  switch (state) {
    case 'on': return { badge: t('learning.state.on.badge'), tone: 'success', text: t('learning.state.on.text'), offerChatMemory: false };
    case 'off_by_user': return { badge: t('learning.state.offByUser.badge'), tone: 'neutral', text: t('learning.state.offByUser.text'), offerChatMemory: false };
    case 'off_chat_memory': return { badge: t('learning.state.offChatMemory.badge'), tone: 'warning', text: t('learning.state.offChatMemory.text'), offerChatMemory: true };
    case 'off_on_server': return { badge: t('learning.state.offServer.badge'), tone: 'warning', text: t('learning.state.offServer.text'), offerChatMemory: false };
    default: return { badge: t('learning.state.unknown.badge'), tone: 'neutral', text: t('learning.state.unknown.text'), offerChatMemory: false };
  }
}

/** The switch can change only what the caller turned off (or on): not the server's or chat memory's choice. */
export function switchEditable(state: LearningState | string): boolean {
  return state === 'on' || state === 'off_by_user';
}

export const toneLabel = (tone: string) => t(`learning.tone.${tone}` as MessageKey);
export const lengthLabel = (length: string) => t(`learning.length.${length}` as MessageKey);
export const languageLabel = (language: string) => t(`learning.language.${language}` as MessageKey);
export const kindLabel = (kind: string) => t(`learning.kind.${kind}` as MessageKey);
export const surfaceLabel = (surface: LearningSurface | string) => t(`learning.surface.${surface}` as MessageKey);

/** A preference scope in words: "Everything", "Email drafts", "Contact: sara@example.com". */
export function scopeLabel(scope: string): string {
  if (scope === 'all') return t('learning.scope.all');
  const [kind, ...rest] = scope.split(':');
  const value = rest.join(':');
  if (kind === 'surface' && (SURFACE_SCOPES as readonly string[]).includes(value)) return t(`learning.scope.surface.${value}` as MessageKey);
  if (kind === 'contact' || kind === 'tool' || kind === 'skill') return t(`learning.scope.label.${kind}` as MessageKey, { value });
  return scope;
}

/** Split a stored scope into the form's two fields. */
export function splitScope(scope: string): { kind: ScopeKind; target: string } {
  if (scope === 'all') return { kind: 'all', target: '' };
  const [kind, ...rest] = scope.split(':');
  const value = rest.join(':');
  if (kind === 'surface' && (SURFACE_SCOPES as readonly string[]).includes(value)) return { kind: `surface:${value}` as ScopeKind, target: '' };
  if (kind === 'contact' || kind === 'tool' || kind === 'skill') return { kind, target: value };
  return { kind: 'all', target: '' };
}

/** The scope to send, or null when a contact, tool or skill scope has no target yet. */
export function joinScope(kind: ScopeKind, target: string): string | null {
  if (kind === 'all' || kind.startsWith('surface:')) return kind;
  const value = target.trim();
  return value ? `${kind}:${value}` : null;
}

export interface PageInfo { page: number; pages: number; from: number; to: number; hasPrev: boolean; hasNext: boolean }

export function pageInfo(total: number, limit: number, offset: number): PageInfo {
  const pages = Math.max(1, Math.ceil(total / limit));
  const page = Math.min(pages, Math.floor(offset / limit) + 1);
  return { page, pages, from: total ? offset + 1 : 0, to: Math.min(total, offset + limit), hasPrev: offset > 0, hasNext: offset + limit < total };
}

/** One line about how a draft was edited: "Changed 45% · 30 → 12 words". */
export function editLine(signal: Pick<LearningSignal, 'metadata'>): string | null {
  const edit = signal.metadata?.edit;
  if (!edit) return null;
  return t('learning.signals.edit', {
    percent: Math.round(edit.changed_ratio * 100),
    before: formatNumber(edit.words_before),
    after: formatNumber(edit.words_after),
  });
}

/** The style form: strings as the inputs hold them. */
export interface StyleForm {
  tone: '' | LearningTone;
  length: '' | LearningLength;
  greeting: string;
  noGreeting: boolean;
  signOff: string;
  noSignOff: boolean;
  language: '' | LearningLanguage;
  notes: string;
  contacts: { contact: string; language: LearningLanguage }[];
}

export function styleForm(profile: LearningProfile): StyleForm {
  const o = profile.overrides ?? {};
  return {
    tone: o.tone ?? '',
    length: o.length ?? '',
    greeting: o.greeting ?? '',
    noGreeting: o.greeting === '',
    signOff: o.sign_off ?? '',
    noSignOff: o.sign_off === '',
    language: o.language ?? '',
    notes: o.notes ?? '',
    contacts: Object.entries(o.contact_languages ?? {}).map(([contact, language]) => ({ contact, language })),
  };
}

/** The `PUT /api/v1/learning/profile` body: empty fields keep what was learned. */
export function overridesFrom(form: StyleForm): LearningStyleOverrides {
  const contacts = form.contacts.filter((row) => row.contact.trim());
  return {
    tone: form.tone || null,
    length: form.length || null,
    greeting: form.noGreeting ? '' : form.greeting.trim() || null,
    sign_off: form.noSignOff ? '' : form.signOff.trim() || null,
    language: form.language || null,
    notes: form.notes.trim() || null,
    contact_languages: contacts.length ? Object.fromEntries(contacts.map((row) => [row.contact.trim(), row.language])) : null,
  };
}

/** "Seen 4 times · Asia/Riyadh" and the confidence as a whole percent. */
export function routineEvidence(routine: Pick<LearnedRoutine, 'occurrences' | 'timezone' | 'confidence'>): string {
  return t('learning.routines.evidence', { count: routine.occurrences, timezone: routine.timezone, confidence: Math.round(routine.confidence * 100) });
}

export function routineStatusTone(status: LearnedRoutine['status']): 'success' | 'neutral' | 'warning' {
  return status === 'confirmed' ? 'success' : status === 'accepted' ? 'warning' : 'neutral';
}

/** Why the Apply button is unavailable, or null when the caller may apply it. */
export function suggestionBlocker(suggestion: Pick<ImprovementSuggestion, 'needs_permission' | 'status'>): string | null {
  if (suggestion.status !== 'open') return null;
  return suggestion.needs_permission ? t('learning.suggestion.needsOwner') : null;
}

export function appliedMessage(suggestion: Pick<ImprovementSuggestion, 'change'>): string {
  return suggestion.change.type === 'approval_policy' ? t('learning.suggestion.appliedPolicy') : t('learning.suggestion.appliedPreference');
}

const SUMMARY_KEYS = ['ratings_up', 'ratings_down', 'drafts_accepted', 'drafts_edited', 'approvals_approved', 'approvals_rejected', 'retries', 'run_failures'] as const;

export function summaryRows(summary: ImprovementNote['summary']): { key: string; label: string; value: number }[] {
  return SUMMARY_KEYS.map((key) => ({ key, label: t(`learning.summary.${key}` as MessageKey), value: summary[key] ?? 0 }));
}

/** The browser's IANA time zone, for routine detection (UTC when unknown). */
export function browserTimeZone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
}

/** Total feedback signals in the overview counts. */
export function signalTotal(counts: Partial<Record<string, number>>): number {
  return Object.values(counts).reduce((sum: number, value) => sum + (value ?? 0), 0);
}
