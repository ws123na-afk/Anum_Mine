/**
 * Which night look the night toggle settles on: the blue hour ("dusk") or full night. Day is the other toggle
 * state. Kept apart from the day/night boolean the pages already pass around, so adding the blue hour changed
 * no prop: the stage reads the preset here and a small store tells it when it changes. Pure apart from storage.
 */
import { blendFor, type LightPreset } from './hqLighting.ts';

const KEY = 'anum.hq.preset';
let current: 'dusk' | 'night' | null = null;
const listeners = new Set<() => void>();

export function readNightPreset(): 'dusk' | 'night' {
  if (current) return current;
  try { current = window.localStorage.getItem(KEY) === 'dusk' ? 'dusk' : 'night'; } catch { current = 'night'; }
  return current;
}

export function writeNightPreset(preset: 'dusk' | 'night'): void {
  current = preset;
  try { window.localStorage.setItem(KEY, preset); } catch { /* remembered for this page only */ }
  for (const l of listeners) l();
}

export function subscribeNightPreset(listener: () => void): () => void { listeners.add(listener); return () => { listeners.delete(listener); }; }

/** The blend value the stage animates to for the toggle state and the remembered night preset. */
export function targetBlend(night: boolean, nightPreset: 'dusk' | 'night'): number {
  const preset: LightPreset = night ? nightPreset : 'day';
  return blendFor(preset);
}
