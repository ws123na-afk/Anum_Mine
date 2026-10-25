/**
 * The camera bridge: the campus and the building view each own a camera in React state. They register a
 * small controller here, and the navigation layer (floor rail, quick views, quick moves) asks the bridge to
 * fly to a framed place. One requestAnimationFrame loop runs only while a flight is under way; a pointer,
 * wheel or key press on the page cancels it so the person is never fought for the camera.
 */
import type { CameraState } from '../lib/headquartersSpatial.ts';
import type { Aabb } from '../lib/hqMath.ts';
import { planFlight } from './flight.ts';

export type CameraController = Readonly<{
  get: () => CameraState;
  set: (camera: CameraState) => void;
  obstacles: () => readonly Aabb[];
  /** The camera that frames a place of this view (a building on the campus, a room in a building), or null when it is not there (yet). */
  frame: (id: string) => CameraState | null;
  home: () => CameraState;
}>;

export type FlightEvent = Readonly<{ phase: 'start' | 'end' | 'instant' | 'missing'; frame: string }>;

const WAIT_FRAME_MS = 600;

export class HqCameraBridge {
  private controller: CameraController | null = null;
  private pending: { frame: string; waitedSince: number } | null = null;
  private raf: number | null = null;
  private listeners = new Set<(event: FlightEvent) => void>();
  private cancelOff: (() => void) | null = null;
  private reduced = false;
  private flying = false;

  get isFlying(): boolean { return this.flying; }
  setReducedMotion(reduced: boolean): void { this.reduced = reduced; }
  subscribe(listener: (event: FlightEvent) => void): () => void { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; }
  private emit(event: FlightEvent): void { for (const l of [...this.listeners]) l(event); }

  /** A view registers its controller; a flight asked for before the view existed starts now. Returns the detach function. */
  attach(controller: CameraController): () => void {
    this.controller = controller;
    if (this.pending) { this.pending = { frame: this.pending.frame, waitedSince: performance.now() }; this.run(this.pending.frame); }
    // Detaching stops a flight in progress but keeps a flight asked for, so a view that is replaced (campus to building) still receives it.
    return () => { if (this.controller === controller) { this.stopLoop(); this.controller = null; } };
  }

  /** Flies to a framed place. When the view is not mounted yet, or the place is not framed yet, it waits a moment for it. */
  flyToFrame(frame: string): void {
    this.cancel();
    this.pending = { frame, waitedSince: performance.now() };
    if (this.controller) this.run(frame);
  }

  /** Flies to the view's own home framing (the campus aerial, a floor's overview). */
  flyHome(): void { this.flyToFrame('__home__'); }

  /** Moves the camera to a state the view computed itself (the label cards use this), eased. */
  flyTo(to: CameraState): void {
    const c = this.controller;
    if (!c) return;
    this.cancel();
    this.start(c, c.get(), to, '');
  }

  cancel(): void {
    this.stopLoop();
    this.pending = null;
  }

  private stopLoop(): void {
    if (this.raf !== null) cancelAnimationFrame(this.raf);
    this.raf = null;
    this.cancelOff?.();
    this.cancelOff = null;
    this.flying = false;
  }

  /** Asks the view for one more frame without moving the camera (a new camera object makes the stage draw). */
  nudge(): void { const c = this.controller; if (c) c.set({ ...c.get() }); }

  private run(frame: string): void {
    const c = this.controller;
    if (!c) return;
    const to = frame === '__home__' ? c.home() : c.frame(frame);
    if (to) { this.pending = null; this.start(c, c.get(), to, frame); return; }
    const waited = this.pending ? performance.now() - this.pending.waitedSince : WAIT_FRAME_MS;
    if (waited >= WAIT_FRAME_MS) { this.pending = null; this.emit({ phase: 'missing', frame }); return; }
    if (this.raf !== null) cancelAnimationFrame(this.raf);
    this.raf = requestAnimationFrame(() => { this.raf = null; this.run(frame); });
  }

  private start(c: CameraController, from: CameraState, to: CameraState, frame: string): void {
    const plan = planFlight(from, to, { obstacles: c.obstacles(), reducedMotion: this.reduced });
    if (plan.duration === 0) { c.set(to); this.flying = false; this.emit({ phase: 'instant', frame }); return; }
    this.flying = true;
    this.emit({ phase: 'start', frame });
    const interrupt = () => { if (this.flying) { this.cancel(); this.emit({ phase: 'end', frame }); } };
    // A deliberate move by the person (drag, wheel, key) ends the flight where it is.
    const stage = document.querySelector('.hqStage');
    const opts = { capture: true, passive: true } as const;
    stage?.addEventListener('pointerdown', interrupt, opts);
    stage?.addEventListener('wheel', interrupt, opts);
    stage?.addEventListener('keydown', interrupt, opts);
    this.cancelOff = () => {
      stage?.removeEventListener('pointerdown', interrupt, opts);
      stage?.removeEventListener('wheel', interrupt, opts);
      stage?.removeEventListener('keydown', interrupt, opts);
    };
    const began = performance.now();
    const step = (now: number) => {
      const p = Math.min(1, (now - began) / plan.duration);
      c.set(plan.at(p));
      if (p >= 1) { this.raf = null; this.cancelOff?.(); this.cancelOff = null; this.flying = false; this.emit({ phase: 'end', frame }); return; }
      this.raf = requestAnimationFrame(step);
    };
    this.raf = requestAnimationFrame(step);
  }
}
