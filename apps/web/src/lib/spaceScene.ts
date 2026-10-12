// The cinematic space scene behind the voice orb (docs/voice.md#the-space-orb), ported from the
// owner's reference (anum-space-handoff/scene.js) to a disposable renderer: one canvas, one
// requestAnimationFrame loop, a ResizeObserver and pointer listeners, all released by destroy().
//
// What is drawn: a parallax star field; photographic sprites from a local atlas (an astronaut that
// floats and turns, Earth that drifts and tilts slightly, a satellite on an ellipse around Earth
// that passes behind it on the far half and in front on the near half, a second satellite crossing
// the upper sky, a small ship); shooting stars from the first second and every 2.5 to 4 s; and the
// orb, a perspective-projected particle sphere with tilting rings, a glass shell and a glowing
// core whose colour, spin and bars follow the voice state. Earth is a sprite that drifts, not a
// globe rotating on its axis; the astronaut is a sprite, not an animated 3D character.
import {
  alphaBounds, nextMeteorDelay, particleBudget, satelliteInFront, SPACE_COLORS, spaceDpr, spaceLevel, spherePoints,
  type Crop, type SpaceMode,
} from './spaceMode.ts';

export type SpaceVariant = 'full' | 'compact';

export interface SpaceSceneOptions {
  atlasUrl: string;
  variant: SpaceVariant;
  /** Where pointer and touch tilt the orb (the stage); defaults to the canvas. */
  pointerTarget?: HTMLElement;
  /** Measured audio level 0..1 (microphone while listening, reply output while speaking). */
  getLevel?: () => number;
  /** `prefers-reduced-motion`, injectable for tests. */
  reducedMotion?: { matches: boolean } | null;
  onStatus?: (status: 'ready' | 'texture-error') => void;
}

export interface SpaceSnapshot {
  t: number;
  mode: SpaceMode;
  paused: boolean;
  frames: number;
  meteorsShown: number;
  tilt: [number, number];
  astronaut: [number, number, number];
  earth: [number, number, number];
  satellite: [number, number, boolean];
  satellite2: [number, number];
  texturesReady: boolean;
  /** The opaque bounds of the astronaut, Earth and satellite in the atlas, once it has loaded. */
  crops: Crop[];
}

export interface SpaceScene {
  setMode(mode: SpaceMode): void;
  setPaused(paused: boolean): void;
  /** Another shooting star now; false while paused or with reduced motion (nothing would move). */
  shootingStar(): boolean;
  snapshot(): SpaceSnapshot;
  destroy(): void;
}

type Vec3 = [number, number, number];

/** Creates the scene on `canvas`; returns null when 2D canvas is unavailable. */
export function createSpaceScene(canvas: HTMLCanvasElement, options: SpaceSceneOptions): SpaceScene | null {
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  const full = options.variant === 'full';
  const reduced = options.reducedMotion ?? (typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : { matches: false });
  let mode: SpaceMode = 'standby';
  let w = 1, h = 1, t = 0, last: number | null = null, raf = 0, paused = false, disposed = false, frames = 0, meteorsShown = 0;
  let tx = 0, ty = 0, rx = 0, ry = 0, level = 0, drawn = '';
  let seed = 9641;
  const random = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
  const stars = Array.from({ length: full ? 340 : 120 }, () => ({ x: random(), y: random(), z: random(), p: random() * 6.283 }));
  let points = spherePoints(particleBudget(320, 320));
  const meteors: { born: number; x: number; y: number; vx: number; vy: number; life: number }[] = [];
  let nextMeteor = 0.2;
  const echoes: { x: number; y: number; born: number }[] = [];
  const crops: Crop[] = [];
  const positions: Pick<SpaceSnapshot, 'astronaut' | 'earth' | 'satellite' | 'satellite2'> = {
    astronaut: [0, 0, 0], earth: [0, 0, 0], satellite: [0, 0, false], satellite2: [0, 0],
  };

  const atlas = new Image();
  if (full) {
    atlas.decoding = 'async';
    atlas.onload = () => {
      if (disposed) return;
      try {
        const off = document.createElement('canvas');
        off.width = atlas.naturalWidth; off.height = atlas.naturalHeight;
        const c = off.getContext('2d', { willReadFrequently: true });
        if (!c) throw new Error('no 2d');
        c.drawImage(atlas, 0, 0);
        const pixels = c.getImageData(0, 0, off.width, off.height).data;
        for (const part of [0, 1, 2] as const) crops[part] = alphaBounds(pixels, off.width, off.height, part);
        off.width = off.height = 0;
        options.onStatus?.('ready');
      } catch {
        options.onStatus?.('texture-error');
      }
    };
    atlas.onerror = () => { if (!disposed) options.onStatus?.('texture-error'); };
    atlas.src = options.atlasUrl;
  }

  function resize() {
    w = Math.max(1, canvas.clientWidth); h = Math.max(1, canvas.clientHeight);
    const d = spaceDpr(window.devicePixelRatio);
    canvas.width = Math.round(w * d); canvas.height = Math.round(h * d);
    ctx!.setTransform(d, 0, 0, d, 0, 0);
    points = spherePoints(particleBudget(w, h));
  }
  const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : null;
  observer?.observe(canvas);
  resize();

  const rgba = (a: number) => `rgba(${SPACE_COLORS[mode].join(',')},${a})`;
  // Orb geometry: centre and radius; the full scene leaves room for the sprites around it.
  const orbCentre = (): [number, number, number] => full
    ? [w * 0.51 + rx * 12, h * 0.45 + ry * 10, Math.min(w * 0.2, h * 0.205)]
    : [w * 0.5 + rx * 6, h * 0.5 + ry * 6, Math.min(w, h) * 0.3];

  function sprite(index: number, x: number, y: number, width: number, angle = 0, alpha = 1) {
    const c = crops[index];
    if (!c) return;
    const height = (width * c.h) / c.w;
    ctx!.save(); ctx!.translate(x, y); ctx!.rotate(angle); ctx!.globalAlpha = alpha;
    ctx!.drawImage(atlas, c.x, c.y, c.w, c.h, -width / 2, -height / 2, width, height);
    ctx!.restore();
  }

  const BANDS = 8;
  const band = (alpha: number, low: number, high: number) => Math.max(0, Math.min(BANDS - 1, Math.floor(((alpha - low) / (high - low)) * BANDS)));
  /** Fills each band's circles (x, y, radius triples) as one path, at the band's middle alpha. */
  function fillBands(bands: number[][], low: number, high: number, colour: (alpha: number) => string) {
    const c = ctx!;
    bands.forEach((dots, i) => {
      if (!dots.length) return;
      c.fillStyle = colour(low + ((i + 0.5) / BANDS) * (high - low));
      c.beginPath();
      for (let k = 0; k < dots.length; k += 3) { c.moveTo(dots[k] + dots[k + 2], dots[k + 1]); c.arc(dots[k], dots[k + 1], dots[k + 2], 0, 6.283); }
      c.fill();
    });
  }

  function background() {
    if (full) {
      ctx!.fillStyle = '#030710'; ctx!.fillRect(0, 0, w, h);
      return;
    }
    // The compact orb sits in a round window of space, readable on light and dark pages.
    const [cx, cy] = [w / 2, h / 2], r = Math.min(w, h) / 2;
    ctx!.beginPath(); ctx!.arc(cx, cy, r, 0, 6.283); ctx!.clip();
    const space = ctx!.createRadialGradient(cx - r * 0.3, cy - r * 0.35, 0, cx, cy, r);
    space.addColorStop(0, '#0d172b'); space.addColorStop(0.6, '#040711'); space.addColorStop(1, '#010308');
    ctx!.fillStyle = space; ctx!.fillRect(0, 0, w, h);
  }

  function starField() {
    const c = ctx!;
    // Stars and particles are filled in a few alpha bands, one path each, instead of one fill per
    // dot: the same picture at a fraction of the cost on software-rendered canvases.
    const bands: number[][] = Array.from({ length: BANDS }, () => []);
    for (const p of stars) {
      const x = (p.x * w - rx * (4 + 15 * p.z) + w) % w, y = (p.y * h - ry * (3 + 9 * p.z) + h) % h;
      const alpha = 0.12 + p.z * 0.42 + 0.1 * Math.sin(t * 0.9 + p.p);
      bands[band(alpha, 0.02, 0.64)].push(x, y, 0.3 + p.z * 0.9);
    }
    fillBands(bands, 0.02, 0.64, (a) => `rgba(207,224,245,${a})`);
    if (full) {
      const nebula = c.createRadialGradient(w * 0.15, h * 0.2, 0, w * 0.15, h * 0.2, w * 0.4);
      nebula.addColorStop(0, '#34609b12'); nebula.addColorStop(0.45, '#4938690a'); nebula.addColorStop(1, '#00000000');
      c.fillStyle = nebula; c.fillRect(0, 0, Math.min(w, w * 0.55), Math.min(h, h * 0.2 + w * 0.4));
    }
  }

  function spawnMeteor() {
    const scale = full ? 1 : 0.6;
    meteors.push({ born: t, x: w * (0.08 + random() * 0.56), y: h * (0.02 + random() * 0.35), vx: w * (0.38 + random() * 0.23) * scale, vy: h * (0.22 + random() * 0.17) * scale, life: 1.3 });
    meteorsShown += 1;
    if (meteors.length > 8) meteors.shift();
  }

  function shootingStars() {
    const c = ctx!;
    for (let i = meteors.length - 1; i >= 0; i -= 1) {
      const m = meteors[i], age = t - m.born;
      if (age > m.life || age < 0) { meteors.splice(i, 1); continue; }
      const x = m.x + age * m.vx, y = m.y + age * m.vy, tail = Math.min(age, 0.2), xx = x - m.vx * tail, yy = y - m.vy * tail;
      c.save();
      c.globalAlpha = Math.min(1, age * 9) * (1 - age / m.life);
      const g = c.createLinearGradient(xx, yy, x, y);
      g.addColorStop(0, '#b9eaff00'); g.addColorStop(0.8, '#b9eaffa0'); g.addColorStop(1, '#f2fdff');
      c.strokeStyle = g; c.lineWidth = 1.6; c.shadowColor = '#90dfff'; c.shadowBlur = 7;
      c.beginPath(); c.moveTo(xx, yy); c.lineTo(x, y); c.stroke();
      c.fillStyle = '#edfbff'; c.beginPath(); c.arc(x, y, 1.8, 0, 6.283); c.fill();
      c.restore();
    }
  }

  function spaceObjects() {
    const c = ctx!;
    const earthX = w * 0.79 - rx * 11, earthY = h * 0.77 + Math.sin(t * 0.12) * 7 - ry * 8, earthSize = Math.min(w * 0.27, 165), a = t * 0.34;
    const satX = earthX + Math.cos(a) * earthSize * 0.73, satY = earthY + Math.sin(a) * earthSize * 0.3, front = satelliteInFront(a);
    c.save(); c.strokeStyle = '#86baff22'; c.lineWidth = 0.7; c.beginPath(); c.ellipse(earthX, earthY, earthSize * 0.73, earthSize * 0.3, 0, 0, 6.283); c.stroke(); c.restore();
    if (!front) sprite(2, satX, satY, Math.min(w * 0.13, 72), Math.sin(a) * 0.25, 0.65);
    const earthAngle = Math.sin(t * 0.065) * 0.045;
    sprite(1, earthX, earthY, earthSize, earthAngle);
    if (front) sprite(2, satX, satY, Math.min(w * 0.16, 92), Math.sin(a) * 0.25);
    const astroX = w * 0.13 + Math.sin(t * 0.3) * Math.min(w * 0.025, 15) - rx * 18, astroY = h * 0.48 + Math.sin(t * 0.42) * 15 - ry * 12;
    const astroAngle = Math.sin(t * 0.21) * 0.13 - 0.12;
    sprite(0, astroX, astroY, Math.min(w * 0.19, 116), astroAngle);
    const sat2X = ((w * 0.8 + t * 13) % (w + 120)) - 60, sat2Y = h * 0.16 + Math.sin(t * 0.23) * 13 - ry * 6;
    sprite(2, sat2X, sat2Y, Math.min(w * 0.16, 90), -0.2 + Math.sin(t * 0.12) * 0.15, 0.9);
    const shipX = ((t * 26 + w * 0.4) % (w + 100)) - 50, shipY = h * 0.89 + Math.sin(t * 0.17) * 9;
    c.save();
    const exhaust = c.createLinearGradient(shipX - 35, shipY, shipX, shipY);
    exhaust.addColorStop(0, '#86dfff00'); exhaust.addColorStop(1, '#86dfff80');
    c.strokeStyle = exhaust; c.lineWidth = 1; c.beginPath(); c.moveTo(shipX - 35, shipY); c.lineTo(shipX, shipY); c.stroke();
    c.fillStyle = '#b2bdc8'; c.beginPath(); c.moveTo(shipX + 7, shipY); c.lineTo(shipX - 5, shipY - 2); c.lineTo(shipX - 5, shipY + 2); c.fill();
    c.restore();
    positions.astronaut = [astroX, astroY, astroAngle];
    positions.earth = [earthX, earthY, earthAngle];
    positions.satellite = [satX, satY, front];
    positions.satellite2 = [sat2X, sat2Y];
  }

  const rotate = (p: Vec3, a: number, b: number): Vec3 => {
    const x = p[0] * Math.cos(b) + p[2] * Math.sin(b), z = p[2] * Math.cos(b) - p[0] * Math.sin(b);
    return [x, p[1] * Math.cos(a) - z * Math.sin(a), p[1] * Math.sin(a) + z * Math.cos(a)];
  };
  const project = (p: Vec3, r: number, cx: number, cy: number): Vec3 => { const k = 3.8 / (3.8 - p[2]); return [cx + p[0] * r * k, cy + p[1] * r * k, k]; };

  function ring(r: number, a: number, b: number, phase: number, opacity: number, cx: number, cy: number) {
    const c = ctx!;
    c.strokeStyle = rgba(opacity); c.lineWidth = 0.9; c.beginPath();
    for (let i = 0; i <= 120; i += 1) {
      const angle = (i / 120) * 6.283, p = project(rotate([Math.cos(angle), Math.sin(angle), 0], a + ry * 0.45, b + rx * 0.5), r, cx, cy);
      if (i) c.lineTo(p[0], p[1]); else c.moveTo(p[0], p[1]);
    }
    c.stroke();
    const p = project(rotate([Math.cos(phase), Math.sin(phase), 0], a + ry * 0.45, b + rx * 0.5), r, cx, cy);
    c.fillStyle = rgba(0.9); c.beginPath(); c.arc(p[0], p[1], 2, 0, 6.283); c.fill();
  }

  function orb() {
    const c = ctx!;
    const [cx, cy, r] = orbCentre(), pulse = 1 + level * 0.035;
    c.save();
    const haze = c.createRadialGradient(cx, cy, 0, cx, cy, r * 1.8);
    haze.addColorStop(0, rgba(0.08 + level * 0.08)); haze.addColorStop(0.5, rgba(0.04)); haze.addColorStop(1, rgba(0));
    c.fillStyle = haze; c.fillRect(cx - r * 1.8, cy - r * 1.8, r * 3.6, r * 3.6);
    ring(r * 1.48, 0.9, 0.2, t * 0.24, 0.2, cx, cy);
    ring(r * 1.28, -0.65, 0.7, -t * 0.3, 0.42, cx, cy);
    ring(r * 1.65, 1.25, -0.5, t * 0.15, 0.16, cx, cy);
    const surface = c.createRadialGradient(cx - r * 0.3, cy - r * 0.4, 0, cx, cy, r);
    surface.addColorStop(0, '#24627444'); surface.addColorStop(0.65, '#06162399'); surface.addColorStop(0.95, '#12344199'); surface.addColorStop(1, rgba(0.35));
    c.fillStyle = surface; c.beginPath(); c.arc(cx, cy, r * pulse, 0, 6.283); c.fill();
    const speed = mode === 'thinking' ? 0.32 : mode === 'standby' || mode === 'alert' ? 0.07 : 0.17;
    const data = points.map((p) => rotate(p, ry * 0.55 + 0.13, rx * 0.65 + t * speed)).sort((a, b) => a[2] - b[2]);
    c.globalCompositeOperation = 'lighter';
    const bands: number[][] = Array.from({ length: BANDS }, () => []);
    for (const p of data) {
      const q = project(p, r * pulse, cx, cy);
      bands[band(0.12 + (p[2] + 1) * 0.3, 0.12, 0.72)].push(q[0], q[1], (0.6 + (p[2] + 1) * 0.4) * q[2]);
    }
    fillBands(bands, 0.12, 0.72, rgba);
    for (let j = 0; j < 3; j += 1) ring(r * (0.32 + j * 0.2), j * 0.6 + t * 0.04, 0.3 + j * 0.3, t * 0.2, 0.15, cx, cy);
    const glow = c.createRadialGradient(cx, cy, 0, cx, cy, r * 0.35);
    glow.addColorStop(0, rgba(0.65 + level * 0.25)); glow.addColorStop(0.2, rgba(0.2)); glow.addColorStop(1, rgba(0));
    c.fillStyle = glow; c.fillRect(cx - r, cy - r, r * 2, r * 2);
    for (let i = 0; i < 50; i += 1) {
      const a = (i / 50) * 6.283;
      const len = mode === 'standby' || mode === 'alert' ? 2 : mode === 'thinking' ? 3 + 8 * (0.5 + 0.5 * Math.sin(a * 3 - t * 3)) : 3 + level * 13 * Math.abs(Math.sin(i * 0.65 + t * 5));
      c.strokeStyle = rgba(0.55); c.lineWidth = 1.5; c.beginPath();
      c.moveTo(cx + Math.cos(a) * r * 1.1, cy + Math.sin(a) * r * 1.1);
      c.lineTo(cx + Math.cos(a) * (r * 1.1 + len), cy + Math.sin(a) * (r * 1.1 + len));
      c.stroke();
    }
    c.restore();
  }

  function signals() {
    const c = ctx!;
    for (let i = echoes.length - 1; i >= 0; i -= 1) {
      const e = echoes[i], age = t - e.born;
      if (age > 2 || age < 0) { echoes.splice(i, 1); continue; }
      c.strokeStyle = rgba((1 - age / 2) * 0.45); c.lineWidth = 1;
      c.beginPath(); c.arc(e.x, e.y, age * 65 + 3, 0, 6.283); c.stroke();
    }
  }

  function frame(now: number) {
    if (disposed) return;
    raf = requestAnimationFrame(frame);
    if (document.hidden) { last = null; return; }
    if (last === null) last = now;
    // Capped so a long stall (a breakpoint, a busy main thread) never jumps the scene; low but steady
    // frame rates still move in real time.
    const dt = Math.min((now - last) / 1000, 0.25);
    last = now;
    const moving = !paused && !reduced.matches;
    if (moving) t += dt;
    const ease = reduced.matches ? 1 : 0.08;
    rx += (tx - rx) * ease; ry += (ty - ry) * ease;
    level = spaceLevel(mode, options.getLevel?.());
    if (moving && t >= nextMeteor) { spawnMeteor(); nextMeteor = t + nextMeteorDelay(random()); }
    // A still scene (paused, reduced motion) is drawn again only when something visible changed.
    const still = `${w}x${h}|${mode}|${level.toFixed(3)}|${rx.toFixed(4)},${ry.toFixed(4)}|${crops.length}|${meteors.length}|${echoes.length}|${t}`;
    if (!moving && still === drawn) return;
    drawn = still;
    const c = ctx!;
    c.save();
    c.setTransform(spaceDpr(window.devicePixelRatio), 0, 0, spaceDpr(window.devicePixelRatio), 0, 0);
    c.clearRect(0, 0, w, h);
    background();
    starField();
    if (full) spaceObjects();
    orb();
    shootingStars();
    signals();
    c.restore();
    frames += 1;
  }

  const listeners: [EventTarget, string, EventListener][] = [];
  const on = (el: EventTarget, event: string, fn: EventListener) => { el.addEventListener(event, fn); listeners.push([el, event, fn]); };
  const target = options.pointerTarget ?? canvas;
  const point = (e: PointerEvent) => {
    const b = canvas.getBoundingClientRect();
    return [e.clientX - b.left, e.clientY - b.top] as const;
  };
  on(target, 'pointermove', ((e: PointerEvent) => {
    const [x, y] = point(e);
    tx = Math.max(-1, Math.min(1, (x / w) * 2 - 1)); ty = Math.max(-1, Math.min(1, (y / h) * 2 - 1));
  }) as EventListener);
  on(target, 'pointerleave', (() => { tx = ty = 0; }) as EventListener);
  on(target, 'pointerdown', ((e: PointerEvent) => {
    const [x, y] = point(e);
    tx = Math.max(-1, Math.min(1, (x / w) * 2 - 1)); ty = Math.max(-1, Math.min(1, (y / h) * 2 - 1));
    echoes.push({ x, y, born: t });
    if (echoes.length > 6) echoes.shift();
  }) as EventListener);
  on(document, 'visibilitychange', (() => { last = null; }) as EventListener);

  raf = requestAnimationFrame(frame);

  return {
    setMode(next) { if (SPACE_COLORS[next]) mode = next; },
    setPaused(next) { paused = next; last = null; },
    shootingStar() {
      if (paused || reduced.matches) return false;
      spawnMeteor();
      return true;
    },
    snapshot() {
      return {
        t, mode, paused, frames, meteorsShown, tilt: [rx, ry], texturesReady: crops.length === 3, crops: crops.map((c) => ({ ...c })), ...positions,
        astronaut: [...positions.astronaut] as [number, number, number], earth: [...positions.earth] as [number, number, number],
        satellite: [...positions.satellite] as [number, number, boolean], satellite2: [...positions.satellite2] as [number, number],
      };
    },
    destroy() {
      if (disposed) return;
      disposed = true;
      cancelAnimationFrame(raf);
      observer?.disconnect();
      for (const [el, event, fn] of listeners) el.removeEventListener(event, fn);
      listeners.length = 0;
      atlas.onload = null; atlas.onerror = null;
      if (full) atlas.removeAttribute('src');
      meteors.length = 0; echoes.length = 0;
      canvas.width = canvas.height = 0;
    },
  };
}
