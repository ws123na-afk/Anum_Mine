// Conversations API (docs/conversations.md) and the streamed POST shared with voice.
import type { ConversationAskBody, ConversationListQuery, ConversationTurnFeedbackResult, ConversationUpdate, ConversationRating } from '@anum/contracts';
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { createSseParser, type ChatCardApproval, type ChatTurn, type Conversation, type ConversationDetail, type SseEvent } from './chat';
import { apiErrorFromResponse } from './errors';

/**
 * POST a JSON body and deliver the server-sent events of the answer as they arrive.
 * Aborting `signal` (the Stop button) closes the request, which the API turns into
 * stopping the model. A non-2xx answer throws the API's error (for example a 402).
 */
export async function postSse(path: string, body: unknown, signal: AbortSignal, onEvent: (event: SseEvent) => void): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: 'POST',
    headers: { ...(await authHeaders()), 'content-type': 'application/json', accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const parser = createSseParser(onEvent);
  if (!response.body) { parser.push(await response.text()); parser.end(); return; }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    parser.push(decoder.decode());
    parser.end();
  } finally {
    reader.releaseLock();
  }
}

/** Unpinned conversations per page of the list (pinned ones all come on the first page). */
export const CONVERSATION_PAGE = 30;

/**
 * The Assistant's threads, or with `agent_id` the threads with that agent (docs/agents.md):
 * pinned first, then by latest activity; `q` searches titles and messages; the cursor pages.
 */
export function listConversations(query: ConversationListQuery = {}): Promise<Conversation[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== '') params.set(key, String(value));
  const search = params.toString();
  return apiRequest(`/api/v1/conversations${search ? `?${search}` : ''}`, { method: 'GET' });
}

/** Rename and/or pin (docs/conversations.md#conversation-list). */
export function updateConversation(id: string, body: ConversationUpdate): Promise<Conversation> {
  return apiRequest(`/api/v1/conversations/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) });
}

/** Delete one message: a question goes with its answers, an answer alone. */
export async function deleteTurn(conversationId: string, turnId: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/api/v1/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}`, { method: 'DELETE', headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
}

/** Thumbs on an answer, stored on the turn; `null` clears it. */
export function rateTurn(conversationId: string, turnId: string, rating: ConversationRating | null, reason?: string): Promise<ConversationTurnFeedbackResult> {
  return apiRequest(`/api/v1/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/feedback`, {
    method: 'PUT', body: JSON.stringify(reason ? { rating, reason } : { rating }),
  });
}

export function createConversation(agentId?: string): Promise<Conversation> {
  return apiRequest('/api/v1/conversations', { method: 'POST', body: JSON.stringify(agentId ? { agent_id: agentId } : {}) });
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return apiRequest(`/api/v1/conversations/${encodeURIComponent(id)}`, { method: 'GET' });
}

export async function deleteConversation(id: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/api/v1/conversations/${encodeURIComponent(id)}`, { method: 'DELETE', headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
}

export type AskBody = ConversationAskBody;

export function askInConversation(id: string, body: AskBody, signal: AbortSignal, onEvent: (event: SseEvent) => void): Promise<void> {
  return postSse(`/api/v1/conversations/${encodeURIComponent(id)}/ask`, body, signal, onEvent);
}

export interface CardDecision {
  task: { status: string };
  run?: { result?: string | null; steps?: { type: string; summary: string }[] } | null;
  approval: { status: string };
}

/**
 * Approve or reject the approval a chat card shows, through the ordinary approvals route with
 * the payload hash the card displayed (docs/approvals-and-risk.md).
 */
export function decideCardApproval(approval: ChatCardApproval, decision: 'approve' | 'reject'): Promise<CardDecision> {
  const body = approval.payload_hash ? { payload_hash: approval.payload_hash } : {};
  return apiRequest(`/api/v1/approvals/${encodeURIComponent(approval.id)}/${decision}`, { method: 'POST', body: JSON.stringify(body) });
}

export type { ChatTurn, Conversation, ConversationDetail };
