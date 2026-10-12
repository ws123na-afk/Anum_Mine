// Conversations API (docs/conversations.md) and the streamed POST shared with voice.
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

/** The Assistant's threads, or with `agentId` the threads with that agent (docs/agents.md). */
export function listConversations(agentId?: string): Promise<Conversation[]> {
  return apiRequest(agentId ? `/api/v1/conversations?agent_id=${encodeURIComponent(agentId)}` : '/api/v1/conversations', { method: 'GET' });
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

export type AskBody = { content: string } | { retry_turn_id: string };

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
