// WhatsApp inbox over the live API (docs/whatsapp.md). The server checks every permission:
// owners and members read and ask to send, owners connect the number and approve sends.
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import type { ConversationFilter, WaAccount, WaConversation, WaSendResult, WaSuggestion, WaTemplate, WaThread } from './whatsapp';

const BASE = '/api/v1/whatsapp';
const id = (value: string) => encodeURIComponent(value);

export function getAccount(): Promise<WaAccount> {
  return apiRequest(`${BASE}/account`, { method: 'GET' });
}

export function saveAccount(body: Record<string, unknown>): Promise<WaAccount> {
  return apiRequest(`${BASE}/account`, { method: 'PUT', body: JSON.stringify(body) });
}

export function checkAccount(): Promise<WaAccount> {
  return apiRequest(`${BASE}/account/check`, { method: 'POST' });
}

export function sendTest(): Promise<{ status: string; to: string }> {
  return apiRequest(`${BASE}/account/test`, { method: 'POST', body: JSON.stringify({}) });
}

export async function disconnect(): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}/account`, { method: 'DELETE', headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
}

export async function listTemplates(refresh = false): Promise<WaTemplate[]> {
  const result = await apiRequest<{ templates: WaTemplate[] }>(`${BASE}/templates${refresh ? '?refresh=true' : ''}`, { method: 'GET' });
  return result.templates;
}

export function listConversations(options: { limit: number; before?: string; query?: string; filter?: ConversationFilter }): Promise<WaConversation[]> {
  const params = new URLSearchParams({ limit: String(options.limit), filter: options.filter ?? 'all' });
  if (options.before) params.set('before', options.before);
  if (options.query?.trim()) params.set('q', options.query.trim());
  return apiRequest(`${BASE}/conversations?${params.toString()}`, { method: 'GET' });
}

export function getThread(conversationId: string, options: { limit: number; before?: string }): Promise<WaThread> {
  const params = new URLSearchParams({ limit: String(options.limit) });
  if (options.before) params.set('before', options.before);
  return apiRequest(`${BASE}/conversations/${id(conversationId)}?${params.toString()}`, { method: 'GET' });
}

export function markRead(conversationId: string): Promise<WaConversation> {
  return apiRequest(`${BASE}/conversations/${id(conversationId)}/read`, { method: 'POST' });
}

export function draftReply(conversationId: string, instructions?: string): Promise<WaSuggestion> {
  return apiRequest(`${BASE}/conversations/${id(conversationId)}/draft`, { method: 'POST', body: JSON.stringify({ instructions: instructions?.trim() || null }) });
}

export function sendText(conversationId: string, text: string): Promise<WaSendResult> {
  return apiRequest(`${BASE}/conversations/${id(conversationId)}/send`, { method: 'POST', body: JSON.stringify({ text }) });
}

export function sendTemplate(conversationId: string, template: { name: string; language: string; parameters: string[] }): Promise<WaSendResult> {
  return apiRequest(`${BASE}/conversations/${id(conversationId)}/send`, { method: 'POST', body: JSON.stringify({ template }) });
}

/** Fetch a message's media with the caller's credentials and open or save it. */
export async function openMedia(messageId: string, filename: string, inline: boolean): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}/messages/${id(messageId)}/media`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  if (inline) { link.target = '_blank'; link.rel = 'noopener'; } else link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
