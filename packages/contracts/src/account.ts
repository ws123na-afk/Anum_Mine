// The caller's own account-level settings and data, as the API returns them:
// retrieval source names, privacy (summary, export, delete), user preferences and the
// workspace model configuration with routing. Wire shapes are snake_case, as the API
// sends them (services/api/anum_api: privacy.py, user_preferences.py, onboarding.py);
// the source lookup is camelCase because clients parse it defensively (like
// `RetrievedSourceUsed`).

// --------------------------------------------------------------------------- retrieval

export type RetrievalSourceType = 'memory' | 'file';

/**
 * One source a run used, resolved by `GET /api/v1/retrieval/sources?memory_id=&file_id=`
 * (at most 50 ids in one request, retrieval_api.MAX_SOURCE_LOOKUP). A source that is gone, expired, private to
 * another user or in another workspace or tenant answers `found: false` with no details,
 * exactly like an unknown id.
 */
export interface RetrievalSourceName {
  sourceType: RetrievalSourceType;
  sourceId: string;
  found: boolean;
  /** A memory's first line or a file's name. */
  title: string | null;
  /** `task_note` or `chat` for a memory (a chat memory is private to the caller); the content type for a file. */
  kind: string | null;
  sizeBytes: number | null;
}

// --------------------------------------------------------------------------- privacy (docs/privacy.md)

/** User-private data: what "Delete my data" removes. */
export interface PrivacyPrivateCounts {
  conversations: number;
  conversation_turns: number;
  private_memories: number;
  retrieval_index_entries: number;
  voice_sessions: number;
  voice_transcript_segments: number;
  notification_preferences: number;
  /** Tenant-wide (every workspace of the organization). Absent from older servers. */
  user_preferences?: number;
  /** Phones registered for push in this workspace (docs/notifications.md). Absent from older servers. */
  push_devices?: number;
  /** What ANUM learned (docs/learning.md): feedback signals. Absent from older servers. */
  learning_signals?: number;
  /** The style profile, learned preferences, routines, notes and suggestions. Absent from older servers. */
  learned_items?: number;
  /** Meetings the caller imported, their transcript lines, recordings and Zoom/Teams connections (docs/meetings.md). Absent from older servers. */
  meetings?: number;
  meeting_transcript_segments?: number;
  meeting_recordings?: number;
  meeting_connections?: number;
}

/** Workspace-shared records the caller created or decided: kept by a deletion. */
export interface PrivacySharedCounts {
  tasks_created: number;
  approvals_decided: number;
  shared_memories_created: number;
  files_uploaded: number;
}

export interface PrivacyRetention {
  /** 0: kept until the user deletes them. */
  conversation_days: number;
  voice_transcript_days: number;
  voice_transcript_choices: string[];
  private_memories: string;
  task_memories: string;
  audit_records: string;
}

export interface PrivacyModelProvider {
  /** The deployment's provider (ANUM_MODEL_PROVIDER). */
  deployment: string;
  /** This workspace's own choice, when it saved one. */
  workspace: string | null;
  /** The deployment-wide switch (ANUM_CONVERSATION_MEMORY_ENABLED) only; kept for older clients. */
  conversation_memory_enabled: boolean;
  /**
   * What applies to the caller: whether a post-reply model call remembers facts from their
   * chats, and if not, why. The same value `GET /api/v1/me/preferences` returns.
   */
  conversation_memory: ConversationMemoryState;
}

/** `GET /api/v1/privacy`: counts only, never content. */
export interface PrivacySummary {
  tenant_id: string;
  workspace_id: string;
  user_id: string;
  role: string | null;
  private: PrivacyPrivateCounts;
  shared: PrivacySharedCounts;
  retention: PrivacyRetention;
  model_provider: PrivacyModelProvider;
  privacy_policy_url: string | null;
  delete_confirmation: string;
  export_max_bytes: number;
  account_note: string;
  shared_note: string;
}

/** The export's `format`; a new version changes it. */
export type PrivacyExportFormat = 'anum.privacy-export/v1';

/**
 * `GET /api/v1/privacy/export` (a file download, `anum.privacy-export/v1`): the caller's
 * data in this tenant and workspace. Records keep the API's own shapes; file bytes are
 * linked by `download_path`, not embedded. Refused with 413 past the server's limit.
 */
export interface PrivacyExport {
  format: PrivacyExportFormat;
  generated_at: string;
  not_included: string[];
  identity: {
    user_id: string;
    tenant_id: string;
    workspace_id: string;
    workspace_roles: string[];
    organization_roles: string[];
    membership: Record<string, unknown> | null;
    note: string;
  };
  conversations: Array<Record<string, unknown> & { id: string; turns: Array<Record<string, unknown>> }>;
  memories: { private: Array<Record<string, unknown>>; shared_created_by_you: Array<Record<string, unknown>> };
  voice_sessions: Array<Record<string, unknown> & { id: string; transcript: Array<Record<string, unknown>> }>;
  tasks_created: Array<Record<string, unknown> & { id: string; runs: Array<Record<string, unknown>> }>;
  approvals_decided: Array<Record<string, unknown> & { your_approval: Record<string, unknown> | null }>;
  notification_preferences: Record<string, boolean> | null;
  user_preferences: UserPreferences | null;
  /** Platform, label, the token's last four characters and dates; never the push token. */
  push_devices?: Array<Record<string, unknown> & { id: string; platform: string; token_hint: string }>;
  /** What ANUM learned from the caller in this workspace, full texts included (absent from older servers). */
  learning?: {
    profile: Record<string, unknown> | null;
    signals: Array<Record<string, unknown>>;
    preferences: Array<Record<string, unknown>>;
    routines: Array<Record<string, unknown>>;
    notes: Array<Record<string, unknown>>;
    suggestions: Array<Record<string, unknown>>;
  };
  files_uploaded: Array<Record<string, unknown> & { id: string; download_path: string }>;
  /** Meetings the caller imported with transcripts and recording links (docs/meetings.md). Absent from older servers. */
  meetings_imported?: Array<Record<string, unknown> & { id: string; transcript: Array<Record<string, unknown>>; recording: (Record<string, unknown> & { download_path: string }) | null }>;
  /** Connected Zoom/Microsoft accounts; never the OAuth tokens. */
  meeting_connections?: Array<Record<string, unknown> & { provider: string; account_label: string }>;
}

/** `POST /api/v1/privacy/delete` with `{"confirm": delete_confirmation}`. */
export interface PrivacyDeleteRequest {
  confirm: string;
}

export interface PrivacyDeleteResult {
  deleted: PrivacyPrivateCounts;
  kept: PrivacySharedCounts;
  account_note: string;
  shared_note: string;
}

// --------------------------------------------------------------------------- user preferences

/** Whether the Assistant remembers from and recalls into the caller's chats, and if not, why. */
export type ConversationMemoryState = 'on' | 'off_by_user' | 'off_on_server' | 'off_for_organization' | 'not_for_role';

/** Stored per user and tenant (every workspace); a missing row means these defaults. */
export interface UserPreferences {
  remember_conversations: boolean;
  updated_at: string | null;
}

/** `GET`/`PUT /api/v1/me/preferences`. */
export interface UserPreferencesView extends UserPreferences {
  conversation_memory: ConversationMemoryState;
}

/** The `PUT /api/v1/me/preferences` body (no other fields are accepted). */
export interface UserPreferencesUpdate {
  remember_conversations: boolean;
}

// --------------------------------------------------------------------------- model configuration

export type WorkspaceModelProvider = 'mock' | 'ollama' | 'openai_compatible' | 'anthropic';
export type ModelEffortLevel = 'low' | 'medium' | 'high' | 'xhigh' | 'max';

/**
 * `GET`/`PUT /api/v1/model-config` (docs/model-gateway.md). The key itself is never
 * returned, only its last characters (`credential_hint`). The routing fields are set for
 * `anthropic` only; null means the main `model` serves that route with no effort sent.
 */
export interface WorkspaceModelConfig {
  provider: string;
  model: string;
  base_url: string;
  fast_model?: string | null;
  capable_model?: string | null;
  fast_effort?: string | null;
  capable_effort?: string | null;
  credential_configured: boolean;
  credential_hint: string | null;
  updated_at: string;
}

/**
 * The `PUT /api/v1/model-config` body. `api_key: null` keeps the saved key, but only
 * while the provider and base URL are unchanged (else 422).
 */
export interface WorkspaceModelConfigWrite {
  provider: WorkspaceModelProvider;
  model: string;
  base_url?: string;
  api_key: string | null;
  fast_model?: string | null;
  capable_model?: string | null;
  fast_effort?: string | null;
  capable_effort?: string | null;
}

/** `POST /api/v1/model-config/test`. `models` lists every model the test called. */
export interface ModelConnectionTest {
  provider: string;
  model: string;
  models?: string[];
  latency_ms: number;
  status: string;
}
