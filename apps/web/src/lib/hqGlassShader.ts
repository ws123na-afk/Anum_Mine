/**
 * The glass shader's look through a pane into the plan atlas (interior mapping), loaded on demand by the renderer when a campus
 * scene carries a plan atlas (`RenderScene.glassPlan`). GLSL only; no three.js, so the unit runner can import it.
 *
 * A pane carries its atlas row in the integer part of its u texture coordinate (`tagPaneUv`). The fragment position and the view
 * ray give the storey (from the height) and the plan position behind the pane: the ray is followed to the floor (or the ceiling)
 * of that storey in up to six steps, stopping at a partition wall; the atlas texel there says what is seen (room floor by kind,
 * desk, screen, a seated figure, a wall) and how lit it is. The software path, and panes far from the camera, read one texel
 * 2.8 m inside the facade instead (no parallax, one texture fetch).
 */
export const MAX_PLAN_SLOTS = 16;

export const PLAN_COMMON = `
uniform sampler2D uPlanTex;
uniform vec4 uPlanRect[${MAX_PLAN_SLOTS}];
uniform vec4 uPlanInfo[${MAX_PLAN_SLOTS}];
uniform vec4 uPlanGrid;
uniform float uPlanCount;
/* One texel of a building's storey at a world position; alpha is zero outside the plan. */
vec4 hq_planAt(int bi, int fl, vec2 xz) {
  vec4 R = uPlanRect[bi];
  vec2 q = (xz - R.xy) * R.zw;
  float inside = step(0.0, q.x) * step(q.x, 1.0) * step(0.0, q.y) * step(q.y, 1.0);
  q = clamp(q, vec2(0.003), vec2(0.997));
  vec4 t = texture2D(uPlanTex, (vec2(float(fl), float(bi)) + q) / uPlanGrid.xy);
  t.a *= inside;
  return t;
}
/* alpha byte to (wall, light, outside) */
vec3 hq_planA(float a) {
  float a8 = floor(a * 255.0 + 0.5);
  float outside = step(a8, 0.5);
  float wall = step(127.5, a8);
  return vec3(wall, clamp((a8 - wall * 128.0 - 1.0) / 126.0, 0.0, 1.0), outside);
}`;

/** Runs after `hqRoom` is declared: fills hqRoom / hqEmit / hqHas / hqPlan for a pane that carries an atlas row. */
export const PLAN_COLOR = `
  {
    int hqBi = int(floor((vPUv.x + 0.05) * 0.5)) - 1;
    if (hqBi >= 0 && float(hqBi) < uPlanCount) {
      vec4 inf = uPlanInfo[hqBi];
      vec3 Pw = vWPos;
      vec3 Dv = normalize(Pw - cameraPosition);
      vec3 Nf = normalize(vWNrm);
      if (dot(Nf, Dv) > 0.0) Nf = -Nf;
      float st = clamp(floor((Pw.y - inf.y) / inf.z), 0.0, inf.x - 1.0);
      int fl = int(st);
      vec3 rgb = vec3(0.34, 0.38, 0.43);
      float lt = 0.18;
      bool deep = false;
#ifdef HQ_RICH
      deep = length(Pw - cameraPosition) < 150.0;
#endif
      if (deep) {
        float yf = inf.y + st * inf.z + 0.02;
        float yc = inf.y + (st + 1.0) * inf.z - 0.35;
        float tm = 40.0;
        bool toCeil = false;
        if (Dv.y < -0.02) tm = min(tm, (yf - Pw.y) / Dv.y);
        else if (Dv.y > 0.02) { float tc = (yc - Pw.y) / Dv.y; if (tc < tm) { tm = tc; toCeil = true; } }
        for (int k = 1; k <= 6; k++) {
          float tk = tm * float(k) / 6.0;
          vec3 Q = Pw + Dv * tk;
          vec4 s = hq_planAt(hqBi, fl, Q.xz);
          vec3 a = hq_planA(s.a);
          if (a.z > 0.5) { rgb = vec3(0.34, 0.38, 0.43); lt = 0.18; break; }
          if (a.x > 0.5) { rgb = s.rgb * 0.85 * mix(0.6, 1.0, clamp((Q.y - yf) / max(yc - yf, 0.1), 0.0, 1.0)); lt = a.y; break; }
          if (k == 6) {
            lt = a.y;
            if (toCeil) {
              float fx = fract(Q.x * 0.3), fz = fract(Q.z * 0.3);
              float panel = step(0.3, fx) * step(fx, 0.7) * step(0.3, fz) * step(fz, 0.7);
              rgb = mix(vec3(0.84, 0.83, 0.8), vec3(1.0, 0.96, 0.85), panel * step(0.5, a.y));
            } else rgb = s.rgb;
          }
        }
      } else {
        vec3 Qp = Pw - Nf * 2.8;
        vec4 s = hq_planAt(hqBi, fl, Qp.xz);
        vec3 a = hq_planA(s.a);
        if (a.z < 0.5) { rgb = s.rgb; lt = a.y; }
      }
      vec3 lin = rgb * rgb;
      // Light falls off fast: a vacant room or a corridor is dim, a room with somebody at work glows, a lit screen is bright.
      hqRoom = lin * (mix(0.22, 0.07, uNight) + lt * lt);
      hqEmit = lin * lt * lt * lt * vec3(1.0, 0.92, 0.78) * 2.6;
      hqHas = 1.0;
      hqPlan = 1.0;
      glowW = 1.0;
    }
  }`;

/** Adds the plan uniforms to the renderer's shared uniforms (the classes come from the renderer, so this chunk imports nothing). */
export function installPlan(shared: Record<string, { value: unknown }>, V4: new () => unknown): void {
  shared.uPlanTex = { value: null };
  shared.uPlanRect = { value: Array.from({ length: MAX_PLAN_SLOTS }, () => new V4()) };
  shared.uPlanInfo = { value: Array.from({ length: MAX_PLAN_SLOTS }, () => new V4()) };
  shared.uPlanGrid = { value: new V4() };
  shared.uPlanCount = { value: 0 };
}

type Vec4Like = { set(x: number, y: number, z: number, w: number): unknown };
export type PlanSource = Readonly<{ cols: number; rows: number; buildings: ReadonlyArray<Readonly<{ rect: readonly number[]; storeys: number; baseY: number; pitch: number }>> }>;

/** Points the glass uniforms at an atlas texture and its per-building rectangles (world x0, z0, 1/width, 1/depth) and storey info. */
export function fillUniforms(u: Record<string, { value: unknown }>, plan: PlanSource, texture: unknown): void {
  const rect = u.uPlanRect.value as Vec4Like[], info = u.uPlanInfo.value as Vec4Like[];
  u.uPlanTex.value = texture;
  (u.uPlanGrid.value as Vec4Like).set(plan.cols, plan.rows, 0, 0);
  u.uPlanCount.value = plan.buildings.length;
  plan.buildings.forEach((b, i) => {
    rect[i].set(b.rect[0], b.rect[1], 1 / (b.rect[2] - b.rect[0]), 1 / (b.rect[3] - b.rect[1]));
    info[i].set(b.storeys, b.baseY, b.pitch, 0);
  });
}

/** three.js constants (stable across versions): RGBAFormat, UnsignedByteType, NearestFilter. */
const RGBA_FORMAT = 1023, UNSIGNED_BYTE = 1009, NEAREST_FILTER = 1003;
type Disposable = { dispose(): void } | null;
type Hooks = Readonly<{
  /** The renderer's shared uniforms, the glass material, the three.js classes needed, the stats and a redraw request. */
  u: Record<string, { value: unknown }>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  g: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  T: Record<string, any>;
  s: { glassPlan?: string };
  r: () => void;
}>;

/**
 * Everything the renderer does for a plan atlas, in this chunk: the first time it adds the uniforms and rewrites the glass program
 * (`patchGlass`, with its own program cache key), then for each revision a fresh nearest-filtered texture and the building rectangles.
 */
export function applyPlan(h: Hooks, plan: PlanSource & { revision: string; data: Uint8Array; width: number; height: number }): void {
  if (plan.revision === h.s.glassPlan) return;
  const shared = h.u, glass = h.g;
  if (!shared.uPlanTex) {
    installPlan(shared, h.T.Vector4);
    if (glass) {
      const before = glass.onBeforeCompile;
      glass.onBeforeCompile = (shader: { fragmentShader: string }, renderer: unknown) => { before(shader, renderer); shader.fragmentShader = patchGlass(shader.fragmentShader); };
      glass.customProgramCacheKey = () => 'hq-glass-plan';
      glass.needsUpdate = true;
    }
  }
  (shared.uPlanTex.value as Disposable)?.dispose();
  const tex = new (h.T.DataTexture as new (d: Uint8Array, w: number, h: number, f: unknown, t: unknown) => Record<string, unknown> & { dispose(): void })(plan.data.slice(), plan.width, plan.height, RGBA_FORMAT, UNSIGNED_BYTE);
  tex.magFilter = tex.minFilter = NEAREST_FILTER;
  tex.generateMipmaps = false;
  tex.needsUpdate = true;
  fillUniforms(shared, plan, tex);
  h.s.glassPlan = plan.revision;
  h.r();
}

const HASH_LINE = 'float hq_hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }';
const ROOM_DECL = '  vec3 hqRoom = vec3(0.0);';
const ROOM_CALL = 'if (hqHas > 0.5) hqRoom = hq_room(';
const LEAN_END = '  hqHas = 0.0;\n#endif';
const LAMP = 'vec3 lamp = mix(paneLit, hqRoom * 1.05, hqHas);';

/**
 * Rewrites the renderer's glass fragment program so that panes carrying an atlas row look into the plan: declarations, the
 * uniforms and lookup functions, the plan colour after the generic room, and the plan's own light for the night glow.
 * A program that does not contain every expected line is returned unchanged (the generic rooms stay).
 */
export function patchGlass(frag: string): string {
  for (const part of [HASH_LINE, ROOM_DECL, ROOM_CALL, LEAN_END, LAMP]) if (!frag.includes(part)) return frag;
  return frag
    .replace(HASH_LINE, `${HASH_LINE}\n${PLAN_COMMON}`)
    .replace(ROOM_DECL, `${ROOM_DECL}\n  float hqPlan = 0.0;\n  vec3 hqEmit = vec3(0.0);`)
    .replace(ROOM_CALL, 'if (hqHas > 0.5 && vPUv.x < 1.5) hqRoom = hq_room(')
    .replace(LEAN_END, `${LEAN_END}\n  if (vPUv.x >= 1.5) hqHas = 0.0;\n${PLAN_COLOR}`)
    .replace(LAMP, 'vec3 lamp = hqPlan > 0.5 ? hqEmit : mix(paneLit, hqRoom * 1.05, hqHas);');
}
