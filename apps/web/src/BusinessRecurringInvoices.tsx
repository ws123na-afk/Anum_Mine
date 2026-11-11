// Recurring invoices and the planned emails (docs/business.md#recurring-invoices, #reminders-planned-by-the-daily-pass).
// A schedule drafts one ordinary invoice per period and may ask an owner to approve sending it; owners turn on the daily
// pass that plans reminders and invoice emails. Nothing here sends anything: every send waits in Approvals.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { CalendarClock, Pause, Play, Plus, RefreshCw, Search, Square, Trash2 } from 'lucide-react';
import type { BusinessAccountRow, BusinessCurrency, BusinessSetup, InvoiceAutomation, InvoiceScheduleDetail, ScheduleStatus } from '@anum/contracts';
import { t, type MessageKey } from './lib/i18n';
import { CURRENCIES, formatMoney } from './lib/business';
import * as b from './lib/businessApi';
import * as inv from './lib/businessInvoicesApi';
import { invoiceStatusLabel, invoiceTone, today } from './lib/invoices';
import {
  SCHEDULE_FREQUENCIES, SCHEDULE_STATUSES, draftOf, emptySchedule, frequencyText, hasInterval, nextText, scheduleBody, statusLabel, statusTone, summaryText,
  type ScheduleDraft,
} from './lib/invoiceSchedules';
import { ConfirmDialog, Drawer, Modal } from './Dialog';
import { Badge, Empty, Note, Pager, day, failure, when } from './BusinessParts';
import { ListState, usePaged } from './BusinessDeals';
import { ManualLines } from './BusinessInvoices';

const money = (value: number, currency: BusinessCurrency) => formatMoney(value, currency);

export function RecurringInvoices({ setup, onOpenInvoice }: { setup: BusinessSetup; onOpenInvoice: (id: string) => void }) {
  const [status, setStatus] = useState<ScheduleStatus | ''>(''), [query, setQuery] = useState(''), [search, setSearch] = useState('');
  const [tick, setTick] = useState(0), [creating, setCreating] = useState(false), [openId, setOpenId] = useState<string | null>(null);
  const list = usePaged((offset) => inv.listSchedules({ status, q: search, limit: 25, offset }), [status, search, tick]);
  const write = setup.permissions.write;
  const searchId = useId(), statusId = useId();
  const filtered = Boolean(status || search);
  const refresh = () => setTick((value) => value + 1);
  return <section className="bzSection" aria-label={t('rinv.title')}>
    <AutomationCard />
    <div className="bzToolbar">
      <form className="bzSearch" role="search" onSubmit={(event) => { event.preventDefault(); setSearch(query.trim()); }}>
        <label className="visuallyHidden" htmlFor={searchId}>{t('rinv.search')}</label>
        <input id={searchId} type="search" value={query} placeholder={t('rinv.search')} onChange={(event) => { setQuery(event.target.value); if (!event.target.value) setSearch(''); }} />
        <button type="submit" className="secondary"><Search aria-hidden="true" />{t('biz.find')}</button>
      </form>
      <label className="bzCompact" htmlFor={statusId}><span>{t('rinv.filter.status')}</span>
        <select id={statusId} value={status} onChange={(event) => setStatus(event.target.value as ScheduleStatus | '')}>
          <option value="">{t('rinv.filter.all')}</option>{SCHEDULE_STATUSES.map((item) => <option key={item} value={item}>{statusLabel(item)}</option>)}</select></label>
      <div className="bzToolbarEnd">{write && <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('rinv.new')}</button>}</div>
    </div>
    <ListState load={list.load} empty={list.items.length === 0} filtered={filtered} onRetry={() => void list.first()} onClear={() => { setStatus(''); setQuery(''); setSearch(''); }} />
    {list.load.kind === 'ready' && list.items.length === 0 && !filtered && <Empty title={t('rinv.emptyTitle')} text={write ? t('rinv.emptyText') : t('rinv.emptyMember')}
      action={write ? <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('rinv.new')}</button> : undefined} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <p className="bzCount" aria-live="polite">{t('rinv.count', { count: list.total })}</p>
      <div className="pane dataTableWrap"><table className="dataTable bzTable">
        <caption className="visuallyHidden">{t('rinv.title')}</caption>
        <thead><tr><th scope="col">{t('rinv.col.name')}</th><th scope="col">{t('rinv.col.status')}</th><th scope="col" className="bzHideMd">{t('rinv.col.repeats')}</th>
          <th scope="col">{t('rinv.col.next')}</th><th scope="col" className="num">{t('rinv.col.total')}</th></tr></thead>
        <tbody>{list.items.map((item) => <tr key={item.id} className={item.id === openId ? 'selected' : undefined}>
          <th scope="row"><button type="button" className="linkButton" onClick={() => setOpenId(item.id)}><span dir="auto">{item.title}</span></button>
            <small dir="auto">{item.account_name ?? t('biz.unknown')}</small></th>
          <td><div className="invBadges"><Badge tone={statusTone(item.status)}>{statusLabel(item.status)}</Badge>{item.last_error && <Badge tone="dangerTone">{t('rinv.needsYou')}</Badge>}</div></td>
          <td className="bzHideMd">{frequencyText(item)}</td>
          <td className="nowrap">{nextText(item, day)}</td>
          <td className="num">{money(item.total, item.currency)}</td>
        </tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {creating && <ScheduleDialog onClose={() => setCreating(false)} onSaved={(saved) => { setCreating(false); refresh(); setOpenId(saved.id); }} />}
    {openId && <ScheduleDrawer id={openId} write={write} onClose={() => setOpenId(null)} onChanged={refresh} onDeleted={() => { setOpenId(null); refresh(); }}
      onOpenInvoice={onOpenInvoice} />}
  </section>;
}

// ---- the daily pass's switches ----------------------------------------------------------------------

function AutomationCard() {
  const [state, setState] = useState<{ kind: 'loading' } | { kind: 'ready'; value: InvoiceAutomation } | { kind: 'error'; text: string }>({ kind: 'loading' });
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const heading = useId(), languageId = useId();
  const load = useCallback(async () => {
    try { setState({ kind: 'ready', value: await inv.getAutomation() }); } catch (error) { setState({ kind: 'error', text: failure(error, t('rinv.auto.title')) }); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  if (state.kind === 'loading') return <Note>{t('biz.loading')}</Note>;
  if (state.kind === 'error') return <Note error>{state.text} <button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></Note>;
  const value = state.value;
  const save = async (patch: Partial<Pick<InvoiceAutomation, 'auto_reminders' | 'email_scheduled' | 'language'>>) => {
    setBusy(true); setNotice(null);
    try {
      const next = await inv.saveAutomation({ auto_reminders: value.auto_reminders, email_scheduled: value.email_scheduled, language: value.language, ...patch });
      setState({ kind: 'ready', value: next }); setNotice({ text: t('rinv.auto.saved') });
    } catch (error) { setNotice({ text: failure(error, t('rinv.auto.title')), error: true }); } finally { setBusy(false); }
  };
  const runNow = async () => {
    setBusy(true); setNotice(null);
    try { const next = await inv.runAutomation(); setState({ kind: 'ready', value: next }); setNotice({ text: t('rinv.auto.ran') }); }
    catch (error) { setNotice({ text: failure(error, t('rinv.auto.run')), error: true }); } finally { setBusy(false); }
  };
  const on = value.auto_reminders || value.email_scheduled;
  const summary = summaryText(value.last_run_summary);
  return <section className="surface bzCard rinvAuto" aria-labelledby={heading}>
    <h2 id={heading}><CalendarClock aria-hidden="true" />{t('rinv.auto.title')}</h2>
    <p className="muted bzHint">{t('rinv.auto.help')}</p>
    <fieldset className="bzFieldset" disabled={!value.can_change || busy}>
      <legend className="visuallyHidden">{t('rinv.auto.title')}</legend>
      <label className="bzCheck"><input type="checkbox" checked={value.auto_reminders} onChange={(event) => void save({ auto_reminders: event.target.checked })} />{t('rinv.auto.reminders')}</label>
      <label className="bzCheck"><input type="checkbox" checked={value.email_scheduled} onChange={(event) => void save({ email_scheduled: event.target.checked })} />{t('rinv.auto.emails')}</label>
      <label className="bzCompact" htmlFor={languageId}><span>{t('rinv.auto.language')}</span>
        <select id={languageId} value={value.language} onChange={(event) => void save({ language: event.target.value as 'en' | 'ar' })}>
          <option value="en">{t('inv.send.lang.en' as MessageKey)}</option><option value="ar">{t('inv.send.lang.ar' as MessageKey)}</option></select></label>
    </fieldset>
    {!value.can_change && <p className="muted bzHint">{t('rinv.auto.ownersOnly')}</p>}
    <p className="muted bzHint">{value.last_run_at ? t('rinv.auto.lastRun', { when: when(value.last_run_at) }) : t('rinv.auto.never')}{summary ? ` ${summary}` : ''}</p>
    {value.last_run_summary?.note && <Note error>{value.last_run_summary.note}</Note>}
    {notice && <Note error={notice.error}>{notice.text}</Note>}
    {value.can_change && on && <div className="bzInline"><button type="button" className="secondary" disabled={busy} onClick={() => void runNow()}><RefreshCw aria-hidden="true" />{t('rinv.auto.run')}</button></div>}
  </section>;
}

// ---- new and edit -------------------------------------------------------------------------------------

function ScheduleDialog({ existing, onClose, onSaved }: { existing?: InvoiceScheduleDetail; onClose: () => void; onSaved: (saved: InvoiceScheduleDetail) => void }) {
  const [draft, setDraft] = useState<ScheduleDraft>(() => (existing ? draftOf(existing) : emptySchedule(today())));
  const [accountQuery, setAccountQuery] = useState(''), [accounts, setAccounts] = useState<{ kind: 'loading' } | { kind: 'ready'; value: BusinessAccountRow[] } | { kind: 'error'; text: string }>({ kind: 'loading' });
  const [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const ids = { search: useId(), account: useId(), currency: useId(), title: useId(), frequency: useId(), interval: useId(), start: useId(), end: useId(), endDate: useId(),
    count: useId(), due: useId(), reference: useId(), notes: useId(), terms: useId() };
  useEffect(() => {
    if (existing) return;
    const handle = window.setTimeout(() => {
      setAccounts({ kind: 'loading' });
      b.listAccounts({ q: accountQuery.trim() || undefined, limit: 100, offset: 0 }).then((page) => setAccounts({ kind: 'ready', value: page.items }))
        .catch((error) => setAccounts({ kind: 'error', text: failure(error, t('inv.new.accounts')) }));
    }, accountQuery ? 250 : 0);
    return () => window.clearTimeout(handle);
  }, [accountQuery, existing]);
  useEffect(() => {
    if (existing) return;
    // New lines start at the billing profile's tax rate, as a new invoice's do.
    void inv.getBillingProfile().then((profile) => setDraft((old) => ({ ...old, dueDays: old.dueDays || String(profile.default_due_days),
      lines: old.lines.map((line) => (line.taxPct ? line : { ...line, taxPct: String(profile.default_tax_rate_pct) })) }))).catch(() => undefined);
  }, [existing]);
  const set = (patch: Partial<ScheduleDraft>) => { setDraft((old) => ({ ...old, ...patch })); setProblem(null); };
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = scheduleBody(draft);
    if (!body.ok) { setProblem(body.error); return; }
    setBusy(true); setProblem(null);
    try { onSaved(existing ? await inv.updateSchedule(existing.id, existing.version, body.value) : await inv.createSchedule(body.value)); }
    catch (error) { setProblem(failure(error, t('rinv.save'))); setBusy(false); }
  }
  const title = existing ? t('rinv.edit') : t('rinv.new');
  return <Modal title={title} className="formDialog bzWideDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={title} noValidate>
      <p className="muted bzHint">{t(existing ? 'rinv.form.editIntro' : 'rinv.form.intro')}</p>
      <fieldset className="bzFieldset">
        <legend>{t('rinv.form.who')}</legend>
        {existing ? <p dir="auto"><strong>{existing.account_name ?? t('biz.unknown')}</strong> <span className="muted">{existing.currency}</span></p> : <>
          <div className="bzGrid2">
            <label className="field" htmlFor={ids.search}><span>{t('inv.new.findClient')}</span><input id={ids.search} type="search" value={accountQuery} onChange={(event) => setAccountQuery(event.target.value)} dir="auto" data-autofocus /></label>
            <label className="field" htmlFor={ids.account}><span>{t('inv.new.client')}</span>
              <select id={ids.account} value={draft.accountId} onChange={(event) => set({ accountId: event.target.value })}>
                <option value="">{accounts.kind === 'loading' ? t('biz.loading') : t('inv.new.chooseClient')}</option>
                {accounts.kind === 'ready' && accounts.value.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          </div>
          {accounts.kind === 'error' && <Note error>{accounts.text}</Note>}
          {accounts.kind === 'ready' && accounts.value.length === 0 && <p className="muted bzHint">{t('inv.new.noClients')}</p>}
          <label className="field bzCompactField" htmlFor={ids.currency}><span>{t('inv.form.currency')}</span>
            <select id={ids.currency} value={draft.currency} onChange={(event) => set({ currency: event.target.value as BusinessCurrency })}>{CURRENCIES.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        </>}
        <label className="field" htmlFor={ids.title}><span>{t('rinv.form.title')}</span><input id={ids.title} value={draft.title} onChange={(event) => set({ title: event.target.value })} dir="auto" placeholder={t('rinv.form.titleHint')} /></label>
      </fieldset>
      <fieldset className="bzFieldset">
        <legend>{t('rinv.form.when')}</legend>
        <div className="bzGrid3">
          <label className="field" htmlFor={ids.frequency}><span>{t('rinv.form.frequency')}</span>
            <select id={ids.frequency} value={draft.frequency} onChange={(event) => set({ frequency: event.target.value as ScheduleDraft['frequency'] })}>
              {SCHEDULE_FREQUENCIES.map((item) => <option key={item} value={item}>{t(`rinv.freq.${item}` as MessageKey)}</option>)}</select></label>
          {hasInterval(draft.frequency) && <label className="field" htmlFor={ids.interval}><span>{t(`rinv.form.every.${draft.frequency}` as MessageKey)}</span>
            <input id={ids.interval} inputMode="numeric" value={draft.interval} onChange={(event) => set({ interval: event.target.value })} /></label>}
          <label className="field" htmlFor={ids.start}><span>{t('rinv.form.start')}</span><input id={ids.start} type="date" value={draft.startDate} onChange={(event) => set({ startDate: event.target.value })} /></label>
        </div>
        <div className="bzGrid3">
          <label className="field" htmlFor={ids.end}><span>{t('rinv.form.ends')}</span>
            <select id={ids.end} value={draft.end} onChange={(event) => set({ end: event.target.value as ScheduleDraft['end'] })}>
              <option value="never">{t('rinv.form.ends.never')}</option><option value="date">{t('rinv.form.ends.date')}</option><option value="count">{t('rinv.form.ends.count')}</option></select></label>
          {draft.end === 'date' && <label className="field" htmlFor={ids.endDate}><span>{t('rinv.form.endDate')}</span><input id={ids.endDate} type="date" value={draft.endDate} onChange={(event) => set({ endDate: event.target.value })} /></label>}
          {draft.end === 'count' && <label className="field" htmlFor={ids.count}><span>{t('rinv.form.count')}</span><input id={ids.count} inputMode="numeric" value={draft.maxCount} onChange={(event) => set({ maxCount: event.target.value })} /></label>}
          <label className="field" htmlFor={ids.due}><span>{t('inv.form.dueDays')}</span><input id={ids.due} inputMode="numeric" value={draft.dueDays} onChange={(event) => set({ dueDays: event.target.value })} /></label>
        </div>
        {existing && existing.created_count > 0 && <p className="muted bzHint">{t('rinv.form.scheduleLocked', { date: existing.last_period ?? '-' })}</p>}
      </fieldset>
      <ManualLines lines={draft.lines} onChange={(lines) => set({ lines })} taxHint="0" help={t('rinv.form.linesHelp')} />
      <fieldset className="bzFieldset">
        <legend>{t('rinv.form.send')}</legend>
        <label className="bzCheck"><input type="radio" name="issue" checked={draft.issueMode === 'draft'} onChange={() => set({ issueMode: 'draft' })} />{t('rinv.issue.draft')}</label>
        <label className="bzCheck"><input type="radio" name="issue" checked={draft.issueMode === 'request_approval'} onChange={() => set({ issueMode: 'request_approval' })} />{t('rinv.issue.request_approval')}</label>
        <p className="muted bzHint">{t('rinv.issue.help')}</p>
      </fieldset>
      <fieldset className="bzFieldset">
        <legend>{t('inv.form.details')}</legend>
        <label className="field" htmlFor={ids.reference}><span>{t('inv.form.reference')}</span><input id={ids.reference} value={draft.reference} onChange={(event) => set({ reference: event.target.value })} dir="auto" /></label>
        <label className="field" htmlFor={ids.notes}><span>{t('inv.form.notes')}</span><textarea id={ids.notes} rows={2} value={draft.notes} onChange={(event) => set({ notes: event.target.value })} dir="auto" /></label>
        <label className="field" htmlFor={ids.terms}><span>{t('inv.form.terms')}</span><textarea id={ids.terms} rows={2} value={draft.terms} onChange={(event) => set({ terms: event.target.value })} dir="auto" /></label>
      </fieldset>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('rinv.save')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- the drawer: details, history and actions --------------------------------------------------------

type Confirm = 'end' | 'delete' | null;

function ScheduleDrawer({ id, write, onClose, onChanged, onDeleted, onOpenInvoice }: {
  id: string; write: boolean; onClose: () => void; onChanged: () => void; onDeleted: () => void; onOpenInvoice: (id: string) => void;
}) {
  const [state, setState] = useState<{ kind: 'loading' } | { kind: 'ready'; value: InvoiceScheduleDetail } | { kind: 'error'; text: string }>({ kind: 'loading' });
  const [busy, setBusy] = useState(false), [editing, setEditing] = useState(false), [confirm, setConfirm] = useState<Confirm>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const load = useCallback(async () => {
    try { setState({ kind: 'ready', value: await inv.getSchedule(id) }); } catch (error) { setState({ kind: 'error', text: failure(error, t('rinv.load')) }); }
  }, [id]);
  useEffect(() => { void load(); }, [load]);
  const act = async (work: () => Promise<InvoiceScheduleDetail | void>, done: string, what: string) => {
    setBusy(true); setNotice(null);
    try {
      const next = await work();
      if (next) setState({ kind: 'ready', value: next });
      setNotice({ text: done }); onChanged();
    } catch (error) { setNotice({ text: failure(error, what), error: true }); void load(); } finally { setBusy(false); setConfirm(null); }
  };
  const title = state.kind === 'ready' ? state.value.title : t('rinv.load');
  return <Drawer title={title} onClose={onClose}>
    {state.kind === 'loading' && <Note>{t('biz.loading')}</Note>}
    {state.kind === 'error' && <Note error>{state.text} <button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></Note>}
    {state.kind === 'ready' && (() => {
      const s = state.value;
      const run = async () => {
        setBusy(true); setNotice(null);
        try {
          const result = await inv.runSchedule(s.id);
          setState({ kind: 'ready', value: result.schedule });
          setNotice(result.drafted ? { text: t('rinv.ran', { count: result.drafted, approval: result.sent_for_approval }) } : { text: result.note || t('rinv.ranNothing') });
          onChanged();
        } catch (error) { setNotice({ text: failure(error, t('rinv.run')), error: true }); } finally { setBusy(false); }
      };
      return <div className="bzDrawer">
        <div className="invBadges"><Badge tone={statusTone(s.status)}>{statusLabel(s.status)}</Badge><Badge tone="neutral">{t(`rinv.issue.short.${s.issue_mode}` as MessageKey)}</Badge></div>
        <dl className="bzFacts">
          <div><dt>{t('rinv.col.client')}</dt><dd dir="auto">{s.account_name ?? t('biz.unknown')}</dd></div>
          <div><dt>{t('rinv.col.repeats')}</dt><dd>{frequencyText(s)}</dd></div>
          <div><dt>{t('rinv.col.next')}</dt><dd>{nextText(s, day)}</dd></div>
          <div><dt>{t('rinv.col.total')}</dt><dd>{money(s.total, s.currency)}</dd></div>
          <div><dt>{t('rinv.form.start')}</dt><dd>{day(s.start_date)}</dd></div>
          <div><dt>{t('rinv.form.ends')}</dt><dd>{s.end_date ? day(s.end_date) : s.max_count ? t('rinv.endsAfter', { count: s.max_count }) : t('rinv.form.ends.never')}</dd></div>
          <div><dt>{t('rinv.made')}</dt><dd>{t('rinv.madeCount', { count: s.created_count })}</dd></div>
        </dl>
        {s.last_error && <Note error>{t('rinv.lastError', { reason: s.last_error })}</Note>}
        {notice && <Note error={notice.error}>{notice.text}</Note>}
        {write && <div className="bzInline">
          {s.status === 'active' && <button type="button" disabled={busy} onClick={() => void run()}><RefreshCw aria-hidden="true" />{t('rinv.run')}</button>}
          {s.status !== 'ended' && <button type="button" className="secondary" disabled={busy} onClick={() => setEditing(true)}>{t('rinv.edit')}</button>}
          {s.status === 'active' && <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => inv.changeSchedule(s.id, 'pause', s.version), t('rinv.paused'), t('rinv.pause'))}><Pause aria-hidden="true" />{t('rinv.pause')}</button>}
          {s.status === 'paused' && <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => inv.changeSchedule(s.id, 'resume', s.version), t('rinv.resumed'), t('rinv.resume'))}><Play aria-hidden="true" />{t('rinv.resume')}</button>}
          {s.status !== 'ended' && <button type="button" className="secondary" disabled={busy} onClick={() => setConfirm('end')}><Square aria-hidden="true" />{t('rinv.end')}</button>}
          {s.created_count === 0 && <button type="button" className="secondary" disabled={busy} onClick={() => setConfirm('delete')}><Trash2 aria-hidden="true" />{t('rinv.delete')}</button>}
        </div>}
        {!write && <p className="muted bzHint">{t('rinv.readOnly')}</p>}
        <section aria-labelledby={`${s.id}-upcoming`}>
          <h3 id={`${s.id}-upcoming`}>{t('rinv.upcoming')}</h3>
          {s.upcoming.length ? <ul className="bzPlainList">{s.upcoming.map((date) => <li key={date}>{day(date)}</li>)}</ul> : <p className="muted">{t('rinv.upcomingNone')}</p>}
        </section>
        <section aria-labelledby={`${s.id}-history`}>
          <h3 id={`${s.id}-history`}>{t('rinv.history')}</h3>
          {s.invoices.length === 0 ? <p className="muted">{t('rinv.historyNone')}</p> : <>
            <ul className="bzPlainList">{s.invoices.map((item) => <li key={item.id}>
              <button type="button" className="linkButton" onClick={() => onOpenInvoice(item.id)}>{item.number ?? t('rinv.draftFor', { date: item.period_date ?? '-' })}</button>{' '}
              <Badge tone={invoiceTone(item.status, false)}>{invoiceStatusLabel(item.status)}</Badge>{' '}<span className="muted">{day(item.period_date)}</span>{' '}<strong>{money(item.total, item.currency)}</strong>
            </li>)}</ul>
            {s.invoices_total > s.invoices.length && <p className="muted bzHint">{t('rinv.historyMore', { shown: s.invoices.length, total: s.invoices_total })}</p>}
          </>}
        </section>
        {editing && <ScheduleDialog existing={s} onClose={() => setEditing(false)} onSaved={(saved) => { setEditing(false); setState({ kind: 'ready', value: saved }); setNotice({ text: t('rinv.saved') }); onChanged(); }} />}
        {confirm === 'end' && <ConfirmDialog title={t('rinv.endTitle')} confirmLabel={t('rinv.end')} danger busy={busy}
          onConfirm={() => void act(() => inv.changeSchedule(s.id, 'end', s.version), t('rinv.ended'), t('rinv.end'))} onCancel={() => setConfirm(null)}><p>{t('rinv.endText')}</p></ConfirmDialog>}
        {confirm === 'delete' && <ConfirmDialog title={t('rinv.deleteTitle')} confirmLabel={t('rinv.delete')} danger busy={busy}
          onConfirm={() => void (async () => { setBusy(true); try { await inv.deleteSchedule(s.id); onDeleted(); } catch (error) { setNotice({ text: failure(error, t('rinv.delete')), error: true }); setConfirm(null); } finally { setBusy(false); } })()}
          onCancel={() => setConfirm(null)}><p>{t('rinv.deleteText')}</p></ConfirmDialog>}
      </div>;
    })()}
  </Drawer>;
}
