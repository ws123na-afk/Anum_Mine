// Assistant conversations (docs/conversations.md): the conversation list, message
// actions and their request bodies, as `/api/v1/conversations` sends and takes them
// (services/api/anum_api/conversations.py). Wire shapes are snake_case.

/** Thumbs on an answer, stored on the turn (migration 0051_chat_depth). */
export type ConversationRating = 'up' | 'down';

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  /** A thread with an agent (docs/agents.md); null for the Assistant's own. */
  agent_id?: string | null;
  /** Pinned conversations lead the first page of the list. */
  pinned?: boolean;
  pinned_at?: string | null;
}

/** `GET /api/v1/conversations` query: pinned first, then keyset pages of the rest. */
export interface ConversationListQuery {
  agent_id?: string;
  /** Words in the title or any message (case-insensitive, at most 200 characters). */
  q?: string;
  /** 1 to 100 unpinned conversations per page (default 50). */
  limit?: number;
  /** Cursor: `updated_at` and `id` of the last unpinned conversation of the previous page. */
  before_updated_at?: string;
  before_id?: string;
}

/** `PATCH /api/v1/conversations/{id}`: rename, pin, or both. */
export interface ConversationUpdate {
  title?: string;
  pinned?: boolean;
}

/**
 * `POST /api/v1/conversations/{id}/ask` (server-sent events). One of:
 * a new question; `retry_turn_id` (Retry or Regenerate: the latest question again, its
 * earlier replies replaced); `edit_turn_id` with `content` (Edit and resend: that question
 * and everything after it replaced).
 */
export type ConversationAskBody =
  | { content: string }
  | { retry_turn_id: string }
  | { edit_turn_id: string; content: string };

/** `PUT /api/v1/conversations/{id}/turns/{turn_id}/feedback`; `rating: null` clears it. */
export interface ConversationTurnFeedback {
  rating: ConversationRating | null;
  reason?: string | null;
}

export interface ConversationTurnFeedbackResult {
  turn: { id: string; feedback?: ConversationRating | null; feedback_reason?: string | null } & Record<string, unknown>;
  /** Also a learning signal while "Learn from my activity" is on (docs/learning.md). */
  learning_recorded: boolean;
  learning_message?: string | null;
}
