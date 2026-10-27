// The "Building sign" section of the office builder (docs/ANUM_MINE_HQ_LOGO_SIGNS.md): upload the owner's own logo, pick the brand
// colour and a sign style, remove the logo, reset to the lettered sign. A lazy chunk: the builder loads it with the Floor tab.
import { useCallback, useEffect, useId, useRef, useState, type DragEvent } from 'react';
import { ApiError } from '../lib/errors.ts';
import { t } from '../lib/i18n.ts';
import { getLocale } from '../lib/i18n.ts';
import { pickName } from '../lib/registryNames.ts';
import type { OfficeBuilding } from '../lib/officeSpaceClient.ts';
import { checkLogoFile, formatBytes, type LogoProblem } from './logoFile.ts';
import { fetchLogo, removeLogo, saveLook, uploadLogo } from './logoClient.ts';
import { SignPreview } from './SignPreview.tsx';
import { HEX_COLOR, SIGN_STYLES, normalizeColor, signLook, type SignStyle } from './signModel.ts';
import './hqLogoSigns.css';

type Props = {
  building: OfficeBuilding;
  /** The caller may change the sign (the workspace owner; the server checks every call). */
  editable: boolean;
  /** The builder is busy with another call. */
  busy: boolean;
  /** The server's answer replaces the building record in the builder. */
  onChanged: (building: OfficeBuilding, message: string) => void;
  onReload: () => void;
};

type Decoded = { image: ImageBitmap | HTMLImageElement; width: number; height: number };
type Load = { state: 'idle' } | { state: 'loading' } | { state: 'ready'; decoded: Decoded } | { state: 'none' } | { state: 'failed' };

async function decode(blob: Blob): Promise<Decoded> {
  if (typeof createImageBitmap === 'function') {
    const bitmap = await createImageBitmap(blob);
    return { image: bitmap, width: bitmap.width, height: bitmap.height };
  }
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    return { image, width: image.naturalWidth, height: image.naturalHeight };
  } finally { URL.revokeObjectURL(url); }
}
const release = (d: Decoded | null): void => { if (d && 'close' in d.image) d.image.close(); };

function messageOf(error: unknown): { text: string; stale: boolean } {
  if (error instanceof ApiError) {
    if (error.status === 409) return { text: t('hqLogo.err.stale'), stale: true };
    if (error.status === 403) return { text: error.detail ?? t('hqLogo.err.denied'), stale: false };
    return { text: error.detail ?? t('hqLogo.err.failed', { reason: String(error.status) }), stale: false };
  }
  return { text: t('hqLogo.err.failed', { reason: error instanceof Error ? error.message : '' }), stale: false };
}

export default function BuildingSignSection({ building, editable, busy, onChanged, onReload }: Props) {
  const locale = getLocale();
  const rtl = locale === 'ar';
  const name = pickName(building.name, building.name_ar, locale);
  const ids = { color: useId(), code: useId(), file: useId(), colorMsg: useId(), help: useId() };
  const fileInput = useRef<HTMLInputElement | null>(null);

  const [load, setLoad] = useState<Load>(building.logo_sha256 ? { state: 'loading' } : { state: 'none' });
  const [attempt, setAttempt] = useState(0);
  const [pending, setPending] = useState<{ file: File; decoded: Decoded } | null>(null);
  const [colorText, setColorText] = useState(building.brand_color ?? '');
  const [style, setStyle] = useState<SignStyle | ''>((building.sign_style as SignStyle | null) ?? '');
  const [problem, setProblem] = useState<{ text: string; stale: boolean } | null>(null);
  const [working, setWorking] = useState<'' | 'upload' | 'remove' | 'look' | 'reset'>('');
  const [confirmReset, setConfirmReset] = useState(false);
  const [dragging, setDragging] = useState(false);
  const decodedRef = useRef<Decoded | null>(null);

  // The stored logo: fetched with the person's credentials, revalidated by its hash; none when the building has none.
  useEffect(() => {
    if (!building.logo_sha256) { release(decodedRef.current); decodedRef.current = null; setLoad({ state: 'none' }); return undefined; }
    const controller = new AbortController();
    setLoad({ state: 'loading' });
    void (async () => {
      try {
        const blob = await fetchLogo(building.id, controller.signal);
        if (controller.signal.aborted) return;
        if (!blob) { setLoad({ state: 'none' }); return; }
        const decoded = await decode(blob);
        if (controller.signal.aborted) { release(decoded); return; }
        release(decodedRef.current);
        decodedRef.current = decoded;
        setLoad({ state: 'ready', decoded });
      } catch {
        if (!controller.signal.aborted) setLoad({ state: 'failed' });
      }
    })();
    return () => controller.abort();
  }, [building.id, building.logo_sha256, attempt]);
  useEffect(() => () => { release(decodedRef.current); }, []);
  useEffect(() => () => { release(pending?.decoded ?? null); }, [pending]);

  // A new server record resets the form.
  useEffect(() => { setColorText(building.brand_color ?? ''); setStyle((building.sign_style as SignStyle | null) ?? ''); }, [building.id, building.revision, building.brand_color, building.sign_style]);

  const color = normalizeColor(colorText);
  const colorInvalid = colorText.trim() !== '' && !HEX_COLOR.test(colorText.trim());
  const look = signLook(style || null, color);
  const shown = pending?.decoded ?? (load.state === 'ready' ? load.decoded : null);
  const lookChanged = (color ?? null) !== (building.brand_color ?? null) || (style || null) !== (building.sign_style ?? null);
  const off = !editable || busy || working !== '';

  const choose = useCallback(async (file: File | undefined) => {
    if (!file) return;
    setProblem(null);
    const check = await checkLogoFile(file);
    if (!check.ok) { setProblem({ text: t(`hqLogo.err.${check.problem}` as `hqLogo.err.${LogoProblem}`), stale: false }); return; }
    try {
      const decoded = await decode(file);
      setPending({ file, decoded });
    } catch { setProblem({ text: t('hqLogo.err.unreadable'), stale: false }); }
  }, []);

  const run = useCallback(async (kind: typeof working, action: () => Promise<{ building: OfficeBuilding; message: string }>) => {
    setWorking(kind); setProblem(null);
    try {
      const done = await action();
      onChanged(done.building, done.message);
      return true;
    } catch (error) {
      setProblem(messageOf(error));
      return false;
    } finally { setWorking(''); }
  }, [onChanged]);

  const upload = () => {
    if (!pending) return;
    void run('upload', async () => ({ building: await uploadLogo(building.id, building.revision, pending.file), message: t('hqLogo.done.logo') })).then((ok) => { if (ok) setPending(null); });
  };
  const remove = () => void run('remove', async () => ({ building: await removeLogo(building.id, building.revision), message: t('hqLogo.done.removed') }));
  const save = () => void run('look', async () => ({
    building: await saveLook(building.id, { expected_revision: building.revision, sign_style: style || null, brand_color: color }), message: t('hqLogo.done.look'),
  }));
  const reset = () => void run('reset', async () => {
    let current = building;
    if (building.logo_sha256) current = await removeLogo(building.id, current.revision);
    if (current.sign_style || current.brand_color) current = await saveLook(building.id, { expected_revision: current.revision, sign_style: null, brand_color: null });
    return { building: current, message: t('hqLogo.done.reset') };
  }).then((ok) => { if (ok) setConfirmReset(false); });

  const onDrop = (event: DragEvent) => { event.preventDefault(); setDragging(false); if (!off) void choose(event.dataTransfer.files[0]); };
  const hasAnything = Boolean(building.logo_sha256 || building.sign_style || building.brand_color);
  const contrastText = !color ? t('hqLogo.contrast.none')
    : look.readable ? t('hqLogo.contrast.ok', { ratio: (look.ratio ?? 0).toFixed(1), needs: String(look.needs) })
      : look.part === 'frame' ? t('hqLogo.contrast.lowFrame', { ratio: (look.ratio ?? 0).toFixed(1), needs: String(look.needs) })
        : t('hqLogo.contrast.low', { ratio: (look.ratio ?? 0).toFixed(1), needs: String(look.needs), part: t(`hqLogo.part.${look.part === 'glow' ? 'glow' : 'bar'}`) });

  return (
    <section className="hqbSection hqlSection" aria-labelledby={`${ids.file}-h`} data-testid="hql-section">
      <h3 id={`${ids.file}-h`}>{t('hqLogo.title')}</h3>
      <p className="hqbHint">{t('hqLogo.intro')}</p>
      {!editable && <p className="hqbHint" role="note" data-testid="hql-denied">{t('hqLogo.denied')}</p>}

      <div className="hqlPreviewWrap">
        <span className="hqbHint" id={`${ids.file}-pv`}>{t('hqLogo.previewLabel')}</span>
        <SignPreview input={{ name, rtl, style: style || null, color, logo: shown?.image ?? null, logoWidth: shown?.width, logoHeight: shown?.height }} />
      </div>

      <div className="hqlLogoBlock">
        <h4>{t('hqLogo.logo')}</h4>
        {load.state === 'loading' && <p className="hqbHint" role="status">{t('hqLogo.loading')}</p>}
        {load.state === 'failed' && (
          <div className="hqbCallout" role="alert">
            <p>{t('hqLogo.loadFailed')}</p>
            <button type="button" onClick={() => setAttempt((n) => n + 1)}>{t('hqLogo.retry')}</button>
          </div>
        )}
        {load.state === 'none' && !pending && <p className="hqbHint" data-testid="hql-empty">{t('hqLogo.empty')}</p>}
        {load.state === 'ready' && !pending && <p className="hqbHint" data-testid="hql-has-logo">{t('hqLogo.current')}: {t('hqLogo.currentAlt', { name })}</p>}

        {editable && (
          <div
            className="hqlDrop" data-dragging={dragging} role="group" aria-label={t('hqLogo.dropAria')}
            onDragOver={(e) => { e.preventDefault(); if (!off) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}
          >
            <input ref={fileInput} id={ids.file} type="file" accept="image/png,image/jpeg,image/webp" hidden data-testid="hql-file"
              onChange={(e) => { void choose(e.target.files?.[0]); e.target.value = ''; }} />
            <button type="button" disabled={off} onClick={() => fileInput.current?.click()} aria-describedby={ids.help}>
              {building.logo_sha256 || pending ? t('hqLogo.replace') : t('hqLogo.choose')}
            </button>
            <span className="hqbHint">{t('hqLogo.drop')}</span>
          </div>
        )}
        <p className="hqbHint" id={ids.help}>{t('hqLogo.help')}</p>
        {pending && (
          <div className="hqlPending" data-testid="hql-pending">
            <span className="hqbHint" dir="auto">{t('hqLogo.file', { name: pending.file.name, size: formatBytes(pending.file.size) })}</span>
            <div className="hqbRow">
              <button type="button" className="hqbPrimary" disabled={off} onClick={upload}>{working === 'upload' ? t('hqLogo.uploading') : t('hqLogo.upload')}</button>
              <button type="button" className="hqbQuiet" disabled={working !== ''} onClick={() => setPending(null)}>{t('hqLogo.cancelPick')}</button>
            </div>
          </div>
        )}
        {editable && building.logo_sha256 && !pending && (
          <div className="hqbRow"><button type="button" className="hqbQuiet" disabled={off} onClick={remove}>{t('hqLogo.remove')}</button></div>
        )}
      </div>

      <div className="hqlColor">
        <label className="hqbField" htmlFor={ids.code}><span>{t('hqLogo.color')}</span></label>
        <div className="hqbRow">
          <input id={ids.color} type="color" className="hqlSwatch" aria-label={t('hqLogo.colorPick')} disabled={off} value={color ?? '#34454f'}
            onChange={(e) => setColorText(e.target.value)} data-testid="hql-color-picker" />
          <input id={ids.code} type="text" inputMode="text" dir="ltr" maxLength={7} placeholder="#1f6f4a" value={colorText} disabled={off}
            aria-invalid={colorInvalid} aria-describedby={`${ids.colorMsg}`} onChange={(e) => setColorText(e.target.value.trim())} data-testid="hql-color" />
          {colorText && <button type="button" className="hqbQuiet" disabled={off} onClick={() => setColorText('')}>{t('hqLogo.colorClear')}</button>}
        </div>
        <p id={ids.colorMsg} className="hqbHint" role="status" data-readable={look.readable} data-testid="hql-contrast">
          {colorInvalid ? t('hqLogo.colorInvalid') : contrastText}
        </p>
        <p className="hqbHint">{t('hqLogo.colorHint')}</p>
      </div>

      <fieldset className="hqbFieldset hqlStyles" disabled={off}>
        <legend>{t('hqLogo.style')}</legend>
        <div className="hqlStyleList" role="radiogroup" aria-label={t('hqLogo.style')}>
          {SIGN_STYLES.map((s) => (
            <label key={s} className="hqlStyle" data-selected={(style || 'plain') === s}>
              <input type="radio" name={`${ids.file}-style`} value={s} checked={(style || 'plain') === s} onChange={() => setStyle(s)} data-testid={`hql-style-${s}`} />
              <span><strong>{t(`hqLogo.style.${s}` as 'hqLogo.style.plain')}</strong><small>{t(`hqLogo.style.${s}.hint` as 'hqLogo.style.plain.hint')}</small></span>
            </label>
          ))}
        </div>
      </fieldset>

      {problem && (
        <div className="hqbBanner hqlProblem" data-tone="error" role="alert" data-testid="hql-problem">
          <p>{problem.text}</p>
          {problem.stale && <button type="button" onClick={onReload}>{t('hqLogo.reload')}</button>}
        </div>
      )}

      {editable && (
        <div className="hqbRow">
          <button type="button" className="hqbPrimary" disabled={off || !lookChanged || colorInvalid} onClick={save} data-testid="hql-save">
            {t('hqLogo.saveLook')}
          </button>
          {hasAnything && !confirmReset && <button type="button" className="hqbQuiet" disabled={off} onClick={() => setConfirmReset(true)} data-testid="hql-reset">{t('hqLogo.reset')}</button>}
        </div>
      )}
      {confirmReset && (
        <div className="hqbConfirm" role="alertdialog" aria-label={t('hqLogo.reset')}>
          <p>{t('hqLogo.resetQuestion')}</p>
          <div className="hqbRow">
            <button type="button" className="hqbDanger" disabled={off} onClick={reset} data-testid="hql-reset-confirm">{t('hqLogo.resetConfirm')}</button>
            <button type="button" onClick={() => setConfirmReset(false)}>{t('hqLogo.cancel')}</button>
          </div>
        </div>
      )}
    </section>
  );
}
