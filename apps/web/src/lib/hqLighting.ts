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
    sunDir: mix3([-0.58, -0.62, -0.46], [0.35, -0.72, 0.3]),
    sunColor: mix3([1.0, 0.89, 0.72], [0.5, 0.6, 1.0]),
    sky: mix3([0.58, 0.69, 0.86], [0.36, 0.44, 0.74]),
    ground: mix3([0.4, 0.35, 0.28], [0.24, 0.26, 0.38]),
    fog: mix3([0.76, 0.84, 0.93], [0.1, 0.14, 0.29]),
    sunIntensity: mix1(4.0, 1.2),
    hemiIntensity: mix1(0.3, 1.05),
    envIntensity: mix1(0.45, 0.3),
    exposure: mix1(1.25, 1.45),
  };
}
