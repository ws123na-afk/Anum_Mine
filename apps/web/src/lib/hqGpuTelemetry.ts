/**
 * Optional graphics telemetry hook. OFF by default: with no sink registered, `emitGpuTelemetry` does nothing, and the only sink the app
 * ever registers by itself exists when the build sets `VITE_ANUM_HQ_GPU_TELEMETRY=on`. That sink does not send anything anywhere: it
 * dispatches a `anum:hq-gpu` event on `window` so a deployment that wants numbers can listen and forward them itself.
 * Events carry a tier name, a rung number and a reason code. Never an id, a name, an address or a renderer string.
 */
export type GpuTelemetryEvent = Readonly<{ kind: 'quality' | 'context-lost' | 'context-restored' | 'fallback'; tier?: string; step?: number; reason?: string }>;
export type GpuTelemetrySink = (event: GpuTelemetryEvent) => void;

const KINDS = ['quality', 'context-lost', 'context-restored', 'fallback'];
let sink: GpuTelemetrySink | null = null;

export const gpuTelemetryBuildFlag = (value: string | undefined = import.meta.env?.VITE_ANUM_HQ_GPU_TELEMETRY as string | undefined): boolean => (value ?? '').trim().toLowerCase() === 'on';

export function setGpuTelemetrySink(next: GpuTelemetrySink | null): void { sink = next; }

/** Keep only the allowed fields, with short plain values. */
export function cleanGpuEvent(event: GpuTelemetryEvent): GpuTelemetryEvent | null {
  if (!KINDS.includes(event.kind)) return null;
  const word = (v: string | undefined): string | undefined => (typeof v === 'string' && /^[a-z0-9-]{1,24}$/.test(v) ? v : undefined);
  const step = typeof event.step === 'number' && Number.isInteger(event.step) && event.step >= 0 && event.step < 100 ? event.step : undefined;
  return { kind: event.kind, tier: word(event.tier), step, reason: word(event.reason) };
}

export function emitGpuTelemetry(event: GpuTelemetryEvent): void {
  if (!sink) return;
  const clean = cleanGpuEvent(event);
  if (!clean) return;
  try { sink(clean); } catch { /* a listener's failure never reaches the scene */ }
}

if (gpuTelemetryBuildFlag() && typeof window !== 'undefined') {
  setGpuTelemetrySink((event) => window.dispatchEvent(new CustomEvent('anum:hq-gpu', { detail: event })));
}
