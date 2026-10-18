// AI calls and the voice Provider of the Phone calls page (CallsView.tsx, docs/calls.md#ai-calls and
// #provider). AI calls go only to contacts with a recorded consent, each waits for an owner's
// approval of the exact number, purpose and script. The provider is the workspace's own Twilio
// account, stored encrypted; nothing here costs anything until the owner connects one.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Bot, Copy, Lock, PhoneCall, Plug, RefreshCw, ShieldOff, Unlink } from 'lucide-react';
import { ConfirmDialog } from './Dialog';
import { ErrorNotice, Notice, Pager } from './CallsParts';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import {
  aiStatusLabel, aiTone, costText, providerBody, providerSteps, PAGE_SIZE,
  type ApiAiCall, type ApiCallsSetup, type ApiContact, type ApiOptOut, type ApiProvider,
} from './lib/calls';
import * as callsApi from './lib/callsApi';
import type { Load } from './lib/pageSummary';

const fail = <T,>(e: unknown): Load<T> => isPermissionDenied(e) ? { state: 'denied', message: describeError(e) } : { state: 'error', message: describeError(e) };

// ----------------------------------------------------------------------------------------- AI calls

export function AiCallsTab({ refresh, setup, contacts, onOpenApprovals, onOpenCall, onOpenContacts }: {
  refresh: number; setup: ApiCallsSetup | null; contacts: ApiContact[]; onOpenApprovals?(): void; onOpenCall(id: string): void; onOpenContacts(): void;
}) {
  const ids = useId();
  const [page, setPage] = useState<Load<{ items: ApiAiCall[]; total: number }>>({ state: 'loading' });
  const [offset, setOffset] = useState(0);
  const [note, setNote] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(() => { callsApi.listAiCalls(offset).then((value) => setPage({ state: 'ready', value }), (e) => setPage(fail(e))); }, [offset, refresh]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  const consenting = contacts.filter((item) => item.ai_consent && !item.opted_out);
  const caps = setup?.capabilities;

  return <div className="callsColumns">
    <section className="pane callsAiNew" aria-labelledby={`${ids}-new`}>
      <div className="paneHead"><h2 id={`${ids}-new`}><Bot aria-hidden="true" />{t('calls.ai.new')}</h2></div>
      <div className="paneBody">
        <p className="muted">{t('calls.ai.rules')}</p>
        {!setup?.can_log && <p className="notice" role="status"><Lock aria-hidden="true" /><span>{t('calls.ai.blocked.role')}</span></p>}
        {setup?.can_log && !caps?.ai_calls && <p className="notice" role="status"><Plug aria-hidden="true" /><span>{caps?.ai_problem ?? t('calls.ai.blocked.off')}</span></p>}
        {setup?.can_log && caps?.ai_calls && (consenting.length
          ? <AiCallForm contacts={consenting} centsPerMinute={caps.cost_per_minute_cents} onSent={() => { setNote(t('calls.ai.sent')); setOffset(0); load(); }} />
          : <div className="paneEmpty"><Bot aria-hidden="true" /><p>{t('calls.ai.noConsenting')}</p><div className="actions"><button type="button" className="secondary" onClick={onOpenContacts}>{t('calls.tab.contacts')}</button></div></div>)}
        {note && <p className="notice" role="status"><Bot aria-hidden="true" /><span>{note}</span>{onOpenApprovals && <button type="button" className="secondary" onClick={onOpenApprovals}>{t('calls.ai.openApprovals')}</button>}</p>}
      </div>
    </section>
    <section className="pane" aria-labelledby={`${ids}-list`} aria-busy={page.state === 'loading'}>
      <div className="paneHead"><h2 id={`${ids}-list`}>{t('calls.ai.title')}</h2></div>
      <div className="paneBody">
        {problem && <Notice error>{problem}</Notice>}
        {page.state === 'loading' && <Notice>{t('calls.ai.loading')}</Notice>}
        {page.state === 'error' && <ErrorNotice message={t('calls.ai.failed', { message: page.message })} onRetry={load} />}
        {page.state === 'denied' && <p className="notice" role="status"><Lock aria-hidden="true" />{page.message}</p>}
        {page.state === 'ready' && !page.value.items.length && <div className="paneEmpty"><Bot aria-hidden="true" /><h3>{t('calls.ai.empty')}</h3><p>{t('calls.ai.emptyText')}</p></div>}
        {page.state === 'ready' && page.value.items.length > 0 && <ul className="callsAiList">{page.value.items.map((item) => <li key={item.id}>
          <div className="callsRowTop"><strong dir="auto">{item.contact_name}</strong><span className={`statusBadge ${aiTone(item.status)}`}>{aiStatusLabel(item.status)}</span></div>
          <small className="muted"><bdi dir="ltr">{item.phone_masked}</bdi> · {formatDateTime(item.created_at)} · {t('calls.ai.requestedBy', { who: item.requested_by })}</small>
          <p dir="auto">{t('calls.ai.purposeShort', { purpose: item.purpose })}</p>
          {item.error && <p className="muted" dir="auto">{item.error}</p>}
          <div className="actions">
            {item.status === 'pending_approval' && onOpenApprovals && <button type="button" className="secondary" onClick={onOpenApprovals}>{t('calls.ai.openApprovals')}</button>}
            {item.status === 'pending_approval' && setup?.can_log && <button type="button" className="secondary" disabled={busy !== null} onClick={() => { setBusy(item.id); setProblem(null); callsApi.cancelAiCall(item.id).then(() => { setNote(t('calls.ai.cancelled')); load(); }, (e) => setProblem(describeError(e))).finally(() => setBusy(null)); }}>{t('calls.ai.cancel')}</button>}
            {item.call_id && <button type="button" className="secondary" onClick={() => onOpenCall(item.call_id!)}><PhoneCall aria-hidden="true" />{t('calls.ai.openCall')}</button>}
          </div>
        </li>)}</ul>}
        {page.state === 'ready' && <Pager offset={offset} limit={PAGE_SIZE} total={page.value.total} onOffset={setOffset} />}
      </div>
    </section>
    <OptOuts setup={setup} />
  </div>;
}

function AiCallForm({ contacts, centsPerMinute, onSent }: { contacts: ApiContact[]; centsPerMinute: number | null; onSent(): void }) {
  const [contactId, setContactId] = useState(contacts[0]?.id ?? '');
  const [purpose, setPurpose] = useState(''), [script, setScript] = useState('');
  const picked = contacts.find((item) => item.id === contactId);
  const [language, setLanguage] = useState<'en' | 'ar'>(picked?.language === 'ar' ? 'ar' : 'en');
  const [seconds, setSeconds] = useState(120);
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  async function send(event: FormEvent) {
    event.preventDefault();
    if (purpose.trim().length < 5 || script.trim().length < 10) { setProblem(t('calls.ai.tooShort')); return; }
    setBusy(true); setProblem(null);
    try { await callsApi.requestAiCall({ contact_id: contactId, purpose: purpose.trim(), script: script.trim(), language, max_seconds: seconds }); setPurpose(''); setScript(''); onSent(); }
    catch (e) { setProblem(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="formGrid callsForm" onSubmit={(event) => void send(event)}>
    <label className="field"><span>{t('calls.ai.contact')}</span><select value={contactId} onChange={(event) => { setContactId(event.target.value); const next = contacts.find((item) => item.id === event.target.value); if (next?.language === 'ar' || next?.language === 'en') setLanguage(next.language); }}>{contacts.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label className="field"><span>{t('calls.ai.language')}</span><select value={language} onChange={(event) => setLanguage(event.target.value as 'en' | 'ar')}><option value="en">{t('calls.contact.lang.en')}</option><option value="ar">{t('calls.contact.lang.ar')}</option></select></label>
    <label className="field callsWide"><span>{t('calls.ai.purpose')}</span><input value={purpose} dir="auto" maxLength={500} onChange={(event) => setPurpose(event.target.value)} /><small className="muted">{t('calls.ai.purposeHint')}</small></label>
    <label className="field callsWide"><span>{t('calls.ai.script')}</span><textarea value={script} dir="auto" maxLength={4000} rows={4} onChange={(event) => setScript(event.target.value)} /><small className="muted">{t('calls.ai.scriptHint')}</small></label>
    <label className="field"><span>{t('calls.ai.maxSeconds')}</span><input type="number" min={30} max={900} step={10} value={seconds} onChange={(event) => setSeconds(Math.max(30, Math.min(900, Number(event.target.value) || 30)))} /><small className="muted">{t('calls.ai.cost', { cost: costText(seconds, centsPerMinute) })}</small></label>
    {problem && <div className="callsWide"><Notice error>{problem}</Notice></div>}
    <div className="actions callsWide"><button type="submit" disabled={busy || !contactId}><Bot aria-hidden="true" />{busy ? t('calls.saving') : t('calls.ai.submit')}</button></div>
  </form>;
}

function OptOuts({ setup }: { setup: ApiCallsSetup | null }) {
  const ids = useId();
  const [page, setPage] = useState<Load<{ items: ApiOptOut[]; total: number }>>({ state: 'loading' });
  const [offset, setOffset] = useState(0);
  const [phone, setPhone] = useState(''), [noteText, setNoteText] = useState('');
  const [lifting, setLifting] = useState<ApiOptOut | null>(null), [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false), [note, setNote] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null);
  const load = useCallback(() => { callsApi.listOptOuts(offset).then((value) => setPage({ state: 'ready', value }), (e) => setPage(fail(e))); }, [offset]);
  useEffect(() => { load(); }, [load]);
  async function add(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setProblem(null); setNote(null);
    try { await callsApi.addOptOut({ phone, ...(noteText.trim() ? { note: noteText.trim() } : {}) }); setPhone(''); setNoteText(''); setNote(t('calls.optOuts.added')); load(); }
    catch (e) { setProblem(describeError(e)); } finally { setBusy(false); }
  }
  return <section className="pane" aria-labelledby={`${ids}-title`}>
    <div className="paneHead"><h2 id={`${ids}-title`}><ShieldOff aria-hidden="true" />{t('calls.optOuts.title')}</h2></div>
    <div className="paneBody">
      <p className="muted">{t('calls.optOuts.text')}</p>
      {setup?.can_log && <form className="formGrid callsForm" onSubmit={(event) => void add(event)}>
        <label className="field"><span>{t('calls.optOuts.number')}</span><input value={phone} dir="ltr" inputMode="tel" maxLength={40} required onChange={(event) => setPhone(event.target.value)} /></label>
        <label className="field"><span>{t('calls.optOuts.note')}</span><input value={noteText} dir="auto" maxLength={500} onChange={(event) => setNoteText(event.target.value)} /></label>
        <div className="actions callsWide"><button type="submit" className="secondary" disabled={busy || !phone.trim()}>{t('calls.optOuts.add')}</button></div>
      </form>}
      {note && <Notice>{note}</Notice>}
      {problem && <Notice error>{problem}</Notice>}
      {page.state === 'loading' && <Notice>{t('calls.ai.loading')}</Notice>}
      {page.state === 'error' && <ErrorNotice message={page.message} onRetry={load} />}
      {page.state === 'ready' && !page.value.items.length && <p className="muted">{t('calls.optOuts.empty')}</p>}
      {page.state === 'ready' && page.value.items.length > 0 && <ul className="callsOptOuts">{page.value.items.map((item) => <li key={item.id}>
        <span><bdi dir="ltr">{item.masked}</bdi><small className="muted">{t(`calls.optOuts.source.${item.source}` as MessageKey)} · {formatDateTime(item.created_at, { dateStyle: 'medium' })}</small></span>
        {setup?.can_manage && <button type="button" className="secondary" aria-label={t('calls.optOuts.removeLabel', { number: item.masked })} onClick={() => { setLifting(item); setReason(''); }}>{t('calls.optOuts.remove')}</button>}
      </li>)}</ul>}
      {page.state === 'ready' && <Pager offset={offset} limit={PAGE_SIZE} total={page.value.total} onOffset={setOffset} />}
    </div>
    {lifting && <ConfirmDialog title={t('calls.optOuts.removeTitle')} confirmLabel={t('calls.optOuts.remove')} busy={busy} confirmDisabled={reason.trim().length < 5} onCancel={() => setLifting(null)}
      onConfirm={() => { setBusy(true); callsApi.removeOptOut(lifting.id, reason.trim()).then(() => { setLifting(null); setNote(t('calls.optOuts.removed')); load(); }, (e) => { setLifting(null); setProblem(describeError(e)); }).finally(() => setBusy(false)); }}>
      <p>{t('calls.optOuts.removeText')}</p>
      <label className="field"><span>{t('calls.optOuts.reason')}</span><input value={reason} dir="auto" maxLength={500} onChange={(event) => setReason(event.target.value)} /></label>
    </ConfirmDialog>}
  </section>;
}

// ----------------------------------------------------------------------------------------- provider

type ProviderForm = {
  account_sid: string; auth_token: string; api_key_sid: string; api_key_secret: string; twiml_app_sid: string; from_number: string;
  business_name: string; greeting: string; recording_enabled: boolean; transcription_enabled: boolean; recording_retention_days: number;
  ai_calls_enabled: boolean; inbound_ai_enabled: boolean; ai_max_seconds: number; ai_max_cost_cents: number; cost_per_minute_cents: number;
};

function formOf(provider: ApiProvider | null): ProviderForm {
  return {
    account_sid: provider?.account_sid ?? '', auth_token: '', api_key_sid: provider?.api_key_sid ?? '', api_key_secret: '', twiml_app_sid: provider?.twiml_app_sid ?? '',
    from_number: provider?.from_number ?? '', business_name: provider?.business_name ?? '', greeting: provider?.greeting ?? '',
    recording_enabled: provider?.recording_enabled ?? false, transcription_enabled: provider?.transcription_enabled ?? false,
    recording_retention_days: provider?.recording_retention_days ?? 30, ai_calls_enabled: provider?.ai_calls_enabled ?? false,
    inbound_ai_enabled: provider?.inbound_ai_enabled ?? false, ai_max_seconds: provider?.ai_max_seconds ?? 180,
    ai_max_cost_cents: provider?.ai_max_cost_cents ?? 100, cost_per_minute_cents: provider?.cost_per_minute_cents ?? 5,
  };
}

export function ProviderTab({ setup, onChanged }: { setup: ApiCallsSetup | null; onChanged(): void }) {
  const ids = useId();
  const owner = setup?.can_manage ?? false;
  const [provider, setProvider] = useState<Load<ApiProvider>>({ state: 'loading' });
  const [form, setForm] = useState<ProviderForm>(formOf(null));
  const [busy, setBusy] = useState<string | null>(null), [note, setNote] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);
  const show = (value: ApiProvider) => { setProvider({ state: 'ready', value }); setForm(formOf(value)); };
  const load = useCallback(() => { if (owner) callsApi.getProvider().then(show, (e) => setProvider(fail(e))); }, [owner]);
  useEffect(() => { load(); }, [load]);
  const set = <K extends keyof ProviderForm>(key: K, value: ProviderForm[K]) => setForm((old) => ({ ...old, [key]: value }));

  if (!owner) return <section className="pane callsProvider" aria-labelledby={`${ids}-title`}>
    <div className="paneHead"><h2 id={`${ids}-title`}><Plug aria-hidden="true" />{t('calls.provider.title')}</h2></div>
    <div className="paneBody">
      <p>{t('calls.provider.what')}</p>
      <p className={`statusBadge ${setup?.capabilities.browser_calls ? 'success' : 'neutral'}`}>{setup?.provider_connected ? t('calls.provider.status.on') : t('calls.provider.status.off')}</p>
      {setup?.capabilities.problem && <p className="muted">{setup.capabilities.problem}</p>}
      <p className="notice" role="status"><Lock aria-hidden="true" /><span>{t('calls.provider.ownerOnly')}</span></p>
    </div>
  </section>;

  const current = provider.state === 'ready' ? provider.value : null;
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy('save'); setProblem(null); setNote(null);
    try { show(await callsApi.saveProvider(providerBody(form))); setNote(t('calls.provider.saved')); onChanged(); }
    catch (e) { setProblem(describeError(e)); } finally { setBusy(null); }
  }
  const copy = (value: string) => { void navigator.clipboard?.writeText(value).then(() => setNote(t('calls.provider.copied')), () => undefined); };
  const status = !current?.connected ? 'off' : current.last_check_status === 'failed' ? 'failed' : 'on';

  return <div className="callsColumns">
    <section className="pane callsProvider" aria-labelledby={`${ids}-title`} aria-busy={provider.state === 'loading'}>
      <div className="paneHead">
        <h2 id={`${ids}-title`}><Plug aria-hidden="true" />{t('calls.provider.title')}</h2>
        <span className={`statusBadge ${status === 'on' ? 'success' : status === 'failed' ? 'dangerTone' : 'neutral'}`}>{t(`calls.provider.status.${status}` as MessageKey)}</span>
      </div>
      <div className="paneBody">
        <p>{t('calls.provider.what')}</p>
        {provider.state === 'loading' && <Notice>{t('calls.provider.loading')}</Notice>}
        {provider.state === 'error' && <ErrorNotice message={t('calls.provider.failed', { message: provider.message })} onRetry={load} />}
        {current && !current.connected && <p className="muted">{t('calls.provider.offText')}</p>}
        {current?.problem && current.connected && <p className="notice" role="status"><Plug aria-hidden="true" /><span>{current.problem}</span></p>}
        {current?.last_checked_at && <p className="muted">{t('calls.provider.lastCheck', { time: formatDateTime(current.last_checked_at), detail: current.last_check_detail ?? '' })}</p>}
        {note && <Notice>{note}</Notice>}
        {problem && <Notice error>{problem}</Notice>}
        {current && <form className="formGrid callsForm callsProviderForm" onSubmit={(event) => void save(event)}>
          <label className="field"><span>{t('calls.provider.accountSid')}</span><input value={form.account_sid} dir="ltr" required autoComplete="off" maxLength={64} onChange={(e) => set('account_sid', e.target.value)} /></label>
          <label className="field"><span>{t('calls.provider.authToken')}</span><input type="password" value={form.auth_token} dir="ltr" autoComplete="new-password" maxLength={200} required={!current.connected} onChange={(e) => set('auth_token', e.target.value)} />{current.credential_hint && <small className="muted">{t('calls.provider.authTokenKept', { hint: current.credential_hint })}</small>}</label>
          <label className="field"><span>{t('calls.provider.fromNumber')}</span><input value={form.from_number} dir="ltr" inputMode="tel" required maxLength={40} onChange={(e) => set('from_number', e.target.value)} /></label>
          <label className="field"><span>{t('calls.provider.business')}</span><input value={form.business_name} dir="auto" required maxLength={120} onChange={(e) => set('business_name', e.target.value)} /></label>
          <label className="field"><span>{t('calls.provider.apiKeySid')}</span><input value={form.api_key_sid} dir="ltr" autoComplete="off" maxLength={64} onChange={(e) => set('api_key_sid', e.target.value)} /></label>
          <label className="field"><span>{t('calls.provider.apiKeySecret')}</span><input type="password" value={form.api_key_secret} dir="ltr" autoComplete="new-password" maxLength={200} onChange={(e) => set('api_key_secret', e.target.value)} />{current.has_api_key_secret && <small className="muted">{t('calls.provider.secretKept')}</small>}</label>
          <label className="field"><span>{t('calls.provider.appSid')}</span><input value={form.twiml_app_sid} dir="ltr" autoComplete="off" maxLength={64} onChange={(e) => set('twiml_app_sid', e.target.value)} /></label>
          <label className="field"><span>{t('calls.provider.greeting')}</span><input value={form.greeting} dir="auto" maxLength={500} onChange={(e) => set('greeting', e.target.value)} /></label>
          <label className="checkRow callsWide"><input type="checkbox" checked={form.recording_enabled} onChange={(e) => { set('recording_enabled', e.target.checked); if (!e.target.checked) set('transcription_enabled', false); }} />{t('calls.provider.recording')}</label>
          <label className="checkRow callsWide"><input type="checkbox" checked={form.transcription_enabled} disabled={!form.recording_enabled} onChange={(e) => set('transcription_enabled', e.target.checked)} />{t('calls.provider.transcription')}</label>
          <label className="field"><span>{t('calls.provider.retention')}</span><input type="number" min={1} max={365} value={form.recording_retention_days} onChange={(e) => set('recording_retention_days', Number(e.target.value) || 30)} /></label>
          <label className="field"><span>{t('calls.provider.rate')}</span><input type="number" min={0} max={1000} value={form.cost_per_minute_cents} onChange={(e) => set('cost_per_minute_cents', Number(e.target.value) || 0)} /></label>
          <label className="checkRow callsWide"><input type="checkbox" checked={form.ai_calls_enabled} onChange={(e) => set('ai_calls_enabled', e.target.checked)} />{t('calls.provider.aiCalls')}</label>
          <label className="checkRow callsWide"><input type="checkbox" checked={form.inbound_ai_enabled} onChange={(e) => set('inbound_ai_enabled', e.target.checked)} />{t('calls.provider.inboundAi')}</label>
          <label className="field"><span>{t('calls.provider.aiMax', { max: current.limits.ai_max_seconds })}</span><input type="number" min={30} max={current.limits.ai_max_seconds} value={form.ai_max_seconds} onChange={(e) => set('ai_max_seconds', Number(e.target.value) || 30)} /></label>
          <label className="field"><span>{t('calls.provider.aiCost', { max: current.limits.ai_max_cost_cents })}</span><input type="number" min={1} max={current.limits.ai_max_cost_cents} value={form.ai_max_cost_cents} onChange={(e) => set('ai_max_cost_cents', Number(e.target.value) || 1)} /></label>
          <div className="actions callsWide">
            <button type="submit" disabled={busy !== null}>{busy === 'save' ? t('calls.saving') : t('calls.provider.save')}</button>
            {current.connected && <button type="button" className="secondary" disabled={busy !== null} onClick={() => { setBusy('check'); setProblem(null); callsApi.checkProvider().then((value) => { show(value); setNote(t('calls.provider.checked', { detail: value.last_check_detail ?? '' })); onChanged(); }, (e) => setProblem(describeError(e))).finally(() => setBusy(null)); }}><RefreshCw aria-hidden="true" />{t('calls.provider.check')}</button>}
            {current.connected && <button type="button" className="danger" disabled={busy !== null} onClick={() => setDisconnecting(true)}><Unlink aria-hidden="true" />{t('calls.provider.disconnect')}</button>}
          </div>
        </form>}
      </div>
    </section>
    <section className="pane" aria-labelledby={`${ids}-steps`}>
      <div className="paneHead"><h2 id={`${ids}-steps`}>{t('calls.provider.steps')}</h2></div>
      <div className="paneBody">
        <ol className="callsSteps">{providerSteps().map((step) => <li key={step}>{step}</li>)}</ol>
        {current && !current.webhooks_ready && <p className="notice" role="status"><Plug aria-hidden="true" /><span>{t('calls.provider.noPublicUrl')}</span></p>}
        {current?.voice_url && <UrlRow label={t('calls.provider.voiceUrl')} value={current.voice_url} onCopy={copy} />}
        {current?.status_url && <UrlRow label={t('calls.provider.statusUrl')} value={current.status_url} onCopy={copy} />}
        <p className="muted">{t('calls.provider.sip')}</p>
      </div>
    </section>
    {disconnecting && <ConfirmDialog title={t('calls.provider.disconnectTitle')} confirmLabel={t('calls.provider.disconnect')} danger busy={busy === 'disconnect'} onCancel={() => setDisconnecting(false)}
      onConfirm={() => { setBusy('disconnect'); callsApi.deleteProvider().then(() => { setDisconnecting(false); setNote(t('calls.provider.disconnected')); load(); onChanged(); }, (e) => { setDisconnecting(false); setProblem(describeError(e)); }).finally(() => setBusy(null)); }}>
      <p>{t('calls.provider.disconnectText')}</p>
    </ConfirmDialog>}
  </div>;
}

function UrlRow({ label, value, onCopy }: { label: string; value: string; onCopy(value: string): void }) {
  return <div className="callsUrl">
    <span className="muted">{label}</span>
    <code dir="ltr">{value}</code>
    <button type="button" className="iconButton small" aria-label={t('calls.provider.copy', { what: label })} title={t('calls.provider.copy', { what: label })} onClick={() => onCopy(value)}><Copy aria-hidden="true" /></button>
  </div>;
}

