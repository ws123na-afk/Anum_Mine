/**
 * Browser loaders for the headquarters' data workstream endpoints (docs/ANUM_MINE_PRESENCE_AND_CAMPUS.md):
 * the live presence snapshot, the campus, the campus profile and a desk's work panel. A deployment
 * without them answers 404, 405, 501 or 503 and the caller falls back to what it can read itself.
 */
import * as api from './api.ts';
import { ApiError } from './errors.ts';
import { createCampusClient } from './officeCampusClient.ts';
import { createPresenceClient } from './officePresenceClient.ts';
import type { PresenceItemLike } from './hqPresence.ts';
import type { StatusKey } from './campusModel.ts';

const campusClient = createCampusClient(api.apiRequest, api.currentScope);
const presenceClient = createPresenceClient(api.apiRequest, api.currentScope);

/** This deployment has no such endpoint (or its database is not configured): use the fallback. */
export const endpointMissing = (error: unknown): boolean =>
  error instanceof ApiError && (error.status === 404 || error.status === 405 || error.status === 501 || error.status === 503);

export type LoadedPresence = Readonly<{ items: PresenceItemLike[]; asOf: string }>;

/** The open workspace's presence items, or null when the endpoint is missing. Real failures throw. */
export async function loadPresence(): Promise<LoadedPresence | null> {
  try {
    const state = await presenceClient.load();
    return { items: [...state.items.values()].map((i) => ({ ...i, state: i.state as StatusKey })), asOf: state.asOf };
  } catch (error) {
    if (endpointMissing(error)) return null;
    throw error;
  }
}

export const loadCampusContract = campusClient.loadCampus;
export const loadDeskWork = campusClient.loadWork;
export const saveCampusProfile = campusClient.saveProfile;
