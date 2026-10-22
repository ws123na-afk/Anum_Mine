/**
 * Small WebGL renderer for the headquarters scenes: depth-tested triangles with hemisphere plus sun
 * lighting, night-time emissive windows, distance fog, a translucent glass pass, per-item frustum
 * culling and render-on-demand. No third-party library: the scenes are procedural low-poly meshes
 * built by `campusScene.ts` and `buildingScene.ts`.
 *
 * Failure handling: `HqRenderer.create` returns null when WebGL is missing; a lost context is
 * reported through `onLost` and the buffers are rebuilt from the CPU meshes on `onRestored`.
 */
import { VERTEX_FLOATS } from './hqGeometry.ts';
import { aabbInFrustum, frustumPlanes, viewProjection, type Aabb } from './hqMath.ts';
import { cameraEye, type CameraState } from './headquartersSpatial.ts';

export type RenderItem = Readonly<{ id: string; vertices: Float32Array; bounds: Aabb; cull?: boolean }>;
export type RenderScene = Readonly<{ opaque: readonly RenderItem[]; glass: readonly RenderItem[]; glassAlpha?: number }>;
export type RenderStats = {
  frames: number; lastDrawMs: number; avgDrawMs: number; drawn: number; culled: number; triangles: number; dpr: number;
};
export type RendererCallbacks = Readonly<{
  onLost?: () => void; onRestored?: () => void; onError?: (error: Error) => void; onFrame?: (vp: Float32Array, aspect: number) => void;
}>;

const VERTEX = `
attribute vec3 aPos; attribute vec3 aNormal; attribute vec3 aColor; attribute float aEmissive;
uniform mat4 uVP;
varying vec3 vWorld; varying vec3 vNormal; varying vec3 vColor; varying float vEmissive;
void main(){ vWorld=aPos; vNormal=aNormal; vColor=aColor; vEmissive=aEmissive; gl_Position=uVP*vec4(aPos,1.0); }`;

const FRAGMENT = `
precision mediump float;
varying vec3 vWorld; varying vec3 vNormal; varying vec3 vColor; varying float vEmissive;
uniform vec3 uEye; uniform vec3 uSunDir; uniform vec3 uSunColor; uniform vec3 uSky; uniform vec3 uGround;
uniform vec3 uFog; uniform vec2 uFogRange; uniform float uNight; uniform float uHighlight; uniform float uAlpha;
void main(){
  vec3 n=normalize(vNormal);
  float up=n.y*0.5+0.5;
  vec3 hemi=mix(uGround,uSky,up);
  float diff=max(dot(n,-uSunDir),0.0);
  vec3 lit=vColor*(hemi+uSunColor*diff);
  float glow=vEmissive*mix(0.22,1.0,uNight);
  vec3 col=lit+vColor*glow*1.5;
  col+=vec3(0.10,0.22,0.19)*uHighlight;
  float dist=length(vWorld-uEye);
  float f=smoothstep(uFogRange.x,uFogRange.y,dist);
  col=mix(col,uFog,f);
  gl_FragColor=vec4(col,uAlpha);
}`;

export type Lighting = Readonly<{
  sunDir: readonly [number, number, number]; sunColor: readonly [number, number, number];
  sky: readonly [number, number, number]; ground: readonly [number, number, number];
  fog: readonly [number, number, number];
}>;

/** Day and night are the two ends of one blend (0 = day, 1 = night), so toggling can animate. */
export function lightingFor(night: number): Lighting {
  const n = Math.max(0, Math.min(1, night));
  const mix3 = (a: readonly number[], b: readonly number[]) => [a[0] + (b[0] - a[0]) * n, a[1] + (b[1] - a[1]) * n, a[2] + (b[2] - a[2]) * n] as const;
  return {
    sunDir: mix3([-0.45, -0.8, -0.38], [0.35, -0.75, 0.3]),
    sunColor: mix3([0.62, 0.58, 0.5], [0.1, 0.14, 0.26]),
    sky: mix3([0.6, 0.68, 0.78], [0.13, 0.17, 0.28]),
    ground: mix3([0.34, 0.33, 0.3], [0.07, 0.08, 0.12]),
    fog: mix3([0.62, 0.74, 0.86], [0.04, 0.07, 0.13]),
  };
}

type GpuItem = { item: RenderItem; buffer: WebGLBuffer | null; count: number };

export class HqRenderer {
  readonly stats: RenderStats = { frames: 0, lastDrawMs: 0, avgDrawMs: 0, drawn: 0, culled: 0, triangles: 0, dpr: 1 };
  private gl: WebGLRenderingContext;
  private program: WebGLProgram | null = null;
  private uniforms: Record<string, WebGLUniformLocation | null> = {};
  private opaque: GpuItem[] = [];
  private glass: GpuItem[] = [];
  private glassAlpha = 0.22;
  private camera: CameraState | null = null;
  private night = 0;
  private hover: string | null = null;
  private selected: string | null = null;
  private lost = false;
  private disposed = false;
  private raf: number | null = null;
  private maxDpr: number;
  private dprScale = 1;
  private slowFrames = 0;
  private readonly cb: RendererCallbacks;
  private readonly canvas: HTMLCanvasElement;
  private readonly onLostEvent: (e: Event) => void;
  private readonly onRestoredEvent: () => void;

  private constructor(canvas: HTMLCanvasElement, gl: WebGLRenderingContext, cb: RendererCallbacks, maxDpr: number) {
    this.canvas = canvas; this.gl = gl; this.cb = cb; this.maxDpr = maxDpr;
    this.onLostEvent = (e) => { e.preventDefault(); this.lost = true; this.cb.onLost?.(); };
    this.onRestoredEvent = () => {
      try { this.lost = false; this.initGl(); this.cb.onRestored?.(); this.requestFrame(); } catch (error) { this.fail(error); }
    };
    canvas.addEventListener('webglcontextlost', this.onLostEvent);
    canvas.addEventListener('webglcontextrestored', this.onRestoredEvent);
  }

  /** Null when the browser cannot give a WebGL context (the caller shows the accessible fallback). */
  static create(canvas: HTMLCanvasElement, cb: RendererCallbacks = {}, maxDpr = 2): HqRenderer | null {
    let gl: WebGLRenderingContext | null = null;
    try {
      gl = canvas.getContext('webgl', { antialias: true, alpha: true, depth: true, powerPreference: 'high-performance' }) as WebGLRenderingContext | null;
    } catch { gl = null; }
    if (!gl) return null;
    const renderer = new HqRenderer(canvas, gl, cb, maxDpr);
    try { renderer.initGl(); } catch (error) { renderer.dispose(); cb.onError?.(error instanceof Error ? error : new Error('WebGL setup failed')); return null; }
    return renderer;
  }

  private fail(error: unknown): void {
    this.cb.onError?.(error instanceof Error ? error : new Error('WebGL failed'));
  }

  private compile(kind: number, source: string): WebGLShader {
    const shader = this.gl.createShader(kind);
    if (!shader) throw new Error('Unable to allocate a shader');
    this.gl.shaderSource(shader, source);
    this.gl.compileShader(shader);
    if (!this.gl.getShaderParameter(shader, this.gl.COMPILE_STATUS)) {
      const log = this.gl.getShaderInfoLog(shader) ?? 'Shader compile failed';
      this.gl.deleteShader(shader);
      throw new Error(log);
    }
    return shader;
  }

  private initGl(): void {
    const gl = this.gl;
    const vs = this.compile(gl.VERTEX_SHADER, VERTEX), fs = this.compile(gl.FRAGMENT_SHADER, FRAGMENT);
    const program = gl.createProgram();
    if (!program) throw new Error('Unable to create a GPU program');
    gl.attachShader(program, vs); gl.attachShader(program, fs); gl.linkProgram(program);
    gl.deleteShader(vs); gl.deleteShader(fs);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) ?? 'Program link failed');
    this.program = program;
    this.uniforms = {};
    for (const name of ['uVP', 'uEye', 'uSunDir', 'uSunColor', 'uSky', 'uGround', 'uFog', 'uFogRange', 'uNight', 'uHighlight', 'uAlpha']) {
      this.uniforms[name] = gl.getUniformLocation(program, name);
    }
    for (const g of [...this.opaque, ...this.glass]) this.upload(g);
  }

  private upload(g: GpuItem): void {
    const gl = this.gl;
    g.buffer = gl.createBuffer();
    if (!g.buffer) throw new Error('Unable to allocate a vertex buffer');
    gl.bindBuffer(gl.ARRAY_BUFFER, g.buffer);
    gl.bufferData(gl.ARRAY_BUFFER, g.item.vertices, gl.STATIC_DRAW);
    g.count = g.item.vertices.length / VERTEX_FLOATS;
  }

  private release(list: GpuItem[]): void {
    if (!this.lost) for (const g of list) if (g.buffer) this.gl.deleteBuffer(g.buffer);
  }

  setScene(scene: RenderScene): void {
    this.release(this.opaque); this.release(this.glass);
    this.glassAlpha = scene.glassAlpha ?? 0.22;
    const make = (item: RenderItem): GpuItem => ({ item, buffer: null, count: 0 });
    this.opaque = scene.opaque.map(make); this.glass = scene.glass.map(make);
    if (!this.lost) {
      try { for (const g of [...this.opaque, ...this.glass]) this.upload(g); } catch (error) { this.fail(error); }
    }
    this.requestFrame();
  }

  setCamera(camera: CameraState): void { this.camera = camera; this.requestFrame(); }
  setNight(night: number): void { this.night = Math.max(0, Math.min(1, night)); this.requestFrame(); }
  setHighlight(hover: string | null, selected: string | null): void {
    if (hover === this.hover && selected === this.selected) return;
    this.hover = hover; this.selected = selected; this.requestFrame();
  }
  get isLost(): boolean { return this.lost; }

  requestFrame(): void {
    if (this.raf !== null || this.disposed) return;
    this.raf = requestAnimationFrame(() => { this.raf = null; this.draw(); });
  }

  private resize(): { width: number; height: number } {
    const box = this.canvas.getBoundingClientRect();
    const dpr = Math.max(0.6, Math.min(this.maxDpr, window.devicePixelRatio || 1) * this.dprScale);
    const width = Math.max(1, Math.round(box.width * dpr)), height = Math.max(1, Math.round(box.height * dpr));
    if (this.canvas.width !== width) this.canvas.width = width;
    if (this.canvas.height !== height) this.canvas.height = height;
    this.stats.dpr = dpr;
    return { width, height };
  }

  draw(): void {
    if (this.disposed || this.lost || !this.program || !this.camera) return;
    const started = performance.now();
    const gl = this.gl;
    try {
      const { width, height } = this.resize();
      const aspect = width / height;
      const vp = viewProjection(this.camera, aspect);
      const planes = frustumPlanes(vp);
      const light = lightingFor(this.night);
      gl.viewport(0, 0, width, height);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.useProgram(this.program);
      const u = this.uniforms;
      const eye = cameraEye(this.camera);
      gl.uniformMatrix4fv(u.uVP, false, vp);
      gl.uniform3f(u.uEye, eye.x, eye.y, eye.z);
      gl.uniform3fv(u.uSunDir, light.sunDir as unknown as number[]);
      gl.uniform3fv(u.uSunColor, light.sunColor as unknown as number[]);
      gl.uniform3fv(u.uSky, light.sky as unknown as number[]);
      gl.uniform3fv(u.uGround, light.ground as unknown as number[]);
      gl.uniform3fv(u.uFog, light.fog as unknown as number[]);
      const d = this.camera.distance;
      gl.uniform2f(u.uFogRange, d * 1.8 + 60, d * 4 + 260);
      gl.uniform1f(u.uNight, this.night);
      gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL);
      this.stats.drawn = 0; this.stats.culled = 0; this.stats.triangles = 0;
      gl.disable(gl.BLEND); gl.depthMask(true); gl.uniform1f(u.uAlpha, 1);
      for (const g of this.opaque) this.drawItem(g, planes);
      if (this.glass.length) {
        gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false);
        gl.uniform1f(u.uAlpha, this.glassAlpha);
        for (const g of this.glass) this.drawItem(g, planes);
        gl.depthMask(true); gl.disable(gl.BLEND);
      }
      this.cb.onFrame?.(vp, aspect);
    } catch (error) {
      this.fail(error);
      return;
    }
    const ms = performance.now() - started;
    const s = this.stats;
    s.frames += 1; s.lastDrawMs = ms; s.avgDrawMs = s.frames === 1 ? ms : s.avgDrawMs * 0.9 + ms * 0.1;
    // Adaptive resolution: three seconds of slow frames lower the pixel ratio one notch, never below 0.6.
    this.slowFrames = ms > 30 ? this.slowFrames + 1 : Math.max(0, this.slowFrames - 1);
    if (this.slowFrames > 20 && this.dprScale > 0.6) { this.dprScale = Math.max(0.6, this.dprScale * 0.8); this.slowFrames = 0; this.requestFrame(); }
  }

  private drawItem(g: GpuItem, planes: ReturnType<typeof frustumPlanes>): void {
    if (!g.buffer || g.count === 0) return;
    if (g.item.cull !== false && !aabbInFrustum(planes, g.item.bounds)) { this.stats.culled += 1; return; }
    const gl = this.gl;
    const stride = VERTEX_FLOATS * 4;
    gl.bindBuffer(gl.ARRAY_BUFFER, g.buffer);
    const attr = (name: string, size: number, offset: number) => {
      const at = gl.getAttribLocation(this.program!, name);
      if (at < 0) return;
      gl.enableVertexAttribArray(at);
      gl.vertexAttribPointer(at, size, gl.FLOAT, false, stride, offset);
    };
    attr('aPos', 3, 0); attr('aNormal', 3, 12); attr('aColor', 3, 24); attr('aEmissive', 1, 36);
    const highlight = g.item.id === this.selected ? 1 : g.item.id === this.hover ? 0.6 : 0;
    gl.uniform1f(this.uniforms.uHighlight, highlight);
    gl.drawArrays(gl.TRIANGLES, 0, g.count);
    this.stats.drawn += 1; this.stats.triangles += g.count / 3;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.raf !== null) cancelAnimationFrame(this.raf);
    this.canvas.removeEventListener('webglcontextlost', this.onLostEvent);
    this.canvas.removeEventListener('webglcontextrestored', this.onRestoredEvent);
    this.release(this.opaque); this.release(this.glass);
    if (!this.lost && this.program) this.gl.deleteProgram(this.program);
    this.opaque = []; this.glass = [];
  }
}
