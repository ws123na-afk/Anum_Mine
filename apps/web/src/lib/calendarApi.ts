// Calendar requests for the web Calendar page (docs/calendar.md#web-page). Each keeps the API's
// own permission check: `calendar:read` lists, `calendar:write` adds, the creator or an owner changes.
import { apiRequest, apiRequestVoid } from './api';
import { eventsQuery, type AttendeeResponse, type CalendarEventView, type EventPage, type NextStep } from './calendar';

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
