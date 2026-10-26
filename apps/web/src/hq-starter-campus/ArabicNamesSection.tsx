import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, Languages, Loader2, ShieldCheck } from 'lucide-react';
import type { ArabicNamesPreview, ArabicNamesResult } from '@anum/contracts';
import { ApiError, describeError, isPermissionDenied } from '../lib/errors';
import { t } from '../lib/i18n';
import type { createStarterCampusClient } from './client';

const SHOWN = 6;

type State =
  | { kind: 'loading' } | { kind: 'denied' } | { kind: 'error'; detail: string }
  | { kind: 'ready'; preview: ArabicNamesPreview };

/**
 * "Add Arabic names": gives the buildings, floors and rooms of a campus that was set up before names had an Arabic form their
 * Arabic names. Only an empty Arabic name whose English name is a name of the starter list is filled, nothing is ever replaced,
 * and only a click by an owner confirms it (the server checks all three again). Lives under the starter campus dialog.
 */
export function ArabicNamesSection({ client, onChanged }: { client: ReturnType<typeof createStarterCampusClient>; onChanged: () => void }) {
  const [state, setState] = useState<State>({ kind: 'loading' });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ArabicNamesResult | null>(null);
  const [failure, setFailure] = useState<{ detail: string; stale: boolean } | null>(null);
  const alive = useRef(true);

  const read = useCallback(async () => {
    try {
      const preview = await client.arabicPreview();
      if (alive.current) setState({ kind: 'ready', preview });
    } catch (e) {
      if (alive.current) setState(isPermissionDenied(e) ? { kind: 'denied' } : { kind: 'error', detail: describeError(e) });
    }
  }, [client]);
  useEffect(() => { alive.current = true; void read(); return () => { alive.current = false; }; }, [read]);

  const add = async (preview: ArabicNamesPreview) => {
    if (busy || !preview.can_confirm || preview.total === 0) return;
    setBusy(true); setFailure(null);
    try {
      const done = await client.arabicConfirm(preview);
      if (!alive.current) return;
      setResult(done);
      if (done.filled > 0) onChanged();
      setState({ kind: 'loading' });
      void read();
    } catch (e) {
      if (alive.current) setFailure({ detail: describeError(e), stale: e instanceof ApiError && e.status === 409 });
    } finally { if (alive.current) setBusy(false); }
  };

  return <section className="hqScSection hqScArabic" aria-label={t('hqSc.ar.title')} data-testid="sc-arabic-names">
    <div className="hqScPickHead">
      <h3><Languages size={14} aria-hidden="true" /> {t('hqSc.ar.title')}</h3>
    </div>
    <p className="hqScNote">{t('hqSc.ar.intro')}</p>
    {state.kind === 'loading' && <p className="hqScNote" role="status"><Loader2 size={14} className="hqScSpin" aria-hidden="true" /> {t('hqSc.ar.loading')}</p>}
    {state.kind === 'denied' && <p className="hqScNote hqScFail" role="alert">{t('hqSc.denied')}</p>}
    {state.kind === 'error' && <div className="hqScNote hqScFail" role="alert">
      <p>{t('hqSc.error', { detail: state.detail })}</p>
      <button type="button" onClick={() => { setState({ kind: 'loading' }); void read(); }}>{t('hqSc.retry')}</button>
    </div>}
    {state.kind === 'ready' && (state.preview.total === 0
      ? <p className="hqScNote" role="status"><CheckCircle2 size={13} aria-hidden="true" /> {t('hqSc.ar.none')}</p>
      : <>
        <p className="hqScNote" role="status" data-testid="sc-arabic-count">{t('hqSc.ar.count', { ...state.preview.counts })}</p>
        <ul className="hqScArabicList" aria-label={t('hqSc.ar.listLabel')}>
          {state.preview.items.slice(0, SHOWN).map((item) => <li key={`${item.kind}:${item.id}`}>
            <bdi>{item.name}</bdi><span aria-hidden="true">{' > '}</span><bdi dir="rtl" lang="ar">{item.name_ar}</bdi>
          </li>)}
        </ul>
        {state.preview.items.length > SHOWN && <p className="hqScNote">{t('hqSc.ar.more', { count: state.preview.items.length - SHOWN })}</p>}
        <p className="hqScNote"><ShieldCheck size={13} aria-hidden="true" /> {state.preview.can_confirm ? t('hqSc.clickOnly') : t('hqSc.ownersOnly')}</p>
        <div className="hqScActions">
          <button type="button" className="hqScPrimary" disabled={busy || !state.preview.can_confirm} onClick={() => void add(state.preview)} data-testid="sc-arabic-add">
            {busy ? t('hqSc.working') : t('hqSc.ar.add', { count: state.preview.total })}</button>
        </div>
      </>)}
    {result && <p className="hqScNote" role="status" data-testid="sc-arabic-done"><CheckCircle2 size={13} aria-hidden="true" /> {result.filled > 0 ? t('hqSc.ar.done', { count: result.filled }) : t('hqSc.ar.doneNothing')}</p>}
    {failure && <div className="hqScNote hqScFail" role="alert">
      <p>{t('hqSc.confirmFailed', { detail: failure.detail })}</p>
      {failure.stale && <p>{t('hqSc.ar.stale')}</p>}
    </div>}
  </section>;
}
