// Finance stage 9 (docs/finance.md#wider-live): wider live use, the framework only and OFF. The
// graduation checklist comes from the server (measured from the stage 7 and 8 records); a step up is
// one tier under hard ceilings, approved by the owner with its code once, then a cooling-off; an
// incident steps the tier down and freezes widening until the owner reviews it. Nothing runs live.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Ban, CircleCheck, CircleX, Layers, Plus, ShieldAlert, Trash2 } from 'lucide-react';
import type { FinanceWider, FinanceWidening } from '@anum/contracts';
import * as api from './lib/financeApi';
import { expiresLabel, formatAmount, shortHash } from './lib/finance';
import { draftFromLimits, limitsFromDraft, type LiveDraft } from './lib/financeTrading';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { Modal } from './Dialog';
import { FormActions, FormError, LoadError, Notice, failed, type Load } from './FinanceParts';

const KINDS = ['trade', 'transfer', 'spend', 'bill_payment'] as const;
const kindLabel = (kind: string) => t(`finance.wider.kind.${kind}` as MessageKey);
const criterionLabel = (code: string) => {
  const key = `finance.wider.criterion.${code}` as MessageKey;
  const text = t(key);
  return text === key ? code : text;
};

export function WiderPanel() {
  const [state, setState] = useState<Load<FinanceWider>>({ kind: 'loading' });
  const load = useCallback(async () => {
    try { setState({ kind: 'ready', value: await api.getWider() }); } catch (e) { setState(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const [proposing, setProposing] = useState(false), [review, setReview] = useState<FinanceWidening | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  if (state.kind === 'loading') return <section className="surface cmPanel"><Notice busy>{t('finance.execution.loading')}</Notice></section>;
  if (state.kind !== 'ready') return <section className="surface cmPanel"><LoadError message={t('finance.wider.error', { message: state.message })} retry={() => void load()} /></section>;
  const w = state.value;
  async function reviewIncidents() {
    try { setState({ kind: 'ready', value: await api.reviewIncidents() }); setNotice({ text: t('finance.wider.reviewed') }); } catch (e) { setNotice({ text: describeError(e), error: true }); }
  }
  const pending = w.widenings.filter((x) => x.status === 'pending');
  const money = (rows: { currency: string; amount: string }[]) => rows.length ? rows.map((r) => formatAmount(r.amount, r.currency)).join(' · ') : t('finance.live.nothingAllowed');
  return <section className="surface cmPanel" aria-labelledby="fin-wider">
    <div className="sectionHeader"><h2 id="fin-wider"><Layers aria-hidden="true" className="cmInlineIcon" />{t('finance.wider.title')}</h2>
      <div className="finHeadActions"><button type="button" className="secondary" disabled={w.frozen} onClick={() => setProposing(true)}><Plus aria-hidden="true" />{t('finance.wider.propose')}</button></div></div>
    <p className="notice errorNotice finLiveOff" role="status"><Ban aria-hidden="true" /><span><strong>{t('finance.wider.off')}</strong> {t('finance.wider.offText')}</span></p>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {w.frozen && <div className="notice finSafeMissing" role="alert"><ShieldAlert aria-hidden="true" />
      <span>{t('finance.wider.frozen', { reason: t(`finance.wider.incident.${w.frozen_reason ?? 'failed_execution'}` as MessageKey), date: w.frozen_at ? formatDateTime(w.frozen_at) : '' })}</span>
      <button type="button" className="secondary" onClick={() => void reviewIncidents()}>{t('finance.wider.review')}</button></div>}
    <dl className="finFigures">
      <div><dt>{t('finance.wider.tier')}</dt><dd>{t('finance.wider.tierValue', { tier: w.tier, max: w.ceilings.max_tier })}</dd></div>
      <div><dt>{t('finance.wider.kinds')}</dt><dd>{w.kinds.map(kindLabel).join(', ')}</dd></div>
      <div><dt>{t('finance.live.perAction')}</dt><dd>{money(w.limits.max_per_action)}</dd></div>
      <div><dt>{t('finance.wider.ceilings')}</dt><dd>{t('finance.wider.ceilingsValue', { percent: w.ceilings.step_percent, hours: w.ceilings.cooling_hours })}</dd></div>
    </dl>
    <h3 className="finSubHead">{t('finance.wider.checklist', { days: w.ceilings.window_days })}</h3>
    <ul className="finRules finPreconditions">{w.criteria.map((c) => <li key={c.code} className={c.met ? '' : 'finUnmet'}>
      {c.met ? <CircleCheck aria-hidden="true" /> : <CircleX aria-hidden="true" />}
      <span>{criterionLabel(c.code)} {/^\d+$/.test(c.value) && /^\d+$/.test(c.needed) ? <small className="muted" dir="ltr">{t('finance.wider.measured', { value: c.value, needed: c.needed })}</small> : null}
        <span className="visuallyHidden"> {t(c.met ? 'finance.live.met' : 'finance.live.unmet')}</span></span></li>)}</ul>
    {pending.length > 0 && <ul className="cmList finItems">{pending.map((x) => <li key={x.id}><div>
      <strong>{t('finance.wider.stepLine', { tier: x.tier })}{x.proposed_by === 'agent' ? <span className="statusBadge neutral">{t('finance.trading.byAgent')}</span> : null}</strong>
      <small>{expiresLabel(x.expires_at)}</small></div>
      <div className="finItemEnd"><button type="button" onClick={() => setReview(x)}>{t('finance.trading.review')}</button></div></li>)}</ul>}
    {w.widenings.some((x) => x.status === 'approved' && x.effective_at && new Date(x.effective_at) > new Date()) && <p className="muted">
      {t('finance.wider.cooling', { date: formatDateTime(w.widenings.find((x) => x.status === 'approved')?.effective_at ?? '') })}</p>}
    <h3 className="finSubHead">{t('finance.wider.incidents')}</h3>
    {w.incidents.length === 0 ? <p className="muted">{t('finance.wider.noIncidents')}</p> : <ul className="cmList finItems">{w.incidents.map((i) => <li key={i.id}><div>
      <strong>{t(`finance.wider.incident.${i.code}` as MessageKey)}</strong>
      <small>{formatDateTime(i.created_at)} · {t('finance.wider.stepDown', { before: i.tier_before, after: i.tier_after })}{i.reviewed_at ? ` · ${t('finance.wider.reviewedAt')}` : ''}</small></div></li>)}</ul>}
    {proposing && <WideningEditor wider={w} onClose={(made, error) => { setProposing(false); if (made) { setReview(made); void load(); } if (error) setNotice({ text: error, error: true }); }} />}
    {review && <WideningReview widening={review} onClose={(text, error) => { setReview(null); if (text) setNotice({ text, error }); void load(); }} />}
  </section>;
}

function WideningEditor({ wider, onClose }: { wider: FinanceWider; onClose: (made: FinanceWidening | null, error?: string) => void }) {
  const [draft, setDraft] = useState<LiveDraft>(() => draftFromLimits(wider.limits));
  const [kinds, setKinds] = useState<string[]>(wider.kinds);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  const set = <K extends keyof LiveDraft>(k: K, v: LiveDraft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = limitsFromDraft(draft);
    if (!checked.ok) { setError(checked.error); return; }
    if (kinds.filter((k) => !wider.kinds.includes(k)).length > 1) { setError(t('finance.wider.error.oneKind')); return; }
    setBusy(true); setError(null);
    try { onClose(await api.proposeWidening({ limits: checked.value, kinds })); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  const rows = (key: 'perAction' | 'perDay' | 'perMonth', legend: string) => <fieldset className="finMapping"><legend>{legend}</legend>
    {draft[key].map((row, index) => <div key={index} className="cmFilters finLimitRow">
      <label className="cmInline"><span>{t('finance.form.currency')}</span><input value={row.currency} maxLength={3} dir="ltr" className="finCurrencyInput"
        onChange={(e) => set(key, draft[key].map((r, i) => (i === index ? { ...r, currency: e.target.value.toUpperCase().slice(0, 3) } : r)))} /></label>
      <label className="cmInline"><span>{t('finance.guardian.amount')}</span><input value={row.amount} inputMode="decimal" dir="ltr"
        onChange={(e) => set(key, draft[key].map((r, i) => (i === index ? { ...r, amount: e.target.value } : r)))} /></label>
      <button type="button" className="iconButton" onClick={() => set(key, draft[key].filter((_, i) => i !== index))} aria-label={t('finance.guardian.removeRow', { currency: row.currency || String(index + 1) })}><Trash2 aria-hidden="true" /></button>
    </div>)}
    <button type="button" className="secondary" onClick={() => set(key, [...draft[key], { currency: 'AED', amount: '' }])}><Plus aria-hidden="true" />{t('finance.guardian.addLimit')}</button>
  </fieldset>;
  return <Modal title={t('finance.wider.proposeTitle', { tier: wider.tier + 1 })} className="formDialog" onClose={() => onClose(null)}>
    <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <p className="muted">{t('finance.wider.proposeIntro', { percent: wider.ceilings.step_percent, hours: wider.ceilings.cooling_hours })}</p>
      {rows('perAction', t('finance.live.perAction'))}
      {rows('perDay', t('finance.live.perDay'))}
      {rows('perMonth', t('finance.live.perMonth'))}
      <fieldset className="finMapping"><legend>{t('finance.wider.kinds')}</legend>
        <div className="finChecks">{KINDS.map((k) => <label key={k} className="finCheck"><input type="checkbox" checked={kinds.includes(k)} disabled={wider.kinds.includes(k)}
          onChange={(e) => setKinds(e.target.checked ? [...kinds, k] : kinds.filter((x) => x !== k))} />{kindLabel(k)}</label>)}</div></fieldset>
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={() => onClose(null)} label={t('finance.guardian.reviewChange')} />
    </form>
  </Modal>;
}

function WideningReview({ widening, onClose }: { widening: FinanceWidening; onClose: (text: string | null, error?: boolean) => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const money = (rows: { currency: string; amount: string }[]) => rows.map((r) => formatAmount(r.amount, r.currency)).join(' · ') || '-';
  async function act(approve: boolean) {
    setBusy(true); setError(null);
    try {
      if (approve) { await api.approveWidening(widening.id, widening.payload_hash); onClose(t('finance.wider.approved')); }
      else { await api.rejectWidening(widening.id); onClose(t('finance.trading.rejected')); }
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t('finance.wider.reviewTitle', { tier: widening.tier })} className="formDialog" onClose={() => onClose(null)}>
    <div className="finChange">
      {widening.proposed_by === 'agent' && <p className="muted">{t('finance.trading.byAgentLong')}</p>}
      <dl className="finFigures">
        <div><dt>{t('finance.live.perAction')}</dt><dd>{money(widening.limits.max_per_action)}</dd></div>
        <div><dt>{t('finance.live.perDay')}</dt><dd>{money(widening.limits.max_per_day)}</dd></div>
        <div><dt>{t('finance.live.perMonth')}</dt><dd>{money(widening.limits.max_per_month)}</dd></div>
        <div><dt>{t('finance.wider.kinds')}</dt><dd>{widening.kinds.map(kindLabel).join(', ')}</dd></div>
      </dl>
      <p className="muted finHelp">{t('finance.wider.reviewText')}</p>
      <p className="muted finHash">{t('finance.trading.code')} <code dir="ltr">{shortHash(widening.payload_hash)}</code> · {expiresLabel(widening.expires_at)}</p>
    </div>
    <FormError id={errorId} text={error} />
    <div className="actions">
      <button type="button" disabled={busy} onClick={() => void act(true)}><CircleCheck aria-hidden="true" />{t('finance.wider.approve')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => void act(false)}><CircleX aria-hidden="true" />{t('finance.trading.reject')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => onClose(null)}>{t('dialog.close')}</button>
    </div>
  </Modal>;
}
