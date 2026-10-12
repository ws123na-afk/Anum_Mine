// The voice assistant's space scene (docs/voice.md#the-space-orb): which state the orb shows for
// the real voice lifecycle, its colours and amplitude, and the scene's pure geometry. DOM-free,
// so `pnpm test:unit` covers it; lib/spaceScene.ts draws it.

/** What the orb shows. `alert` is the governance signal: an approval waits for a person. */
export type SpaceMode = 'standby' | 'listening' | 'thinking' | 'speaking' | 'alert';
export const SPACE_MODES: readonly SpaceMode[] = ['standby', 'listening', 'thinking', 'speaking', 'alert'];

/** State colours from the reference: restrained cyan, reactive green, purple, warm; amber for alert. */
export const SPACE_COLORS: Record<SpaceMode, readonly [number, number, number]> = {
  standby: [99, 202, 233],
  listening: [102, 239, 193],
  thinking: [180, 143, 251],
  speaking: [255, 194, 130],
  alert: [255, 181, 71],
};

/** The Voice view's console states (VoiceView.tsx) and the call's phases (lib/callEngine.ts). */
export type ConsoleState = 'idle' | 'listening' | 'thinking' | 'speaking';
export type CallPhase = 'connecting' | 'listening' | 'hearing' | 'thinking' | 'speaking' | 'ended' | string;

/**
 * The orb for the Voice view. Microphone stopped (idle, after any error, denial, silence timeout,
 * cancel or stopped audio, all of which return the console to idle) is standby; microphone active
 * is listening; waiting for the reply is thinking; reply audio playing is speaking. A pending
 * approval shows amber only while nothing else is happening.
 */
export function voiceSpaceMode(state: ConsoleState, alert: boolean): SpaceMode {
  if (state === 'listening' || state === 'thinking' || state === 'speaking') return state;
  return alert ? 'alert' : 'standby';
}

/** The orb for a call: hearing the caller counts as listening; anything else not active is standby. */
export function callSpaceMode(phase: CallPhase | null | undefined, alert = false): SpaceMode {
  switch (phase) {
    case 'listening': case 'hearing': return 'listening';
    case 'thinking': return 'thinking';
    case 'speaking': return 'speaking';
    default: return alert ? 'alert' : 'standby';
  }
}

/**
 * The bars' amplitude, 0..1. Listening and speaking follow the measured audio (microphone or reply
 * output); thinking pulses on its own; standby and alert stay still. A missing measurement (no
 * analyser, muted) gives a low idle level rather than a fake waveform.
 */
export function spaceLevel(mode: SpaceMode, measured: number | null | undefined): number {
  if (mode === 'thinking') return 0.5;
  if (mode !== 'listening' && mode !== 'speaking') return 0;
  const value = typeof measured === 'number' && Number.isFinite(measured) ? measured : 0;
  return Math.min(1, Math.max(0, value));
}

/** Seconds until the next shooting star: 2.5 to 4 s, from a 0..1 random number. */
export function nextMeteorDelay(random: number): number {
  return 2.5 + Math.min(1, Math.max(0, random)) * 1.5;
}

/** The orbiting satellite is in front of Earth on the lower half of its ellipse. */
export function satelliteInFront(angle: number): boolean {
  return Math.sin(angle) > 0;
}

/** Device pixel ratio for the canvas: sharp on retina, capped at 2 as the reference asks. */
export function spaceDpr(devicePixelRatio: number): number {
  return Math.min(Number.isFinite(devicePixelRatio) && devicePixelRatio > 0 ? devicePixelRatio : 1, 2);
}

/** Atlas regions as fractions of its width: astronaut, Earth, satellite. */
export const ATLAS_REGIONS = [0, 0.275, 0.635, 1] as const;

export interface Crop { x: number; y: number; w: number; h: number }

/**
 * The opaque bounds of one atlas region (alpha above `threshold`), so sprites draw without their
 * transparent margins; the whole region when it has no opaque pixel.
 */
export function alphaBounds(pixels: ArrayLike<number>, width: number, height: number, part: 0 | 1 | 2, threshold = 35): Crop {
  const start = Math.floor(ATLAS_REGIONS[part] * width), end = Math.floor(ATLAS_REGIONS[part + 1] * width);
  let left = end, right = start, top = height, bottom = 0;
  for (let y = 0; y < height; y += 1) {
    for (let x = start; x < end; x += 1) {
      if (pixels[(y * width + x) * 4 + 3] > threshold) {
        if (x < left) left = x;
        if (x > right) right = x;
        if (y < top) top = y;
        if (y > bottom) bottom = y;
      }
    }
  }
  return right >= left && bottom >= top ? { x: left, y: top, w: right - left + 1, h: bottom - top + 1 } : { x: start, y: 0, w: end - start, h: height };
}

/** Points evenly spread on a unit sphere (Fibonacci lattice): the orb's particles. */
export function spherePoints(count: number): [number, number, number][] {
  return Array.from({ length: count }, (_, i) => {
    const y = 1 - (2 * (i + 0.5)) / count, a = i * 2.39996323, r = Math.sqrt(1 - y * y);
    return [Math.cos(a) * r, y, Math.sin(a) * r];
  });
}

/** Particle count by canvas size: fewer on phones and in the compact orb. */
export function particleBudget(width: number, height: number): number {
  return Math.round(Math.min(850, Math.max(320, Math.min(width, height) * 1.6)));
}
