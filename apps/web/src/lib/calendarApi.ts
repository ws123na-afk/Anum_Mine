// Calendar requests for the web Calendar page (docs/calendar.md#web-page). Each keeps the API's
// own permission check: `calendar:read` lists, `calendar:write` adds, the creator or an owner changes.
import { apiRequest, apiRequestVoid } from './api';
import {
  eventsQuery, type AttendeeResponse, type CalendarEventView, type EventPage, type InvitationRequest, type NextStep, type SyncConnection, type SyncProvider, type SyncSetup, type SyncSummary,
} from './calendar';

const base = '/api/v1/calendar';
const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const listEvents = (start: Date, end: Date, includeCancelled: boolean) =>
  apiRequest<EventPage>(`${base}/events?${eventsQuery(start, end, includeCancelled)}`, { method: 'GET' });
export const getEvent = (id: string) => apiRequest<CalendarEventView>(`${base}/events/${encodeURIComponent(id)}`, { method: 'GET' });
export const createEvent = (body: Record<string, unknown>) => apiRequest<CalendarEventView>(`${base}/events`, json('POST', body));
export const updateEvent = (id: string, body: Record<string, unknown>) => apiRequest<CalendarEventView>(`${base}/events/${encodeURIComponent(id)}`, json('PATCH', body));
export const deleteEvent = (id: string) => apiRequestVoid(`${base}/events/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const setResponse = (id: string, attendeeId: string, response: AttendeeResponse) =>
  apiRequest<CalendarEventView>(`${base}/events/${encodeURIComponent(id)}/attendees/${encodeURIComponent(attendeeId)}/response`, json('PUT', { response }));
export const getNextSteps = () => apiRequest<{ items: NextStep[] }>(`${base}/next-steps`, { method: 'GET' });

// Calendar sync with Google and Microsoft (docs/calendar.md#sync). Connecting, syncing and
// disconnecting need `calendar:manage` (owners); the status is readable by every role.
const sync = `${base}/sync`;
export const getSync = () => apiRequest<SyncSetup>(sync, { method: 'GET' });
export const startConnect = (provider: SyncProvider) => apiRequest<{ authorization_url: string; expires_at: string }>(`${sync}/oauth/${provider}/start`, { method: 'POST' });
export const completeConnect = (connection: string) => apiRequest<SyncConnection>(`${sync}/oauth/complete`, json('POST', { connection }));
export const syncNow = (id: string) => apiRequest<{ connection: SyncConnection; summary: SyncSummary }>(`${sync}/connections/${encodeURIComponent(id)}/sync`, { method: 'POST' });
export const disconnect = (id: string) => apiRequest<{ revoked_at_provider: boolean; events_kept: number; detail: string }>(`${sync}/connections/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const requestInvitations = (eventId: string) => apiRequest<InvitationRequest>(`${base}/events/${encodeURIComponent(eventId)}/invitations`, { method: 'POST' });
