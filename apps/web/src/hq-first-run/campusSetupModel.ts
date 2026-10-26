/** Pure parts of the one-click campus flow (no browser, no network): the state the panel and the assistant card share. */
import type { StarterCampusPreview, StarterCampusResult, StarterCounts } from '@anum/contracts';
import { hasWork } from '../hq-starter-campus/client.ts';

export type SetupPhase = 'loading' | 'ready' | 'denied' | 'error' | 'creating' | 'done' | 'partial' | 'failed';
export interface SetupState {
  phase: SetupPhase;
  preview: StarterCampusPreview | null;
  result: StarterCampusResult | null;
  /** What went wrong, already in words (never a stack trace). */
  detail: string | null;
  /** The last confirm was refused because the records moved; the plan on screen is the new one. */
  stale: boolean;
}

export const INITIAL_SETUP: SetupState = { phase: 'loading', preview: null, result: null, detail: null, stale: false };

/** What a click would create right now (all buildings), or null while there is no plan. */
export function createCounts(preview: StarterCampusPreview | null): StarterCounts | null {
  return preview ? preview.totals.create : null;
}

/** A person who may confirm, and a plan that still has something to create. */
export function canCreate(state: SetupState): boolean {
  return state.phase === 'ready' && state.preview !== null && state.preview.can_confirm && hasWork(state.preview.totals.create);
}
