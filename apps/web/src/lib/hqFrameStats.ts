/** Frame time statistics shared by the governor and the Graphics check. Pure. */
export type FrameSummary = Readonly<{ count: number; medianMs: number; p95Ms: number; worstMs: number; meanMs: number; droppedFrames: number; fps: number }>;

/** A frame longer than this (about 1.5 refreshes at 60 Hz) counts as dropped. */
export const DROPPED_FRAME_MS = 25;

export function percentile(sorted: readonly number[], p: number): number {
  if (sorted.length === 0) return 0;
  const rank = Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1));
  return sorted[rank];
}

export function summarize(frames: readonly number[]): FrameSummary {
  const clean = frames.filter((n) => Number.isFinite(n) && n > 0);
  if (clean.length === 0) return { count: 0, medianMs: 0, p95Ms: 0, worstMs: 0, meanMs: 0, droppedFrames: 0, fps: 0 };
  const sorted = [...clean].sort((a, b) => a - b);
  const mean = clean.reduce((a, b) => a + b, 0) / clean.length;
  const median = percentile(sorted, 0.5);
  return {
    count: clean.length, medianMs: median, p95Ms: percentile(sorted, 0.95), worstMs: sorted[sorted.length - 1], meanMs: mean,
    droppedFrames: clean.filter((n) => n > DROPPED_FRAME_MS).length, fps: 1000 / mean,
  };
}

/**
 * The last `size` samples; the median of them is the rolling frame time. Allocation free: the governor feeds it on every frame, so it
 * keeps a fixed ring and sorts into a fixed scratch buffer (no array copies, no garbage for the collector to pause on).
 */
export class RollingFrames {
  private readonly ring: Float64Array;
  private readonly scratch: Float64Array;
  private readonly size: number;
  private head = 0;
  private count = 0;
  constructor(size: number) { this.size = Math.max(1, size); this.ring = new Float64Array(this.size); this.scratch = new Float64Array(this.size); }
  push(ms: number): void { this.ring[this.head] = ms; this.head = (this.head + 1) % this.size; if (this.count < this.size) this.count += 1; }
  clear(): void { this.head = 0; this.count = 0; }
  get length(): number { return this.count; }
  /** The `p` quantile (0 to 1) with the same rank rule as `percentile`; 0 when empty. */
  quantile(p: number): number {
    const n = this.count, a = this.scratch;
    if (n === 0) return 0;
    for (let i = 0; i < n; i++) {
      const v = this.ring[i];
      let j = i - 1;
      while (j >= 0 && a[j] > v) { a[j + 1] = a[j]; j -= 1; }
      a[j + 1] = v;
    }
    return a[Math.min(n - 1, Math.max(0, Math.ceil(p * n) - 1))];
  }
  median(): number { return this.quantile(0.5); }
}
