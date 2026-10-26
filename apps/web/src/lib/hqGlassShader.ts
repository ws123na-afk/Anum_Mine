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
uniform vec2 uPlanGrid;
uniform float uPlanCount;
/* One texel of a building's storey at a world position; alpha is zero outside the plan. */
vec4 hq_planAt(int bi, int fl, vec2 xz) {
  vec4 R = uPlanRect[bi];
  vec2 q = (xz - R.xy) * R.zw;
  float inside = step(0.0, q.x) * step(q.x, 1.0) * step(0.0, q.y) * step(q.y, 1.0);
  q = clamp(q, vec2(0.003), vec2(0.997));
  vec4 t = texture2D(uPlanTex, (vec2(float(fl), float(bi)) + q) / uPlanGrid);
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
      hqEmit = lin * lt * lt * vec3(1.0, 0.92, 0.78) * 1.8;
      hqHas = 1.0;
      hqPlan = 1.0;
      glowW = 1.0;
    }
  }`;
