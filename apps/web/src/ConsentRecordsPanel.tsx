// Consent records for campaigns (docs/privacy.md#consent-records-for-campaigns): who agreed to marketing,
// on what lawful basis, where and when, and who withdrew. Marketing sends check them as well as the
// opt-out list. Owners and organization administrators manage them; the API's 403 is shown in place.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { CircleDot, Lock, Plus, RefreshCw, ShieldCheck, Undo2 } from 'lucide-react';
import { ConfirmDialog } from './Dialog';
import { apiRequest } from './lib/api';
import {
  CONSENT_CHANNELS, CONSENT_PAGE, LAWFUL_BASES, basisLabel, consentBody, consentChannelLabel, consentsPath, emptyConsentDraft, withdrawReason,
  type ConsentChannel, type ConsentDraft, type ConsentPage, type ConsentRecord, type LawfulBasis,
} from './lib/consents';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';

type Flash = { text: string; error?: boolean } | null;
const today = () => new Date().toISOString().slice(0, 10);
const day = (value: string) => formatDateTime(value, { dateStyle: 'medium' });

function Notice({ text, error }: { text: string; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{text}</p>;
}

export function ConsentRecordsPanel() {
  const [page, setPage] = useState<ConsentPage | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'forbidden' | { error: string }>('loading');
  const [q, setQ] = useState('');
  const [filter, setFilter] = useState<'active' | 'withdrawn' | ''>('');
  const [offset, setOffset] = useState(0);
  const [draft, setDraft] = useState<ConsentDraft>(() => emptyConsentDraft(today()));
  const [formError, setFormError] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [withdrawing, setWithdrawing] = useState<ConsentRecord | null>(null);
  const [reason, setReason] = useState('');
  const request = useRef(0);
  const id = useId();
  const fetchPage = useCallback(async (query: string, st: 'active' | 'withdrawn' | '', at: number) => {
    const mine = ++request.current;
    try {
      const next = await apiRequest<ConsentPage>(consentsPath(query, st, at), { method: 'GET' });
      if (mine === request.current) { setPage(next); setState('ready'); }
    } catch (error) {
      if (mine === request.current) setState(isPermissionDenied(error) ? 'forbidden' : { error: describeError(error) });
    }
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => { void fetchPage(q, filter, offset); }, q ? 300 : 0);
    return () => clearTimeout(timer);
  }, [fetchPage, q, filter, offset]);

  if (state === 'forbidden') {
    return <section className="surface adminForbidden" aria-label={t('consent.title')}><span className="adminIcon"><Lock aria-hidden="true" /></span>
      <div><h3>{t('consent.title')}</h3><p>{t('consent.denied')}</p></div></section>;
  }
  const record = async (event: FormEvent) => {
    event.preventDefault();
    const checked = consentBody(draft, today());
    if (!checked.ok) { setFormError(t(checked.error)); return; }
    setFormError(null); setBusy('record'); setFlash(null);
    try {
      const saved = await apiRequest<ConsentRecord>('/api/v1/contact-consents', { method: 'POST', body: JSON.stringify(checked.body) });
      setFlash({ text: t('consent.recorded', { contact: saved.masked }) });
      setDraft(emptyConsentDraft(today())); setOffset(0); await fetchPage(q, filter, 0);
    } catch (error) { setFormError(describeError(error)); }
    finally { setBusy(null); }
  };
  const setRule = async (on: boolean) => {
    setBusy('rule'); setFlash(null);
    try {
      await apiRequest('/api/v1/contact-consents/settings', { method: 'PUT', body: JSON.stringify({ require_for_marketing: on }) });
      setFlash({ text: on ? t('consent.ruleOn') : t('consent.ruleOff') });
      await fetchPage(q, filter, offset);
    } catch (error) { setFlash({ text: describeError(error), error: true }); }
    finally { setBusy(null); }
  };
  const withdraw = async () => {
    const value = withdrawReason(reason);
    if (!withdrawing || !value) return;
    setBusy('withdraw');
    try {
      await apiRequest(`/api/v1/contact-consents/${encodeURIComponent(withdrawing.id)}/withdraw`, { method: 'POST', body: JSON.stringify({ reason: value }) });
      setFlash({ text: t('consent.withdrawn', { contact: withdrawing.masked }) });
      setWithdrawing(null); setReason(''); await fetchPage(q, filter, offset);
    } catch (error) { setFlash({ text: describeError(error), error: true }); setWithdrawing(null); }
    finally { setBusy(null); }
  };
  const rule = page?.settings.require_for_marketing ?? false;
  return <section className="surface consentPanel" aria-labelledby={`${id}-title`}>
    <div className="sectionHeader"><div>
      <p className="eyebrow">{t('consent.eyebrow')}</p><h2 id={`${id}-title`}>{t('consent.title')}</h2><p className="muted">{t('consent.intro')}</p>
    </div><ShieldCheck className="sectionIcon" aria-hidden="true" /></div>
    {page && <label className="consentRule"><input type="checkbox" role="switch" checked={rule} disabled={busy !== null} onChange={(event) => void setRule(event.target.checked)} />
      <span>{t('consent.rule')}<small className="muted">{t('consent.ruleHint')}</small></span></label>}
    {flash && <Notice text={flash.text} error={flash.error} />}
    {typeof state === 'object' && <div className="actions"><Notice text={t('consent.error', { message: state.error })} error />
      <button type="button" className="secondary" onClick={() => void fetchPage(q, filter, offset)}><RefreshCw aria-hidden="true" />{t('consent.retry')}</button></div>}
    <div className="suppLayout">
      <form className="surface suppAdd" aria-label={t('consent.addTitle')} noValidate onSubmit={(event) => void record(event)}>
        <h3>{t('consent.addTitle')}</h3>
        <label className="field" htmlFor={`${id}-contact`}><span>{t('consent.field.contact')}</span>
          <input id={`${id}-contact`} dir="ltr" value={draft.recipient} maxLength={320} autoComplete="off" spellCheck={false} onChange={(e) => setDraft({ ...draft, recipient: e.target.value })} /></label>
        <div className="suppPair">
          <label className="field"><span>{t('consent.field.channel')}</span>
            <select value={draft.channel} onChange={(e) => setDraft({ ...draft, channel: e.target.value as ConsentChannel })}>
              {CONSENT_CHANNELS.map((c) => <option key={c} value={c}>{consentChannelLabel(c)}</option>)}</select></label>
          <label className="field"><span>{t('consent.field.basis')}</span>
            <select value={draft.lawful_basis} onChange={(e) => setDraft({ ...draft, lawful_basis: e.target.value as LawfulBasis })}>
              {LAWFUL_BASES.map((b) => <option key={b} value={b}>{basisLabel(b)}</option>)}</select></label>
        </div>
        <label className="field" htmlFor={`${id}-source`}><span>{t('consent.field.source')}</span>
          <input id={`${id}-source`} value={draft.source} maxLength={200} placeholder={t('consent.field.sourceHint')} onChange={(e) => setDraft({ ...draft, source: e.target.value })} /></label>
        <label className="field" htmlFor={`${id}-date`}><span>{t('consent.field.date')}</span>
          <input id={`${id}-date`} type="date" dir="ltr" value={draft.consented_on} max={today()} onChange={(e) => setDraft({ ...draft, consented_on: e.target.value })} /></label>
        <label className="field" htmlFor={`${id}-note`}><span>{t('consent.field.note')}</span>
          <input id={`${id}-note`} value={draft.note} maxLength={300} onChange={(e) => setDraft({ ...draft, note: e.target.value })} /></label>
        {formError && <Notice text={formError} error />}
        <div className="actions"><button type="submit" disabled={busy !== null}><Plus aria-hidden="true" />{busy === 'record' ? t('consent.recording') : t('consent.record')}</button></div>
      </form>
      <div className="consentList">
        <div className="suppFilters">
          <label className="field" htmlFor={`${id}-q`}><span>{t('consent.search')}</span>
            <input id={`${id}-q`} type="search" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} /></label>
          <label className="field"><span>{t('consent.filter')}</span>
            <select value={filter} onChange={(e) => { setFilter(e.target.value as 'active' | 'withdrawn' | ''); setOffset(0); }}>
              <option value="">{t('consent.filterAll')}</option><option value="active">{t('consent.state.active')}</option><option value="withdrawn">{t('consent.state.withdrawn')}</option></select></label>
        </div>
        {state === 'loading' && !page && <Notice text={t('consent.loading')} />}
        {page && <p className="muted">{t('consent.counts', { active: page.active, withdrawn: page.withdrawn })}</p>}
        {page && page.total === 0 && <div className="emptyState"><ShieldCheck aria-hidden="true" />
          <h3>{q || filter ? t('consent.noMatch') : t('consent.emptyTitle')}</h3><p>{q || filter ? '' : t('consent.emptyText')}</p>
          {(q || filter) && <button type="button" className="secondary" onClick={() => { setQ(''); setFilter(''); setOffset(0); }}>{t('consent.clear')}</button>}</div>}
        {page && page.items.length > 0 && <ul className="consentRows">{page.items.map((item) => <li key={item.id} className="consentRow">
          <div className="consentHead">
            <strong dir="ltr">{item.masked}</strong>
            <span className={`statusBadge ${item.status === 'active' ? 'success' : 'neutral'}`}>{t(`consent.state.${item.status}`)}</span>
            <span className="statusBadge neutral">{consentChannelLabel(item.channel)}</span>
          </div>
          <p>{basisLabel(item.lawful_basis)}: <bdi>{item.source}</bdi>, {day(item.consented_at)}</p>
          {item.note && <p className="muted" dir="auto">{item.note}</p>}
          {item.status === 'withdrawn' && <p className="muted">{t('consent.withdrawnOn', { date: day(item.withdrawn_at ?? item.updated_at), reason: item.withdrawn_reason ?? '-' })}</p>}
          {item.status === 'active' && <div className="actions"><button type="button" className="secondary" disabled={busy !== null}
            aria-label={t('consent.withdrawLabel', { contact: item.masked })} onClick={() => { setWithdrawing(item); setReason(''); }}><Undo2 aria-hidden="true" />{t('consent.withdraw')}</button></div>}
        </li>)}</ul>}
        {page && page.total > CONSENT_PAGE && <nav className="consentPager" aria-label={t('consent.paging')}>
          <span className="muted">{t('consent.range', { from: offset + 1, to: Math.min(offset + page.items.length, page.total), total: page.total })}</span>
          <div className="actions">
            <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - CONSENT_PAGE))}>{t('consent.previous')}</button>
            <button type="button" className="secondary" disabled={offset + CONSENT_PAGE >= page.total} onClick={() => setOffset(offset + CONSENT_PAGE)}>{t('consent.next')}</button>
          </div></nav>}
      </div>
    </div>
    {withdrawing && <ConfirmDialog title={t('consent.withdrawTitle', { contact: withdrawing.masked })} confirmLabel={t('consent.withdraw')} danger busy={busy === 'withdraw'}
      confirmDisabled={!withdrawReason(reason)} onConfirm={() => void withdraw()} onCancel={() => setWithdrawing(null)}>
      <p>{t('consent.withdrawBody')}</p>
      <label className="field" htmlFor={`${id}-reason`}><span>{t('consent.reason')}</span>
        <input id={`${id}-reason`} value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} /></label>
    </ConfirmDialog>}
  </section>;
}
