// Conversations API (docs/conversations.md) and the streamed POST shared with voice.
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { createSseParser, type ChatTurn, type Conversation, type ConversationDetail, type SseEvent } from './chat';
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

export function listConversations(): Promise<Conversation[]> {
  return apiRequest('/api/v1/conversations', { method: 'GET' });
}

export function createConversation(): Promise<Conversation> {
  return apiRequest('/api/v1/conversations', { method: 'POST', body: JSON.stringify({}) });
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

export type { ChatTurn, Conversation, ConversationDetail };
