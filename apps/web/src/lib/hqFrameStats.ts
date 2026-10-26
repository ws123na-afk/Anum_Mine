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

/** The last `size` samples; the median of them is the rolling frame time. */
export class RollingFrames {
  private readonly values: number[] = [];
  private readonly size: number;
  constructor(size: number) { this.size = size; }
  push(ms: number): void { this.values.push(ms); if (this.values.length > this.size) this.values.shift(); }
  clear(): void { this.values.length = 0; }
  get length(): number { return this.values.length; }
  median(): number { return percentile([...this.values].sort((a, b) => a - b), 0.5); }
}
