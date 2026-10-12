// What ANUM learned (docs/learning.md): requests to /api/v1/learning, always for the signed-in caller.
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import type {
  ImprovementNotePage, ImprovementNoteView, ImprovementSuggestion, LearnedPreference, LearnedPreferencePage, LearnedRoutine,
  LearnedRoutineList, LearningFeedback, LearningFeedbackResult, LearningOverview, LearningProfile, LearningRatings,
  LearningSignal, LearningSignalPage, LearningStyleOverrides,
} from './learning';

const BASE = '/api/v1/learning';
const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : '';
}

async function remove(path: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${path}`, { method: 'DELETE', headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
}

export const getOverview = () => apiRequest<LearningOverview>(BASE, { method: 'GET' });
export const setLearning = (enabled: boolean) => apiRequest<LearningOverview>(`${BASE}/settings`, { method: 'PUT', body: JSON.stringify({ enabled }) });

export const sendFeedback = (body: LearningFeedback) => apiRequest<LearningFeedbackResult>(`${BASE}/feedback`, json(body));
export function getRatings(surface: string, subjectIds: string[]): Promise<LearningRatings> {
  const search = new URLSearchParams({ surface });
  for (const id of subjectIds.slice(0, 100)) search.append('subject_id', id);
  return apiRequest<LearningRatings>(`${BASE}/ratings?${search}`, { method: 'GET' });
}

export const listSignals = (params: { kind?: string; q?: string; limit: number; offset: number }) =>
  apiRequest<LearningSignalPage>(`${BASE}/signals${query(params)}`, { method: 'GET' });
export const getSignal = (id: string) => apiRequest<LearningSignal>(`${BASE}/signals/${encodeURIComponent(id)}`, { method: 'GET' });
export const deleteSignal = (id: string) => remove(`${BASE}/signals/${encodeURIComponent(id)}`);

export const saveProfile = (overrides: LearningStyleOverrides) => apiRequest<LearningProfile>(`${BASE}/profile`, { method: 'PUT', body: JSON.stringify(overrides) });
export const resetProfile = () => apiRequest<{ profile: LearningProfile; signals_deleted: number }>(`${BASE}/profile/reset`, json({ confirm: true }));

export const listPreferences = (params: { q?: string; limit: number; offset: number }) =>
  apiRequest<LearnedPreferencePage>(`${BASE}/preferences${query(params)}`, { method: 'GET' });
export const createPreference = (scope: string, text: string) => apiRequest<LearnedPreference>(`${BASE}/preferences`, json({ scope, text }));
export const updatePreference = (id: string, scope: string, text: string) =>
  apiRequest<LearnedPreference>(`${BASE}/preferences/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify({ scope, text }) });
export const deletePreference = (id: string) => remove(`${BASE}/preferences/${encodeURIComponent(id)}`);

export const listRoutines = () => apiRequest<LearnedRoutineList>(`${BASE}/routines`, { method: 'GET' });
export const detectRoutines = (timezone: string) => apiRequest<LearnedRoutineList>(`${BASE}/routines/detect`, json({ timezone }));
export const routineAction = (id: string, action: 'accept' | 'confirm' | 'dismiss') =>
  apiRequest<LearnedRoutine>(`${BASE}/routines/${encodeURIComponent(id)}/${action}`, { method: 'POST' });
export const deleteRoutine = (id: string) => remove(`${BASE}/routines/${encodeURIComponent(id)}`);

export const listNotes = (limit: number, offset: number) => apiRequest<ImprovementNotePage>(`${BASE}/notes${query({ limit, offset })}`, { method: 'GET' });
export const refreshNote = () => apiRequest<ImprovementNoteView>(`${BASE}/notes/refresh`, { method: 'POST' });
export const suggestionAction = (id: string, action: 'apply' | 'dismiss') =>
  apiRequest<ImprovementSuggestion>(`${BASE}/suggestions/${encodeURIComponent(id)}/${action}`, { method: 'POST' });
