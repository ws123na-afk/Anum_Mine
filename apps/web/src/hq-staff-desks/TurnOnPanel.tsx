import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { CheckCircle2, Loader2, ShieldCheck } from 'lucide-react';
import type { StaffPausedPage, StaffTurnOnPreview, StaffTurnOnResult } from '@anum/contracts';
import * as api from '../lib/api';
import { describeError, isPermissionDenied } from '../lib/errors';
import { t, type MessageKey } from '../lib/i18n';
import { createStaffDesksClient, money } from './client';

const client = createStaffDesksClient(api.apiRequest, api.currentScope);
const PAGE = 25;

/** The agents Staff my campus made that are still paused: turn one on, or review the cost cap total of a selection first. */
export function TurnOnPanel({ locale, onChanged }: { locale: 'en' | 'ar'; onChanged: () => void }) {
  const [page, setPage] = useState(0);
  const [data, setData] = useState<StaffPausedPage | null>(null);
  const [state, setState] = useState<'loading' | 'denied' | 'error' | 'ready'>('loading');
  const [detail, setDetail] = useState('');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [review, setReview] = useState<StaffTurnOnPreview | null>(null);
  const [result, setResult] = useState<StaffTurnOnResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const alive = useRef(true);
  const headId = useId();

  const read = useCallback(async (at: number) => {
    setState('loading');
    try {
      const next = await client.paused(at * PAGE, PAGE);
      if (!alive.current) return;
      setData(next);
      setPicked((old) => new Set([...old].filter((id) => next.all_ids.includes(id))));
      setState('ready');
    } catch (e) {
      if (!alive.current) return;
      setDetail(describeError(e));
      setState(isPermissionDenied(e) ? 'denied' : 'error');
    }
  }, []);
  useEffect(() => { alive.current = true; void read(page); return () => { alive.current = false; }; }, [read, page]);

  const change = (next: Set<string>) => { setPicked(next); setReview(null); setResult(null); setMessage(''); };
  const flip = (id: string) => { const next = new Set(picked); if (next.has(id)) next.delete(id); else next.add(id); change(next); };
  const guard = async (work: () => Promise<void>) => { setBusy(true); setMessage(''); try { await work(); } catch (e) { setMessage(describeError(e)); } finally { setBusy(false); } };
  const reviewCost = (ids: string[]) => guard(async () => { setReview(await client.turnOnPreview(ids)); });
  const confirm = () => guard(async () => {
    if (!review) return;
    const out = await client.turnOn([...picked], review);
    setResult(out); setReview(null); setPicked(new Set());
    onChanged();
    await read(page);
  });
  // One agent: the same bulk path with a selection of one, so the cap is shown and the click is the confirmation.
  const one = (id: string) => guard(async () => {
    const pv = await client.turnOnPreview([id]);
    const out = await client.turnOn([id], pv);
    setResult(out); onChanged(); await read(page);
  });

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;
  return <section className="hqSdSection" aria-labelledby={headId}>
    <h3 id={headId}>{t('hqSd.on.title')}</h3>
    <p className="hqSdNote">{t('hqSd.on.intro')}</p>
    {state === 'loading' && <p className="hqSdNote" role="status"><Loader2 size={14} className="hqSdSpin" aria-hidden="true" /> {t('hqSd.loading')}</p>}
    {state === 'denied' && <p className="hqSdNote hqSdFail" role="alert">{t('hqSd.denied')}</p>}
    {state === 'error' && <div className="hqSdNote hqSdFail" role="alert"><p>{t('hqSd.error', { detail })}</p><button type="button" onClick={() => void read(page)}>{t('hqSd.retry')}</button></div>}
    {state === 'ready' && data && (data.total === 0
      ? <p className="hqSdNote">{t('hqSd.on.empty')}</p>
      : <>
        <div className="hqSdPickHead">
          <span className="hqSdCount" role="status">{t('hqSd.on.selected', { count: picked.size, total: data.total })}</span>
          <button type="button" onClick={() => change(new Set([...picked, ...data.items.map((i) => i.agent_id)]))}>{t('hqSd.on.selectPage')}</button>
          <button type="button" onClick={() => change(new Set(data.all_ids))}>{t('hqSd.on.selectAll', { total: data.total })}</button>
          <button type="button" onClick={() => change(new Set())} disabled={picked.size === 0}>{t('hqSd.selectNone')}</button>
        </div>
        <ul className="hqSdDesks">
          {data.items.map((a) => <li key={a.agent_id}>
            <input type="checkbox" checked={picked.has(a.agent_id)} onChange={() => flip(a.agent_id)} aria-label={a.name} />
            <div><strong>{a.name}</strong><small>{a.monthly_cost_limit_usd == null ? t('hqSd.on.noCap') : t('hqSd.on.cap', { cap: money(a.monthly_cost_limit_usd, locale) })}</small></div>
            {data.can_confirm && <button type="button" disabled={busy} aria-label={t('hqSd.on.oneLabel', { name: a.name })} onClick={() => void one(a.agent_id)}>{t('hqSd.on.one')}</button>}
          </li>)}
        </ul>
        {pages > 1 && <nav className="hqSdPager" aria-label={t('hqSd.desks.pager')}>
          <button type="button" onClick={() => setPage(page - 1)} disabled={page === 0}>{t('hqSd.desks.prev')}</button>
          <span role="status">{t('hqSd.desks.page', { page: page + 1, pages })}</span>
          <button type="button" onClick={() => setPage(page + 1)} disabled={page >= pages - 1}>{t('hqSd.desks.next')}</button>
        </nav>}
        {data.can_confirm ? <div className="hqSdReview">
          <button type="button" onClick={() => void reviewCost([...picked])} disabled={busy || picked.size === 0}>{t('hqSd.on.review')}</button>
          {review && <div className="hqSdCost" role="status">
            <p><strong>{t('hqSd.on.cost', { count: review.items.length, total: money(review.max_monthly_usd, locale) })}</strong></p>
            {review.workspace_budget.visible && review.workspace_budget.monthly_cost_limit_usd != null
              && <p className="hqSdNote" data-tone={review.within_budget === false ? 'warn' : undefined}>
                {t('hqSd.cost.budget', { limit: money(review.workspace_budget.monthly_cost_limit_usd, locale) })} {review.within_budget ? t('hqSd.cost.within') : t('hqSd.cost.over')}</p>}
            {review.agents_without_cap.length > 0 && <p className="hqSdWarn">{t('hqSd.on.noCapWarn', { count: review.agents_without_cap.length })}</p>}
            <p className="hqSdNote"><ShieldCheck size={13} aria-hidden="true" /> {t('hqSd.clickOnly')}</p>
            <button type="button" className="hqSdPrimary" onClick={() => void confirm()} disabled={busy || review.items.length === 0}>{t('hqSd.on.confirm', { count: review.items.length })}</button>
          </div>}
        </div> : <p className="hqSdNote">{t('hqSd.ownersOnly')}</p>}
      </>)}
    {result && <p className="hqSdNote" role="status" data-tone={result.counts.failed ? 'warn' : undefined}><CheckCircle2 size={14} aria-hidden="true" /> {t('hqSd.on.done', { on: result.counts.on, failed: result.counts.failed })}</p>}
    {message && <p className="hqSdNote hqSdFail" role="alert">{t('hqSd.on.failed', { detail: message })}</p>}
  </section>;
}
