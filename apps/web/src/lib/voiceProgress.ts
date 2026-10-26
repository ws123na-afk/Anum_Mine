// What the Voice page shows while a reply has not started (docs/voice.md#progress): a plain
// progress line, the seconds after 2 s, a "taking longer" message with Cancel and Try again after
// 8 s, and a hard stop after 30 s. Pure, so the thresholds are unit-tested.
import type { MessageKey } from './i18n';

export const ELAPSED_AFTER_MS = 2000;
export const SLOW_AFTER_MS = 8000;
export const HARD_TIMEOUT_MS = 30000;

export interface ProgressMarker {
  phase: 'thinking' | 'checking';
  topic: string;
}

export interface ThinkingView {
  /** The message for the progress line. */
  key: MessageKey;
  /** Whole seconds to show, or null before 2 s. */
  seconds: number | null;
  /** Taking longer than usual: the page offers Cancel and Try again. */
  slow: boolean;
  /** Past the hard limit: the page stops the request. */
  timedOut: boolean;
}

/** Narrow an event payload to a marker (anything else is ignored). */
export function asProgress(value: unknown): ProgressMarker | null {
  if (!value || typeof value !== 'object') return null;
  const data = value as Record<string, unknown>;
  if (data.phase !== 'thinking' && data.phase !== 'checking') return null;
  return { phase: data.phase, topic: typeof data.topic === 'string' ? data.topic : '' };
}

export function thinkingView(elapsedMs: number, marker: ProgressMarker | null): ThinkingView {
  let key: MessageKey = 'voice.state.thinking';
  if (marker?.phase === 'checking') {
    key = marker.topic === 'tyre' ? 'voice.progress.tyre'
      : marker.topic === 'tasks' ? 'voice.progress.tasks'
      : marker.topic === 'approvals' ? 'voice.progress.approvals'
      : marker.topic === 'sales' ? 'voice.progress.sales'
      : 'voice.progress.data';
  }
  return {
    key,
    seconds: elapsedMs >= ELAPSED_AFTER_MS ? Math.floor(elapsedMs / 1000) : null,
    slow: elapsedMs >= SLOW_AFTER_MS,
    timedOut: elapsedMs >= HARD_TIMEOUT_MS,
  };
}
