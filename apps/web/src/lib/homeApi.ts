// Every API call Home makes (docs/home.md#the-web-home), in one file. Home only reads other modules
// through their own endpoints, so each part keeps that module's permission check.
import type { ModelBudgetOverview } from '@anum/contracts';
import { apiRequest, apiRequestVoid } from './api';
import { getModelBudgets } from './adminApi';
import { listConversations } from './whatsappApi';
import type { CalendarEvent, HomeSummary, InsightList, Pin, PinKind } from './home';
import { todayRange } from './home';

const base = '/api/v1/home';
const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const getSummary = () => apiRequest<HomeSummary>(`${base}/summary`, { method: 'GET' });
export const getInsights = () => apiRequest<InsightList>(`${base}/insights`, { method: 'GET' });
export const dismissInsight = (key: string) => apiRequest<unknown>(`${base}/insights/${encodeURIComponent(key)}/dismiss`, { method: 'POST' });
export const snoozeInsight = (key: string, hours = 24) => apiRequest<unknown>(`${base}/insights/${encodeURIComponent(key)}/snooze`, json('POST', { hours }));

export const listPins = () => apiRequest<Pin[]>(`${base}/pins`, { method: 'GET' });
export const addPin = (pin: { kind: PinKind; label: string; target: string; icon?: string | null }) => apiRequest<Pin>(`${base}/pins`, json('POST', pin));
export const removePin = (id: string) => apiRequestVoid(`${base}/pins/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const reorderPins = (ids: string[]) => apiRequest<Pin[]>(`${base}/pins/order`, json('PUT', { ids }));

/** Today's events (local day) from the calendar module (`calendar:read`). */
export async function todaysEvents(now = new Date()): Promise<{ items: CalendarEvent[]; truncated: boolean }> {
  const { start, end } = todayRange(now);
  const query = new URLSearchParams({ start, end, limit: '50' });
  return apiRequest<{ items: CalendarEvent[]; truncated: boolean }>(`/api/v1/calendar/events?${query}`, { method: 'GET' });
}

/** Unread WhatsApp: chats with unread messages and their message count (the first 50 such chats). */
export async function whatsappUnread(): Promise<{ chats: number; messages: number; more: boolean }> {
  const chats = await listConversations({ limit: 50, filter: 'unread' });
  return { chats: chats.length, messages: chats.reduce((sum, chat) => sum + (chat.unread_count ?? 0), 0), more: chats.length >= 50 };
}

/** This month's model budgets and usage (owners: `organization:manage`). */
export const budgets = (): Promise<ModelBudgetOverview> => getModelBudgets();
