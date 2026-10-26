/**
 * Graphics check: reports honestly what this browser and graphics card are doing for the 3D office, measures frame times per quality
 * tier with a scripted camera orbit, gives a plain verdict, can pin a tier, and saves a report with no personal data.
 * A sandbox run proves the logic only; what a real GPU does is for the owner's own device to say. Docs: docs/ANUM_MINE_HQ_GPU_CHECK.md.
 */
import { Download, Gauge, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { getLiveRenderer } from '../lib/hqGpuBridge.ts';
import { getGpuState, subscribeGpuState } from '../lib/hqGpuState.ts';
import { buildReport, verdictFor, type GpuProbe, type TierResult } from '../lib/hqGpuReport.ts';
import { HQ_TIERS, QUALITY_PIN_KEY } from '../lib/hqQualityTiers.ts';
import { reasonText, tierName } from '../lib/hqGpuText.ts';
import { t } from '../lib/i18n.ts';
import { useLocale } from '../useLocale';
import { readGpuProbe } from './gpuProbe.ts';
import './hqGpuCheck.css';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea, [tabindex]:not([tabindex="-1"])';
const LENGTHS = [2, 5, 10] as const;

type Run = { state: 'idle' } | { state: 'running'; tier: number; index: number; total: number } | { state: 'done'; count: number } | { state: 'stopped' } | { state: 'error' };

const ms1 = (n: number): string => (n > 0 ? n.toFixed(1) : '-');

function readPin(): string | null {
  try { return window.localStorage.getItem(QUALITY_PIN_KEY); } catch { return null; }
}
function writePin(id: number | null): void {
  try { if (id === null) window.localStorage.removeItem(QUALITY_PIN_KEY); else window.localStorage.setItem(QUALITY_PIN_KEY, String(id)); } catch { /* storage is blocked: the pin lasts until the page closes */ }
}

export default function HqGpuCheckPanel({ onClose }: { onClose: () => void }) {
  useLocale();
  const quality = useSyncExternalStore(subscribeGpuState, getGpuState, getGpuState);
  const root = useRef<HTMLElement>(null);
  const opener = useRef<Element | null>(typeof document === 'undefined' ? null : document.activeElement);
  const abort = useRef<AbortController | null>(null);
  const [probe, setProbe] = useState<GpuProbe>(() => readGpuProbe(getLiveRenderer()?.parts().gl ?? null));
  const [results, setResults] = useState<TierResult[]>([]);
  const [run, setRun] = useState<Run>({ state: 'idle' });
  const [seconds, setSeconds] = useState<number>(5);
  const [note, setNote] = useState('');
  const live = getLiveRenderer();

  const close = useCallback(() => {
    abort.current?.abort();
    onClose();
    const target = opener.current;
    if (target instanceof HTMLElement && document.contains(target)) window.setTimeout(() => target.focus(), 0);
  }, [onClose]);

  useEffect(() => { root.current?.querySelector<HTMLElement>('[data-autofocus]')?.focus(); }, []);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => { if (event.key === 'Escape' && !event.defaultPrevented) { event.stopPropagation(); close(); } };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [close]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key !== 'Tab' || !root.current) return;
    const nodes = [...root.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((n) => n.offsetParent !== null);
    if (nodes.length === 0) return;
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };

  const measure = async (ids: readonly number[]) => {
    const renderer = getLiveRenderer();
    if (!renderer || run.state === 'running') return;
    const controller = new AbortController();
    abort.current = controller;
    setNote('');
    setRun({ state: 'running', tier: ids[0], index: 0, total: ids.length });
    try {
      const { runBenchmark } = await import('./gpuBench.ts');
      const got = await runBenchmark(renderer, ids, seconds * 1000, controller.signal, ({ tier, index, total }) => setRun({ state: 'running', tier, index, total }));
      setResults((previous) => [...previous.filter((p) => !got.some((g) => g.tier === p.tier)), ...got].sort((a, b) => a.tier - b.tier));
      setProbe(readGpuProbe(renderer.parts().gl));
      setRun(controller.signal.aborted ? { state: 'stopped' } : { state: 'done', count: got.length });
    } catch {
      setRun({ state: 'error' });
    }
  };

  const verdict = useMemo(() => verdictFor(probe, results), [probe, results]);
  const pinned = readPin();
  const pinnedNow = quality?.reason === 'pinned' ? quality.tier : null;
  const report = useMemo(() => buildReport({ now: new Date(), probe, quality, pinned: pinnedNow, results, notes: probe.software === true ? ['software rasteriser: timings do not describe a real graphics card'] : [] }), [probe, quality, pinnedNow, results]);
  const json = useMemo(() => JSON.stringify(report, null, 2), [report]);

  const usePin = (id: number | null) => {
    writePin(id);
    getLiveRenderer()?.setPin(id);
    setNote(id === null ? t('hqGpu.auto.done') : t('hqGpu.use.done', { tier: tierName(id) }));
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url; a.download = 'anum-hq-graphics-report.json';
    document.body.appendChild(a); a.click(); a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(json); setNote(t('hqGpu.report.copied')); } catch { setNote(t('hqGpu.report.copyFailed')); }
  };

  const running = run.state === 'running';
  const verdictText = verdict.kind === 'fits' ? t('hqGpu.verdict.fits', { tier: tierName(verdict.tier ?? 0) })
    : verdict.kind === 'reduced' ? t(verdict.fps === 30 ? 'hqGpu.verdict.reduced30' : 'hqGpu.verdict.reduced60', { tier: tierName(verdict.tier ?? 0) })
      : verdict.kind === 'poor' ? t('hqGpu.verdict.poor', { tier: tierName(verdict.tier ?? 0), fps: verdict.fps ?? 0 })
        : verdict.kind === 'software' ? t('hqGpu.verdict.software')
          : verdict.kind === 'no-webgl' ? t('hqGpu.verdict.nowebgl') : t('hqGpu.verdict.unmeasured');
  const progress = run.state === 'running' ? t('hqGpu.progress', { tier: tierName(run.tier), done: run.index + 1, total: run.total })
    : run.state === 'done' ? t('hqGpu.progress.done', { count: run.count }) : run.state === 'stopped' ? t('hqGpu.progress.stopped') : run.state === 'error' ? t('hqGpu.progress.error') : '';
  const softwareText = probe.software === true ? t('hqGpu.fact.software.yes') : probe.software === false ? t('hqGpu.fact.software.no') : t('hqGpu.fact.software.unknown');
  const governorText = !quality ? t('hqGpu.fact.unknown')
    : quality.governed && !running ? t('hqGpu.quality.governor.on', { step: quality.step, steps: quality.steps - 1 })
      : t('hqGpu.quality.governor.off', { why: running ? t('hqGpu.quality.governor.why.run') : quality.reason === 'pinned' ? t('hqGpu.quality.governor.why.pinned') : t('hqGpu.quality.governor.why.software') });
  const canPin = verdict.tier !== null && (verdict.kind === 'fits' || verdict.kind === 'reduced' || verdict.kind === 'poor' || verdict.kind === 'software');

  return (
    <>
      <div className="hqGpuScrim" onClick={close} aria-hidden="true" />
      <aside ref={root} className="hqGpu" role="dialog" aria-modal="true" aria-labelledby="hqGpuTitle" onKeyDown={onKeyDown} data-testid="hq-gpu-check">
        <header className="hqGpuHead">
          <div>
            <h2 id="hqGpuTitle"><Gauge aria-hidden="true" />{t('hqGpu.title')}</h2>
            <p className="hqGpuHint">{t('hqGpu.intro')}</p>
          </div>
          <button type="button" className="hqIconButton" data-autofocus onClick={close} aria-label={t('hqGpu.close')}><X aria-hidden="true" /></button>
        </header>
        <div className="hqGpuBody">
          <section aria-labelledby="hqGpuDevice">
            <h3 id="hqGpuDevice">{t('hqGpu.section.device')}</h3>
            <dl className="hqGpuFacts" data-testid="hq-gpu-facts">
              <dt>{t('hqGpu.fact.webgl')}</dt><dd data-fact="webgl">{probe.webglVersion === 0 ? t('hqGpu.fact.webgl.none') : `WebGL ${probe.webglVersion}`}</dd>
              <dt>{t('hqGpu.fact.renderer')}</dt><dd data-fact="renderer" dir="ltr">{probe.renderer || '-'}</dd>
              <dt>{t('hqGpu.fact.vendor')}</dt><dd dir="ltr">{probe.vendor || '-'}</dd>
              <dt>{t('hqGpu.fact.software')}</dt><dd data-fact="software" data-software={String(probe.software)}>{softwareText}</dd>
              <dt>{t('hqGpu.fact.texture')}</dt><dd dir="ltr">{probe.maxTextureSize || '-'}</dd>
              <dt>{t('hqGpu.fact.dpr')}</dt><dd dir="ltr">{probe.dpr.toFixed(2)}</dd>
              <dt>{t('hqGpu.fact.extensions')}</dt>
              <dd dir="ltr"><ul className="hqGpuExt">{Object.entries(report.gpu.used).map(([name, have]) => (
                <li key={name} data-have={String(have)}>{name} <span>{have ? t('hqGpu.fact.extensions.have') : t('hqGpu.fact.extensions.missing')}</span></li>
              ))}</ul></dd>
              <dt>{t('hqGpu.fact.cores')}</dt><dd dir="ltr">{probe.cores ?? t('hqGpu.fact.unknown')}</dd>
              <dt>{t('hqGpu.fact.memory')}</dt><dd dir="ltr">{probe.deviceMemoryGb !== null ? `${probe.deviceMemoryGb} GB` : t('hqGpu.fact.unknown')}</dd>
            </dl>
            {probe.webglVersion > 0 && !probe.unmasked && <p className="hqGpuHint">{t('hqGpu.fact.masked')}</p>}
          </section>

          <section aria-labelledby="hqGpuQuality">
            <h3 id="hqGpuQuality">{t('hqGpu.section.quality')}</h3>
            {quality ? (
              <dl className="hqGpuFacts" data-testid="hq-gpu-quality">
                <dt>{t('hqGpu.quality.tier')}</dt><dd data-fact="tier">{tierName(quality.tier)}</dd>
                <dt>{t('hqGpu.quality.reason')}</dt><dd>{reasonText(quality)}</dd>
                <dt>{t('hqGpu.quality.governor')}</dt><dd>{governorText}</dd>
                <dd className="hqGpuHint hqGpuSpan">{t('hqGpu.quality.detail', {
                  dpr: quality.settings.dprScale.toFixed(2), shadows: quality.settings.shadowSize > 0 ? `${t('hqGpu.on')} ${quality.settings.shadowSize}` : t('hqGpu.off'),
                  glow: quality.settings.bloom ? t('hqGpu.on') : t('hqGpu.off'), materials: quality.settings.lean ? t('hqGpu.materials.simple') : t('hqGpu.materials.full'),
                })}</dd>
              </dl>
            ) : <p className="hqGpuHint" data-testid="hq-gpu-no3d">{t('hqGpu.no3d')}</p>}
          </section>

          <section aria-labelledby="hqGpuMeasure">
            <h3 id="hqGpuMeasure">{t('hqGpu.section.measure')}</h3>
            <p className="hqGpuHint">{t('hqGpu.measure.hint')}</p>
            <div className="hqGpuControls">
              <label>{t('hqGpu.length')}
                <select value={seconds} disabled={running} onChange={(e) => setSeconds(Number(e.target.value))} data-testid="hq-gpu-length">
                  {LENGTHS.map((n) => <option key={n} value={n}>{t('hqGpu.length.option', { seconds: n })}</option>)}
                </select>
              </label>
              <button type="button" className="hqGhost" disabled={!live || running || !quality} onClick={() => void measure([quality?.tier ?? HQ_TIERS[0].id])} data-testid="hq-gpu-measure">{t('hqGpu.measure.current')}</button>
              <button type="button" className="hqPrimary" disabled={!live || running || !quality} onClick={() => void measure(HQ_TIERS.map((x) => x.id))} data-testid="hq-gpu-bench">{t('hqGpu.measure.all')}</button>
              {running && <button type="button" className="hqGhost" onClick={() => abort.current?.abort()}>{t('hqGpu.measure.cancel')}</button>}
            </div>
            <p className="hqGpuHint" role="status" data-testid="hq-gpu-progress">{progress}</p>
          </section>

          <section aria-labelledby="hqGpuResults">
            <h3 id="hqGpuResults">{t('hqGpu.section.results')}</h3>
            {results.length === 0 ? <p className="hqGpuHint">{t('hqGpu.results.empty')}</p> : (
              <ul className="hqGpuCards" data-testid="hq-gpu-results">
                {results.map((r) => (
                  <li key={r.tier} data-tier={r.key} className="hqGpuCard">
                    <h4>{tierName(r.tier)}<span dir="ltr">{r.frames.fps > 0 ? `${r.frames.fps.toFixed(0)} ${t('hqGpu.col.fps')}` : '-'}</span></h4>
                    <dl>
                      <dt>{t('hqGpu.col.median')}</dt><dd dir="ltr">{ms1(r.frames.medianMs)} ms</dd>
                      <dt>{t('hqGpu.col.p95')}</dt><dd dir="ltr">{ms1(r.frames.p95Ms)} ms</dd>
                      <dt>{t('hqGpu.col.worst')}</dt><dd dir="ltr">{ms1(r.frames.worstMs)} ms</dd>
                      <dt>{t('hqGpu.col.dropped')}</dt><dd dir="ltr">{r.frames.droppedFrames}</dd>
                      <dt>{t('hqGpu.col.calls')}</dt><dd dir="ltr">{r.drawCalls}</dd>
                      <dt>{t('hqGpu.col.triangles')}</dt><dd dir="ltr">{r.triangles.toLocaleString('en')}</dd>
                      <dt>{t('hqGpu.col.memory')}</dt><dd dir="ltr">{r.memoryEstimateMb.toFixed(0)} MB</dd>
                    </dl>
                  </li>
                ))}
              </ul>
            )}
            {results.length > 0 && <p className="hqGpuHint">{results.some((r) => r.floorChange) ? t('hqGpu.results.floor') : t('hqGpu.results.nofloor')}</p>}
            <div className="hqGpuVerdict" data-kind={verdict.kind} data-testid="hq-gpu-verdict">
              <p>{verdictText}</p>
              {probe.software === true && results.length > 0 && <p className="hqGpuHint">{t('hqGpu.verdict.softwareNote')}</p>}
              <div className="hqGpuControls">
                {canPin && verdict.tier !== null && <button type="button" className="hqPrimary" onClick={() => usePin(verdict.tier)} data-testid="hq-gpu-use">{t('hqGpu.use', { tier: tierName(verdict.tier) })}</button>}
                {(pinned !== null || quality?.reason === 'pinned') && <button type="button" className="hqGhost" onClick={() => usePin(null)} data-testid="hq-gpu-auto">{t('hqGpu.auto')}</button>}
              </div>
            </div>
            <p className="hqGpuHint" role="status" data-testid="hq-gpu-note">{note}</p>
          </section>

          <section aria-labelledby="hqGpuReport">
            <h3 id="hqGpuReport">{t('hqGpu.section.report')}</h3>
            <div className="hqGpuControls">
              <button type="button" className="hqGhost" onClick={download} data-testid="hq-gpu-download"><Download aria-hidden="true" />{t('hqGpu.report.download')}</button>
              <button type="button" className="hqGhost" onClick={() => void copy()} data-testid="hq-gpu-copy">{t('hqGpu.report.copy')}</button>
            </div>
            <p className="hqGpuHint">{t('hqGpu.report.privacy')}</p>
          </section>
        </div>
      </aside>
    </>
  );
}

