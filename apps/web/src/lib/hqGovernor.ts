/**
 * The frame governor: a small state machine that watches real frame times and decides when the picture should get cheaper
 * (down one rung of the quality ladder) or may get richer again (up one rung). It never touches three.js: the renderer feeds it
 * samples and applies the decision, which keeps every rule below testable with simulated frame times.
 *
 * Rules: judged on the rolling median (one hitch never steps anything) and on the 95th percentile of a longer window (frequent
 * hitches step down even when the typical frame is fast, as the first real GPU report showed: Ultra on an integrated Intel UHD 620 had a
 * 16.7 ms median but a 66.7 ms 95th percentile), a step down needs a full window of slow frames and a cool-down since the last change, a step up needs several seconds of healthy rendering and waits longer after every step down
 * that followed a step up (no flip-flopping), a gap longer than `gapMs` (an idle scene, a hidden tab) is not a frame time.
 */
import { RollingFrames } from './hqFrameStats.ts';

export type GovernorDecision = 'down' | 'up' | null;

export type GovernorOptions = Readonly<{
  /** Rolling median above this steps down. About 38 frames a second. */
  slowMs: number;
  /** Rolling median at or below this counts towards stepping up. */
  healthyMs: number;
  window: number;
  minSamples: number;
  cooldownMs: number;
  /** Healthy rendering time needed before the first step up. It doubles (to `maxUpWaitMs`) after a step down that follows a step up within `flipMs`. */
  upWaitMs: number;
  maxUpWaitMs: number;
  flipMs: number;
  /** A sample whose frame took longer than this says nothing about drawing speed. */
  gapMs: number;
  /** The longer window the 95th percentile is read over (about two seconds at 60 frames a second), and the samples it needs first. */
  hitchWindow: number;
  hitchMinSamples: number;
  /** A 95th percentile above this steps down: more than 1 frame in 20 took longer than two and a half refreshes at 60 Hz. */
  hitchP95Ms: number;
  /** Stepping up also needs the 95th percentile at or under this (the verdict's "holds 60" limit), so a hitchy rung is not climbed back into. */
  healthyP95Ms: number;
}>;

export const DEFAULT_GOVERNOR: GovernorOptions = {
  slowMs: 26, healthyMs: 19, window: 40, minSamples: 24, cooldownMs: 3000, upWaitMs: 12_000, maxUpWaitMs: 96_000, flipMs: 30_000, gapMs: 250,
  hitchWindow: 120, hitchMinSamples: 60, hitchP95Ms: 40, healthyP95Ms: 25,
};

export class FrameGovernor {
  /** How many rungs below the full tier the picture is now (0 = full). */
  step = 0;
  enabled = true;
  private readonly frames: RollingFrames;
  private readonly long: RollingFrames;
  private lastChangeAt = -Infinity;
  private lastUpAt = -Infinity;
  private healthyMs = 0;
  private upWait: number;
  private lastSampleAt = 0;

  readonly steps: number;
  private readonly opt: GovernorOptions;

  constructor(steps: number, opt: GovernorOptions = DEFAULT_GOVERNOR) {
    this.steps = steps;
    this.opt = opt;
    this.frames = new RollingFrames(opt.window);
    this.long = new RollingFrames(opt.hitchWindow);
    this.upWait = opt.upWaitMs;
  }

  /** Forget everything measured (the tab was hidden, the settings changed). */
  reset(): void { this.frames.clear(); this.long.clear(); this.healthyMs = 0; this.lastSampleAt = 0; }

  /** The rolling median frame time, 0 until there are enough samples. */
  get medianMs(): number { return this.frames.length >= this.opt.minSamples ? this.frames.median() : 0; }

  /** The 95th percentile over the longer window, 0 until there are enough samples. */
  get p95Ms(): number { return this.long.length >= this.opt.hitchMinSamples ? this.long.quantile(0.95) : 0; }

  /** Feed one frame (`ms` = time that frame took, `now` = performance.now()). Returns what to do, if anything; call `apply` to take it. */
  sample(ms: number, now: number): GovernorDecision {
    if (!this.enabled || !Number.isFinite(ms) || ms <= 0) return null;
    const elapsed = this.lastSampleAt > 0 ? Math.min(now - this.lastSampleAt, this.opt.gapMs) : 0;
    this.lastSampleAt = now;
    if (ms > this.opt.gapMs) { this.healthyMs = 0; return null; }
    this.frames.push(ms);
    this.long.push(ms);
    if (this.frames.length < this.opt.minSamples) return null;
    const median = this.frames.median();
    const p95 = this.p95Ms;
    if (median > this.opt.slowMs || p95 > this.opt.hitchP95Ms) {
      this.healthyMs = 0;
      return this.step < this.steps - 1 && now - this.lastChangeAt >= this.opt.cooldownMs ? 'down' : null;
    }
    // Healthy needs a typical frame inside a 60 Hz refresh and, once the long window is full enough, few hitches.
    if (median <= this.opt.healthyMs && p95 <= this.opt.healthyP95Ms) this.healthyMs += elapsed; else this.healthyMs = 0;
    if (this.step > 0 && this.healthyMs >= this.upWait && now - this.lastChangeAt >= this.opt.cooldownMs) return 'up';
    return null;
  }

  /** Take a decision `sample` returned: move one rung and start measuring afresh at the new settings. */
  apply(decision: 'down' | 'up', now: number): void {
    if (decision === 'down') {
      if (this.step >= this.steps - 1) return;
      this.step += 1;
      if (now - this.lastUpAt < this.opt.flipMs) this.upWait = Math.min(this.opt.maxUpWaitMs, this.upWait * 2);
    } else {
      if (this.step <= 0) return;
      this.step -= 1;
      this.lastUpAt = now;
    }
    this.lastChangeAt = now;
    this.reset();
  }

  /** The renderer chose to spend the slow frames elsewhere (ambient motion steps down first): keep the rung, measure again. */
  defer(now: number): void { this.lastChangeAt = now; this.reset(); }
}
