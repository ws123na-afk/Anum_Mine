/**
 * GLSL for the photographic tier, kept as plain strings (no three.js import) so tests can pin their shape and the renderer chunk
 * never carries them: they arrive through the lazy `hqPhotoFx.ts` chunk.
 *
 * PBR detail (`PBR_*`): the generated normal and roughness maps of `hqPbrTextures.ts`, sampled triplanar from the world position, so
 * they need no UV unwrap and tile across every surface the same way. The normal offset, the roughness change and the cavity
 * darkening all fade out between `uDetail.x` and `uDetail.y` (the distances the albedo detail already uses), so far surfaces do not
 * shimmer. Leaves also get light shining through them when the sun is behind them (translucency).
 *
 * Post (`SHAFT_*`, `GRADE_*`): light shafts as a radial blur of the bright pixels towards the sun's screen position, in linear light
 * before tone mapping; then, after tone mapping, a vignette, film grain, a touch of saturation and an ordered dither against banding.
 */
export const PBR_COMMON = `
uniform sampler2D uPbrMap;
uniform vec4 uPbr; // x: repeats per metre, y: normal strength, z: roughness influence, w: cavity influence
vec4 hq_pbr_sample() {
  vec3 an = abs(vWNrm);
  vec2 tuv = (an.y >= an.x && an.y >= an.z) ? vWPos.xz : ((an.x > an.z) ? vWPos.zy : vWPos.xy);
  return texture2D(uPbrMap, tuv * uPbr.x);
}`;

/** After `#include <roughnessmap_fragment>`: sample once, change roughness, darken cavities. Declares `hqPm` and `hqFade` for the chunks after it. */
export const PBR_ROUGH = `
  vec4 hqPm = vec4(0.5, 0.5, 0.5, 1.0);
  float hqFade = 1.0 - smoothstep(uDetail.x, uDetail.y, length(vWPos - cameraPosition));
  {
    hqPm = hq_pbr_sample();
    roughnessFactor = clamp(roughnessFactor * mix(1.0, hqPm.b * 2.0, uPbr.z * hqFade), 0.04, 1.0);
    diffuseColor.rgb *= mix(1.0, mix(0.55, 1.0, hqPm.a), uPbr.w * hqFade);
  }`;

/** After `#include <normal_fragment_maps>`: bend the view space normal by the map's offset, expressed in world axes. */
export const PBR_NORMAL = `
  {
    vec3 an = abs(vWNrm);
    vec2 tn = (hqPm.rg * 2.0 - 1.0) * uPbr.y * hqFade;
    vec3 off = (an.y >= an.x && an.y >= an.z) ? vec3(tn.x, 0.0, tn.y) : ((an.x > an.z) ? vec3(0.0, tn.y, tn.x) : vec3(tn.x, tn.y, 0.0));
    normal = normalize(normal + (viewMatrix * vec4(off, 0.0)).xyz);
  }`;

/** Foliage only, after the emissive chunk: sunlight through thin parts of a leaf when the sun is behind it. */
export const PBR_LEAF = `
  {
    vec3 Vw = normalize(cameraPosition - vWPos);
    vec3 S = -normalize(uSunDir);
    float back = pow(max(dot(-Vw, S), 0.0), 3.0) * (1.0 - uNight * 0.85);
    totalEmissiveRadiance += diffuseColor.rgb * vec3(1.15, 1.25, 0.55) * uSunCol * back * mix(0.25, 1.0, 1.0 - hqPm.a) * 0.9;
  }`;

export const FULLSCREEN_VERT = 'varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';

/** Light shafts: 28 steps from the pixel towards the sun, summing what is brighter than the threshold (the sun disc, bright sky, lit glass). */
export const SHAFT_FRAG = `
uniform sampler2D tDiffuse;
uniform vec2 uSun;      // sun position in uv
uniform float uShaft;   // strength, 0 turns the pass into a copy
uniform float uThresh;
uniform vec3 uTint;
varying vec2 vUv;
void main() {
  vec4 base = texture2D(tDiffuse, vUv);
  if (uShaft <= 0.001) { gl_FragColor = base; return; }
  vec2 delta = (uSun - vUv) / 28.0;
  vec2 p = vUv;
  vec3 acc = vec3(0.0);
  float w = 1.0;
  for (int i = 0; i < 28; i++) {
    p += delta;
    vec3 s = texture2D(tDiffuse, clamp(p, 0.001, 0.999)).rgb;
    float l = dot(s, vec3(0.2126, 0.7152, 0.0722));
    acc += s * max(l - uThresh, 0.0) * w;
    w *= 0.955;
  }
  float reach = 1.0 - clamp(length(uSun - vUv) * 0.8, 0.0, 1.0);
  gl_FragColor = vec4(base.rgb + acc * uTint * (uShaft / 28.0) * (0.35 + 0.65 * reach), base.a);
}`;

/** After tone mapping (sRGB in, sRGB out): vignette, saturation, grain and dither. */
export const GRADE_FRAG = `
uniform sampler2D tDiffuse;
uniform float uVig;
uniform float uGrain;
uniform float uSat;
uniform float uContrast;
uniform float uTime;
uniform vec2 uRes;
varying vec2 vUv;
float h21(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
void main() {
  vec4 c = texture2D(tDiffuse, vUv);
  float lum = dot(c.rgb, vec3(0.2126, 0.7152, 0.0722));
  c.rgb = mix(vec3(lum), c.rgb, uSat);
  c.rgb = mix(c.rgb, c.rgb * c.rgb * (3.0 - 2.0 * c.rgb), uContrast);
  vec2 q = (vUv - 0.5) * vec2(uRes.x / uRes.y, 1.0);
  c.rgb *= 1.0 - uVig * smoothstep(0.38, 1.1, length(q));
  float g = h21(vUv * uRes + fract(uTime) * 91.7) - 0.5;
  c.rgb += g * uGrain * (1.0 - lum * 0.7);
  c.rgb += (h21(gl_FragCoord.xy) - 0.5) / 255.0;
  gl_FragColor = vec4(clamp(c.rgb, 0.0, 1.0), c.a);
}`;
