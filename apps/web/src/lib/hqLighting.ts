/**
 * Day, dusk and night lighting for the headquarters scenes. The three presets are keyframes of one
 * blend (0 = day, `DUSK` = blue hour, 1 = night) so the toggle can animate through them. Colours are
 * sRGB triples in 0..1; the renderer converts them. Pure data in, data out.
 */
export type Triple = readonly [number, number, number];

export type Lighting = Readonly<{
  /** The direction the sun (or moon) light travels. */
  sunDir: Triple; sunColor: Triple; sky: Triple; ground: Triple; fog: Triple;
  /** Linear light strengths for the directional light, the hemisphere light and the reflection environment. */
  sunIntensity: number; hemiIntensity: number; envIntensity: number; exposure: number;
  /** Sky dome: the top colour, and the warm band that sits on the horizon (added on top of the fog coloured horizon). */
  zenith: Triple; glow: Triple;
  /** 0..1: how lit the windows, lamps and signs are (rises through dusk, so blue hour already glows). */
  lit: number;
  /** 0..1: how much of the sky shows cloud (thin at dusk, fainter at night). */
  cloud: number;
}>;

/** The blend value of the blue hour preset. */
export const DUSK = 0.58;

export type LightPreset = 'day' | 'dusk' | 'night';
export const LIGHT_PRESETS: readonly LightPreset[] = ['day', 'dusk', 'night'];

/** The blend value a preset settles on. */
export function blendFor(preset: LightPreset): number { return preset === 'day' ? 0 : preset === 'dusk' ? DUSK : 1; }

type Key = Omit<Lighting, 'lit'>;

const DAY: Key = {
  sunDir: [-0.58, -0.62, -0.46], sunColor: [1.0, 0.89, 0.72], sky: [0.58, 0.69, 0.86], ground: [0.4, 0.35, 0.28], fog: [0.76, 0.84, 0.93],
  sunIntensity: 4.0, hemiIntensity: 0.3, envIntensity: 0.45, exposure: 1.25, zenith: [0.2, 0.44, 0.84], glow: [0.05, 0.03, 0.01], cloud: 0.8,
};
// Blue hour: a low warm sun behind the horizon, a lavender haze, a deep blue zenith and an orange rim, like the owner's concept renders.
const DUSKK: Key = {
  sunDir: [-0.7, -0.3, -0.5], sunColor: [1.0, 0.6, 0.38], sky: [0.46, 0.5, 0.8], ground: [0.3, 0.28, 0.36], fog: [0.46, 0.46, 0.68],
  sunIntensity: 1.9, hemiIntensity: 0.78, envIntensity: 0.4, exposure: 1.42, zenith: [0.07, 0.12, 0.34], glow: [0.6, 0.27, 0.12], cloud: 0.55,
};
const NIGHT: Key = {
  sunDir: [0.35, -0.72, 0.3], sunColor: [0.5, 0.6, 1.0], sky: [0.36, 0.44, 0.74], ground: [0.24, 0.26, 0.38], fog: [0.1, 0.14, 0.29],
  sunIntensity: 1.2, hemiIntensity: 1.05, envIntensity: 0.3, exposure: 1.45, zenith: [0.012, 0.03, 0.095], glow: [0.3, 0.12, 0.05], cloud: 0.3,
};

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const lerp3 = (a: Triple, b: Triple, t: number): Triple => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
const smooth = (a: number, b: number, x: number) => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

export function lightingFor(night: number): Lighting {
  const n = Math.max(0, Math.min(1, night));
  const [from, to, t] = n <= DUSK ? [DAY, DUSKK, n / DUSK] as const : [DUSKK, NIGHT, (n - DUSK) / (1 - DUSK)] as const;
  return {
    sunDir: lerp3(from.sunDir, to.sunDir, t), sunColor: lerp3(from.sunColor, to.sunColor, t), sky: lerp3(from.sky, to.sky, t),
    ground: lerp3(from.ground, to.ground, t), fog: lerp3(from.fog, to.fog, t), zenith: lerp3(from.zenith, to.zenith, t), glow: lerp3(from.glow, to.glow, t),
    sunIntensity: lerp(from.sunIntensity, to.sunIntensity, t), hemiIntensity: lerp(from.hemiIntensity, to.hemiIntensity, t),
    envIntensity: lerp(from.envIntensity, to.envIntensity, t), exposure: lerp(from.exposure, to.exposure, t), cloud: lerp(from.cloud, to.cloud, t),
    lit: smooth(0.2, DUSK, n),
  };
}
