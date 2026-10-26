/**
 * What the Graphics check knows and how it turns into a verdict and a report the owner can paste to us. Pure: the browser reads
 * (`hq-gpu/gpuProbe.ts`) hand their raw values to the functions below, which keep a fixed list of fields. The report carries
 * hardware and timing facts only: no account, workspace or tenant id, no address, no user name and no page URL.
 */
import { HQ_TIERS, tierById, type QualityState } from './hqQualityTiers.ts';
import type { FrameSummary } from './hqFrameStats.ts';

/** Names that mean the browser draws on the CPU. */
export const SOFTWARE_RENDERER = /swiftshader|llvmpipe|softpipe|software|basic render|microsoft basic|mesa offscreen|lavapipe/i;
export const isSoftwareRenderer = (name: string): boolean => SOFTWARE_RENDERER.test(name);

export type GpuProbe = Readonly<{
  /** 0 when no WebGL context could be made. */
  webglVersion: 0 | 1 | 2;
  renderer: string;
  vendor: string;
  /** True when WEBGL_debug_renderer_info gave the real strings; false when only the masked ones were available. */
  unmasked: boolean;
  /** null when the strings are masked and say nothing. */
  software: boolean | null;
  maxTextureSize: number;
  maxCubeMapSize: number;
  maxAnisotropy: number;
  maxVertexUniformVectors: number;
  maxFragmentUniformVectors: number;
  extensions: readonly string[];
  /** Extensions the headquarters scene uses, and whether this context has them. */
  used: Readonly<Record<string, boolean>>;
  dpr: number;
  viewport: Readonly<{ width: number; height: number }>;
  cores: number | null;
  deviceMemoryGb: number | null;
  browser: string;
  os: string;
}>;

/** The extensions the scene or three.js ask for; the report says which of them this GPU has. */
export const USED_EXTENSIONS = ['EXT_color_buffer_float', 'EXT_color_buffer_half_float', 'OES_texture_float_linear', 'EXT_texture_filter_anisotropic', 'WEBGL_debug_renderer_info', 'KHR_parallel_shader_compile', 'EXT_disjoint_timer_query_webgl2'] as const;

export type TierResult = Readonly<{
  tier: number;
  key: string;
  frames: FrameSummary;
  drawCalls: number;
  triangles: number;
  dpr: number;
  shadowSize: number;
  bloom: boolean;
  /** Rough estimate in megabytes (geometry, textures, shadow map, canvas and glow targets); the browser does not report real GPU memory. */
  memoryEstimateMb: number;
  /** False when the scene had no cut-away floor to change (the campus view). */
  floorChange: boolean;
}>;

export type VerdictKind = 'no-webgl' | 'software' | 'unmeasured' | 'fits' | 'reduced' | 'poor';
export type Verdict = Readonly<{ kind: VerdictKind; tier: number | null; fps: number | null }>;

/** Holds 60 frames a second: the typical frame fits a 60 Hz refresh and nearly all of them do. */
export const holds60 = (f: FrameSummary): boolean => f.count >= 20 && f.medianMs <= 18.5 && f.p95Ms <= 25;
export const holds30 = (f: FrameSummary): boolean => f.count >= 20 && f.medianMs <= 34 && f.p95Ms <= 50;

/** The plain verdict: the heaviest tier that holds 60 frames a second, else the heaviest that holds 30, else the lightest. */
export function verdictFor(probe: GpuProbe, results: readonly TierResult[]): Verdict {
  if (probe.webglVersion === 0) return { kind: 'no-webgl', tier: null, fps: null };
  if (probe.software === true) return { kind: 'software', tier: HQ_TIERS[0].id, fps: null };
  const measured = [...results].sort((a, b) => a.tier - b.tier);
  if (measured.length === 0) return { kind: 'unmeasured', tier: null, fps: null };
  const top = measured[measured.length - 1];
  const fast = [...measured].reverse().find((r) => holds60(r.frames));
  if (fast) return { kind: fast.tier === top.tier ? 'fits' : 'reduced', tier: fast.tier, fps: 60 };
  const ok = [...measured].reverse().find((r) => holds30(r.frames));
  if (ok) return { kind: 'reduced', tier: ok.tier, fps: 30 };
  return { kind: 'poor', tier: measured[0].tier, fps: Math.round(measured[0].frames.fps) || null };
}

export const REPORT_SCHEMA = 'anum.hq.gpu-report/1';

export type GpuReport = Readonly<{
  schema: typeof REPORT_SCHEMA;
  generatedAt: string;
  gpu: Omit<GpuProbe, 'viewport' | 'cores' | 'deviceMemoryGb'> & { cores: number | null; deviceMemoryGb: number | null; viewport: { width: number; height: number } };
  quality: Readonly<{ tier: string; reason: string; governorStep: number; governorSteps: number; dprScale: number; shadowSize: number; bloom: boolean; lean: boolean; pinned: string | null }> | null;
  tiers: readonly TierResult[];
  verdict: Verdict & { tierKey: string | null };
  notes: readonly string[];
}>;

const clip = (s: string, n: number): string => String(s).replace(/[^\x20-\x7e]/g, '?').slice(0, n);
const num = (n: number, digits = 2): number => (Number.isFinite(n) ? Number(n.toFixed(digits)) : 0);

function cleanFrames(f: FrameSummary): FrameSummary {
  return { count: Math.round(f.count), medianMs: num(f.medianMs), p95Ms: num(f.p95Ms), worstMs: num(f.worstMs), meanMs: num(f.meanMs), droppedFrames: Math.round(f.droppedFrames), fps: num(f.fps, 1) };
}

/**
 * Build the report from known pieces only. Anything not named here (a stray account id on an object passed in, a URL) is dropped by
 * construction, because every field is copied by name and strings are clipped to printable ASCII.
 */
export function buildReport(input: { now: Date; probe: GpuProbe; quality: QualityState | null; pinned: number | null; results: readonly TierResult[]; notes?: readonly string[] }): GpuReport {
  const { probe, quality } = input;
  const verdict = verdictFor(probe, input.results);
  const tierKey = (id: number | null): string | null => (id === null ? null : tierById(id).key);
  return {
    schema: REPORT_SCHEMA,
    generatedAt: input.now.toISOString(),
    gpu: {
      webglVersion: probe.webglVersion, renderer: clip(probe.renderer, 160), vendor: clip(probe.vendor, 80), unmasked: probe.unmasked, software: probe.software,
      maxTextureSize: probe.maxTextureSize, maxCubeMapSize: probe.maxCubeMapSize, maxAnisotropy: probe.maxAnisotropy,
      maxVertexUniformVectors: probe.maxVertexUniformVectors, maxFragmentUniformVectors: probe.maxFragmentUniformVectors,
      extensions: probe.extensions.slice(0, 80).map((e) => clip(e, 60)),
      used: Object.fromEntries(USED_EXTENSIONS.map((e) => [e, probe.used[e] === true])),
      dpr: num(probe.dpr), viewport: { width: Math.round(probe.viewport.width), height: Math.round(probe.viewport.height) },
      cores: probe.cores, deviceMemoryGb: probe.deviceMemoryGb, browser: clip(probe.browser, 40), os: clip(probe.os, 40),
    },
    quality: quality ? {
      tier: tierById(quality.tier).key, reason: quality.reason, governorStep: quality.step, governorSteps: quality.steps,
      dprScale: num(quality.settings.dprScale), shadowSize: quality.settings.shadowSize, bloom: quality.settings.bloom, lean: quality.settings.lean, pinned: tierKey(input.pinned),
    } : null,
    tiers: input.results.map((r) => ({
      tier: r.tier, key: clip(r.key, 20), frames: cleanFrames(r.frames), drawCalls: Math.round(r.drawCalls), triangles: Math.round(r.triangles), dpr: num(r.dpr),
      shadowSize: r.shadowSize, bloom: r.bloom, memoryEstimateMb: num(r.memoryEstimateMb, 1), floorChange: r.floorChange,
    })),
    verdict: { ...verdict, tierKey: tierKey(verdict.tier) },
    notes: (input.notes ?? []).slice(0, 10).map((n) => clip(n, 200)),
  };
}

/** Browser family and major version, and the operating system family, from a user agent string; nothing else of the string is kept. */
export function describeAgent(ua: string): { browser: string; os: string } {
  const pick = (re: RegExp): string | null => { const m = re.exec(ua); return m ? m[1] : null; };
  const edge = pick(/Edg\/(\d+)/), chrome = pick(/Chrome\/(\d+)/), firefox = pick(/Firefox\/(\d+)/), safari = pick(/Version\/(\d+)[^ ]* (?:Mobile\/\S+ )?Safari/);
  const browser = edge ? `Edge ${edge}` : firefox ? `Firefox ${firefox}` : chrome ? `Chrome ${chrome}` : safari ? `Safari ${safari}` : 'unknown';
  const os = /Windows/.test(ua) ? 'Windows' : /Android/.test(ua) ? 'Android' : /iPhone|iPad|iPod/.test(ua) ? 'iOS' : /Mac OS X|Macintosh/.test(ua) ? 'macOS' : /CrOS/.test(ua) ? 'ChromeOS' : /Linux/.test(ua) ? 'Linux' : 'unknown';
  return { browser, os };
}

/** Reports re-read by a test or a script: true when the JSON holds only what the schema allows (no id, address or URL-like text). */
export function reportLooksAnonymous(json: string): boolean {
  return !/https?:\/\/|@[a-z0-9-]+\.[a-z]{2,}|tenant|workspace|user_?id|account|session|token|bearer/i.test(json);
}

