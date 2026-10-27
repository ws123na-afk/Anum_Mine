/**
 * The 3D stage shared by the campus and the building cutaway: one canvas, camera controls (mouse,
 * touch, wheel, keyboard), picking, day and night, and HTML label cards pinned to 3D points.
 *
 * Labels are real DOM (buttons and cards), positioned from the renderer's frame callback without
 * a React re-render per frame. The canvas itself is decorative for assistive technology: the same
 * things are reachable through the label buttons and the List view.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode, type RefObject } from 'react';
import { readNightPreset, subscribeNightPreset, targetBlend } from './lib/hqLightPreset.ts';
import type { HqRenderer } from './lib/hqThreeRenderer.ts';
import type { RenderScene } from './lib/hqRenderTypes.ts';
import type { GlassPlanData } from './lib/hqPlanAtlas.ts';
import { setGpuState } from './lib/hqGpuState.ts';
import { setLiveRenderer } from './lib/hqGpuBridge.ts';
import { emitGpuTelemetry } from './lib/hqGpuTelemetry.ts';
import { tierById } from './lib/hqQualityTiers.ts';
import { pickNearest, type Aabb, type PickTarget } from './lib/hqMath.ts';
import type { Density, Rect, placeLabels as PlaceLabels } from './lib/hqLabels.ts';
import { cameraEye, dolly, isCameraAtRest, orbit, pan, screenRay, stepCamera, type CameraState } from './lib/headquartersSpatial.ts';
import { alignYaw, applyCameraAction, keyToCameraAction } from './lib/hqCamera.ts';
import type { Vec3 } from './lib/headquartersProjection.ts';
import { t } from './lib/i18n.ts';

// Started with the stage's own chunk, so the placement code arrives while the renderer loads.
const labelsLoad = import('./lib/hqLabels.ts');

export type StageLabel = Readonly<{
  id: string;
  anchor: Vec3;
  node: ReactNode;
  /** Hide the label when the camera is farther than this from the anchor. */
  maxDistance?: number;
  /** Lower numbers are drawn on top when labels overlap. */
  priority?: number;
  /** The densities the label may take, richest first (docs/ANUM_MINE_HQ_LABELS.md). */
  tiers?: readonly Density[];
  /** Never hidden for lack of room (see `LabelInput.keep`). */
  keep?: boolean;
  /** Solid boxes that hide the label while they stand between the eye and the anchor. */
  occluders?: readonly Aabb[];
  /** Other anchors for the same label; the one nearest the eye is used. */
  alternatives?: readonly Vec3[];
  /** A large card (a billboard): the placement searches in small steps for a free spot. */
  fine?: boolean;
}>;

export type StageStatus = 'ok' | 'recovering';

type Props = Readonly<{
  label: string;
  description: string;
  scene: RenderScene | null;
  /** The plan atlas the glass looks through (campus only). Changing it never rebuilds the scene meshes. */
  glassPlan?: GlassPlanData | null;
  pickTargets: readonly PickTarget[];
  camera: CameraState;
  homeCamera: CameraState;
  onCamera: (camera: CameraState) => void;
  night: boolean;
  reducedMotion: boolean;
  hoverId: string | null;
  selectedId: string | null;
  onHover: (id: string | null) => void;
  onPick: (id: string | null) => void;
  labels: readonly StageLabel[];
  /**
   * A second, separate pass of cards (the billboards): placed after the main cards by their own call, round the main cards and the
   * panels named by `extraKeepOut`, and never able to move, hide or park a main card.
   */
  extraLabels?: readonly StageLabel[];
  extraKeepOut?: string;
  /** Dragging with the primary button moves the scene sideways instead of turning it (the toolbar's Pan). */
  panMode?: boolean;
  /** Elements next to the stage (CSS selector, searched in the stage's parent) that cards keep clear of: the controls drawn over the scene. */
  labelKeepOut?: string;
  /** Called with the footprint inputs on every drawn frame (the minimap listens). */
  onView?: (camera: CameraState, aspect: number) => void;
  onUnavailable: (reason: string) => void;
  /** Called inside each drawn frame with the canvas (the navigation layer takes thumbnails here). */
  onCanvasFrame?: (canvas: HTMLCanvasElement) => void;
  className?: string;
  children?: ReactNode;
}>;

/** Hands the plan atlas to the renderer; the plan shader (its own lazy chunk) is fetched the first time a scene has a plan, and the renderer then draws it. */
function giveGlassPlan(r: HqRenderer | null, plan: GlassPlanData | null | undefined, stillWanted: () => boolean): void {
  if (!r || !plan) { if (r) r.stats.glassPlan = ''; return; }
  void import('./lib/hqGlassShader.ts').then((m) => { if (stillWanted()) m.applyPlan(r.glassHooks(), plan); }).catch(() => undefined);
}

type Pointer = { x: number; y: number };

export function HqStage(props: Props) {
  const { scene, pickTargets, camera, homeCamera, night, reducedMotion, labels } = props;
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const renderer = useRef<HqRenderer | null>(null);
  const live = useRef<CameraState>(camera);
  const goal = useRef<CameraState>(camera);
  const emitted = useRef(new WeakSet<object>());
  const preset = useSyncExternalStore(subscribeNightPreset, readNightPreset, () => 'night' as const);
  const nightNow = useRef(targetBlend(night, readNightPreset()));
  const anim = useRef<number | null>(null);
  const lastTick = useRef(0);
  const labelEls = useRef(new Map<string, HTMLDivElement>());
  const labelSizes = useRef(new Map<string, { w: number; h: number }>());
  const labelsRef = useRef(labels);
  const extraRef = useRef<readonly StageLabel[]>([]);
  const extraEls = useRef(new Map<string, HTMLDivElement>());
  const extraSizes = useRef(new Map<string, { w: number; h: number }>());
  const extraHost = useRef<HTMLDivElement>(null);
  const keepOut = useRef<readonly Rect[]>([]);
  const lastModelSig = useRef('');
  const size = useRef({ width: 1, height: 1 });
  const pointers = useRef(new Map<number, Pointer>());
  const gesture = useRef<{ moved: boolean; button: number; startX: number; startY: number; pinch: number } | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  const [status, setStatus] = useState<StageStatus>('ok');
  const lostTimer = useRef<number | null>(null);
  labelsRef.current = labels;
  extraRef.current = props.extraLabels ?? [];

  const emit = useCallback((next: CameraState) => {
    live.current = next;
    goal.current = next;
    emitted.current.add(next);
    renderer.current?.setCamera(next);
    propsRef.current.onCamera(next);
  }, []);

  // Renderer lifecycle. three.js is loaded on demand, so the page, the list view and the no-WebGL fallback never download it.
  useEffect(() => {
    const el = canvas.current;
    if (!el) return undefined;
    let cancelled = false;
    let created: HqRenderer | null = null;
    let resizeObserver: ResizeObserver | null = null;
    // The label placement loads with the renderer: nothing is placed before the scene draws, so it stays out of the page's own chunk.
    let placeLabels: typeof PlaceLabels | null = null;
    let stopKeepOut: (() => void) | null = null;
    Promise.all([import('./lib/hqThreeRenderer.ts'), labelsLoad]).then(([{ HqRenderer: Renderer }, labelLib]) => {
      if (cancelled) return;
      placeLabels = labelLib.placeLabels;
      // The controls over the scene are read when they change size (never per frame), so a card is not drawn under one.
      if (propsRef.current.labelKeepOut && host.current) stopKeepOut = labelLib.bindKeepOut(host.current, propsRef.current.labelKeepOut, (rects) => { keepOut.current = rects; created?.requestFrame(); });
      const r: HqRenderer | null = Renderer.create(el, {
        onQuality: (state) => { setGpuState(state); emitGpuTelemetry({ kind: 'quality', tier: tierById(state.tier).key, step: state.step, reason: state.reason }); },
        onLost: () => {
          setStatus('recovering');
          emitGpuTelemetry({ kind: 'context-lost' });
          lostTimer.current = window.setTimeout(() => propsRef.current.onUnavailable('context-lost'), 8000);
        },
        onRestored: () => {
          if (lostTimer.current !== null) window.clearTimeout(lostTimer.current);
          setStatus('ok');
          emitGpuTelemetry({ kind: 'context-restored' });
          if (propsRef.current.scene) renderer.current?.setScene(propsRef.current.scene);
        },
        onError: (error) => propsRef.current.onUnavailable(error.message),
        onFrame: (vp, aspect) => {
          const { width, height } = size.current;
          placeLabels?.(labelsRef.current, labelEls.current, labelSizes.current, vp, width, height, 64, cameraEye(live.current), keepOut.current);
          if (extraRef.current.length && placeLabels && host.current) {
            // The billboard pass: it reads where the main cards and the panels are and keeps round them; the main pass above never sees it.
            const box = host.current.getBoundingClientRect();
            const around: Rect[] = [];
            const add = (el: Element) => {
              const r = el.getBoundingClientRect();
              if (r.width > 14 && r.height > 14) around.push({ x0: r.left - box.left - 4, y0: r.top - box.top - 4, x1: r.right - box.left + 4, y1: r.bottom - box.top + 4 });
            };
            labelEls.current.forEach((el) => { if (el.style.display !== 'none' && el.dataset.hidden !== 'crowded') add(el); });
            const sel = propsRef.current.extraKeepOut;
            if (sel) host.current.parentElement?.querySelectorAll(sel).forEach(add);
            if (extraHost.current) { const w = width >= 700 ? '1' : '0'; if (extraHost.current.dataset.wide !== w) extraHost.current.dataset.wide = w; }
            placeLabels(extraRef.current, extraEls.current, extraSizes.current, vp, width, height, 64, cameraEye(live.current), around);
          }
          propsRef.current.onView?.(live.current, aspect);
          if (canvas.current) propsRef.current.onCanvasFrame?.(canvas.current);
          const s = created?.stats;
          const modelSig = s ? `${s.models.placed}/${s.models.figures}/${s.models.loaded}/${s.models.asked}/${s.models.fallbacks.join(',')}/${s.models.looks.join('|')}` : '';
          const modelsChanged = modelSig !== lastModelSig.current;
          if (modelsChanged) lastModelSig.current = modelSig;
          if (s && (s.frames === 1 || s.frames % 10 === 0 || modelsChanged || s.glassPlan !== (canvas.current?.dataset.glassPlan ?? '')) && canvas.current) {
            canvas.current.dataset.drawMs = s.avgDrawMs.toFixed(2);
            canvas.current.dataset.triangles = String(Math.round(s.triangles));
            canvas.current.dataset.drawn = String(s.drawn);
            canvas.current.dataset.culled = String(s.culled);
            canvas.current.dataset.dpr = s.dpr.toFixed(2);
            canvas.current.dataset.shadow = String(s.shadowSize);
            canvas.current.dataset.bloom = s.bloom ? 'true' : 'false';
            canvas.current.dataset.tier = String(s.tier);
            canvas.current.dataset.fx = s.fx;
            canvas.current.dataset.glassPlan = s.glassPlan;
            canvas.current.dataset.sceneryLevel = String(s.scenery.level);
            canvas.current.dataset.sceneryFrames = String(s.scenery.frames);
            canvas.current.dataset.sceneryClock = s.scenery.clock.toFixed(2);
            canvas.current.dataset.sceneryVehicles = String(s.scenery.vehicles);
            canvas.current.dataset.ambient = s.ambientMoving ? String(s.ambient) + ' moving' : String(s.ambient);
            canvas.current.dataset.modelsPlaced = String(s.models.placed);
            canvas.current.dataset.modelFigures = String(s.models.figures);
            canvas.current.dataset.modelKit = String(s.models.kit ?? 0);
            // Avatar styles drawn: build/hair/outfit/skin per figure (codes only, never a name), and a count per build.
            canvas.current.dataset.figureLooks = [...s.models.looks].sort().join(' ');
            canvas.current.dataset.figureStyles = ['feminine', 'masculine', 'neutral'].map((b) => `${b}:${s.models.looks.filter((l) => l.startsWith(`${b}/`)).length}`).join(',');
            canvas.current.dataset.modelsLoaded = `${s.models.loaded}/${s.models.asked}`;
            canvas.current.dataset.modelBytes = String(s.models.bytes);
            canvas.current.dataset.modelLoadMs = String(s.models.loadMs);
            canvas.current.dataset.modelFallbacks = s.models.fallbacks.join(',');
          }
        },
      });
      if (!r) { propsRef.current.onUnavailable('webgl-unavailable'); return; }
      created = r;
      renderer.current = r;
      setLiveRenderer(r);
      r.setReducedMotion(propsRef.current.reducedMotion);
      r.setCamera(live.current);
      r.setNight(nightNow.current);
      if (propsRef.current.scene) r.setScene(propsRef.current.scene);
      { const plan = propsRef.current.glassPlan; giveGlassPlan(r, plan, () => propsRef.current.glassPlan === plan); }
      r.setHighlight(propsRef.current.hoverId, propsRef.current.selectedId);
      resizeObserver = new ResizeObserver(() => {
        const box = el.getBoundingClientRect();
        size.current = { width: Math.max(1, box.width), height: Math.max(1, box.height) };
        r.requestFrame();
      });
      resizeObserver.observe(el);
    }).catch((error: unknown) => {
      if (!cancelled) propsRef.current.onUnavailable(error instanceof Error ? error.message : 'webgl-unavailable');
    });
    return () => {
      cancelled = true;
      resizeObserver?.disconnect();
      stopKeepOut?.();
      if (lostTimer.current !== null) window.clearTimeout(lostTimer.current);
      created?.dispose();
      renderer.current = null;
      setLiveRenderer(null);
      setGpuState(null);
    };
    // The renderer is created once per mounted stage.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { renderer.current?.setReducedMotion(reducedMotion); }, [reducedMotion]);
  useEffect(() => { if (scene) renderer.current?.setScene(scene); }, [scene]);
  // The revision of the plan atlas the scene asks for (the drawn one is `data-glass-plan`): a layout or presence change moves it.
  useEffect(() => {
    const plan = props.glassPlan;
    if (canvas.current) canvas.current.dataset.glassPlanWanted = plan?.revision ?? '';
    giveGlassPlan(renderer.current, plan, () => propsRef.current.glassPlan === plan);
  }, [props.glassPlan]);
  useEffect(() => { renderer.current?.setHighlight(props.hoverId, props.selectedId); }, [props.hoverId, props.selectedId]);
  useLayoutEffect(() => { labelSizes.current.clear(); renderer.current?.requestFrame(); }, [labels]);
  useLayoutEffect(() => { extraSizes.current.clear(); renderer.current?.requestFrame(); }, [props.extraLabels]);

  // Camera changes from the controls tween; changes the stage itself emitted are already in place.
  useEffect(() => {
    if (emitted.current.has(camera)) return;
    const aligned = alignYaw(live.current, camera);
    goal.current = aligned;
    if (reducedMotion) { live.current = aligned; renderer.current?.setCamera(aligned); return; }
    if (anim.current !== null) return;
    lastTick.current = performance.now();
    const step = (now: number) => {
      const dt = now - lastTick.current;
      lastTick.current = now;
      if (isCameraAtRest(live.current, goal.current, 0.02)) {
        live.current = goal.current;
        renderer.current?.setCamera(live.current);
        anim.current = null;
        return;
      }
      live.current = stepCamera(live.current, goal.current, dt, 110);
      renderer.current?.setCamera(live.current);
      anim.current = requestAnimationFrame(step);
    };
    anim.current = requestAnimationFrame(step);
  }, [camera, reducedMotion]);
  useEffect(() => () => { if (anim.current !== null) cancelAnimationFrame(anim.current); }, []);

  // Day and night blend.
  useEffect(() => {
    const to = targetBlend(night, preset);
    if (reducedMotion) { nightNow.current = to; renderer.current?.setNight(to); return; }
    let raf = 0;
    let last = performance.now();
    const step = (now: number) => {
      const dt = now - last; last = now;
      const delta = to - nightNow.current;
      if (Math.abs(delta) < 0.01) { nightNow.current = to; renderer.current?.setNight(to); return; }
      nightNow.current += Math.sign(delta) * Math.min(Math.abs(delta), dt / 500);
      renderer.current?.setNight(nightNow.current);
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [night, preset, reducedMotion]);

  // Wheel zoom needs a non-passive listener to stop the page from scrolling.
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      emit(dolly(live.current, event.deltaMode === 1 ? event.deltaY * 33 : event.deltaY));
    };
    el.addEventListener('wheel', wheel, { passive: false });
    return () => el.removeEventListener('wheel', wheel);
  }, [emit]);

  const pickAt = useCallback((clientX: number, clientY: number): string | null => {
    const el = canvas.current;
    if (!el) return null;
    const box = el.getBoundingClientRect();
    const nx = ((clientX - box.left) / box.width - 0.5) * 2;
    const ny = (0.5 - (clientY - box.top) / box.height) * 2;
    const ray = screenRay(live.current, nx, ny, box.width / box.height);
    return pickNearest(ray, propsRef.current.pickTargets)?.id ?? null;
  }, []);

  const down = (event: React.PointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const pinch = pointers.current.size === 2 ? distanceBetween(pointers.current) : 0;
    gesture.current = { moved: false, button: event.button, startX: event.clientX, startY: event.clientY, pinch };
    host.current?.focus({ preventScroll: true });
  };
  const move = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const g = gesture.current;
    const prev = pointers.current.get(event.pointerId);
    if (!g || !prev) {
      const id = event.pointerType === 'mouse' ? pickAt(event.clientX, event.clientY) : null;
      if (event.pointerType === 'mouse' && id !== propsRef.current.hoverId) propsRef.current.onHover(id);
      return;
    }
    const dx = event.clientX - prev.x, dy = event.clientY - prev.y;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (Math.hypot(event.clientX - g.startX, event.clientY - g.startY) > 5) g.moved = true;
    if (!g.moved) return;
    if (pointers.current.size >= 2) {
      const d = distanceBetween(pointers.current);
      if (g.pinch > 0 && d > 0) emit(dolly(live.current, Math.log(g.pinch / d) / 0.001));
      g.pinch = d;
      return;
    }
    const panning = g.button === 2 || g.button === 1 || event.shiftKey || propsRef.current.panMode === true;
    emit(panning ? pan(live.current, dx, dy) : orbit(live.current, dx, -dy));
  };
  const up = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const g = gesture.current;
    pointers.current.delete(event.pointerId);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (!g || pointers.current.size > 0) return;
    gesture.current = null;
    if (!g.moved && g.button === 0) propsRef.current.onPick(pickAt(event.clientX, event.clientY));
  };
  const keydown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget || event.ctrlKey || event.metaKey || event.altKey) return;
    const action = keyToCameraAction(event.key, event.shiftKey);
    if (!action) return;
    event.preventDefault();
    emit(applyCameraAction(live.current, action, homeCamera));
  };

  const labelNodes = useMemo(() => labels.map((spec) => (
    <div
      key={spec.id}
      className="hqLabelAnchor"
      style={{ display: 'none' }}
      ref={(node) => { if (node) labelEls.current.set(spec.id, node); else labelEls.current.delete(spec.id); }}
    >
      {spec.node}
    </div>
  )), [labels]);

  const extraNodes = useMemo(() => (props.extraLabels ?? []).map((spec) => (
    <div
      key={spec.id}
      className="hqLabelAnchor"
      style={{ display: 'none' }}
      ref={(node) => { if (node) extraEls.current.set(spec.id, node); else extraEls.current.delete(spec.id); }}
    >
      {spec.node}
    </div>
  )), [props.extraLabels]);

  return (
    <div
      ref={host}
      className={`hqStage ${props.className ?? ''}`}
      data-night={night ? 'true' : 'false'}
      role="group"
      aria-label={props.label}
      aria-describedby="hq-stage-help"
      tabIndex={0}
      onKeyDown={keydown}
    >
      <canvas
        ref={canvas}
        className="hqCanvas"
        aria-hidden="true"
        data-hover={props.hoverId ? 'true' : 'false'}
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={up}
        onPointerLeave={() => { if (!gesture.current) propsRef.current.onHover(null); }}
        onContextMenu={(event) => event.preventDefault()}
      />
      <p id="hq-stage-help" className="hqSrOnly">{props.description}</p>
      {extraNodes.length > 0 && <div className="hqLabels hqLabelsExtra" ref={extraHost}>{extraNodes}</div>}
      <div className="hqLabels">{labelNodes}</div>
      {status === 'recovering' && (
        <div className="hqRecovering" role="status">{t('hq.stage.recovering')}</div>
      )}
      {props.children}
    </div>
  );
}

function distanceBetween(points: Map<number, Pointer>): number {
  const [a, b] = [...points.values()];
  return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0;
}

/** Width over height of an element, rounded to 0.1 so small resizes do not reframe the camera. */
export function useAspect(): [RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [aspect, setAspect] = useState(1.7);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => {
      const box = el.getBoundingClientRect();
      if (box.width > 0 && box.height > 0) setAspect(Math.round((box.width / box.height) * 10) / 10);
    };
    read();
    const observer = new ResizeObserver(read);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, aspect];
}
