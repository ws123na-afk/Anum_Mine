// Contact opt-outs (docs/privacy.md#contact-suppression): the tenant's suppression list. Owners and
// organization administrators view, search, add and remove entries (a removal needs a reason);
// only owners export it. Everyone else gets the API's 403, explained in place.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { BellOff, CircleDot, Download, Lock, Plus, RefreshCw, RotateCcw, Search, Trash2 } from 'lucide-react';
import { ConfirmDialog } from './Dialog';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import {
  CHANNELS, PAGE_SIZE, PURPOSES, addedBy, channelLabel, emptySuppressionDraft, purposeLabel, removalReason, sourceLabel, suppressionBody,
  type SuppressionChannel, type SuppressionDraft, type SuppressionEntry,
} from './lib/suppression';
import * as api from './lib/suppressionApi';
import './admin.css';
import './suppression.css';

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'forbidden'; detail: string } | { kind: 'error'; detail: string };
type Flash = { text: string; error?: boolean } | null;

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

const when = (value: string) => formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' });

export function SuppressionView() {
  const [items, setItems] = useState<SuppressionEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [canExport, setCanExport] = useState(false);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [query, setQuery] = useState({ q: '', channel: '' as SuppressionChannel | '' });
  const [search, setSearch] = useState({ q: '', channel: '' as SuppressionChannel | '' });
  const [draft, setDraft] = useState<SuppressionDraft>(emptySuppressionDraft);
  const [formError, setFormError] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [removing, setRemoving] = useState<SuppressionEntry | null>(null);
  const [removeReasonText, setRemoveReasonText] = useState('');
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [more, setMore] = useState<'idle' | 'loading' | { error: string }>('idle');
  const request = useRef(0);
  const ids = { contact: useId(), reason: useId(), removeReason: useId(), contactHint: useId(), reasonHint: useId() };

  const fetchFirst = useCallback(async (next: { q: string; channel: SuppressionChannel | '' }) => {
    const id = ++request.current;
    setLoad({ kind: 'loading' });
    setMore('idle');
    try {
      const page = await api.listSuppressions({ ...next, limit: PAGE_SIZE, offset: 0 });
      if (id !== request.current) return;
      setItems(page.items); setTotal(page.total); setCanExport(page.can_export); setLoad({ kind: 'ready' });
    } catch (error) {
      if (id !== request.current) return;
      setLoad(isPermissionDenied(error) ? { kind: 'forbidden', detail: describeError(error) } : { kind: 'error', detail: describeError(error) });
    }
  }, []);
  useEffect(() => { void fetchFirst({ q: '', channel: '' }); }, [fetchFirst]);

  const loadMore = async () => {
    setMore('loading');
    try {
      const page = await api.listSuppressions({ ...query, limit: PAGE_SIZE, offset: items.length });
      setItems((current) => [...current, ...page.items]); setTotal(page.total); setMore('idle');
    } catch (error) { setMore({ error: describeError(error) }); }
  };

  const add = async (event: FormEvent) => {
    event.preventDefault();
    const body = suppressionBody(draft);
    if (!body.ok) { setFormError(body.error); return; }
    setFormError(null); setFlash(null); setBusy('add');
    try {
      const { entry, created } = await api.addSuppression(body.value);
      setFlash({ text: t(created ? 'supp.added' : 'supp.already', { contact: entry.masked }) });
      setDraft(emptySuppressionDraft());
      await fetchFirst(query);
    } catch (error) {
      setFormError(describeError(error));
    } finally { setBusy(null); }
  };

  const find = (event: FormEvent) => { event.preventDefault(); setQuery(search); void fetchFirst(search); };
  const clear = () => { const empty = { q: '', channel: '' as const }; setSearch(empty); setQuery(empty); void fetchFirst(empty); };

  const confirmRemove = async () => {
    if (!removing) return;
    const reason = removalReason(removeReasonText);
    if (!reason.ok) { setRemoveError(reason.error); return; }
    setBusy('remove');
    try {
      await api.removeSuppression(removing.id, reason.value);
      setFlash({ text: t('supp.removed', { contact: removing.masked }) });
      setRemoving(null); setRemoveReasonText(''); setRemoveError(null);
      await fetchFirst(query);
    } catch (error) {
      setRemoveError(describeError(error));
    } finally { setBusy(null); }
  };

  const exportCsv = async () => {
    setBusy('export'); setFlash(null);
    try { setFlash({ text: t('supp.exported', { name: await api.downloadSuppressions() }) }); }
    catch (error) { setFlash({ text: describeError(error), error: true }); }
    finally { setBusy(null); }
  };

  const filtered = Boolean(query.q.trim() || query.channel);
  const ready = load.kind === 'ready';
  return <>
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('supp.eyebrow')}</p><h2>{t('supp.title')}</h2><p>{t('supp.intro')}</p><p className="muted">{t('supp.storage')}</p></div>
      {ready && <div className="actions">
        {canExport
          ? <button type="button" className="secondary" disabled={busy !== null} onClick={() => void exportCsv()}><Download aria-hidden="true" />{busy === 'export' ? t('supp.exporting') : t('supp.export')}</button>
          : <p className="muted suppExportNote">{t('supp.exportOwners')}</p>}
      </div>}
    </section>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}

    {load.kind === 'forbidden' && <section className="surface adminForbidden" aria-label={t('supp.deniedTitle')}>
      <span className="adminIcon"><Lock aria-hidden="true" /></span>
      <div><h3>{t('supp.deniedTitle')}</h3><p>{t('supp.deniedText')}</p><p className="muted">{t('supp.apiAnswered', { message: load.detail })}</p></div>
    </section>}
    {load.kind === 'error' && <section className="surface adminForbidden" aria-label={t('supp.errorTitle')}>
      <span className="adminIcon"><BellOff aria-hidden="true" /></span>
      <div><h3>{t('supp.errorTitle')}</h3><p>{load.detail}</p>
        <div className="actions"><button type="button" onClick={() => void fetchFirst(query)}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>
    </section>}

    {load.kind !== 'forbidden' && load.kind !== 'error' && <div className="suppLayout">
      <form className="surface suppAdd" aria-label={t('supp.addTitle')} noValidate onSubmit={(event) => void add(event)}>
        <h3>{t('supp.addTitle')}</h3>
        <label className="field" htmlFor={ids.contact}><span>{t('supp.field.contact')}</span>
          <input id={ids.contact} dir="ltr" value={draft.recipient} maxLength={320} autoComplete="off" spellCheck={false} aria-describedby={ids.contactHint}
            onChange={(e) => setDraft({ ...draft, recipient: e.target.value })} /></label>
        <small className="muted" id={ids.contactHint}>{t('supp.field.contactHint')}</small>
        <div className="suppPair">
          <label className="field"><span>{t('supp.field.channel')}</span>
            <select value={draft.channel} onChange={(e) => setDraft({ ...draft, channel: e.target.value as SuppressionChannel })}>
              {CHANNELS.map((channel) => <option key={channel} value={channel}>{channelLabel(channel)}</option>)}
            </select></label>
          <label className="field"><span>{t('supp.field.purpose')}</span>
            <select value={draft.purpose} onChange={(e) => setDraft({ ...draft, purpose: e.target.value as SuppressionDraft['purpose'] })}>
              {PURPOSES.map((purpose) => <option key={purpose} value={purpose}>{purposeLabel(purpose)}</option>)}
            </select></label>
        </div>
        <label className="field" htmlFor={ids.reason}><span>{t('supp.field.reason')}</span>
          <input id={ids.reason} dir="auto" value={draft.reason} maxLength={300} aria-describedby={ids.reasonHint} onChange={(e) => setDraft({ ...draft, reason: e.target.value })} /></label>
        <small className="muted" id={ids.reasonHint}>{t('supp.field.reasonHint')}</small>
        {formError && <Notice error>{formError}</Notice>}
        <div className="actions"><button type="submit" disabled={busy !== null || !ready}><Plus aria-hidden="true" />{t('supp.add')}</button></div>
      </form>

      <section className="surface suppList" aria-label={t('supp.table')}>
        <form className="suppFilters" role="search" onSubmit={find}>
          <label className="field suppSearch"><span>{t('supp.search')}</span>
            <input type="search" dir="auto" value={search.q} maxLength={320} placeholder={t('supp.searchPlaceholder')} onChange={(e) => setSearch({ ...search, q: e.target.value })} /></label>
          <label className="field"><span>{t('supp.field.channel')}</span>
            <select value={search.channel} onChange={(e) => setSearch({ ...search, channel: e.target.value as SuppressionChannel | '' })}>
              <option value="">{t('supp.anyChannel')}</option>
              {CHANNELS.map((channel) => <option key={channel} value={channel}>{channelLabel(channel)}</option>)}
            </select></label>
          <div className="actions">
            <button type="submit" className="secondary" disabled={load.kind === 'loading'}><Search aria-hidden="true" />{t('supp.find')}</button>
            {filtered && <button type="button" className="secondary" onClick={clear}><RotateCcw aria-hidden="true" />{t('supp.clear')}</button>}
          </div>
        </form>
        {load.kind === 'loading' && <Notice>{t('supp.loading')}</Notice>}
        {ready && <>
          <h3 className="suppCount">{t(filtered ? 'supp.countFiltered' : 'supp.count', { count: total })}</h3>
          {!items.length && (filtered
            ? <div className="emptyState"><Search aria-hidden="true" /><h3>{t('supp.noMatchTitle')}</h3><p>{t('supp.noMatchText')}</p><button type="button" className="secondary" onClick={clear}>{t('supp.clear')}</button></div>
            : <div className="emptyState"><BellOff aria-hidden="true" /><h3>{t('supp.emptyTitle')}</h3><p>{t('supp.emptyText')}</p></div>)}
          {items.length > 0 && <ul className="suppRows">
            {items.map((entry) => <li className="suppRow" key={entry.id}>
              <div className="suppMain">
                <strong dir="ltr">{entry.masked}</strong>
                <div className="tagRow">
                  <span>{channelLabel(entry.channel)}</span>
                  <span className={entry.purpose === 'all' ? 'suppAll' : undefined}>{purposeLabel(entry.purpose)}</span>
                  <span>{sourceLabel(entry.source)}</span>
                </div>
                <small className="muted">{addedBy(entry)} · {when(entry.created_at)}</small>
                {entry.reason && <small className="suppReason">{t('supp.reasonLine', { reason: entry.reason })}</small>}
              </div>
              <button type="button" className="secondary" disabled={busy !== null} aria-label={t('supp.removeLabel', { contact: entry.masked }, { isolate: false })}
                onClick={() => { setRemoving(entry); setRemoveReasonText(''); setRemoveError(null); }}><Trash2 aria-hidden="true" />{t('supp.remove')}</button>
            </li>)}
          </ul>}
          {items.length < total && <div className="actions suppMore">
            <button type="button" className="secondary" disabled={more === 'loading'} onClick={() => void loadMore()}>{more === 'loading' ? t('common.loading') : t('supp.more')}</button>
          </div>}
          {typeof more === 'object' && <Notice error>{more.error}</Notice>}
        </>}
      </section>
    </div>}

    {removing && <ConfirmDialog title={t('supp.removeTitle', { contact: removing.masked })} confirmLabel={t('supp.remove')} danger busy={busy === 'remove'}
      onConfirm={() => void confirmRemove()} onCancel={() => setRemoving(null)}>
      <p>{t('supp.removeBody', { channel: channelLabel(removing.channel) })}</p>
      <label className="field" htmlFor={ids.removeReason}><span>{t('supp.removeReason')}</span>
        <input id={ids.removeReason} dir="auto" value={removeReasonText} maxLength={300} onChange={(e) => setRemoveReasonText(e.target.value)} /></label>
      {removeError && <Notice error>{removeError}</Notice>}
    </ConfirmDialog>}
  </>;
}
