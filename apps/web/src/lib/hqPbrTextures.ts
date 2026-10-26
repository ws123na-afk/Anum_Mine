/**
 * Procedural PBR detail maps for the photographic tier (docs/ANUM_MINE_HQ_PHOTOREAL.md). Pure: no three.js, no DOM, no network,
 * no image files. Each kind is generated once at start-up into one small RGBA byte array (128 x 128, 64 kB) that the renderer
 * uploads as a repeating data texture:
 *
 *   R, G  tangent space normal x and y (0.5 is flat); z is rebuilt in the shader, so it needs no storage
 *   B     roughness multiplier (0.5 leaves the material's roughness as authored, lower is glossier, higher is rougher)
 *   A     cavity or thickness: 1 on open surface, darker in grooves and pores (leaf: how thin the blade is, for translucency)
 *
 * Every generator is deterministic (an integer hash of the cell and the kind, no sequential random state) and tileable (the
 * noise lattice and the normal filter wrap), so the texture repeats without a visible seam. Tests: `test/hqPbrTextures.test.ts`.
 */
export type PbrKind = 'concrete' | 'brushedMetal' | 'wood' | 'marble' | 'carpet' | 'asphaltWorn' | 'paving' | 'leaf';
export const PBR_KINDS: readonly PbrKind[] = ['concrete', 'brushedMetal', 'wood', 'marble', 'carpet', 'asphaltWorn', 'paving', 'leaf'];
export const PBR_SIZE = 128;

const KIND_SEED: Readonly<Record<PbrKind, number>> = { concrete: 11, brushedMetal: 23, wood: 37, marble: 41, carpet: 53, asphaltWorn: 67, paving: 71, leaf: 83 };
/** How strongly the height field bends the normal, per kind. */
const STRENGTH: Readonly<Record<PbrKind, number>> = { concrete: 2.6, brushedMetal: 1.1, wood: 1.8, marble: 1.2, carpet: 3.2, asphaltWorn: 3.6, paving: 2.4, leaf: 1.6 };

/** Integer lattice hash to 0..1. */
function cell(ix: number, iy: number, seed: number): number {
  let h = Math.imul(ix, 374761393) ^ Math.imul(iy, 668265263) ^ Math.imul(seed, 2147483647);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

const wrap = (i: number, n: number) => ((i % n) + n) % n;
const smooth = (t: number) => t * t * (3 - 2 * t);

/** Value noise over `px` by `py` lattice cells that wraps, sampled at u,v in 0..1. */
function noise(u: number, v: number, px: number, py: number, seed: number): number {
  const x = u * px, y = v * py;
  const x0 = Math.floor(x), y0 = Math.floor(y);
  const fx = smooth(x - x0), fy = smooth(y - y0);
  const a = cell(wrap(x0, px), wrap(y0, py), seed), b = cell(wrap(x0 + 1, px), wrap(y0, py), seed);
  const c = cell(wrap(x0, px), wrap(y0 + 1, py), seed), d = cell(wrap(x0 + 1, px), wrap(y0 + 1, py), seed);
  return a + (b - a) * fx + (c - a) * fy + (a - b - c + d) * fx * fy;
}

function fbm(u: number, v: number, base: number, octaves: number, seed: number): number {
  let sum = 0, amp = 0.5, total = 0, p = base;
  for (let o = 0; o < octaves; o++) { sum += amp * noise(u, v, p, p, seed + o * 7); total += amp; amp *= 0.5; p *= 2; }
  return sum / total;
}

const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);

type Texel = { h: number; rough: number; thick: number };

/** Height (0..1), roughness multiplier (0..1, 0.5 neutral) and thickness for one texel, at u,v in 0..1. */
function texel(kind: PbrKind, u: number, v: number, seed: number, out: Texel): void {
  switch (kind) {
    case 'concrete': {
      const pits = cell(Math.floor(u * 128), Math.floor(v * 128), seed) > 0.965 ? 0.35 : 0;
      let h = 0.55 * fbm(u, v, 6, 4, seed) + 0.2 * noise(u, v, 64, 64, seed + 3) - pits;
      // Formwork joints at half the texture and a few tie holes.
      const j = Math.min(Math.abs(u - 0.5), Math.abs(v - 0.5));
      if (j < 0.008) h -= 0.45;
      const tx = (u * 4) % 1, ty = (v * 2) % 1;
      if (Math.hypot(tx - 0.5, ty - 0.5) < 0.018) h -= 0.5;
      out.h = h; out.rough = 0.62 + 0.25 * fbm(u, v, 3, 3, seed + 9) - pits * 0.3; out.thick = 1 - pits;
      return;
    }
    case 'brushedMetal': {
      // Streaks along x: low frequency across x, very high across y.
      const s = noise(u, v, 3, 120, seed) * 0.6 + noise(u, v, 5, 64, seed + 5) * 0.4;
      out.h = s; out.rough = 0.3 + 0.35 * noise(u, v, 2, 90, seed + 11); out.thick = 0.78 + 0.22 * s;
      return;
    }
    case 'wood': {
      // Four planks side by side (along v); rings stretched along u, warped by noise.
      const plank = Math.floor(v * 4), pv = (v * 4) % 1;
      const warp = fbm(u, v, 3, 3, seed + plank) * 2.2;
      const rings = Math.sin((pv * 9 + warp + cell(plank, 0, seed) * 6) * Math.PI * 2);
      let h = 0.5 + 0.18 * rings + 0.2 * noise(u, v, 2, 220, seed + 4);
      const seam = Math.min(pv, 1 - pv) < 0.034;
      if (seam) h -= 0.6;
      out.h = h; out.rough = 0.45 + 0.18 * rings * 0.5 + 0.1 * noise(u, v, 8, 160, seed + 8); out.thick = seam ? 0.3 : 1;
      return;
    }
    case 'marble': {
      const warp = fbm(u, v, 3, 4, seed) * 3.0;
      const vein = Math.abs(Math.sin((u * 2 + v * 3 + warp) * Math.PI));
      const thin = vein < 0.08 ? 1 - vein / 0.08 : 0;
      out.h = 0.5 + 0.16 * fbm(u, v, 8, 3, seed + 2) - thin * 0.12;
      out.rough = 0.28 + thin * 0.3 + 0.12 * fbm(u, v, 4, 3, seed + 6); out.thick = 1 - thin * 0.1;
      return;
    }
    case 'carpet': {
      const tuft = noise(u, v, 96, 96, seed) * 0.7 + noise(u, v, 48, 48, seed + 1) * 0.3;
      out.h = tuft; out.rough = 0.88 + 0.08 * noise(u, v, 12, 12, seed + 2); out.thick = 0.55 + 0.45 * tuft;
      return;
    }
    case 'asphaltWorn': {
      const grain = cell(Math.floor(u * 128), Math.floor(v * 128), seed);
      const aggregate = grain > 0.8 ? 0.3 + 0.5 * grain : 0.2 * grain;
      // Wear: wheel polish smooths and darkens blotches; cracks are thin ridged-noise lines.
      const wear = clamp01((fbm(u, v, 3, 4, seed + 13) - 0.45) * 3.2);
      const ridge = Math.abs(fbm(u, v, 5, 4, seed + 17) - 0.5);
      const crack = ridge < 0.012 ? 1 - ridge / 0.012 : 0;
      out.h = aggregate * (1 - 0.7 * wear) + 0.2 * fbm(u, v, 8, 2, seed + 19) - crack * 0.55;
      out.rough = 0.88 - wear * 0.34 + (crack > 0 ? 0.1 : 0); out.thick = 1 - crack * 0.6;
      return;
    }
    case 'paving': {
      // 4 x 4 slabs, each with its own height and tilt, grout joints, a speckled surface.
      const sx = Math.floor(u * 4), sy = Math.floor(v * 4);
      const fx = (u * 4) % 1, fy = (v * 4) % 1;
      const own = cell(sx, sy, seed);
      const edge = Math.min(fx, 1 - fx, fy, 1 - fy);
      const joint = edge < 0.035;
      out.h = (joint ? 0.12 : 0.6 + 0.1 * own + 0.04 * (fx - 0.5) * (own - 0.5)) + 0.12 * noise(u, v, 48, 48, seed + 3) + 0.08 * cell(Math.floor(u * 128), Math.floor(v * 128), seed + 5);
      out.rough = joint ? 0.95 : 0.55 + 0.18 * own + 0.1 * noise(u, v, 16, 16, seed + 7); out.thick = joint ? 0.35 : 1;
      return;
    }
    case 'leaf': {
      // A blade across the texture: a midrib, side veins at an angle, thin lamina between them.
      const dx = u - 0.5, dy = v - 0.5;
      const mid = Math.abs(dx) < 0.025 ? 1 : 0;
      const side = Math.abs(Math.sin((dy * 14 + Math.abs(dx) * 9) * Math.PI)) < 0.12 ? 1 : 0;
      const vein = Math.max(mid, side * 0.7);
      out.h = 0.45 + vein * 0.35 + 0.1 * noise(u, v, 24, 24, seed);
      out.rough = 0.42 + 0.1 * noise(u, v, 8, 8, seed + 2); out.thick = 1 - 0.55 * (1 - vein) * (0.6 + 0.4 * noise(u, v, 6, 6, seed + 4));
      return;
    }
  }
}

/** Generates the four-channel map of `kind` (see the file header). Deterministic and tileable. */
export function generatePbr(kind: PbrKind, size: number = PBR_SIZE): Uint8Array {
  const n = Math.max(8, Math.min(512, Math.floor(size)));
  const seed = KIND_SEED[kind];
  const height = new Float32Array(n * n), rough = new Float32Array(n * n), thick = new Float32Array(n * n);
  const t: Texel = { h: 0, rough: 0.5, thick: 1 };
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      texel(kind, (x + 0.5) / n, (y + 0.5) / n, seed, t);
      const i = y * n + x;
      height[i] = t.h; rough[i] = t.rough; thick[i] = t.thick;
    }
  }
  const out = new Uint8Array(n * n * 4);
  const k = STRENGTH[kind] * (n / 128);
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const at = (xx: number, yy: number) => height[wrap(yy, n) * n + wrap(xx, n)];
      // Sobel with wrap-around, so the edge texels match the opposite edge.
      const dx = (at(x + 1, y - 1) + 2 * at(x + 1, y) + at(x + 1, y + 1)) - (at(x - 1, y - 1) + 2 * at(x - 1, y) + at(x - 1, y + 1));
      const dy = (at(x - 1, y + 1) + 2 * at(x, y + 1) + at(x + 1, y + 1)) - (at(x - 1, y - 1) + 2 * at(x, y - 1) + at(x + 1, y - 1));
      let nx = -dx * k * 0.25, ny = -dy * k * 0.25;
      const len = Math.hypot(nx, ny, 1);
      nx /= len; ny /= len;
      const i = (y * n + x) * 4;
      out[i] = Math.round((nx * 0.5 + 0.5) * 255);
      out[i + 1] = Math.round((ny * 0.5 + 0.5) * 255);
      out[i + 2] = Math.round(clamp01(rough[y * n + x]) * 255);
      out[i + 3] = Math.round(clamp01(thick[y * n + x]) * 255);
    }
  }
  return out;
}

/** Which kind each renderer surface class uses (classes not listed keep their plain material). */
export const PBR_OF_CLASS: Readonly<Record<string, PbrKind>> = {
  concrete: 'concrete', metal: 'brushedMetal', roller: 'brushedMetal', wood: 'wood', tile: 'marble', asphalt: 'asphaltWorn', court: 'paving', cloth: 'carpet', foliage: 'leaf',
};
