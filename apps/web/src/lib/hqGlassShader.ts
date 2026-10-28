/**
 * The glass shader's look through a pane into the plan atlas (interior mapping), loaded on demand by the renderer when a campus
 * scene carries a plan atlas (`RenderScene.glassPlan`). GLSL only; no three.js, so the unit runner can import it.
 *
 * A pane carries its atlas row in the integer part of its u texture coordinate (`tagPaneUv`). The fragment position and the view
 * ray give the storey (from the height) and the plan position behind the pane: the ray is followed to the floor (or the ceiling)
 * of that storey in up to six steps, stopping at a partition wall; the atlas texel there says what is seen (room floor by kind,
 * desk, screen, a seated figure, a wall) and how lit it is. The software path, and panes far from the camera, read one texel
 * 2.8 m inside the facade instead (no parallax, one texture fetch).
 *
 * A building on the campus is turned by quarter turns so its front faces the plaza: the lookup turns the world position back into the
 * building's own frame (`uPlanRect.xy` is the tile's middle, `uPlanInfo.w` the quarter turns), so the rooms turn with the facade.
 * A new occupancy (same layout) cross-dissolves from the previous atlas (`uPlanPrev`, `uPlanMix` from 0 to 1 over `PLAN_FADE_MS`);
 * under reduced motion, or when the layout changed, the new atlas shows at once.
 */
/** How long a change of what the glass shows takes to dissolve in. */
export const PLAN_FADE_MS = 450;
export const MAX_PLAN_SLOTS = 16;

export const PLAN_COMMON = `
uniform sampler2D uPlanTex;
uniform sampler2D uPlanPrev;
uniform float uPlanMix;
uniform vec4 uPlanRect[${MAX_PLAN_SLOTS}];
uniform vec4 uPlanInfo[${MAX_PLAN_SLOTS}];
uniform vec4 uPlanGrid;
uniform float uPlanCount;
/* One texel of a building's storey at a world position; alpha is zero outside the plan. */
vec4 hq_planAt(int bi, int fl, vec2 xz) {
  vec4 R = uPlanRect[bi];
  vec2 d = xz - R.xy;
  float tn = floor(uPlanInfo[bi].w + 0.5);
  /* back into the building's own frame: undo its quarter turns */
  vec2 l = tn < 0.5 ? d : tn < 1.5 ? vec2(d.y, -d.x) : tn < 2.5 ? -d : vec2(-d.y, d.x);
  vec2 q = l * R.zw + 0.5;
  float inside = step(0.0, q.x) * step(q.x, 1.0) * step(0.0, q.y) * step(q.y, 1.0);
  q = clamp(q, vec2(0.003), vec2(0.997));
  vec2 uv = (vec2(float(fl), float(bi)) + q) / uPlanGrid.xy;
  vec4 t = texture2D(uPlanTex, uv);
  if (uPlanMix < 0.999) {
    /* cross-dissolve from the previous atlas; the wall bit is kept, only colour and light blend */
    vec4 p = texture2D(uPlanPrev, uv);
    float an = floor(t.a * 255.0 + 0.5), ap = floor(p.a * 255.0 + 0.5);
    float same = step(0.5, an) * step(0.5, ap) * (1.0 - abs(step(127.5, an) - step(127.5, ap)));
    t.rgb = mix(p.rgb, t.rgb, uPlanMix);
    t.a = mix(t.a, mix(p.a, t.a, uPlanMix), same);
  }
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

/** The few members of a three.js Vector4 the uniform upload reads. */
class Vec4 {
  x = 0; y = 0; z = 0; w = 0;
  set(x: number, y: number, z: number, w: number): this { this.x = x; this.y = y; this.z = z; this.w = w; return this; }
  toArray(a: number[], o = 0): number[] { a[o] = this.x; a[o + 1] = this.y; a[o + 2] = this.z; a[o + 3] = this.w; return a; }
}

/** Adds the plan uniforms to the renderer's shared uniforms (the classes come from the renderer, so this chunk imports nothing). */
export function installPlan(shared: Record<string, { value: unknown }>, V4: new () => unknown): void {
  shared.uPlanTex = { value: null };
  shared.uPlanPrev = { value: null };
  shared.uPlanMix = { value: 1 };
  shared.uPlanRect = { value: Array.from({ length: MAX_PLAN_SLOTS }, () => new V4()) };
  shared.uPlanInfo = { value: Array.from({ length: MAX_PLAN_SLOTS }, () => new V4()) };
  shared.uPlanGrid = { value: new V4() };
  shared.uPlanCount = { value: 0 };
}

type Vec4Like = { set(x: number, y: number, z: number, w: number): unknown };
export type PlanSource = Readonly<{
  cols: number; rows: number;
  buildings: ReadonlyArray<Readonly<{ origin: readonly number[]; turn: number; size: readonly number[]; storeys: number; baseY: number; pitch: number }>>;
}>;

/** Points the glass uniforms at an atlas texture and its per-building frames (middle x, z, 1/width, 1/depth; storeys, base, pitch, quarter turns). */
export function fillUniforms(u: Record<string, { value: unknown }>, plan: PlanSource, texture: unknown): void {
  const rect = u.uPlanRect.value as Vec4Like[], info = u.uPlanInfo.value as Vec4Like[];
  u.uPlanTex.value = texture;
  (u.uPlanGrid.value as Vec4Like).set(plan.cols, plan.rows, 0, 0);
  u.uPlanCount.value = plan.buildings.length;
  plan.buildings.forEach((b, i) => {
    rect[i].set(b.origin[0], b.origin[1], 1 / b.size[0], 1 / b.size[1]);
    info[i].set(b.storeys, b.baseY, b.pitch, b.turn);
  });
}

/**
 * The cross-dissolve weight `elapsed` ms into a change: 0 to 1 with an ease in and out, and 1 at once under reduced motion or with
 * no previous atlas to dissolve from. Pure, so the unit runner pins it.
 */
export function planFade(elapsed: number, reducedMotion: boolean, hasPrevious: boolean): number {
  if (reducedMotion || !hasPrevious || !(elapsed >= 0)) return reducedMotion || !hasPrevious ? 1 : 0;
  const t = Math.min(1, elapsed / PLAN_FADE_MS);
  return t * t * (3 - 2 * t);
}

/** Whether a new atlas can dissolve from the old one: the same layout, size and building frames (only the occupancy moved). */
export function canDissolve(prev: PlanKey | null, next: PlanKey): boolean {
  return !!prev && prev.layoutRevision === next.layoutRevision && prev.width === next.width && prev.height === next.height && prev.frames === next.frames;
}
export type PlanKey = Readonly<{ layoutRevision: string; width: number; height: number; frames: string }>;
export const planKeyOf = (plan: PlanSource & { layoutRevision?: string; width: number; height: number }): PlanKey => ({
  layoutRevision: plan.layoutRevision ?? '', width: plan.width, height: plan.height,
  frames: plan.buildings.map((b) => `${b.origin.join(',')},${b.turn},${b.size.join(',')}`).join('/'),
});

/** three.js constants (stable across versions): RGBAFormat, UnsignedByteType, NearestFilter. */
const RGBA_FORMAT = 1023, UNSIGNED_BYTE = 1009, NEAREST_FILTER = 1003;
type Disposable = { dispose(): void } | null;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Hooks = Readonly<{ r: any; T: any; reducedMotion?: () => boolean }>;

/**
 * Everything the renderer does for a plan atlas, in this chunk: the first time it adds the uniforms and rewrites the glass program
 * (`patchGlass`, with its own program cache key), then for each revision a fresh nearest-filtered texture and the building rectangles.
 */
/** Per renderer: the atlas on screen, its key, and the dissolve under way. */
const fades = new WeakMap<object, { key: PlanKey; tex: { dispose(): void }; prev: { dispose(): void } | null; t0: number; raf: number }>();

export function applyPlan(h: Hooks, plan: PlanSource & { revision: string; layoutRevision?: string; data: Uint8Array; width: number; height: number }): void {
  const r = h.r;
  if (plan.revision === r.stats.glassPlan) return;
  const shared = r.shared, glass = r.materials.get('glass');
  if (!shared.uPlanTex) {
    installPlan(shared, Vec4);
    if (glass) {
      const before = glass.onBeforeCompile;
      glass.onBeforeCompile = (shader: { fragmentShader: string }, renderer: unknown) => { before(shader, renderer); shader.fragmentShader = patchGlass(shader.fragmentShader); };
      glass.customProgramCacheKey = () => 'hq-glass-plan';
      glass.needsUpdate = true;
    }
  }
  const tex = new (h.T as new (d: Uint8Array, w: number, h: number, f: unknown, t: unknown) => Record<string, unknown> & { dispose(): void })(plan.data.slice(), plan.width, plan.height, RGBA_FORMAT, UNSIGNED_BYTE);
  tex.magFilter = tex.minFilter = NEAREST_FILTER;
  tex.generateMipmaps = false;
  tex.needsUpdate = true;
  const key = planKeyOf(plan);
  const was = fades.get(r);
  const reduced = h.reducedMotion?.() ?? false;
  // The old atlas stays bound as the previous one while the new one dissolves in; anything older is let go now.
  if (was) { cancelAnimationFrameSafe(was.raf); was.prev?.dispose(); }
  const dissolve = !!was && !reduced && canDissolve(was.key, key) && typeof requestAnimationFrame === 'function';
  if (was && !dissolve) was.tex.dispose();
  else if (!was) (shared.uPlanTex.value as Disposable)?.dispose();
  const state = { key, tex, prev: dissolve ? was!.tex : null, t0: now(), raf: 0 };
  fades.set(r, state);
  fillUniforms(shared, plan, tex);
  shared.uPlanPrev.value = dissolve ? was!.tex : tex;
  shared.uPlanMix.value = planFade(0, reduced, dissolve);
  if (dissolve) {
    const step = () => {
      const k = planFade(now() - state.t0, h.reducedMotion?.() ?? false, true);
      shared.uPlanMix.value = k;
      r.requestFrame();
      if (k < 1) { state.raf = requestAnimationFrame(step); return; }
      state.prev?.dispose();
      state.prev = null;
      shared.uPlanPrev.value = tex;
    };
    state.raf = requestAnimationFrame(step);
  }
  r.stats.glassPlan = plan.revision;
  r.requestFrame();
}

const now = (): number => (typeof performance !== 'undefined' ? performance.now() : Date.now());
const cancelAnimationFrameSafe = (id: number): void => { if (id && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(id); };

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
