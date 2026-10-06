import { useEffect, useRef, useState } from 'react';

export type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking';

interface Orb3DProps {
  state: OrbState;
  /** Governance signal: something is waiting for a human decision. Turns the rim amber. */
  alert?: boolean;
  /** Microphone level 0..1 while listening. */
  level: number;
  size: number;
  /** Rendered when WebGL is unavailable or the user prefers reduced motion. */
  fallback: React.ReactNode;
}

const vertexShader = /* glsl */ `
  attribute vec3 position;
  attribute vec3 normal;
  uniform mat4 modelViewMatrix;
  uniform mat4 projectionMatrix;
  uniform mat3 normalMatrix;
  uniform float uTime;
  uniform float uAmp;
  uniform float uSpeed;
  varying vec3 vNormal;
  varying vec3 vView;
  varying float vNoise;

  // Simplex noise, Ashima Arts / Stefan Gustavson (MIT).
  vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
  vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
  vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
  vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
  float snoise(vec3 v){
    const vec2 C=vec2(1.0/6.0,1.0/3.0);const vec4 D=vec4(0.0,0.5,1.0,2.0);
    vec3 i=floor(v+dot(v,C.yyy));vec3 x0=v-i+dot(i,C.xxx);
    vec3 g=step(x0.yzx,x0.xyz);vec3 l=1.0-g;vec3 i1=min(g.xyz,l.zxy);vec3 i2=max(g.xyz,l.zxy);
    vec3 x1=x0-i1+C.xxx;vec3 x2=x0-i2+C.yyy;vec3 x3=x0-D.yyy;i=mod289(i);
    vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
    float n_=0.142857142857;vec3 ns=n_*D.wyz-D.xzx;vec4 j=p-49.0*floor(p*ns.z*ns.z);
    vec4 x_=floor(j*ns.z);vec4 y_=floor(j-7.0*x_);vec4 x=x_*ns.x+ns.yyyy;vec4 y=y_*ns.x+ns.yyyy;vec4 h=1.0-abs(x)-abs(y);
    vec4 b0=vec4(x.xy,y.xy);vec4 b1=vec4(x.zw,y.zw);vec4 s0=floor(b0)*2.0+1.0;vec4 s1=floor(b1)*2.0+1.0;vec4 sh=-step(h,vec4(0.0));
    vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
    vec3 p0=vec3(a0.xy,h.x);vec3 p1=vec3(a0.zw,h.y);vec3 p2=vec3(a1.xy,h.z);vec3 p3=vec3(a1.zw,h.w);
    vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
    vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);m=m*m;
    return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
  }

  void main(){
    // Low-frequency noise keeps the surface smooth, like liquid glass rather than rock.
    float n = snoise(normal * 0.9 + vec3(uTime * uSpeed));
    n += 0.25 * snoise(normal * 1.8 - vec3(uTime * uSpeed * 1.3));
    vNoise = n;
    vec3 displaced = position + normal * n * uAmp;
    vec4 mv = modelViewMatrix * vec4(displaced, 1.0);
    vNormal = normalize(normalMatrix * normal);
    vView = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`;

const fragmentShader = /* glsl */ `
  precision highp float;
  uniform vec3 uSky;
  uniform vec3 uRim;
  uniform float uAlert;
  uniform vec3 uViolet;
  uniform vec3 uRose;
  uniform float uGlow;
  varying vec3 vNormal;
  varying vec3 vView;
  varying float vNoise;

  void main(){
    float fresnel = pow(1.0 - max(dot(vNormal, vView), 0.0), 2.2);
    float t = clamp(vNoise * 0.6 + 0.5, 0.0, 1.0);
    vec3 base = mix(uSky, uViolet, smoothstep(0.2, 0.7, t));
    base = mix(base, uRose, smoothstep(0.65, 1.0, t) * 0.7);
    // Soft key light from the upper left plus a specular glint for depth.
    vec3 light = normalize(vec3(-0.5, 0.7, 0.6));
    float key = max(dot(vNormal, light), 0.0);
    float spec = pow(max(dot(reflect(-light, vNormal), vView), 0.0), 28.0);
    vec3 rim = mix(vec3(0.9, 0.92, 1.0), uRim, uAlert);
    vec3 color = base * (0.7 + 0.35 * key) + spec * 0.55 + fresnel * uGlow * rim;
    float alpha = 0.95;
    gl_FragColor = vec4(color, alpha);
  }
`;

const motion: Record<OrbState, { amp: number; speed: number; glow: number; spin: number }> = {
  idle: { amp: 0.07, speed: 0.16, glow: 0.65, spin: 0.08 },
  listening: { amp: 0.13, speed: 0.45, glow: 0.95, spin: 0.18 },
  thinking: { amp: 0.1, speed: 0.8, glow: 0.75, spin: 0.55 },
  speaking: { amp: 0.17, speed: 0.6, glow: 1.05, spin: 0.22 },
};

function webglAvailable(): boolean {
  try {
    const canvas = document.createElement('canvas');
    return Boolean(canvas.getContext('webgl2') ?? canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

/**
 * Audio-reactive 3D orb: a noise-displaced sphere with a Fresnel rim, lit from
 * the upper left. The rim turns amber while an approval is waiting.
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
    void import('ogl').then(({ Renderer, Camera, Program, Mesh, Sphere, Color }) => {
      if (disposed || !host.current) return;
      const renderer = new Renderer({ dpr: Math.min(window.devicePixelRatio, 1.5), alpha: true, antialias: true, powerPreference: 'low-power' });
      const gl = renderer.gl;
      renderer.setSize(size, size);
      host.current.appendChild(gl.canvas);
      const camera = new Camera(gl, { fov: 35 });
      camera.position.z = 4.2;
      const uniforms = {
        uTime: { value: 0 },
        uAmp: { value: motion.idle.amp },
        uSpeed: { value: motion.idle.speed },
        uGlow: { value: motion.idle.glow },
        uAlert: { value: 0 },
        uSky: { value: new Color('#7dd3fc') },
        uViolet: { value: new Color('#a78bfa') },
        uRose: { value: new Color('#f9a8d4') },
        uRim: { value: new Color('#ffb547') },
      };
      const geometry = new Sphere(gl, { radius: 1, widthSegments: 96, heightSegments: 64 });
      const program = new Program(gl, { vertex: vertexShader, fragment: fragmentShader, uniforms, transparent: true });
      const mesh = new Mesh(gl, { geometry, program });

      let frame = 0;
      let last = performance.now();
      let visible = true;
      const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; });
      observer.observe(gl.canvas);
      const tick = (now: number) => {
        frame = requestAnimationFrame(tick);
        const current = live.current;
        // Idle needs no more than 30 fps; save battery when nothing is happening.
        if (!visible || document.hidden || (current.state === 'idle' && now - last < 33)) return;
        const dt = Math.min(0.05, (now - last) / 1000);
        last = now;
        const target = motion[current.state];
        const voice = current.state === 'listening' ? current.level * 0.22 : 0;
        // Ease toward the target so state changes morph instead of jumping.
        uniforms.uAmp.value += (target.amp + voice - uniforms.uAmp.value) * Math.min(1, dt * 6);
        uniforms.uSpeed.value += (target.speed - uniforms.uSpeed.value) * Math.min(1, dt * 3);
        uniforms.uGlow.value += (target.glow + (current.alert ? 0.35 : 0) - uniforms.uGlow.value) * Math.min(1, dt * 4);
        uniforms.uAlert.value += ((current.alert ? 1 : 0) - uniforms.uAlert.value) * Math.min(1, dt * 3);
        uniforms.uTime.value += dt;
        mesh.rotation.y += dt * target.spin;
        mesh.rotation.x = Math.sin(uniforms.uTime.value * 0.3) * 0.15;
        renderer.render({ scene: mesh, camera });
      };
      frame = requestAnimationFrame(tick);
      setMode('3d');

      cleanup = () => {
        cancelAnimationFrame(frame);
        observer.disconnect();
        geometry.remove();
        program.remove();
        gl.getExtension('WEBGL_lose_context')?.loseContext();
        gl.canvas.remove();
      };
    }).catch(() => setMode('fallback'));

    return () => { disposed = true; cleanup(); };
  }, [size]);

  if (mode === 'fallback') return <>{fallback}</>;
  return <div ref={host} className="orb3d" style={{ width: size, height: size }} aria-hidden="true" />;
}
