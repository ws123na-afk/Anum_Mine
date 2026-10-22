/**
 * Loads the campus. Preferred source: `GET /api/v1/office/campus` (one building per authorised
 * workspace, with verified presence). Until a deployment has it, the caller's workspace
 * memberships stand in, and only the open workspace carries counts.
 */
import * as api from './api.ts';
import { ApiError } from './errors.ts';
import { campusFromApi, campusFromMemberships, type Campus, type CampusTotals } from './campusModel.ts';
import { loadCampusContract } from './hqPresenceApi.ts';

export type CampusSource = 'api' | 'memberships';
export type LoadedCampus = Readonly<{ campus: Campus; source: CampusSource }>;

export async function loadCampus(local: Readonly<{ totals: CampusTotals | null; floors: number | null; verifiedAt: string | null }>): Promise<LoadedCampus> {
  try {
    const payload = await loadCampusContract();
    return { campus: campusFromApi(payload), source: 'api' };
  } catch (error) {
    // A refusal means the person may not read the campus: say so. A missing endpoint or a network
    // failure falls back to the caller's own memberships, which carry no counts beyond the open workspace.
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) throw error;
  }
  const memberships = await api.listMyWorkspaces();
  return { campus: campusFromMemberships(memberships, api.currentScope(), local), source: 'memberships' };
}
