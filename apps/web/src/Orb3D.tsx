import { useEffect, useRef, useState } from 'react';
import {
  crystalGeometry, ORB_CANVAS_SCALE, ORB_MOTION, orbColors, orbDpr, orbPalette, particleCount, particleSeeds, voiceLevel,
  type OrbPalette, type OrbState, type Rgb,
} from './lib/orb';

export type { OrbState } from './lib/orb';

interface Orb3DProps {
  state: OrbState;
  /** Governance signal: something is waiting for a human decision. Turns the rim and rings amber. */
  alert?: boolean;
  /** Microphone level 0..1 while listening. */
  level: number;
  size: number;
  /** Rendered when WebGL is unavailable, the context is lost, or the user prefers reduced motion. */
  fallback: React.ReactNode;
}

// Shared tone mapping (ACES fit) and gamma, so every part of the scene is lit the same way.
const TONE = /* glsl */ `
  vec3 tone(vec3 c){ c = (c*(2.51*c+0.03))/(c*(2.43*c+0.59)+0.14); return pow(clamp(c,0.0,1.0), vec3(1.0/2.2)); }
  vec3 lin(vec3 c){ return pow(c, vec3(2.2)); }
`;

// The faceted glass core: flat facets, GGX highlights from a warm key light and a coloured fill,
// a thin-film sheen that shifts with the viewing angle, light from inside and a Fresnel rim.
const coreVertex = /* glsl */ `
  attribute vec3 position; attribute vec3 normal; attribute vec3 bary; attribute float seed;
  uniform mat4 modelViewMatrix; uniform mat4 projectionMatrix; uniform mat3 normalMatrix;
  varying vec3 vNormal; varying vec3 vView; varying vec3 vBary; varying float vSeed;
  void main(){
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vNormal = normalize(normalMatrix * normal); vView = -mv.xyz; vBary = bary; vSeed = seed;
    gl_Position = projectionMatrix * mv;
  }
`;
const coreFragment = /* glsl */ `
  precision highp float;
  uniform vec3 uCore; uniform vec3 uHeart; uniform vec3 uRim; uniform vec3 uAlertColor;
  uniform float uAlert; uniform float uEnergy; uniform float uTime; uniform float uShimmer; uniform float uLevel;
  varying vec3 vNormal; varying vec3 vView; varying vec3 vBary; varying float vSeed;
  ${TONE}
  float ggx(vec3 N, vec3 V, vec3 L, float r){
    vec3 H = normalize(V + L); float a = r*r; float a2 = a*a;
    float NdH = max(dot(N,H),0.0); float d = NdH*NdH*(a2-1.0)+1.0;
    float NdL = max(dot(N,L),0.0); float NdV = max(dot(N,V),0.001);
    float k = (r+1.0)*(r+1.0)/8.0; float g = (NdL/(NdL*(1.0-k)+k))*(NdV/(NdV*(1.0-k)+k));
    float f = 0.04 + 0.96*pow(1.0-max(dot(H,V),0.0), 5.0);
    return a2/(3.14159*d*d) * g * f / max(4.0*NdL*NdV, 0.001) * NdL;
  }
  void main(){
    vec3 N = normalize(vNormal); vec3 V = normalize(vView);
    float NdV = clamp(dot(N,V), 0.0, 1.0);
    vec3 rimColor = lin(mix(uRim, uAlertColor, uAlert));
    vec3 key = normalize(vec3(-0.55, 0.75, 0.65)); vec3 fill = normalize(vec3(0.7, -0.35, 0.45));
    float shimmer = 0.5 + 0.5*sin(uTime*uShimmer + vSeed*40.0);
    vec3 film = 0.55 + 0.45*cos(6.2831*(vec3(0.0,0.33,0.67) + NdV*1.3 + vSeed*0.25 + uTime*0.04));
    vec3 base = lin(uCore) * mix(vec3(1.0), film*1.25, 0.3);
    float lit = max(dot(N,key),0.0);
    // Facets turned away from the key fall into the navy of the page: depth, not a flat pastel.
    vec3 deep = vec3(0.012, 0.018, 0.06);
    vec3 diffuse = mix(deep, base, 0.18 + 0.82*pow(lit, 1.3)) + base*rimColor*0.3*max(dot(N,fill),0.0);
    // A studio environment: the reflection picks up a light ceiling and a dark floor.
    vec3 R = reflect(-V, N);
    vec3 env = mix(vec3(0.02,0.025,0.07), mix(rimColor, vec3(1.0), 0.45), smoothstep(-0.2, 0.9, R.y));
    float fres = 0.06 + 0.94*pow(1.0 - NdV, 4.0);
    vec3 spec = vec3(1.0,0.96,0.9)*ggx(N,V,key,0.22)*1.8 + rimColor*ggx(N,V,fill,0.32)*0.9 + env*fres*0.6;
    vec3 heart = lin(uHeart) * pow(NdV, 5.0) * uEnergy * (0.45 + 0.25*shimmer + uLevel*0.6);
    float edge = 1.0 - smoothstep(0.0, 0.025, min(min(vBary.x, vBary.y), vBary.z));
    vec3 edges = mix(vec3(1.0), rimColor, 0.5) * edge * (0.07 + 0.16*shimmer*uEnergy) * (0.4 + 0.6*lit);
    vec3 rim = rimColor * pow(1.0 - NdV, 3.5) * (0.55 + uAlert*1.3);
    gl_FragColor = vec4(tone(diffuse + spec + heart + edges + rim), 1.0);
  }
`;

// Thin metallic orbit rings with a bright comet that travels round them (activity).
const ringVertex = /* glsl */ `
  attribute vec3 position; attribute vec3 normal; attribute vec2 uv;
  uniform mat4 modelViewMatrix; uniform mat4 projectionMatrix; uniform mat3 normalMatrix;
  varying vec3 vNormal; varying vec3 vView; varying float vAround;
  void main(){
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vNormal = normalize(normalMatrix * normal); vView = -mv.xyz; vAround = uv.x;
    gl_Position = projectionMatrix * mv;
  }
`;
const ringFragment = /* glsl */ `
  precision highp float;
  uniform vec3 uColor; uniform vec3 uAlertColor; uniform float uAlert; uniform float uHead; uniform float uEnergy;
  varying vec3 vNormal; varying vec3 vView; varying float vAround;
  ${TONE}
  void main(){
    vec3 N = normalize(vNormal); vec3 V = normalize(vView);
    vec3 c = lin(mix(uColor, uAlertColor, uAlert*0.7));
    vec3 key = normalize(vec3(-0.55, 0.75, 0.65));
    vec3 H = normalize(V + key);
    float spec = pow(max(dot(N,H),0.0), 60.0);
    float lambert = 0.25 + 0.6*max(dot(N,key),0.0);
    float d = fract(vAround - uHead);
    float comet = pow(1.0 - d, 10.0) * uEnergy * 2.4;
    vec3 col = c*lambert*0.8 + vec3(1.0)*spec*0.9 + c*comet;
    gl_FragColor = vec4(tone(col), 1.0);
  }
`;

// Soft round particles on inclined orbits, added to the light behind them.
const sparkVertex = /* glsl */ `
  attribute vec4 orbit;
  uniform mat4 modelViewMatrix; uniform mat4 projectionMatrix;
  uniform float uTime; uniform float uOrbit; uniform float uLevel; uniform float uSize; uniform float uSpread;
  varying float vFade;
  void main(){
    float r = orbit.x * (1.0 + uLevel*0.22*sin(orbit.y*7.0 + uTime*8.0)) * uSpread;
    float a = orbit.y + uTime*uOrbit*orbit.z;
    vec3 p = vec3(cos(a)*r, sin(a)*r*0.35, sin(a)*r);
    float ci = cos(orbit.w), si = sin(orbit.w);
    p = vec3(p.x, p.y*ci - p.z*si, p.y*si + p.z*ci);
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    vFade = 0.45 + 0.55*fract(orbit.y*3.7);
    gl_PointSize = uSize * (0.6 + vFade) * (4.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`;
const sparkFragment = /* glsl */ `
  precision highp float;
  uniform vec3 uColor; uniform float uEnergy;
  varying float vFade;
  void main(){
    vec2 q = gl_PointCoord - 0.5; float d = dot(q,q)*4.0;
    float a = exp(-d*4.0) * vFade * (0.35 + uEnergy*0.5);
    gl_FragColor = vec4(uColor * a, 0.0);
  }
`;

// The halo: a radial glow behind the core, added to the page (a bloom without a post pass).
const glowVertex = /* glsl */ `
  attribute vec3 position; attribute vec2 uv;
  uniform mat4 modelViewMatrix; uniform mat4 projectionMatrix;
  varying vec2 vUv;
  void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
`;
const glowFragment = /* glsl */ `
  precision highp float;
  uniform vec3 uInner; uniform vec3 uOuter; uniform vec3 uAlertColor; uniform float uAlert; uniform float uGlow;
  varying vec2 vUv;
  void main(){
    float d = length(vUv - 0.5) * 2.0;
    float core = exp(-d*d*5.5); float wide = exp(-d*d*2.2);
    vec3 col = uInner*core*0.55 + mix(uOuter, uAlertColor, uAlert*0.6)*wide*0.35;
    gl_FragColor = vec4(col * uGlow * smoothstep(1.0, 0.7, d), 0.0);
  }
`;

let webglSupport: boolean | null = null;

/**
 * Whether the browser can draw WebGL, probed once per page. The probe context is released at
 * once: browsers keep only a handful of live contexts (Chrome drops the oldest after 16), so a
 * probe per Voice or Call visit would push out real ones.
 */
function webglAvailable(): boolean {
  if (webglSupport !== null) return webglSupport;
  try {
    const canvas = document.createElement('canvas');
    const probe = (canvas.getContext('webgl2') ?? canvas.getContext('webgl')) as WebGLRenderingContext | null;
    webglSupport = Boolean(probe);
    probe?.getExtension('WEBGL_lose_context')?.loseContext();
  } catch {
    webglSupport = false;
  }
  return webglSupport;
}

function readTokens() {
  const style = getComputedStyle(document.documentElement);
  return orbColors((name) => style.getPropertyValue(name));
}

const ease = (from: number, to: number, rate: number) => from + (to - from) * rate;
const easeRgb = (from: Rgb, to: Rgb, rate: number) => { for (let i = 0; i < 3; i += 1) from[i] = ease(from[i], to[i], rate); };

/**
 * The voice assistant's 3D presence: a faceted glass core lit from within, two orbit rings with a
 * travelling light, a ring of particles and a halo, painted with ANUM's tokens. Each state has its
 * own colours and motion (lib/orb.ts); the microphone level swells the particles while listening;
 * a pending approval turns the rim and rings amber. The canvas reaches past the button so nothing
 * clips, and is absolutely positioned so it never shifts the layout.
 */
export function Orb3D({ state, alert = false, level, size, fallback }: Orb3DProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const live = useRef({ state, level, alert });
  live.current = { state, level, alert };
  const [mode, setMode] = useState<'loading' | '3d' | 'fallback'>('loading');

  useEffect(() => {
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (reduced || !webglAvailable()) { setMode('fallback'); return; }
    let disposed = false;
    let cleanup = () => undefined as void;

    // OGL is ~29 kB gzipped (three.js is ~4x that), loaded only when the orb is shown.
    void import('ogl').then(({ Renderer, Camera, Program, Mesh, Geometry, Torus, Plane, Transform }) => {
      if (disposed || !host.current) return;
      const canvasSize = Math.round(size * ORB_CANVAS_SCALE);
      const renderer = new Renderer({ dpr: orbDpr(window.devicePixelRatio), alpha: true, antialias: true, premultipliedAlpha: true, powerPreference: 'low-power' });
      const gl = renderer.gl;
      gl.clearColor(0, 0, 0, 0);
      renderer.setSize(canvasSize, canvasSize);
      host.current.appendChild(gl.canvas);
      const camera = new Camera(gl, { fov: 35, near: 0.1, far: 30 });
      camera.position.z = 6;
      const scene = new Transform();
      const colors = readTokens();
      const alertColor = colors.alert;
      const current: OrbPalette = JSON.parse(JSON.stringify(orbPalette(state, colors)));
      const motion = { ...ORB_MOTION[state] };

      const crystal = crystalGeometry(0.92);
      const coreGeometry = new Geometry(gl, {
        position: { size: 3, data: crystal.position }, normal: { size: 3, data: crystal.normal },
        bary: { size: 3, data: crystal.bary }, seed: { size: 1, data: crystal.seed },
      });
      const coreUniforms = {
        uCore: { value: current.core }, uHeart: { value: current.heart }, uRim: { value: current.rim }, uAlertColor: { value: alertColor },
        uAlert: { value: 0 }, uEnergy: { value: motion.energy }, uTime: { value: 0 }, uShimmer: { value: motion.shimmer }, uLevel: { value: 0 },
      };
      const coreProgram = new Program(gl, { vertex: coreVertex, fragment: coreFragment, uniforms: coreUniforms });
      const core = new Mesh(gl, { geometry: coreGeometry, program: coreProgram });
      core.setParent(scene);

      const rings = [
        { radius: 1.3, tube: 0.018, color: current.ringA, base: [1.15, 0.25, 0.1], dir: 1 },
        { radius: 1.56, tube: 0.012, color: current.ringB, base: [1.38, -0.55, 0.4], dir: -1 },
      ].map((spec) => {
        const geometry = new Torus(gl, { radius: spec.radius, tube: spec.tube, radialSegments: 10, tubularSegments: 160 });
        const uniforms = { uColor: { value: spec.color }, uAlertColor: { value: alertColor }, uAlert: { value: 0 }, uHead: { value: Math.random() }, uEnergy: { value: motion.energy } };
        const program = new Program(gl, { vertex: ringVertex, fragment: ringFragment, uniforms });
        const mesh = new Mesh(gl, { geometry, program });
        mesh.rotation.set(spec.base[0], spec.base[1], spec.base[2]);
        mesh.setParent(scene);
        return { ...spec, geometry, program, mesh, uniforms, spin: 0 };
      });

      const count = particleCount(size);
      const sparkGeometry = new Geometry(gl, { orbit: { size: 4, data: particleSeeds(count) } });
      const sparkUniforms = {
        uColor: { value: current.spark }, uTime: { value: 0 }, uOrbit: { value: motion.orbit }, uLevel: { value: 0 },
        uSize: { value: 5.5 * renderer.dpr * (size / 260) }, uEnergy: { value: motion.energy }, uSpread: { value: 1 },
      };
      const sparkProgram = new Program(gl, { vertex: sparkVertex, fragment: sparkFragment, uniforms: sparkUniforms, transparent: true, depthWrite: false });
      sparkProgram.setBlendFunc(gl.ONE, gl.ONE);
      const sparks = new Mesh(gl, { geometry: sparkGeometry, program: sparkProgram, mode: gl.POINTS, frustumCulled: false });
      sparks.setParent(scene);

      const glowGeometry = new Plane(gl, { width: 4.6, height: 4.6 });
      const glowUniforms = { uInner: { value: current.heart }, uOuter: { value: current.rim }, uAlertColor: { value: alertColor }, uAlert: { value: 0 }, uGlow: { value: motion.glow } };
      const glowProgram = new Program(gl, { vertex: glowVertex, fragment: glowFragment, uniforms: glowUniforms, transparent: true, depthWrite: false });
      glowProgram.setBlendFunc(gl.ONE, gl.ONE);
      const glow = new Mesh(gl, { geometry: glowGeometry, program: glowProgram });
      glow.position.z = -1.4;
      glow.setParent(scene);

      let frame = 0;
      let last = performance.now();
      let visible = true;
      let time = 0;
      let breath = 0;
      const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; });
      observer.observe(gl.canvas);
      const tick = (now: number) => {
        frame = requestAnimationFrame(tick);
        const { state: s, level: rawLevel, alert: warn } = live.current;
        // Idle needs no more than 30 fps; nothing renders while hidden or scrolled away.
        if (!visible || document.hidden || (s === 'idle' && now - last < 33)) return;
        const dt = Math.min(0.05, (now - last) / 1000);
        last = now;
        time += dt;
        const target = ORB_MOTION[s];
        const palette = orbPalette(s, colors);
        const voice = voiceLevel(s, rawLevel);
        const k = Math.min(1, dt * 3.5);
        for (const key of Object.keys(target) as (keyof typeof target)[]) motion[key] = ease(motion[key], target[key], k);
        for (const key of Object.keys(palette) as (keyof OrbPalette)[]) easeRgb(current[key], palette[key], Math.min(1, dt * 2.5));
        const alertTo = warn ? 1 : 0;
        coreUniforms.uAlert.value = ease(coreUniforms.uAlert.value, alertTo, Math.min(1, dt * 3));
        coreUniforms.uLevel.value = ease(coreUniforms.uLevel.value, voice, Math.min(1, dt * 10));
        coreUniforms.uTime.value = time;
        coreUniforms.uEnergy.value = motion.energy;
        coreUniforms.uShimmer.value = motion.shimmer;
        breath += dt * motion.pulseRate;
        const scale = 1 + Math.sin(breath) * motion.pulse + coreUniforms.uLevel.value * 0.06;
        core.scale.set(scale, scale, scale);
        core.rotation.y += dt * motion.spin;
        core.rotation.x = Math.sin(time * 0.35) * 0.25;
        for (const ring of rings) {
          ring.spin += dt * motion.ringSpeed * ring.dir;
          ring.mesh.rotation.x = ring.base[0] - motion.tilt * ring.dir * 0.6;
          ring.mesh.rotation.y = ring.base[1] + Math.sin(time * 0.4 + ring.dir) * 0.12;
          ring.mesh.rotation.z = ring.base[2] + ring.spin;
          ring.uniforms.uAlert.value = coreUniforms.uAlert.value;
          ring.uniforms.uEnergy.value = motion.energy;
          ring.uniforms.uHead.value = (ring.uniforms.uHead.value + dt * (0.08 + motion.ringSpeed * 0.12)) % 1;
        }
        sparkUniforms.uTime.value = time;
        sparkUniforms.uOrbit.value = motion.orbit;
        sparkUniforms.uLevel.value = coreUniforms.uLevel.value;
        sparkUniforms.uEnergy.value = motion.energy;
        sparkUniforms.uSpread.value = s === 'thinking' ? 0.88 + Math.sin(time * 2) * 0.04 : 1;
        glowUniforms.uAlert.value = coreUniforms.uAlert.value;
        glowUniforms.uGlow.value = motion.glow + coreUniforms.uLevel.value * 0.5 + coreUniforms.uAlert.value * 0.2;
        renderer.render({ scene, camera });
      };
      frame = requestAnimationFrame(tick);

      const onLost = (event: Event) => { event.preventDefault(); cancelAnimationFrame(frame); setMode('fallback'); };
      gl.canvas.addEventListener('webglcontextlost', onLost);
      setMode('3d');

      cleanup = () => {
        cancelAnimationFrame(frame);
        observer.disconnect();
        gl.canvas.removeEventListener('webglcontextlost', onLost);
        for (const geometry of [coreGeometry, sparkGeometry, glowGeometry, ...rings.map((r) => r.geometry)]) geometry.remove();
        for (const program of [coreProgram, sparkProgram, glowProgram, ...rings.map((r) => r.program)]) program.remove();
        gl.getExtension('WEBGL_lose_context')?.loseContext();
        gl.canvas.remove();
      };
    }).catch(() => setMode('fallback'));

    return () => { disposed = true; cleanup(); };
    // The scene starts from the state it mounts in; later changes ease in through `live`.
  }, [size]);

  if (mode === 'fallback') return <>{fallback}</>;
  const canvasSize = Math.round(size * ORB_CANVAS_SCALE);
  const offset = (size - canvasSize) / 2;
  return <div ref={host} className="orb3d" data-orb-state={state} data-orb-alert={alert ? 'true' : undefined}
    style={{ width: canvasSize, height: canvasSize, left: offset, top: offset }} aria-hidden="true" />;
}
