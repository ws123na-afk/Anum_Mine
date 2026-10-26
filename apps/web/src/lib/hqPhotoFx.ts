/**
 * The photographic tier's three.js side (docs/ANUM_MINE_HQ_PHOTOREAL.md). A lazy chunk: the renderer imports it only when the tier is
 * 4, so a lower tier never downloads it and software GL never runs it unless a person pins tier 4.
 *
 *   - PBR detail kits: the procedural maps of `hqPbrTextures.ts` as data textures plus the shader chunks of `hqPhotoShaders.ts`
 *   - a post stack: ambient occlusion (GTAO), depth of field, bloom, light shafts, AgX tone mapping, then vignette, grain and dither
 *   - four warm point lights picked from the scene's lamp and entrance halos
 *
 * What to draw and how strongly comes from the pure `hqLightRig.ts` (grade per preset, sun on screen, picked lights).
 */
import {
  AgXToneMapping, ACESFilmicToneMapping, DataTexture, HalfFloatType, LinearMipmapLinearFilter, LinearFilter, NoColorSpace, PointLight, RepeatWrapping, RGBAFormat, SRGBColorSpace, UnsignedByteType, Vector2, Vector3, Vector4, WebGLRenderTarget,
  type Object3D, type PerspectiveCamera, type Scene, type WebGLRenderer, type IUniform,
} from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js';
import { BokehPass } from 'three/addons/postprocessing/BokehPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import type { FxName } from './hqQualityTier.ts';
import { PBR_KINDS, PBR_OF_CLASS, PBR_SIZE, generatePbr, type PbrKind } from './hqPbrTextures.ts';
import { FULLSCREEN_VERT, GRADE_FRAG, PBR_COMMON, PBR_LEAF, PBR_NORMAL, PBR_ROUGH, SHAFT_FRAG } from './hqPhotoShaders.ts';
import { fitShadow, focusRange, gradeFor, pickLights, pointLightGain, sunOnScreen, type Fit, type Grade } from './hqLightRig.ts';
import { lightingFor } from './hqLighting.ts';
import type { CameraState } from './headquartersSpatial.ts';
import type { Mat4 } from './hqMath.ts';

/** What the renderer's `patched()` splices into a surface class: shader chunks and the uniforms they read. */
export type PbrHook = Readonly<{ key: string; common: string; rough: string; normal: string; emissive: string; uniforms: Readonly<Record<string, IUniform>> }>;

/** Repeats per metre, normal strength, roughness influence and cavity influence for each surface class. */
const PARAMS: Readonly<Record<string, readonly [number, number, number, number]>> = {
  concrete: [0.22, 0.8, 0.8, 0.5], metal: [1.2, 0.6, 0.7, 0.2], roller: [1.2, 0.6, 0.7, 0.2], wood: [0.9, 0.9, 0.6, 0.5], tile: [0.35, 0.5, 0.8, 0.3],
  asphalt: [0.25, 1.0, 1.0, 0.5], court: [0.3, 0.8, 0.6, 0.4], cloth: [3.0, 1.0, 0.3, 0.5], foliage: [1.6, 0.5, 0.3, 0.3],
};

export const POINT_LIGHTS = 4;
/** World radius in metres and strength of the screen space occlusion. */
const AO_RADIUS = 3.2, AO_SCALE = 1.35;
/** Candela of a full strength interior light: about the glow of a lit lobby at 4 m. */
const LIGHT_CANDELA = 36;

export type PhotoFrame = Readonly<{
  camera: CameraState; viewProjection: Mat4; width: number; height: number; dpr: number; night: number; time: number; halos: Float32Array | undefined; reducedMotion: boolean;
}>;

export class PhotoFx {
  private readonly textures = new Map<PbrKind, DataTexture>();
  private readonly hooks = new Map<string, PbrHook>();
  private readonly lights: PointLight[] = [];
  private composer: EffectComposer | null = null;
  private gtao: GTAOPass | null = null;
  private bokeh: BokehPass | null = null;
  private bloom: UnrealBloomPass | null = null;
  private shafts: ShaderPass | null = null;
  private grade: ShaderPass | null = null;
  private readonly sunUv = new Vector2();
  private readonly tint = new Vector3();

  constructor(private readonly renderer: WebGLRenderer, private readonly scene: Scene, private readonly camera: PerspectiveCamera, private off: ReadonlySet<FxName> = new Set()) {
    for (let i = 0; i < POINT_LIGHTS; i++) {
      const l = new PointLight(0xffd9a0, 0, 12, 2);
      l.castShadow = false;
      scene.add(l);
      this.lights.push(l);
    }
  }

  private texture(kind: PbrKind): DataTexture {
    let t = this.textures.get(kind);
    if (!t) {
      t = new DataTexture(generatePbr(kind, PBR_SIZE), PBR_SIZE, PBR_SIZE, RGBAFormat, UnsignedByteType);
      t.colorSpace = NoColorSpace;
      t.wrapS = t.wrapT = RepeatWrapping;
      t.minFilter = LinearMipmapLinearFilter;
      t.magFilter = LinearFilter;
      t.generateMipmaps = true;
      t.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
      t.needsUpdate = true;
      this.textures.set(kind, t);
    }
    return t;
  }

  /** The shader hook of a surface class, or null for classes without a detail map. */
  pbr(cls: string): PbrHook | null {
    const kind = PBR_OF_CLASS[cls];
    const p = PARAMS[cls];
    if (!kind || !p) return null;
    let hook = this.hooks.get(cls);
    if (!hook) {
      hook = {
        key: `pbr-${kind}`, common: PBR_COMMON, rough: PBR_ROUGH, normal: PBR_NORMAL, emissive: cls === 'foliage' ? PBR_LEAF : '',
        uniforms: { uPbrMap: { value: this.texture(kind) }, uPbr: { value: new Vector4(p[0], p[1], p[2], p[3]) } },
      };
      this.hooks.set(cls, hook);
    }
    return hook;
  }

  private tune: Readonly<{ grade?: Partial<Grade>; aoRadius?: number; aoScale?: number }> = {};
  /** Overrides for the grade and the occlusion radius while running: the look is tuned in one session with these (`__anumHqTune` in the console). */
  setTune(t: Readonly<{ grade?: Partial<Grade>; aoRadius?: number; aoScale?: number }>): void {
    this.tune = t;
    this.gtao?.updateGtaoMaterial({ radius: t.aoRadius ?? AO_RADIUS, scale: t.aoScale ?? AO_SCALE });
  }

  private hidden: Object3D | null = null;
  /** An object that stays in the picture but is left out of the depth and normal prepasses of the occlusion and focus passes (the flat contact discs). */
  setPrepassHidden(object: Object3D | null): void { this.hidden = object; }

  private withoutHidden<T extends object>(pass: T): T {
    const target = pass as unknown as { render: (...a: unknown[]) => void };
    const original = target.render.bind(pass);
    target.render = (...a: unknown[]) => {
      const was = this.hidden?.visible ?? false;
      if (this.hidden) this.hidden.visible = false;
      try { original(...a); } finally { if (this.hidden) this.hidden.visible = was; }
    };
    return pass;
  }

  /** Switches effects off or on while running (the cost measurements do this; a person does it with `anum.hq.fxoff` before loading). */
  setOff(off: ReadonlySet<FxName>): void { this.off = off; }

  /** The shadow sphere fitted to the camera (see `fitShadow`). */
  fit(camera: CameraState, region: Fit, mapSize: number): Fit { return fitShadow(camera, region, mapSize); }

  /** Warm lights for the lamps and entrances nearest the camera; none by day. */
  private setLights(camera: CameraState, halos: Float32Array | undefined, night: number): void {
    const picked = this.off.has('lights') ? [] : pickLights(halos, camera.target, POINT_LIGHTS);
    const gain = pointLightGain(night);
    this.lights.forEach((l, i) => {
      const p = picked[i];
      if (!p || gain <= 0) { l.intensity = 0; return; }
      l.position.set(p.x, p.y, p.z);
      l.color.setRGB(p.color[0], p.color[1], p.color[2], SRGBColorSpace);
      l.distance = p.distance;
      l.intensity = LIGHT_CANDELA * gain * Math.min(1.5, p.weight);
    });
  }

  private build(width: number, height: number, dpr: number): EffectComposer {
    const target = new WebGLRenderTarget(Math.round(width * dpr), Math.round(height * dpr), { type: HalfFloatType, samples: 4 });
    const composer = new EffectComposer(this.renderer, target);
    composer.setPixelRatio(dpr);
    composer.setSize(Math.round(width), Math.round(height));
    composer.addPass(new RenderPass(this.scene, this.camera));
    this.gtao = new GTAOPass(this.scene, this.camera, Math.round(width), Math.round(height));
    this.gtao.output = GTAOPass.OUTPUT.Default;
    this.gtao.updateGtaoMaterial({ radius: AO_RADIUS, distanceExponent: 1.4, thickness: 1.6, scale: AO_SCALE, samples: 16, distanceFallOff: 1, screenSpaceRadius: false });
    this.gtao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 8, radiusExponent: 1, rings: 2, samples: 16 });
    composer.addPass(this.withoutHidden(this.gtao));
    this.bokeh = new BokehPass(this.scene, this.camera, { focus: 60, aperture: 0.00005, maxblur: 0.004 });
    composer.addPass(this.withoutHidden(this.bokeh));
    this.bloom = new UnrealBloomPass(new Vector2(Math.round(width), Math.round(height)), 0.5, 0.4, 1.0);
    composer.addPass(this.bloom);
    this.shafts = new ShaderPass({
      uniforms: { tDiffuse: { value: null }, uSun: { value: this.sunUv }, uShaft: { value: 0 }, uThresh: { value: 1.1 }, uTint: { value: this.tint } },
      vertexShader: FULLSCREEN_VERT, fragmentShader: SHAFT_FRAG,
    });
    composer.addPass(this.shafts);
    composer.addPass(new OutputPass());
    this.grade = new ShaderPass({
      uniforms: { tDiffuse: { value: null }, uVig: { value: 0.25 }, uGrain: { value: 0.03 }, uSat: { value: 1.05 }, uContrast: { value: 0.3 }, uTime: { value: 0 }, uRes: { value: new Vector2(width, height) } },
      vertexShader: FULLSCREEN_VERT, fragmentShader: GRADE_FRAG,
    });
    composer.addPass(this.grade);
    this.composer = composer;
    return composer;
  }

  /** Draws one frame through the stack with AgX tone mapping, then puts the renderer's tone mapping back for the direct path. */
  frame(f: PhotoFrame): void {
    const composer = this.composer ?? this.build(f.width, f.height, f.dpr);
    const g = { ...gradeFor(f.night), ...this.tune.grade };
    if (this.gtao) this.gtao.enabled = !this.off.has('gtao');
    if (this.bokeh) this.bokeh.enabled = !this.off.has('dof');
    if (this.shafts) this.shafts.enabled = !this.off.has('shafts');
    if (this.grade) this.grade.enabled = !this.off.has('grain');
    const light = lightingFor(f.night);
    const sun = sunOnScreen(f.viewProjection, f.camera, light.sunDir);
    this.setLights(f.camera, f.halos, f.night);
    this.renderer.toneMapping = AgXToneMapping;
    this.renderer.toneMappingExposure = g.exposure;
    if (this.gtao) this.gtao.blendIntensity = g.ao;
    if (this.bokeh) {
      const r = focusRange(f.camera.distance);
      const u = this.bokeh.uniforms as Record<string, IUniform<number>>;
      u.focus.value = r.focus; u.aperture.value = r.aperture * g.dof; u.maxblur.value = r.maxBlur * g.dof;
    }
    if (this.bloom) this.bloom.enabled = !this.off.has('bloom') && !f.reducedMotion;
    if (this.bloom) { this.bloom.strength = 0.14 + 0.4 * Math.min(1, f.night * 1.4); this.bloom.threshold = 1.0; this.bloom.radius = 0.4; }
    if (this.shafts) {
      const u = this.shafts.uniforms as Record<string, IUniform>;
      this.sunUv.set(sun.x * 0.5 + 0.5, sun.y * 0.5 + 0.5);
      this.tint.set(light.sunColor[0], light.sunColor[1], light.sunColor[2]);
      u.uShaft.value = f.reducedMotion ? 0 : g.shafts * sun.weight;
    }
    if (this.grade) {
      const u = this.grade.uniforms as Record<string, IUniform>;
      u.uVig.value = g.vignette; u.uGrain.value = g.grain; u.uSat.value = g.saturation; u.uContrast.value = g.contrast; u.uTime.value = f.reducedMotion ? 0 : f.time;
      (u.uRes.value as Vector2).set(f.width * f.dpr, f.height * f.dpr);
    }
    try { composer.render(); } finally { this.renderer.toneMapping = ACESFilmicToneMapping; }
  }

  /** Lights off, for a step down to the lower tier. */
  lightsOff(): void { for (const l of this.lights) l.intensity = 0; }

  setSize(width: number, height: number, dpr: number): void {
    this.composer?.setPixelRatio(dpr);
    this.composer?.setSize(Math.round(width), Math.round(height));
  }

  get passes(): number { return this.composer ? this.composer.passes.length : 0; }
  get kinds(): number { return PBR_KINDS.length; }

  dispose(): void {
    for (const l of this.lights) this.scene.remove(l);
    for (const t of this.textures.values()) t.dispose();
    this.textures.clear();
    this.composer?.dispose();
    this.composer = null;
  }
}
