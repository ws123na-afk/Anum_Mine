/**
 * The headquarters renderer, built on three.js: PBR materials with procedural canvas textures,
 * a directional shadow map, a procedural sky as the reflection environment, fog, and a bloom pass
 * for lit windows, signs and fountain jets at night.
 *
 * The scene arrives as the pure meshes of `campusScene.ts` and `buildingScene.ts` (interleaved
 * float arrays with a surface class per vertex); they are split by class into geometries here.
 * This module is only ever loaded through a dynamic import from `HqStage.tsx`, so three.js stays
 * out of every other chunk and out of the unit runner.
 *
 * Ambient motion (scenery only, see `hqAssets/environment/ambient.ts`): traffic loops, water ripples, tree and flag
 * sway, drifting clouds, gently shimmering window light and a blinking rooftop beacon, all driven by one scene clock.
 * The clock stops, and every motion with it, under reduced motion, in a hidden tab and on a software rasteriser.
 *
 * Quality steps down on sustained slow frames: ambient motion first, then bloom, the shadow map size, shadows,
 * then the pixel ratio. Reduced motion turns bloom off. A browser without WebGL 2 gets null from
 * `create` and the page falls back to the list and the 2D plan.
 */
import {
  ACESFilmicToneMapping, AmbientLight, BackSide, BufferGeometry, CanvasTexture, Color, DirectionalLight, DoubleSide, Float32BufferAttribute, Fog, HalfFloatType,
  HemisphereLight, LinearMipmapLinearFilter, Material, Mesh, MeshLambertMaterial, MeshBasicMaterial, MeshPhysicalMaterial, MeshStandardMaterial, NoColorSpace, PCFShadowMap,
  PerspectiveCamera, PMREMGenerator, RepeatWrapping, Scene, ShaderMaterial, SphereGeometry, SRGBColorSpace, Texture, Vector2, Vector3, Vector4, WebGLRenderer, WebGLRenderTarget,
  LinearFilter, type IUniform,
} from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { VERTEX_FLOATS, MAT } from './hqGeometry.ts';
import { cameraEye, type CameraState } from './headquartersSpatial.ts';
import { DUSK, lightingFor } from './hqLighting.ts';
import { AmbientLayer } from './hqAmbientLayer.ts';
import { AMBIENT_FPS, MAX_RIPPLES, ambientLevelFor, readAmbientOverride, type AmbientLevel } from '../hqAssets/environment/ambient.ts';
import { clipPlanes, viewProjection } from './hqMath.ts';
import type { RenderItem, RenderScene, RenderStats, RendererCallbacks } from './hqRenderTypes.ts';
import { drawMaterialTexture, drawSignAtlas, type MaterialTextureKind } from './hqTextures.ts';
import { ModelLayer } from './hqModelLayer.ts';
import type { ModelGroup } from './hqModels.ts';

export type { RenderItem, RenderScene, RenderStats, RendererCallbacks } from './hqRenderTypes.ts';

type Cls = 'paint' | 'flat' | 'concrete' | 'tile' | 'asphalt' | 'grass' | 'wood' | 'metal' | 'water' | 'glass' | 'roller' | 'sign' | 'court' | 'pane' | 'foliage' | 'cloth' | 'beacon';

const CLASS_OF_MAT: Readonly<Record<number, Cls>> = {
  [MAT.flat]: 'flat', [MAT.concrete]: 'concrete', [MAT.tile]: 'tile', [MAT.asphalt]: 'asphalt', [MAT.grass]: 'grass', [MAT.wood]: 'wood',
  [MAT.metal]: 'metal', [MAT.water]: 'water', [MAT.glass]: 'glass', [MAT.roller]: 'roller', [MAT.sign]: 'sign', [MAT.court]: 'court', [MAT.paint]: 'paint',
  [MAT.foliage]: 'foliage', [MAT.cloth]: 'cloth', [MAT.beacon]: 'beacon',
};

/** World units per texture repeat, as a frequency. */
const TEX_SCALE: Partial<Record<Cls, number>> = { concrete: 0.25, tile: 0.4, asphalt: 0.3, grass: 0.1, wood: 0.5, metal: 0.5, roller: 0.5, court: 0.25 };

const srgbToLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);

// ---------------------------------------------------------------- shader patches

const VERT_COMMON = 'attribute float aEm;\nvarying float vEm;\nvarying vec3 vWPos;\nvarying vec3 vWNrm;\nvarying vec2 vPUv;\nuniform float uTime;\nuniform float uAmb;';
const VERT_BODY = 'vPUv = uv;\nvEm = aEm;\nvWPos = (modelMatrix * vec4(position, 1.0)).xyz;\nvWNrm = normalize(mat3(modelMatrix) * normal);';
const FRAG_COMMON = `varying float vEm;
varying vec3 vWPos;
varying vec3 vWNrm;
varying vec2 vPUv;
uniform float uNight;
uniform float uLit;
uniform float uTime;
uniform float uAmb;
uniform float uHighlight;
uniform vec2 uDetail;
uniform sampler2D uTexMap;
uniform float uTexScale;
uniform vec3 uSkyTop;
uniform vec3 uSkyHor;
uniform vec3 uSunDir;
uniform vec3 uSunCol;
uniform vec4 uRipples[${MAX_RIPPLES}];
float hq_hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
/* A lit room seen through a pane: ceiling light, darker floor, desks and a few screens (q is 0..1 across the pane, seed picks the room). */
vec3 hq_room(vec2 q, float seed) {
  float ceilL = smoothstep(0.78, 0.9, q.y) * (1.0 - smoothstep(0.93, 0.99, q.y));
  float flo = smoothstep(0.0, 0.25, q.y);
  float cx = floor(q.x * 5.0);
  float desks = step(0.14, q.y) * step(q.y, 0.34) * step(0.42, hq_hash(vec2(cx, seed)));
  float fx = fract(q.x * 5.0);
  float scr = step(0.34, q.y) * step(q.y, 0.47) * step(0.8, hq_hash(vec2(cx + 3.0, seed))) * step(0.3, fx) * step(fx, 0.7);
  vec3 wallC = mix(vec3(1.0, 0.8, 0.54), vec3(0.82, 0.88, 0.98), step(0.85, seed));
  vec3 room = wallC * (0.3 + 0.7 * flo) + vec3(1.0, 0.93, 0.78) * ceilL * 1.3;
  room = mix(room, room * 0.32, desks);
  return room + vec3(0.35, 0.65, 1.0) * scr * 0.9;
}`;

/** Leaves and fronds lean with the wind: more the higher they are, out of step with their neighbours. Frozen (rest pose) when uAmb is 0. */
const SWAY_FOLIAGE = `
  {
    float k = uAmb * clamp(position.y, 0.0, 14.0) * 0.011;
    float ph = position.x * 0.31 + position.z * 0.23;
    transformed.x += (sin(uTime * 1.25 + ph) + 0.5 * sin(uTime * 2.7 + ph * 2.1)) * k;
    transformed.z += cos(uTime * 1.05 + ph * 1.3) * k * 0.8;
  }`;
/** A flag ripples more towards its free end (uv.x is the distance from the pole). */
const SWAY_CLOTH = `
  {
    float a = uAmb * uv.x * 0.42;
    transformed.y += sin(uTime * 4.1 - uv.x * 7.0 + position.x * 0.5) * a * 0.5;
    transformed.z += sin(uTime * 3.3 - uv.x * 6.0 + position.z * 0.4) * a;
  }`;

const TRIPLANAR = `
  {
    vec3 an = abs(vWNrm);
    vec2 tuv = (an.y >= an.x && an.y >= an.z) ? vWPos.xz : ((an.x > an.z) ? vWPos.zy : vWPos.xy);
    vec3 tx = texture2D(uTexMap, tuv * uTexScale).rgb;
    float dd = 1.0 - smoothstep(uDetail.x, uDetail.y, length(vWPos - cameraPosition));
    diffuseColor.rgb *= mix(vec3(1.0), tx * 2.0, dd);
  }`;

/**
 * Glass panes: a reflection of the sky that depends on which way the facade faces (so no two faces read alike and the
 * glass looks like an environment map even on the software path), a small reflectance change from pane to pane, and
 * lit warm panes once the lights are on. The glow weight is the emissive weight above 2.
 */
const GLASS_COLOR = `
  float glowW = clamp(vEm - 2.0, 0.0, 1.0);
  vec3 paneLit = diffuseColor.rgb;
  // The room behind a pane (exterior panes carry local coordinates): a ceiling light, a darker floor, a few desks with screens.
  float hqHas = step(0.0005, max(vPUv.x, vPUv.y));
  vec3 hqRoom = vec3(0.0);
  if (hqHas > 0.5) hqRoom = hq_room(vPUv, hq_hash(floor(vec2(vWPos.x + vWPos.z, vWPos.y / 3.6)) + 3.0));
  {
    float gy = clamp(vWPos.y / 48.0, 0.0, 1.0);
    vec2 cell = floor(vec2(vWPos.x + vWPos.z, vWPos.y / 3.6));
    float vary = 0.88 + 0.24 * hq_hash(cell);
    vec3 dayGlass = mix(vec3(0.20, 0.36, 0.50), vec3(0.50, 0.70, 0.86), gy) * vary;
    vec3 nightGlass = mix(vec3(0.04, 0.09, 0.15), paneLit * 0.3, step(0.05, glowW));
    diffuseColor.rgb = mix(dayGlass, nightGlass, uNight);
    vec3 V = normalize(cameraPosition - vWPos);
    vec3 N = normalize(vWNrm);
    if (dot(V, N) < 0.0) N = -N;
    vec3 R = reflect(-V, N);
    float fr = pow(1.0 - clamp(dot(V, N), 0.0, 1.0), 3.0);
    float up = clamp(R.y, 0.0, 1.0);
    vec3 refl = mix(uSkyHor, uSkyTop, pow(up, 0.5));
    refl = mix(refl, uSkyHor * 0.45, smoothstep(0.0, -0.25, R.y));
    float sunSpec = pow(max(dot(R, -normalize(uSunDir)), 0.0), 18.0);
    refl += uSunCol * sunSpec * (0.55 + 0.6 * (1.0 - uNight));
    float keep = 1.0 - 0.8 * glowW * uLit;
    diffuseColor.rgb = mix(diffuseColor.rgb, refl, (0.16 + 0.5 * fr) * keep);
    // See through: the lit room shows in the glass, dim by day, glowing from the blue hour on; an unlit pane shows a dark room.
    float seeIn = hqHas * (1.0 - 0.75 * fr) * mix(0.42, 0.9, uLit);
    diffuseColor.rgb = mix(diffuseColor.rgb, hqRoom * mix(0.35, 0.18, step(glowW, 0.05)), seeIn);
  }`;
/** Lit panes glow warm. A few of them drift slowly in brightness (a screen, a lamp being dimmed): never on and off, never a pattern of life. */
const GLASS_EMISSIVE = `
  {
    vec2 wc = floor(vec2(vWPos.x + vWPos.z, vWPos.y / 3.6));
    float hh = hq_hash(wc + 7.0);
    float drift = 1.0 - uAmb * 0.16 * step(0.9, hh) * (0.5 + 0.5 * sin(uTime * (0.45 + hh * 1.1) + hh * 60.0));
    vec3 lamp = mix(paneLit, hqRoom * 1.05, hqHas);
    totalEmissiveRadiance += lamp * uLit * glowW * mix(1.9, 1.3, hqHas) * drift;
  }`;
const GENERIC_EMISSIVE = `
  {
    float k = vEm > 0.8 ? 2.4 : 0.9;
    totalEmissiveRadiance += diffuseColor.rgb * vEm * mix(0.22, 1.0, uLit) * k;
  }`;
/** A red rooftop light: blinks while ambient motion runs, rests at a steady glow otherwise. */
const BEACON_EMISSIVE = `
  {
    float blink = mix(0.55, pow(0.5 + 0.5 * sin(uTime * 4.2), 4.0), uAmb);
    totalEmissiveRadiance += diffuseColor.rgb * (0.5 + 3.4 * blink);
  }`;
/** Water: a calm swell, rings spreading from the fountain and ponds, and a few glints on the sun side. */
const WATER_NORMAL = `
  {
    vec2 wp = vWPos.xz;
    float t = uTime;
    vec2 g = vec2(sin(wp.x * 0.9 + t * 1.1) + sin(wp.y * 0.7 - t * 0.8 + wp.x * 0.3), cos(wp.y * 1.1 + t * 0.9) + sin(wp.x * 0.5 + t * 1.3)) * 0.028;
    for (int i = 0; i < ${MAX_RIPPLES}; i++) {
      vec4 r = uRipples[i];
      if (r.z > 0.0) {
        vec2 dv = wp - r.xy;
        float d = length(dv) + 1e-4;
        float ring = sin((d - t * 1.5) * 3.4) * exp(-d * 0.1) * smoothstep(r.z, r.z * 0.35, d);
        g += (dv / d) * ring * 0.16;
      }
    }
    normal = normalize(normal + (viewMatrix * vec4(g.x, 0.0, g.y, 0.0)).xyz);
  }`;
const WATER_GLINT = `
  {
    float s = sin(vWPos.x * 2.3 + uTime * 1.7) * sin(vWPos.z * 2.1 - uTime * 1.3);
    totalEmissiveRadiance += uSunCol * pow(max(s, 0.0), 14.0) * 0.5 * (1.0 - uNight) * uAmb;
  }`;
const HIGHLIGHT = '  totalEmissiveRadiance += vec3(0.08, 0.2, 0.17) * uHighlight;';

type Shared = {
  uNight: IUniform<number>; uLit: IUniform<number>; uTime: IUniform<number>; uAmb: IUniform<number>; uDetail: IUniform<Vector2>;
  uSkyTop: IUniform<Color>; uSkyHor: IUniform<Color>; uSunDir: IUniform<Vector3>; uSunCol: IUniform<Color>; uRipples: IUniform<Vector4[]>;
};
type Variant = 'std' | 'glass' | 'water' | 'foliage' | 'cloth' | 'beacon';

function patched(material: Material, shared: Shared, opts: { texture?: Texture; scale?: number; variant?: Variant }): void {
  const hl: IUniform<number> = { value: 0 };
  const variant = opts.variant ?? 'std';
  material.userData.hl = hl;
  material.onBeforeCompile = (shader) => {
    for (const key of Object.keys(shared) as Array<keyof Shared>) shader.uniforms[key] = shared[key];
    shader.uniforms.uHighlight = hl;
    shader.uniforms.uTexMap = { value: opts.texture ?? null };
    shader.uniforms.uTexScale = { value: opts.scale ?? 0.25 };
    const sway = variant === 'foliage' ? SWAY_FOLIAGE : variant === 'cloth' ? SWAY_CLOTH : '';
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERT_COMMON}`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>\n${VERT_BODY}${sway}`);
    let frag = shader.fragmentShader.replace('#include <common>', `#include <common>\n${FRAG_COMMON}`);
    if (opts.texture) frag = frag.replace('#include <map_fragment>', `#include <map_fragment>\n${TRIPLANAR}`);
    if (variant === 'glass') frag = frag.replace('#include <color_fragment>', `#include <color_fragment>\n${GLASS_COLOR}`);
    if (variant === 'water') frag = frag.replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>\n${WATER_NORMAL}`);
    const emissive = variant === 'glass' ? GLASS_EMISSIVE : variant === 'beacon' ? BEACON_EMISSIVE : GENERIC_EMISSIVE;
    frag = frag.replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${emissive}${variant === 'water' ? WATER_GLINT : ''}\n${HIGHLIGHT}`);
    shader.fragmentShader = frag;
  };
  // A distinct cache key per variant so the programs are not shared between patched and plain materials.
  material.customProgramCacheKey = () => `hq-${variant}-${opts.texture ? 'tex' : 'plain'}`;
}

// ---------------------------------------------------------------- sky

const SKY_VERT = 'varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';
/**
 * Zenith to horizon gradient, a warm band at the horizon (dusk glow, day haze), the ground below it, the sun or moon disc
 * and, above the horizon, soft clouds that drift on the scene clock (cover from uCloud, tint from uCloudCol).
 */
const SKY_FRAG = `varying vec3 vDir; uniform vec3 zenith; uniform vec3 horizon; uniform vec3 ground; uniform vec3 glow; uniform vec3 sunDir; uniform vec3 sunColor; uniform float sunPower; uniform float uDisc;
  uniform float uTime; uniform float uCloud; uniform vec3 uCloudCol;
  float h2(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
  float vn(vec2 p) { vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f); return mix(mix(h2(i), h2(i + vec2(1.0, 0.0)), f.x), mix(h2(i + vec2(0.0, 1.0)), h2(i + vec2(1.0, 1.0)), f.x), f.y); }
  void main(){
    vec3 d = normalize(vDir);
    float t = clamp(d.y, -1.0, 1.0);
    vec3 sky = mix(horizon, zenith, pow(max(t, 0.0), 0.55));
    sky += glow * exp(-abs(t) * 9.0);
    if (uCloud > 0.001 && t > 0.015) {
      vec2 p = d.xz / (t + 0.2) * 1.15 + vec2(uTime * 0.012, uTime * 0.004);
      float n = 0.55 * vn(p) + 0.3 * vn(p * 2.1 + 3.7) + 0.15 * vn(p * 4.3 + 9.1);
      float c = smoothstep(0.52 - uCloud * 0.14, 0.82, n) * smoothstep(0.015, 0.2, t) * uCloud;
      sky = mix(sky, uCloudCol, clamp(c, 0.0, 0.85));
    }
    vec3 col = t >= 0.0 ? sky : mix(horizon * 0.8, ground, clamp(-t * 3.0, 0.0, 1.0));
    float s = max(dot(d, normalize(sunDir)), 0.0);
    col += sunColor * (pow(s, sunPower) * 6.0 + pow(s, 4.0) * 0.25) * uDisc;
    gl_FragColor = vec4(col, 1.0);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }`;

// ---------------------------------------------------------------- the renderer

/** A person (or a test) can pin the quality with `localStorage["anum.hq.quality"]` = 0 to 3 (3 = shadows and night glow). */
function readQualityOverride(): Quality | null {
  try {
    const raw = window.localStorage.getItem('anum.hq.quality');
    if (raw === null) return null;
    const n = Number(raw);
    return n === 0 || n === 1 || n === 2 || n === 3 ? (n as Quality) : null;
  } catch { return null; }
}

/** True on a software rasteriser (SwiftShader, llvmpipe): it gets the light settings. Probed on a scratch canvas before the real context exists. */
function probeSoftwareRenderer(): boolean {
  try {
    const scratch = document.createElement('canvas');
    const gl = scratch.getContext('webgl2');
    if (!gl) return false;
    const info = gl.getExtension('WEBGL_debug_renderer_info');
    const name = info ? String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL)) : '';
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return /swiftshader|llvmpipe|software|basic render/i.test(name);
  } catch { return false; }
}

type Quality = 0 | 1 | 2 | 3;
const SHADOW_SIZE: Record<Quality, number> = { 0: 0, 1: 1024, 2: 1024, 3: 2048 };

export class HqRenderer {
  readonly stats: RenderStats = { frames: 0, lastDrawMs: 0, avgDrawMs: 0, drawn: 0, culled: 0, triangles: 0, dpr: 1, shadowSize: 0, bloom: false, ambient: { level: 0, frames: 0, clock: 0, vehicles: 0 }, models: { placed: 0, figures: 0, loaded: 0, asked: 0, bytes: 0, loadMs: 0, fallbacks: [], looks: [] } };
  private readonly renderer: WebGLRenderer;
  private readonly scene = new Scene();
  private readonly camera = new PerspectiveCamera(40, 1, 0.2, 1000);
  private readonly sun = new DirectionalLight(0xffffff, 1);
  private readonly hemi = new HemisphereLight(0xffffff, 0x444444, 1);
  private readonly fog = new Fog(0xffffff, 100, 500);
  private readonly shared: Shared = {
    uNight: { value: 0 }, uLit: { value: 0 }, uTime: { value: 0 }, uAmb: { value: 0 }, uDetail: { value: new Vector2(60, 200) },
    uSkyTop: { value: new Color() }, uSkyHor: { value: new Color() }, uSunDir: { value: new Vector3(0, -1, 0) }, uSunCol: { value: new Color() },
    uRipples: { value: Array.from({ length: MAX_RIPPLES }, () => new Vector4(0, 0, 0, 0)) },
  };
  private readonly materials = new Map<Cls, Material>();
  private readonly textures: Texture[] = [];
  private signTexture: Texture | null = null;
  private signKey = '';
  private envs: Texture[] = [];
  private sky: Mesh | null = null;
  private meshes: Mesh[] = [];
  private layer: ModelLayer | null = null;
  /** Model groups whose loaded models replace the procedural pieces tagged `fallbackOf` (kept across scene rebuilds, so nothing flickers). */
  private readyGroups: ReadonlySet<ModelGroup> = new Set();
  private composer: EffectComposer | null = null;
  private bloom: UnrealBloomPass | null = null;
  private cameraState: CameraState | null = null;
  private night = 0;
  private hover: string | null = null;
  private selected: string | null = null;
  private shadowRegion: { center: { x: number; y: number; z: number }; radius: number } | null = null;
  private shadowDirty = true;
  private lastSunKey = '';
  private quality: Quality = 3;
  private reducedMotion = false;
  private cheap = false;
  private lost = false;
  private disposed = false;
  private raf: number | null = null;
  private maxDpr: number;
  private dprScale = 1;
  private slowFrames = 0;
  private readonly cb: RendererCallbacks;
  private readonly canvas: HTMLCanvasElement;
  private readonly onLostEvent: () => void;
  private readonly onRestoredEvent: () => void;
  private sizeKey = '';
  // Ambient motion: one scene clock, the level it runs at, and the loop that keeps frames coming while it is above 0.
  private ambient: AmbientLayer | null = null;
  private clock = 0;
  private lastClockAt = 0;
  private lastDrawAt = 0;
  private ambientRaf: number | null = null;
  private slowSteps = 0;
  private software = false;
  private forcedAmbient: AmbientLevel | null = null;
  private level: AmbientLevel = 0;
  private readonly onVisibility: () => void;

  private constructor(canvas: HTMLCanvasElement, renderer: WebGLRenderer, cb: RendererCallbacks, maxDpr: number) {
    this.canvas = canvas; this.renderer = renderer; this.cb = cb; this.maxDpr = maxDpr;
    this.onLostEvent = () => { this.lost = true; this.cb.onLost?.(); };
    this.onRestoredEvent = () => { this.lost = false; this.sizeKey = ''; this.shadowDirty = true; this.cb.onRestored?.(); this.requestFrame(); };
    canvas.addEventListener('webglcontextlost', this.onLostEvent);
    canvas.addEventListener('webglcontextrestored', this.onRestoredEvent);
    this.onVisibility = () => { if (!document.hidden) { this.lastClockAt = 0; this.requestFrame(); } };
    document.addEventListener('visibilitychange', this.onVisibility);
    renderer.setClearColor(0x000000, 0);
    renderer.toneMapping = ACESFilmicToneMapping;
    renderer.outputColorSpace = SRGBColorSpace;
    renderer.info.autoReset = false;
    renderer.shadowMap.type = PCFShadowMap;
    renderer.shadowMap.autoUpdate = false;
    this.scene.fog = this.fog;
    this.scene.add(this.sun, this.sun.target, this.hemi, new AmbientLight(0xffffff, 0.04));
    this.sun.castShadow = true;
    this.sun.shadow.bias = -0.0005;
    this.sun.shadow.radius = 2.5;
  }

  /** Null when the browser cannot give a WebGL 2 context (the caller shows the accessible fallback). */
  static create(canvas: HTMLCanvasElement, cb: RendererCallbacks = {}, maxDpr = 2): HqRenderer | null {
    // A software rasteriser (SwiftShader, llvmpipe) cannot afford PBR shading, a shadow map or antialiasing:
    // it gets simple lit materials, no reflection map, no antialiasing and a lower pixel ratio, unless quality is pinned.
    const forced = readQualityOverride();
    const software = probeSoftwareRenderer();
    const cheap = forced === null && software;
    let renderer: WebGLRenderer;
    try {
      renderer = new WebGLRenderer({ canvas, antialias: !cheap, alpha: true, powerPreference: 'high-performance' });
    } catch { return null; }
    const made = new HqRenderer(canvas, renderer, cb, cheap ? 0.7 : maxDpr);
    try {
      made.cheap = cheap;
      made.software = software;
      try { made.forcedAmbient = readAmbientOverride(window.localStorage); } catch { made.forcedAmbient = null; }
      made.buildMaterials();
      made.buildSky();
      if (!cheap) made.buildEnvironments();
    } catch (error) {
      made.dispose();
      cb.onError?.(error instanceof Error ? error : new Error('WebGL setup failed'));
      return null;
    }
    const pixels = canvas.clientWidth * canvas.clientHeight * (window.devicePixelRatio || 1) ** 2;
    if (forced !== null) made.quality = forced;
    else if (cheap) made.quality = 0;
    else if (pixels > 4_000_000) made.quality = 2;
    return made;
  }

  private fail(error: unknown): void {
    this.cb.onError?.(error instanceof Error ? error : new Error('WebGL failed'));
  }

  // -------------------------------------------------------------- materials and environment

  private texture(kind: MaterialTextureKind): Texture | undefined {
    const source = drawMaterialTexture(kind);
    if (!source) return undefined;
    const tex = new CanvasTexture(source);
    tex.colorSpace = NoColorSpace;
    tex.wrapS = tex.wrapT = RepeatWrapping;
    tex.minFilter = LinearMipmapLinearFilter;
    tex.magFilter = LinearFilter;
    tex.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
    this.textures.push(tex);
    return tex;
  }

  private buildMaterials(): void {
    const std = (cls: Cls, params: { roughness: number; metalness: number; env?: number }, kind?: MaterialTextureKind, variant: Variant = 'std'): void => {
      const m: Material = this.cheap
        ? new MeshLambertMaterial({ vertexColors: true, side: DoubleSide })
        : new MeshStandardMaterial({ vertexColors: true, roughness: params.roughness, metalness: params.metalness, envMapIntensity: params.env ?? 1, side: DoubleSide });
      const texture = kind ? this.texture(kind) : undefined;
      patched(m, this.shared, { texture, scale: TEX_SCALE[cls], variant });
      this.materials.set(cls, m);
    };
    std('flat', { roughness: 0.82, metalness: 0 });
    std('paint', { roughness: 0.28, metalness: 0.6, env: 1.5 });
    std('concrete', { roughness: 0.88, metalness: 0 }, 'concrete');
    std('tile', { roughness: 0.42, metalness: 0.05, env: 0.9 }, 'tile');
    std('asphalt', { roughness: 0.95, metalness: 0 }, 'asphalt');
    std('grass', { roughness: 1, metalness: 0 }, 'grass');
    std('wood', { roughness: 0.55, metalness: 0 }, 'wood');
    std('metal', { roughness: 0.42, metalness: 0.55, env: 1.2 }, 'metal');
    std('roller', { roughness: 0.45, metalness: 0.5, env: 1.1 }, 'roller');
    std('court', { roughness: 0.7, metalness: 0 }, 'court');
    std('water', { roughness: 0.03, metalness: 0.7, env: 2.0 }, undefined, 'water');
    std('glass', { roughness: 0.06, metalness: 0.62, env: 1.6 }, undefined, 'glass');
    std('foliage', { roughness: 0.9, metalness: 0 }, undefined, 'foliage');
    std('cloth', { roughness: 0.85, metalness: 0 }, undefined, 'cloth');
    std('beacon', { roughness: 0.5, metalness: 0 }, undefined, 'beacon');
    const sign = new MeshBasicMaterial({ color: new Color(1, 1, 1), toneMapped: true });
    this.materials.set('sign', sign);
    const pane = this.cheap
      ? new MeshLambertMaterial({ vertexColors: true, transparent: true, opacity: 0.22, depthWrite: false, side: DoubleSide })
      : new MeshPhysicalMaterial({ vertexColors: true, transparent: true, opacity: 0.22, roughness: 0.05, metalness: 0, envMapIntensity: 1.2, depthWrite: false, side: DoubleSide });
    if (!this.cheap) {
      // Partition glass: clear face-on, mirror-like at grazing angles (Fresnel), so glass walls read as glass and not as tinted film.
      pane.onBeforeCompile = (shader) => {
        shader.fragmentShader = shader.fragmentShader.replace(
          '#include <opaque_fragment>',
          `#include <opaque_fragment>
          {
            float fres = pow(1.0 - clamp(abs(dot(normalize(vNormal), normalize(vViewPosition))), 0.0, 1.0), 3.0);
            gl_FragColor.a = clamp(diffuseColor.a * (0.75 + 3.2 * fres), 0.0, 0.72);
            gl_FragColor.rgb += vec3(0.55, 0.7, 0.82) * fres * 0.35;
          }`,
        );
      };
      pane.customProgramCacheKey = () => 'hq-pane-fresnel';
    }
    this.materials.set('pane', pane);
  }

  /** The visible sky: one inside-out sphere that follows the camera, so the horizon meets the fog and no page colour shows behind the scene. */
  private buildSky(): void {
    const material = new ShaderMaterial({
      side: BackSide, depthWrite: false, fog: false,
      uniforms: {
        zenith: { value: new Color() }, horizon: { value: new Color() }, ground: { value: new Color() }, glow: { value: new Color() },
        sunDir: { value: [0, 1, 0] }, sunColor: { value: new Color() }, sunPower: { value: 40 }, uDisc: { value: 0.0 },
        uTime: { value: 0 }, uCloud: { value: 0 }, uCloudCol: { value: new Color() },
      },
      vertexShader: SKY_VERT, fragmentShader: SKY_FRAG,
    });
    const dome = new Mesh(new SphereGeometry(1, 24, 12), material);
    dome.frustumCulled = false; dome.renderOrder = -10;
    this.scene.add(dome);
    this.sky = dome;
  }

  /** A procedural sky (zenith, horizon, ground and the sun or moon disc) baked into reflection maps for day and night. */
  private buildEnvironments(): void {
    const pmrem = new PMREMGenerator(this.renderer);
    const make = (night: number): Texture => {
      const light = lightingFor(night);
      const lin = (t: readonly [number, number, number]) => new Color().setRGB(t[0], t[1], t[2], SRGBColorSpace);
      const material = new ShaderMaterial({
        side: BackSide, depthWrite: false,
        uniforms: {
          zenith: { value: lin(light.zenith) }, horizon: { value: lin(light.fog) }, ground: { value: lin(light.ground) }, glow: { value: lin(light.glow) },
          sunDir: { value: [-light.sunDir[0], -light.sunDir[1], -light.sunDir[2]] },
          sunColor: { value: lin(light.sunColor) }, sunPower: { value: night > 0.3 ? 6 : 26 }, uDisc: { value: 1 },
          uTime: { value: 0 }, uCloud: { value: 0 }, uCloudCol: { value: new Color() },
        },
        vertexShader: SKY_VERT, fragmentShader: SKY_FRAG,
      });
      const sphere = new Mesh(new SphereGeometry(50, 32, 16), material);
      const envScene = new Scene();
      envScene.add(sphere);
      const target = pmrem.fromScene(envScene, 0.03);
      sphere.geometry.dispose(); material.dispose();
      return target.texture;
    };
    // Day, blue hour and night: the reflection map follows the light preset.
    this.envs = [make(0), make(DUSK), make(1)];
    pmrem.dispose();
  }

  // -------------------------------------------------------------- scene

  private disposeMeshes(): void {
    for (const mesh of this.meshes) { this.scene.remove(mesh); mesh.geometry.dispose(); }
    this.meshes = [];
  }

  private addItem(item: RenderItem, transparent: boolean, scene: RenderScene): void {
    const v = item.vertices;
    const total = v.length / VERTEX_FLOATS;
    const buckets = new Map<Cls, number[]>();
    for (let t = 0; t + 2 < total; t += 3) {
      const base = t * VERTEX_FLOATS;
      const em = v[base + 9];
      const cls: Cls = transparent ? 'pane' : em >= 2 ? 'glass' : (CLASS_OF_MAT[v[base + 10]] ?? 'flat');
      let list = buckets.get(cls);
      if (!list) { list = []; buckets.set(cls, list); }
      list.push(t);
    }
    for (const [cls, tris] of buckets) {
      const n = tris.length * 3;
      const position = new Float32Array(n * 3), normal = new Float32Array(n * 3), color = new Float32Array(n * 3), em = new Float32Array(n), uv = new Float32Array(n * 2);
      let o = 0;
      for (const t of tris) {
        for (let k = 0; k < 3; k++) {
          const s = (t + k) * VERTEX_FLOATS;
          position.set([v[s], v[s + 1], v[s + 2]], o * 3);
          normal.set([v[s + 3], v[s + 4], v[s + 5]], o * 3);
          color.set([srgbToLinear(v[s + 6]), srgbToLinear(v[s + 7]), srgbToLinear(v[s + 8])], o * 3);
          em[o] = v[s + 9];
          uv.set([v[s + 11], v[s + 12]], o * 2);
          o += 1;
        }
      }
      const geometry = new BufferGeometry();
      geometry.setAttribute('position', new Float32BufferAttribute(position, 3));
      geometry.setAttribute('normal', new Float32BufferAttribute(normal, 3));
      geometry.setAttribute('color', new Float32BufferAttribute(color, 3));
      geometry.setAttribute('aEm', new Float32BufferAttribute(em, 1));
      geometry.setAttribute('uv', new Float32BufferAttribute(uv, 2));
      geometry.computeBoundingSphere();
      const material = this.materials.get(cls)!;
      if (cls === 'pane') (material as MeshPhysicalMaterial).opacity = scene.glassAlpha ?? 0.22;
      const mesh = new Mesh(geometry, material);
      mesh.frustumCulled = item.cull !== false;
      mesh.userData.id = item.id;
      mesh.userData.hl = 0;
      mesh.userData.fallbackOf = item.fallbackOf ?? null;
      mesh.visible = !item.fallbackOf || !this.readyGroups.has(item.fallbackOf);
      const sunOk = cls !== 'sign' && cls !== 'pane' && cls !== 'glass';
      mesh.castShadow = sunOk && cls !== 'water';
      mesh.receiveShadow = cls !== 'sign' && cls !== 'pane';
      mesh.onBeforeRender = (_r, _s, _c, _g, mat) => {
        const hl = (mat as Material).userData.hl as IUniform<number> | undefined;
        if (!hl) return;
        const value = mesh.userData.hl as number;
        if (hl.value !== value) { hl.value = value; (mat as Material & { uniformsNeedUpdate: boolean }).uniformsNeedUpdate = true; }
      };
      this.scene.add(mesh);
      this.meshes.push(mesh);
    }
  }

  setScene(scene: RenderScene): void {
    this.disposeMeshes();
    if (scene.models?.length && !this.layer) this.layer = new ModelLayer(this.scene, this.cheap, (ready) => this.applyReady(ready), () => this.requestFrame());
    if (!this.layer && scene.halos?.length) this.layer = new ModelLayer(this.scene, this.cheap, (ready) => this.applyReady(ready), () => this.requestFrame());
    if (this.layer) { this.readyGroups = scene.models?.length ? this.layer.readyNow(scene.models) : new Set(); this.layer.setNight(this.night); }
    for (const item of scene.opaque) this.addItem(item, false, scene);
    for (const item of scene.glass) this.addItem(item, true, scene);
    if (this.layer) {
      this.layer.setHalos(scene.halos);
      void this.layer.setPlacements(scene.models ?? []).catch(() => undefined);
    }
    if (scene.ambient?.vehicles.length || this.ambient) {
      if (!this.ambient) this.ambient = new AmbientLayer(this.scene, this.cheap);
      this.ambient.setPlan(scene.ambient);
    }
    const ripples = scene.ambient?.ripples;
    for (let i = 0; i < MAX_RIPPLES; i++) {
      const on = !!ripples && i * 3 + 2 < ripples.length;
      this.shared.uRipples.value[i].set(on ? ripples![i * 3] : 0, on ? ripples![i * 3 + 1] : 0, on ? ripples![i * 3 + 2] : 0, 0);
    }
    this.shadowRegion = scene.shadow ? { center: { ...scene.shadow.center }, radius: scene.shadow.radius } : null;
    const key = JSON.stringify(scene.signs ?? []);
    if (key !== this.signKey) {
      this.signKey = key;
      this.signTexture?.dispose();
      this.signTexture = null;
      const source = scene.signs && scene.signs.length ? drawSignAtlas(scene.signs) : null;
      if (source) {
        const tex = new CanvasTexture(source);
        tex.colorSpace = SRGBColorSpace;
        tex.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
        this.signTexture = tex;
      }
      const sign = this.materials.get('sign') as MeshBasicMaterial;
      sign.map = this.signTexture;
      sign.needsUpdate = true;
    }
    this.applyHighlight();
    this.shadowDirty = true;
    this.requestFrame();
  }

  /** A model group finished loading (or was found unavailable): hide the stand-ins of the ready groups, show the rest. */
  private applyReady(ready: ReadonlySet<ModelGroup>): void {
    this.readyGroups = ready;
    for (const mesh of this.meshes) {
      const group = mesh.userData.fallbackOf as ModelGroup | null;
      if (group) mesh.visible = !ready.has(group);
    }
    this.shadowDirty = true;
    this.requestFrame();
  }

  setCamera(camera: CameraState): void { this.cameraState = camera; this.requestFrame(); }
  setNight(night: number): void { this.night = Math.max(0, Math.min(1, night)); this.layer?.setNight(this.night); this.requestFrame(); }

  setHighlight(hover: string | null, selected: string | null): void {
    if (hover === this.hover && selected === this.selected) return;
    this.hover = hover; this.selected = selected;
    this.applyHighlight();
    this.requestFrame();
  }

  /** Bloom is a motion-like effect (it shimmers as the camera moves): the stage turns it off under reduced motion. */
  setReducedMotion(reduced: boolean): void { this.reducedMotion = reduced; this.requestFrame(); }

  private applyHighlight(): void {
    for (const mesh of this.meshes) mesh.userData.hl = mesh.userData.id === this.selected ? 1 : mesh.userData.id === this.hover ? 0.6 : 0;
  }

  get isLost(): boolean { return this.lost; }

  requestFrame(): void {
    if (this.raf !== null || this.disposed) return;
    this.raf = requestAnimationFrame(() => { this.raf = null; this.draw(); });
  }

  /** The level ambient motion runs at right now (0 = still): reduced motion, a hidden tab, a software rasteriser and slow frames all lower it. */
  private ambientLevelNow(): AmbientLevel {
    const pixels = this.canvas.clientWidth * this.canvas.clientHeight * (window.devicePixelRatio || 1) ** 2;
    return ambientLevelFor({ software: this.software, reducedMotion: this.reducedMotion, hidden: document.hidden, forced: this.forcedAmbient, pixels, slowSteps: this.slowSteps });
  }

  /** Keeps frames coming at the ambient frame rate while the level is above 0; a still scene draws only when something asks. */
  private scheduleAmbient(): void {
    if (this.ambientRaf !== null || this.disposed || this.lost) return;
    this.ambientRaf = requestAnimationFrame(() => {
      this.ambientRaf = null;
      if (this.disposed) return;
      const fps = AMBIENT_FPS[this.level];
      if (fps === 0) return;
      if (performance.now() - this.lastDrawAt >= 1000 / fps - 3) this.draw(); else this.scheduleAmbient();
    });
  }

  // -------------------------------------------------------------- frame

  private applyLighting(): void {
    const light = lightingFor(this.night);
    const set = (target: Color, t: readonly [number, number, number]) => target.setRGB(t[0], t[1], t[2], SRGBColorSpace);
    set(this.sun.color, light.sunColor);
    this.sun.intensity = light.sunIntensity;
    set(this.hemi.color, light.sky);
    set(this.hemi.groundColor, light.ground);
    this.hemi.intensity = light.hemiIntensity;
    set(this.fog.color, light.fog);
    this.renderer.toneMappingExposure = light.exposure;
    this.scene.environment = this.cheap || !this.envs.length ? null : this.envs[this.night < 0.29 ? 0 : this.night < 0.79 ? 1 : 2];
    this.scene.environmentIntensity = light.envIntensity;
    if (this.cheap) this.hemi.intensity = light.hemiIntensity + light.envIntensity * 0.8;
    const sh = this.shared;
    sh.uNight.value = this.night;
    sh.uLit.value = light.lit;
    // What the glass reflects: the sky colours of the moment and the sun (or moon) direction.
    set(sh.uSkyTop.value, light.zenith);
    sh.uSkyTop.value.lerp(this.fog.color, 0.25);
    sh.uSkyHor.value.copy(this.fog.color);
    sh.uSkyHor.value.r += light.glow[0] * 0.5; sh.uSkyHor.value.g += light.glow[1] * 0.5; sh.uSkyHor.value.b += light.glow[2] * 0.5;
    sh.uSunDir.value.set(light.sunDir[0], light.sunDir[1], light.sunDir[2]);
    set(sh.uSunCol.value, light.sunColor);
    this.ambient?.setLit(light.lit);
    if (this.sky) {
      const u = (this.sky.material as ShaderMaterial).uniforms;
      set(u.zenith.value, light.zenith);
      u.horizon.value.copy(this.fog.color);
      u.ground.value.copy(this.fog.color).multiplyScalar(0.7);
      set(u.glow.value, light.glow);
      u.sunDir.value[0] = -light.sunDir[0]; u.sunDir.value[1] = -light.sunDir[1]; u.sunDir.value[2] = -light.sunDir[2];
      set(u.sunColor.value, light.sunColor);
      u.sunPower.value = 30 + this.night * 20;
      u.uDisc.value = 0.7;
      u.uTime.value = sh.uTime.value;
      u.uCloud.value = light.cloud;
      const k = Math.min(1, light.sunIntensity / 2) * 0.4;
      u.uCloudCol.value.setRGB(
        this.fog.color.r * 0.6 + sh.uSunCol.value.r * k + sh.uSkyHor.value.r * 0.0 + light.glow[0] * 0.35,
        this.fog.color.g * 0.6 + sh.uSunCol.value.g * k + light.glow[1] * 0.35,
        this.fog.color.b * 0.6 + sh.uSunCol.value.b * k + light.glow[2] * 0.35,
      );
    }
    const sign = this.materials.get('sign') as MeshBasicMaterial;
    const gain = 0.9 + 1.3 * light.lit;
    sign.color.setRGB(gain, gain, gain);
    // Shadow light: from the sun (or moon) towards the middle of the region.
    const region = this.shadowRegion;
    const on = region !== null && this.quality > 0;
    if (on && region) {
      const d = new Vector2(light.sunDir[0], light.sunDir[2]);
      void d;
      const dir = { x: light.sunDir[0], y: light.sunDir[1], z: light.sunDir[2] };
      const len = Math.hypot(dir.x, dir.y, dir.z);
      const R = region.radius;
      const key = `${region.center.x.toFixed(1)},${region.center.z.toFixed(1)},${R.toFixed(1)},${(dir.x / len).toFixed(2)},${(dir.z / len).toFixed(2)},${this.quality}`;
      if (key !== this.lastSunKey) {
        this.lastSunKey = key;
        this.sun.position.set(region.center.x - (dir.x / len) * R * 2, region.center.y - (dir.y / len) * R * 2, region.center.z - (dir.z / len) * R * 2);
        this.sun.target.position.set(region.center.x, region.center.y, region.center.z);
        const cam = this.sun.shadow.camera;
        cam.left = -R; cam.right = R; cam.top = R; cam.bottom = -R; cam.near = 0.5; cam.far = R * 4.5;
        cam.updateProjectionMatrix();
        this.sun.target.updateMatrixWorld();
        const size = SHADOW_SIZE[this.quality];
        this.sun.shadow.normalBias = (1.6 * 2 * R) / Math.max(1, size);
        if (this.sun.shadow.mapSize.x !== size) { this.sun.shadow.mapSize.set(size, size); this.sun.shadow.map?.dispose(); this.sun.shadow.map = null; }
        this.shadowDirty = true;
      }
    } else {
      const d = { x: light.sunDir[0], y: light.sunDir[1], z: light.sunDir[2] };
      this.sun.position.set(-d.x * 100, -d.y * 100, -d.z * 100);
      this.sun.target.position.set(0, 0, 0);
      this.sun.target.updateMatrixWorld();
    }
    const shadowsOn = on;
    if (this.renderer.shadowMap.enabled !== shadowsOn) {
      this.renderer.shadowMap.enabled = shadowsOn;
      for (const material of this.materials.values()) material.needsUpdate = true;
    }
    this.sun.castShadow = shadowsOn;
    this.stats.shadowSize = shadowsOn ? SHADOW_SIZE[this.quality] : 0;
  }

  private resize(): { width: number; height: number; dpr: number } {
    const box = this.canvas.getBoundingClientRect();
    const dpr = Math.max(0.6, Math.min(this.maxDpr, window.devicePixelRatio || 1) * this.dprScale);
    const key = `${box.width.toFixed(1)}x${box.height.toFixed(1)}@${dpr.toFixed(2)}`;
    if (key !== this.sizeKey) {
      this.sizeKey = key;
      this.renderer.setPixelRatio(dpr);
      this.renderer.setSize(Math.max(1, Math.round(box.width)), Math.max(1, Math.round(box.height)), false);
      this.composer?.setPixelRatio(dpr);
      this.composer?.setSize(Math.max(1, Math.round(box.width)), Math.max(1, Math.round(box.height)));
    }
    this.stats.dpr = dpr;
    return { width: Math.max(1, box.width), height: Math.max(1, box.height), dpr };
  }

  private ensureComposer(width: number, height: number, dpr: number): EffectComposer {
    if (!this.composer) {
      const target = new WebGLRenderTarget(Math.round(width * dpr), Math.round(height * dpr), { type: HalfFloatType, samples: 4 });
      const composer = new EffectComposer(this.renderer, target);
      composer.setPixelRatio(dpr);
      composer.setSize(Math.round(width), Math.round(height));
      composer.addPass(new RenderPass(this.scene, this.camera));
      this.bloom = new UnrealBloomPass(new Vector2(Math.round(width), Math.round(height)), 0.85, 0.5, 1.0);
      composer.addPass(this.bloom);
      composer.addPass(new OutputPass());
      this.composer = composer;
    }
    return this.composer;
  }

  draw(): void {
    if (this.disposed || this.lost || !this.cameraState) return;
    const started = performance.now();
    // Two requests in one animation frame (a camera move and the ambient loop) draw once.
    if (started - this.lastDrawAt < 3 && this.stats.frames > 0) { this.scheduleAmbientIfRunning(); return; }
    this.lastDrawAt = started;
    this.level = this.ambientLevelNow();
    if (this.level > 0 && this.lastClockAt > 0) this.clock += Math.min(0.1, Math.max(0, (started - this.lastClockAt) / 1000));
    this.lastClockAt = started;
    this.shared.uTime.value = this.clock;
    this.shared.uAmb.value = this.level >= 2 ? 1 : 0;
    this.ambient?.update(this.clock);
    try {
      const { width, height, dpr } = this.resize();
      const aspect = width / height;
      const cs = this.cameraState;
      const eye = cameraEye(cs);
      const { near, far } = clipPlanes(cs);
      this.camera.fov = cs.fov; this.camera.aspect = aspect; this.camera.near = near; this.camera.far = far;
      this.camera.position.set(eye.x, eye.y, eye.z);
      this.camera.up.set(0, 1, 0);
      this.camera.lookAt(cs.target.x, cs.target.y, cs.target.z);
      this.camera.updateProjectionMatrix();
      this.camera.updateMatrixWorld();
      this.sky?.position.copy(this.camera.position);
      this.sky?.scale.setScalar(far * 0.9);
      this.fog.near = cs.distance * 1.8 + 60; this.fog.far = cs.distance * 4 + 260;
      this.shared.uDetail.value.set(cs.distance * 0.7 + 25, cs.distance * 1.6 + 70);
      this.applyLighting();
      if (this.shadowDirty && this.renderer.shadowMap.enabled) { this.renderer.shadowMap.needsUpdate = true; this.shadowDirty = false; }
      this.renderer.info.reset();
      const useBloom = this.quality >= 2 && !this.reducedMotion && this.night > 0.04;
      if (useBloom) {
        const composer = this.ensureComposer(width, height, dpr);
        if (this.bloom) { this.bloom.strength = 0.5 * Math.min(1, this.night * 1.4); this.bloom.threshold = 1.05; this.bloom.radius = 0.4; }
        composer.render();
      } else {
        this.renderer.render(this.scene, this.camera);
      }
      this.stats.bloom = useBloom;
      this.stats.ambient.level = this.level; this.stats.ambient.clock = this.clock; this.stats.ambient.vehicles = this.ambient?.count ?? 0;
      if (this.level > 0) this.stats.ambient.frames += 1;
      if (this.layer) Object.assign(this.stats.models, this.layer.stats);
      this.stats.triangles = this.renderer.info.render.triangles;
      this.stats.drawn = this.renderer.info.render.calls;
      this.stats.culled = Math.max(0, this.meshes.length - this.meshes.filter((m) => m.visible).length);
      this.cb.onFrame?.(viewProjection(cs, aspect), aspect);
    } catch (error) {
      this.fail(error);
      return;
    }
    const ms = performance.now() - started;
    const s = this.stats;
    this.scheduleAmbientIfRunning();
    s.frames += 1; s.lastDrawMs = ms;
    // The first frames compile shaders and upload buffers; they say nothing about steady drawing, so the average starts after them.
    const warm = s.frames > 3;
    if (warm) s.avgDrawMs = s.avgDrawMs === 0 ? ms : s.avgDrawMs * 0.9 + ms * 0.1;
    // Adaptive quality: three seconds of slow frames take one notch off (bloom, then shadow size, then shadows, then pixel ratio).
    if (!warm) return;
    this.slowFrames = ms > 30 ? this.slowFrames + 1 : Math.max(0, this.slowFrames - 1);
    if (this.slowFrames > 20) {
      this.slowFrames = 0;
      // Ambient motion is the newest cost and the first to go: it keeps frames coming, so it steps down before the picture does.
      if (this.level > 0) this.slowSteps += 1;
      else if (this.quality > 0) { this.quality = (this.quality - 1) as Quality; this.shadowDirty = true; this.lastSunKey = ''; }
      else if (this.dprScale > 0.6) this.dprScale = Math.max(0.6, this.dprScale * 0.8);
      this.requestFrame();
    }
  }

  private scheduleAmbientIfRunning(): void { if (this.level > 0) this.scheduleAmbient(); }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.raf !== null) cancelAnimationFrame(this.raf);
    this.canvas.removeEventListener('webglcontextlost', this.onLostEvent);
    this.canvas.removeEventListener('webglcontextrestored', this.onRestoredEvent);
    this.disposeMeshes();
    this.layer?.dispose();
    for (const material of this.materials.values()) material.dispose();
    for (const texture of this.textures) texture.dispose();
    this.signTexture?.dispose();
    if (this.sky) { this.sky.geometry.dispose(); (this.sky.material as Material).dispose(); }
    for (const env of this.envs) env.dispose();
    this.ambient?.dispose();
    document.removeEventListener('visibilitychange', this.onVisibility);
    if (this.ambientRaf !== null) cancelAnimationFrame(this.ambientRaf);
    this.composer?.dispose();
    this.sun.shadow.map?.dispose();
    this.renderer.dispose();
  }
}
