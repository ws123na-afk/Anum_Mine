// What ANUM learned (docs/learning.md): the caller's own feedback signals, style profile,
// learned preferences, detected routines and weekly improvement notes, as
// `/api/v1/learning` returns them (services/api/anum_api: learning.py, learning_api.py).
// Wire shapes are snake_case, as the API sends them.

/** Whether ANUM learns from the caller now, and if not, why. */
export type LearningState = 'on' | 'off_by_user' | 'off_chat_memory' | 'off_on_server';

export type LearningSignalKind =
  | 'rating_up'
  | 'rating_down'
  | 'draft_accepted'
  | 'draft_edited'
  | 'approval_approved'
  | 'approval_rejected'
  | 'retry';

/** Where a signal came from; clients post feedback for the first four. */
export type LearningSurface = 'chat' | 'email' | 'whatsapp' | 'task' | 'approval' | 'automation';
export type LearningTone = 'formal' | 'neutral' | 'casual';
export type LearningLength = 'short' | 'medium' | 'long';
export type LearningLanguage = 'ar' | 'en' | 'mixed';

/** How the user changed a draft before sending (counts only). */
export interface LearningEditSummary {
  words_before: number;
  words_after: number;
  words_added: number;
  words_removed: number;
  /** 0: unchanged, 1: rewritten. */
  changed_ratio: number;
  greeting_before: string | null;
  greeting_after: string | null;
  sign_off_before: string | null;
  sign_off_after: string | null;
}

export interface LearningSignal {
  id: string;
  kind: LearningSignalKind;
  surface: LearningSurface;
  subject_id: string | null;
  skill: string | null;
  tool: string | null;
  contact: string | null;
  language: LearningLanguage | null;
  original_text: string | null;
  final_text: string | null;
  reason: string | null;
  metadata: { edit?: LearningEditSummary; risk_level?: string; [key: string]: unknown };
  created_at: string;
  /** Lists carry the first 500 characters of each text; `GET /signals/{id}` has all of it. */
  truncated: boolean;
}

export interface LearningSignalPage {
  items: LearningSignal[];
  total: number;
  limit: number;
  offset: number;
}

/** `POST /api/v1/learning/feedback`: a rating, or a draft and what was sent instead. */
export interface LearningFeedback {
  surface: 'chat' | 'email' | 'whatsapp' | 'task';
  subject_id?: string | null;
  rating?: 'up' | 'down' | null;
  original?: string | null;
  final?: string | null;
  contact?: string | null;
  skill?: string | null;
  tool?: string | null;
  reason?: string | null;
}

export interface LearningFeedbackResult {
  recorded: boolean;
  state: LearningState;
  signal: LearningSignal | null;
  message: string | null;
}

/** `GET /api/v1/learning/ratings?surface=&subject_id=`: the caller's current rating per subject. */
export interface LearningRatings {
  ratings: { subject_id: string; rating: 'up' | 'down'; signal_id: string; reason: string | null }[];
}

export interface LearningStyle {
  tone: LearningTone | null;
  length: LearningLength | null;
  target_words: number | null;
  greeting: string | null;
  sign_off: string | null;
  language: LearningLanguage | null;
  contact_languages: Record<string, LearningLanguage>;
  emoji: boolean | null;
  /** Words sent / words drafted over edited drafts; below 1 the user shortens. */
  edit_length_ratio: number | null;
  samples: number;
}

/** The user's own choices; null keeps what was learned; an empty greeting or sign-off means none. */
export interface LearningStyleOverrides {
  tone?: LearningTone | null;
  length?: LearningLength | null;
  greeting?: string | null;
  sign_off?: string | null;
  language?: LearningLanguage | null;
  contact_languages?: Record<string, LearningLanguage> | null;
  notes?: string | null;
}

export interface LearningProfile {
  enabled: boolean;
  derived: LearningStyle;
  overrides: LearningStyleOverrides;
  effective: LearningStyle;
  derived_at: string | null;
  updated_at: string | null;
}

/** `GET /api/v1/learning`. */
export interface LearningOverview {
  state: LearningState;
  profile: LearningProfile;
  counts: {
    signals: Partial<Record<LearningSignalKind, number>>;
    preferences: number;
    routines_proposed: number;
    suggestions_open: number;
  };
  can_manage_automations: boolean;
  can_apply_autonomy: boolean;
}

/** `all`, `surface:<chat|email|whatsapp|task>`, `skill:<id>`, `tool:<name>` or `contact:<key>`. */
export type LearningScope = string;

export interface LearnedPreference {
  id: string;
  scope: LearningScope;
  text: string;
  source: 'user' | 'suggestion';
  suggestion_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface LearnedPreferencePage {
  items: LearnedPreference[];
  total: number;
  limit: number;
  offset: number;
}

export type LearnedRoutineStatus = 'proposed' | 'accepted' | 'confirmed' | 'dismissed';

export interface LearnedRoutine {
  id: string;
  fingerprint: string;
  status: LearnedRoutineStatus;
  title: string;
  cadence: 'daily' | 'weekly';
  /** Five-field cron, in `timezone`. */
  cron: string;
  timezone: string;
  steps: { key: string; label: string; prompt: string; source: 'task' | 'automation' }[];
  occurrences: number;
  weeks_observed: number;
  confidence: number;
  last_seen_at: string | null;
  /** Set once accepted: the automation and its (paused until confirmed) schedule. */
  workflow_id: string | null;
  schedule_id: string | null;
  detected_at: string;
  decided_at: string | null;
}

export interface LearnedRoutineList {
  items: LearnedRoutine[];
  detected: number;
  state: LearningState;
}

export type ImprovementSuggestionKind = 'instruction' | 'template' | 'autonomy';
export type ImprovementSuggestionStatus = 'open' | 'applied' | 'dismissed';

export interface ImprovementSuggestion {
  id: string;
  week: string;
  fingerprint: string;
  rule: string;
  kind: ImprovementSuggestionKind;
  title: string;
  body: string;
  target: string | null;
  change:
    | { type: 'preference'; scope: string; text: string }
    | { type: 'approval_policy'; medium_risk_requires_approval: boolean };
  evidence: Record<string, unknown>;
  status: ImprovementSuggestionStatus;
  created_at: string;
  updated_at: string;
  decided_at: string | null;
  /** The permission applying needs when the caller lacks it (`policy:manage`). */
  needs_permission: string | null;
}

export interface ImprovementNote {
  /** ISO week, e.g. `2026-W41`. */
  week: string;
  period_start: string;
  period_end: string;
  summary: {
    ratings_up: number;
    ratings_down: number;
    drafts_accepted: number;
    drafts_edited: number;
    approvals_approved: number;
    approvals_rejected: number;
    retries: number;
    run_failures: number;
  };
  generated_at: string;
}

export interface ImprovementNoteView {
  note: ImprovementNote;
  suggestions: ImprovementSuggestion[];
}

export interface ImprovementNotePage {
  items: ImprovementNoteView[];
  total: number;
  state: LearningState;
}
