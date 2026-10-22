/**
 * Day and night lighting for the headquarters scenes. Day and night are the two ends of one blend
 * (0 = day, 1 = night) so the toggle can animate. Colours are sRGB triples in 0..1; the renderer
 * converts them. Pure data in, data out.
 */
export type Triple = readonly [number, number, number];

export type Lighting = Readonly<{
  /** The direction the sun (or moon) light travels. */
  sunDir: Triple; sunColor: Triple; sky: Triple; ground: Triple; fog: Triple;
  /** Linear light strengths for the directional light, the hemisphere light and the reflection environment. */
  sunIntensity: number; hemiIntensity: number; envIntensity: number; exposure: number;
}>;

export function lightingFor(night: number): Lighting {
  const n = Math.max(0, Math.min(1, night));
  const mix3 = (a: Triple, b: Triple): Triple => [a[0] + (b[0] - a[0]) * n, a[1] + (b[1] - a[1]) * n, a[2] + (b[2] - a[2]) * n];
  const mix1 = (a: number, b: number) => a + (b - a) * n;
  return {
    sunDir: mix3([-0.45, -0.8, -0.38], [0.35, -0.75, 0.3]),
    sunColor: mix3([1.0, 0.93, 0.8], [0.45, 0.56, 0.95]),
    sky: mix3([0.6, 0.68, 0.78], [0.27, 0.33, 0.5]),
    ground: mix3([0.34, 0.33, 0.3], [0.15, 0.17, 0.24]),
    fog: mix3([0.62, 0.74, 0.86], [0.05, 0.09, 0.17]),
    sunIntensity: mix1(2.9, 0.75),
    hemiIntensity: mix1(0.85, 0.7),
    envIntensity: mix1(0.85, 0.32),
    exposure: mix1(0.95, 1.1),
  };
}
