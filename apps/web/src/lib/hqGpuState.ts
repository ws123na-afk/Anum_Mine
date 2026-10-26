/** A tiny store for the renderer's quality state, so the header chip and the Graphics check follow it without the renderer knowing React. */
import type { QualityState } from './hqQualityTiers.ts';

let current: QualityState | null = null;
const listeners = new Set<() => void>();

export const getGpuState = (): QualityState | null => current;
export function setGpuState(next: QualityState | null): void {
  if (next === current) return;
  current = next;
  for (const l of [...listeners]) l();
}
export function subscribeGpuState(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
