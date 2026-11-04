// iPhone SMS banking and the double-entry ledger (docs/finance-sms.md). Its own lazy chunk: the
// "iPhone SMS" tab (safety check, switch, devices with a token shown once, the Shortcut steps, the
// review queue with corrections and taught formats, banks, reconciliation) and the "Ledger" tab (the
// journal's dashboard with filters). Nothing here moves money.
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent } from 'react';
import { CheckCircle2, ClipboardCopy, MessageSquareText, Pause, Play, Plus, RefreshCw, ShieldCheck, Smartphone, Trash2, Undo2, XCircle } from 'lucide-react';
import type {
  FinanceAccount, FinanceCategory, FinanceImport, FinanceJournalPage, FinanceLedgerDashboard, FinanceReconcileView, FinanceSmsBank,
  FinanceSmsCorrection, FinanceSmsDevice, FinanceSmsDeviceToken, FinanceSmsMessage, FinanceSmsMessagePage, FinanceSmsRule, FinanceSmsSettings,
  FinanceSmsStatus, FinanceSmsStatusView,
} from '@anum/contracts';
import * as api from './lib/financeSmsApi';
import { listAccounts, listImports } from './lib/financeApi';
import { apiBaseUrl } from './lib/api';
import { SMS_KINDS, SMS_STATUSES, correctionBody, correctionDefaults, ingestUrl, kindLabel, ledgerAccountLabel, reasonLabel, shortcutSteps } from './lib/financeSms';
import { categoryLabel, formatAmounts, monthLabel, shiftMonth } from './lib/finance';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, FormError, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import './financeSms.css';

const PAGE = 20;
const CATEGORIES: FinanceCategory[] = [
  'groceries', 'dining', 'transport', 'fuel', 'utilities', 'telecom', 'housing', 'shopping', 'health', 'education', 'entertainment',
  'subscriptions', 'travel', 'insurance', 'fees', 'cash', 'transfer', 'income', 'other',
];
const when = (value: string | null | undefined) => (value ? formatDateTime(value) : '-');

function useLoad<T>(load: () => Promise<T>): [Load<T>, () => void] {
  const [state, setState] = useState<Load<T>>({ kind: 'loading' });
  const run = useCallback(() => { load().then((value) => setState({ kind: 'ready', value }), (e) => setState(failed(e))); }, [load]);
  useEffect(() => { run(); }, [run]);
  return [state, run];
}

// ---- iPhone SMS ------------------------------------------------------------------------------

type SmsSection = 'setup' | 'review' | 'rules' | 'banks' | 'reconcile';
const SECTIONS: SmsSection[] = ['setup', 'review', 'rules', 'banks', 'reconcile'];

export function SmsPanel() {
  const [status, reloadStatus] = useLoad(api.smsStatus);
  const [settings, reloadSettings] = useLoad(api.smsSettings);
  const [accounts, reloadAccounts] = useLoad(listAccounts);
  const [section, setSection] = useState<SmsSection>('setup');
  const reload = () => { reloadStatus(); reloadSettings(); reloadAccounts(); };
  if (status.kind === 'denied' || settings.kind === 'denied') return <Denied />;
  if (status.kind === 'error') return <LoadError message={t('fsms.error.load', { message: status.message })} retry={reload} />;
  if (settings.kind === 'error') return <LoadError message={t('fsms.error.load', { message: settings.message })} retry={reload} />;
  if (status.kind !== 'ready' || settings.kind !== 'ready') return <section className="surface cmPanel"><Notice busy>{t('fsms.loading')}</Notice></section>;
  const accountList = accounts.kind === 'ready' ? accounts.value.filter((a) => !a.archived) : [];
  return <div className="finSms">
    <StatusCard status={status.value} settings={settings.value} onChanged={reload} onReview={() => setSection('review')} />
    <nav className="settingsTabs cmTabs finSmsTabs" aria-label={t('fsms.sections')}>
      {SECTIONS.map((id) => <button key={id} type="button" aria-current={section === id ? 'page' : undefined} onClick={() => setSection(id)}>
        {t(`fsms.section.${id}` as MessageKey)}{id === 'review' && status.value.review_count > 0 ? ` (${status.value.review_count})` : ''}</button>)}
    </nav>
    {section === 'setup' && <>
      <Devices status={status.value} onChanged={reload} />
      <SettingsForm settings={settings.value} accounts={accountList} onSaved={reload} />
    </>}
    {section === 'review' && <Review accounts={accountList} aiOn={settings.value.ai.enabled} onChanged={reload} />}
    {section === 'rules' && <Rules accounts={accountList} />}
    {section === 'banks' && <Banks />}
    {section === 'reconcile' && <Reconcile />}
  </div>;
}

function StatusCard({ status, settings, onChanged, onReview }: { status: FinanceSmsStatusView; settings: FinanceSmsSettings; onChanged: () => void; onReview: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checks, setChecks] = useState<{ name: string; passed: boolean }[] | null>(null);
  const selfTest = async () => {
    setBusy(true); setError(null);
    try { const result = await api.runSelfTest(); setChecks(result.checks); if (!result.passed) setError(t('fsms.selfTest.failed')); onChanged(); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const toggle = async () => {
    setBusy(true); setError(null);
    try { await api.saveSmsSettings({ version: settings.version, enabled: !settings.enabled }); onChanged(); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <section className="surface cmPanel" aria-labelledby="fsms-status">
    <div className="sectionHeader"><h2 id="fsms-status"><Smartphone aria-hidden="true" className="cmInlineIcon" />{t('fsms.title')}</h2>
      <span className={`statusBadge ${status.enabled ? 'success' : 'neutral'}`}>{status.enabled ? t('fsms.on') : t('fsms.off')}</span></div>
    <p className="muted">{t('fsms.intro')}</p>
    <dl className="finSmsFacts">
      <div><dt>{t('fsms.safetyCheck')}</dt><dd>{status.ready ? t('fsms.selfTest.passedAt', { date: when(status.self_test_passed_at) }) : t('fsms.selfTest.notRun')}</dd></div>
      <div><dt>{t('fsms.ledgerKind')}</dt><dd>{t(`fsms.ledgerKind.${status.ledger_kind}` as MessageKey)}</dd></div>
      <div><dt>{t('fsms.devicesActive')}</dt><dd>{status.devices_active}</dd></div>
      <div><dt>{t('fsms.lastReceived')}</dt><dd>{when(status.last_received_at)}</dd></div>
      <div><dt>{t('fsms.lastOk')}</dt><dd>{when(status.last_ok_at)}</dd></div>
      <div><dt>{t('fsms.lastError')}</dt><dd>{status.last_error ? `${reasonLabel(status.last_error)} (${when(status.last_error_at)})` : '-'}</dd></div>
    </dl>
    {checks && <ul className="finSmsChecks" aria-label={t('fsms.selfTest.results')}>{checks.map((c) => <li key={c.name}>
      {c.passed ? <CheckCircle2 aria-hidden="true" className="finIn" /> : <XCircle aria-hidden="true" className="finOut" />}
      {t(`fsms.check.${c.name}` as MessageKey)}: {c.passed ? t('fsms.passed') : t('fsms.failed')}</li>)}</ul>}
    <FormError id="fsms-status-error" text={error} />
    <div className="actions">
      <button type="button" className={status.ready ? 'secondary' : undefined} disabled={busy} onClick={() => void selfTest()}><ShieldCheck aria-hidden="true" />{t('fsms.selfTest.run')}</button>
      <button type="button" className={status.enabled ? 'secondary' : undefined} disabled={busy || !status.ready} onClick={() => void toggle()}>
        {status.enabled ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}{status.enabled ? t('fsms.turnOff') : t('fsms.turnOn')}</button>
      {status.review_count > 0 && <button type="button" className="secondary" onClick={onReview}>{t('fsms.reviewWaiting', { count: status.review_count })}</button>}
    </div>
    {!status.ready && <p className="muted finHelp">{t('fsms.selfTest.why')}</p>}
  </section>;
}

function Devices({ status, onChanged }: { status: FinanceSmsStatusView; onChanged: () => void }) {
  const [devices, reload] = useLoad(api.smsDevices);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [issued, setIssued] = useState<FinanceSmsDeviceToken | null>(null);
  const [revoking, setRevoking] = useState<FinanceSmsDevice | null>(null);
  const nameId = useId();
  const url = ingestUrl(apiBaseUrl, status.ingest_path);
  const add = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(null);
    try { setIssued(await api.createDevice(name.trim() || 'iPhone')); setName(''); reload(); onChanged(); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const change = async (device: FinanceSmsDevice, what: 'pause' | 'resume' | 'revoke' | 'rotate') => {
    setBusy(true); setError(null);
    try {
      if (what === 'rotate') setIssued(await api.rotateDevice(device.id)); else await api.changeDevice(device.id, what);
      reload(); onChanged();
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); setRevoking(null); }
  };
  return <section className="surface cmPanel" aria-labelledby="fsms-devices">
    <h2 id="fsms-devices">{t('fsms.devices')}</h2>
    {url ? <div className="finSmsEndpoint"><span className="muted">{t('fsms.endpoint')}</span><code dir="ltr">{url}</code></div>
      : <p className="muted">{t('fsms.endpointHidden')}</p>}
    {devices.kind === 'loading' && <Notice busy>{t('fsms.loading')}</Notice>}
    {devices.kind === 'error' && <LoadError message={devices.message} retry={reload} />}
    {devices.kind === 'ready' && (devices.value.length === 0
      ? <Empty icon={<Smartphone aria-hidden="true" />} title={t('fsms.devices.none')} text={status.ready ? t('fsms.devices.noneText') : t('fsms.devices.needCheck')} />
      : <ul className="cmList">{devices.value.map((d) => <li key={d.id} className="finSmsDevice">
        <div><strong dir="auto">{d.name}</strong>
          <small>{t(`fsms.device.${d.status}` as MessageKey)} · {t('fsms.device.token', { hint: d.token_hint })} · {t('fsms.device.messages', { count: d.message_count })}</small>
          <small>{t('fsms.lastOk')}: {when(d.last_ok_at)} · {t('fsms.lastError')}: {d.last_error ? `${reasonLabel(d.last_error)} (${when(d.last_error_at)})` : '-'}</small></div>
        {d.status !== 'revoked' && <div className="actions">
          {d.status === 'active'
            ? <button type="button" className="secondary" disabled={busy} onClick={() => void change(d, 'pause')}><Pause aria-hidden="true" />{t('fsms.device.pause')}</button>
            : <button type="button" className="secondary" disabled={busy} onClick={() => void change(d, 'resume')}><Play aria-hidden="true" />{t('fsms.device.resume')}</button>}
          <button type="button" className="secondary" disabled={busy} onClick={() => void change(d, 'rotate')}><RefreshCw aria-hidden="true" />{t('fsms.device.rotate')}</button>
          <button type="button" className="secondary" disabled={busy} onClick={() => setRevoking(d)}><Trash2 aria-hidden="true" />{t('fsms.device.revoke')}</button>
        </div>}
      </li>)}</ul>)}
    <form className="finSmsAdd" onSubmit={(e) => void add(e)}>
      <label className="cmInline" htmlFor={nameId}><span>{t('fsms.device.name')}</span></label>
      <input id={nameId} value={name} maxLength={60} placeholder="iPhone" onChange={(e) => setName(e.target.value)} disabled={!status.ready} />
      <button type="submit" disabled={busy || !status.ready}><Plus aria-hidden="true" />{t('fsms.device.add')}</button>
    </form>
    <FormError id="fsms-device-error" text={error} />
    {issued && <TokenDialog issued={issued} url={url} onClose={() => setIssued(null)} />}
    {revoking && <ConfirmDialog title={t('fsms.device.revokeTitle')} confirmLabel={t('fsms.device.revoke')} danger busy={busy}
      onConfirm={() => void change(revoking, 'revoke')} onCancel={() => setRevoking(null)}><p>{t('fsms.device.revokeText', { name: revoking.name })}</p></ConfirmDialog>}
  </section>;
}

function TokenDialog({ issued, url, onClose }: { issued: FinanceSmsDeviceToken; url: string | null; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const full = url ?? ingestUrl(apiBaseUrl, issued.ingest_path) ?? '';
  const copy = async () => {
    try { await navigator.clipboard.writeText(issued.token); setCopied(true); } catch { setCopied(false); }
  };
  return <Modal title={t('fsms.token.title')} onClose={onClose} className="finSmsToken">
    <p className="notice">{t('fsms.token.once')}</p>
    <label className="field"><span>{t('fsms.token.label')}</span><textarea readOnly value={issued.token} rows={3} dir="ltr" onFocus={(e) => e.currentTarget.select()} /></label>
    <div className="actions"><button type="button" className="secondary" onClick={() => void copy()}><ClipboardCopy aria-hidden="true" />{copied ? t('fsms.token.copied') : t('fsms.token.copy')}</button></div>
    <h3>{t('fsms.shortcut.title')}</h3>
    <dl className="finSmsFacts">
      <div><dt>{t('fsms.endpoint')}</dt><dd><code dir="ltr">{full}</code></dd></div>
      <div><dt>{t('fsms.shortcut.method')}</dt><dd><code dir="ltr">POST</code></dd></div>
      <div><dt>{t('fsms.shortcut.headers')}</dt><dd>{Object.entries(issued.headers).map(([k, v]) => <code key={k} dir="ltr">{k}: {v}</code>)}</dd></div>
      <div><dt>{t('fsms.shortcut.json')}</dt><dd>{Object.entries(issued.json_fields).map(([k, v]) => <code key={k} dir="ltr">{k} = {v}</code>)}</dd></div>
    </dl>
    <ol className="finSmsSteps">{shortcutSteps(full).map((step, index) => <li key={index}>{step}</li>)}</ol>
    <p className="muted finHelp">{t('fsms.shortcut.limits')}</p>
    <div className="actions"><button type="button" onClick={onClose}>{t('fsms.token.done')}</button></div>
  </Modal>;
}

interface MapRow { last4: string; account_id: string; credit_limit: string }

function SettingsForm({ settings, accounts, onSaved }: { settings: FinanceSmsSettings; accounts: FinanceAccount[]; onSaved: () => void }) {
  const [kind, setKind] = useState(settings.ledger_kind);
  const [autoPost, setAutoPost] = useState(settings.auto_post);
  const [retention, setRetention] = useState(String(settings.body_retention_days));
  const [defaultAccount, setDefaultAccount] = useState(settings.default_account_id ?? '');
  const [cashAccount, setCashAccount] = useState(settings.cash_account_id ?? '');
  const [rows, setRows] = useState<MapRow[]>(settings.account_map.map((m) => ({ last4: m.last4, account_id: m.account_id, credit_limit: m.credit_limit ?? '' })));
  const [senders, setSenders] = useState(settings.extra_senders.map((s) => s.sender).join(', '));
  const [ai, setAi] = useState(settings.ai.enabled);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const errorId = useId();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(null); setSaved(false);
    try {
      await api.saveSmsSettings({
        version: settings.version, ledger_kind: kind, auto_post: autoPost, body_retention_days: Number(retention),
        default_account_id: defaultAccount || null, cash_account_id: cashAccount || null,
        account_map: rows.filter((r) => r.last4 && r.account_id).map((r) => ({ last4: r.last4, account_id: r.account_id, credit_limit: r.credit_limit || null })),
        extra_senders: senders.split(',').map((s) => s.trim()).filter(Boolean).map((s) => ({ sender: s, bank: s.toLowerCase().slice(0, 40) })),
        ai_enabled: ai, ai_consent: ai && !settings.ai.enabled ? consent : undefined,
      });
      setSaved(true); onSaved();
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const cash = accounts.filter((a) => a.kind === 'cash');
  return <section className="surface cmPanel" aria-labelledby="fsms-settings">
    <h2 id="fsms-settings">{t('fsms.settings')}</h2>
    <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <div className="cmFormGrid">
        <label className="field"><span>{t('fsms.ledgerKind')}</span><select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
          <option value="personal">{t('fsms.ledgerKind.personal')}</option><option value="business">{t('fsms.ledgerKind.business')}</option></select></label>
        <label className="field"><span>{t('fsms.retention')}</span><input type="number" min={0} max={365} value={retention} onChange={(e) => setRetention(e.target.value)} dir="ltr" /></label>
        <label className="field"><span>{t('fsms.defaultAccount')}</span><select value={defaultAccount} onChange={(e) => setDefaultAccount(e.target.value)}>
          <option value="">{t('fsms.none')}</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
        <label className="field"><span>{t('fsms.cashAccount')}</span><select value={cashAccount} onChange={(e) => setCashAccount(e.target.value)}>
          <option value="">{t('fsms.cashAsSpending')}</option>{cash.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      </div>
      <p className="muted finHelp">{t('fsms.retentionHelp')} {t('fsms.ledgerKindHelp')}</p>
      <label className="finCheck"><input type="checkbox" checked={autoPost} onChange={(e) => setAutoPost(e.target.checked)} />{t('fsms.autoPost')}</label>
      <fieldset className="finMapping"><legend>{t('fsms.map')}</legend>
        <p className="muted finHelp">{t('fsms.mapHelp')}</p>
        {rows.map((row, index) => <div key={index} className="cmFilters finLimitRow">
          <label className="cmInline"><span>{t('fsms.map.last4')}</span><input value={row.last4} inputMode="numeric" maxLength={4} pattern="\d{4}" dir="ltr"
            onChange={(e) => setRows(rows.map((r, i) => (i === index ? { ...r, last4: e.target.value } : r)))} /></label>
          <label className="cmInline"><span>{t('fsms.map.account')}</span><select value={row.account_id} onChange={(e) => setRows(rows.map((r, i) => (i === index ? { ...r, account_id: e.target.value } : r)))}>
            <option value="">{t('fsms.pick')}</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
          <label className="cmInline"><span>{t('fsms.map.limit')}</span><input value={row.credit_limit} inputMode="decimal" dir="ltr" onChange={(e) => setRows(rows.map((r, i) => (i === index ? { ...r, credit_limit: e.target.value } : r)))} /></label>
          <button type="button" className="iconButton small" aria-label={t('fsms.map.remove')} onClick={() => setRows(rows.filter((_, i) => i !== index))}><Trash2 aria-hidden="true" /></button>
        </div>)}
        <button type="button" className="secondary" onClick={() => setRows([...rows, { last4: '', account_id: '', credit_limit: '' }])}><Plus aria-hidden="true" />{t('fsms.map.add')}</button>
      </fieldset>
      <label className="field"><span>{t('fsms.senders')}</span><input value={senders} onChange={(e) => setSenders(e.target.value)} dir="ltr" placeholder="MyBank, MyCard" /></label>
      <p className="muted finHelp">{t('fsms.sendersHelp')}</p>
      <fieldset className="finMapping"><legend>{t('fsms.ai')}</legend>
        <p className="muted finHelp">{t('fsms.aiHelp')}</p>
        <label className="finCheck"><input type="checkbox" checked={ai} onChange={(e) => setAi(e.target.checked)} />{t('fsms.aiOn')}</label>
        {ai && !settings.ai.enabled && <label className="finCheck"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />{t('fsms.aiConsent')}</label>}
      </fieldset>
      <FormError id={errorId} text={error} />
      {saved && <Notice>{t('fsms.saved')}</Notice>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('finance.saving') : t('finance.save')}</button></div>
    </form>
  </section>;
}

function Review({ accounts, aiOn, onChanged }: { accounts: FinanceAccount[]; aiOn: boolean; onChanged: () => void }) {
  const [status, setStatus] = useState<FinanceSmsStatus | ''>('review');
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Load<FinanceSmsMessagePage>>({ kind: 'loading' });
  const [open, setOpen] = useState<FinanceSmsMessage | null>(null);
  const load = useCallback(() => {
    api.smsMessages(status, PAGE, offset).then((value) => setPage({ kind: 'ready', value }), (e) => setPage(failed(e)));
  }, [status, offset]);
  useEffect(() => { load(); }, [load]);
  const changed = () => { load(); onChanged(); };
  return <>
    <Paste onDone={changed} />
    <section className="surface cmPanel" aria-labelledby="fsms-review">
      <div className="sectionHeader"><h2 id="fsms-review">{t('fsms.review')}</h2>
        <label className="cmInline"><span>{t('fsms.status')}</span><select value={status} onChange={(e) => { setOffset(0); setStatus(e.target.value as FinanceSmsStatus | ''); }}>
          <option value="">{t('fsms.status.all')}</option>{SMS_STATUSES.map((s) => <option key={s} value={s}>{t(`fsms.status.${s}` as MessageKey)}</option>)}</select></label></div>
      {page.kind === 'loading' && <Notice busy>{t('fsms.loading')}</Notice>}
      {page.kind === 'denied' && <Denied />}
      {page.kind === 'error' && <LoadError message={page.message} retry={load} />}
      {page.kind === 'ready' && (page.value.items.length === 0
        ? <Empty icon={<MessageSquareText aria-hidden="true" />} title={t('fsms.review.none')} text={t('fsms.review.noneText')} />
        : <>
          <ul className="cmList">{page.value.items.map((m) => <li key={m.id} className="finSmsMessage">
            <div><strong dir="ltr">{m.sender}</strong>
              <small>{when(m.received_at)} · {t(`fsms.status.${m.status}` as MessageKey)} · {reasonLabel(m.reason)}</small>
              <small>{kindLabel(m.parsed.kind)}{m.parsed.amount ? ` · ${m.parsed.currency ?? ''} ${m.parsed.amount}` : ''}{m.parsed.merchant ? ` · ${m.parsed.merchant}` : ''}</small>
              {m.body_masked && <p className="finSmsBody" dir="auto">{m.body_masked}</p>}</div>
            {m.status !== 'rejected' && m.status !== 'dismissed' && <button type="button" className="secondary" onClick={() => setOpen(m)}>{m.status === 'posted' ? t('fsms.correct') : t('fsms.check')}</button>}
          </li>)}</ul>
          <div className="cmPager"><button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>{t('fsms.prev')}</button>
            <span className="muted cmPagerText">{t('fsms.range', { from: offset + 1, to: Math.min(offset + PAGE, page.value.total), total: page.value.total })}</span>
            <button type="button" className="secondary" disabled={offset + PAGE >= page.value.total} onClick={() => setOffset(offset + PAGE)}>{t('fsms.next')}</button></div>
        </>)}
    </section>
    {open && <CorrectDialog message={open} accounts={accounts} aiOn={aiOn} onClose={(done) => { setOpen(null); if (done) changed(); }} />}
  </>;
}

function Paste({ onDone }: { onDone: () => void }) {
  const [sender, setSender] = useState('');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(null); setResult(null);
    try { const r = await api.pasteMessage(sender, text); setResult(`${r.text} ${r.reason ? `(${reasonLabel(r.reason)})` : ''}`); setText(''); onDone(); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <section className="surface cmPanel" aria-labelledby="fsms-paste">
    <h2 id="fsms-paste">{t('fsms.paste')}</h2>
    <p className="muted">{t('fsms.pasteHelp')}</p>
    <form className="finForm" onSubmit={(e) => void submit(e)}>
      <label className="field"><span>{t('fsms.paste.sender')}</span><input value={sender} required maxLength={200} onChange={(e) => setSender(e.target.value)} dir="auto" /></label>
      <label className="field"><span>{t('fsms.paste.text')}</span><textarea value={text} required maxLength={2000} rows={3} onChange={(e) => setText(e.target.value)} dir="auto" /></label>
      <FormError id="fsms-paste-error" text={error} />
      {result && <Notice>{result}</Notice>}
      <div className="actions"><button type="submit" disabled={busy}>{t('fsms.paste.send')}</button></div>
    </form>
  </section>;
}

function CorrectDialog({ message, accounts, aiOn, onClose }: { message: FinanceSmsMessage; accounts: FinanceAccount[]; aiOn: boolean; onClose: (done: boolean) => void }) {
  const [form, setForm] = useState<FinanceSmsCorrection>(() => correctionDefaults(message));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const errorId = useId();
  const set = <K extends keyof FinanceSmsCorrection>(key: K, value: FinanceSmsCorrection[K]) => setForm({ ...form, [key]: value });
  const act = async (what: 'post' | 'dismiss' | 'reverse' | 'suggest', event?: FormEvent) => {
    event?.preventDefault();
    setBusy(true); setError(null);
    try {
      if (what === 'post') {
        const result = await api.postMessage(message.id, correctionBody(form));
        if (result.rule_error) { setNote(result.rule_error); setBusy(false); return; }
        onClose(true); return;
      }
      if (what === 'dismiss') { await api.dismissMessage(message.id); onClose(true); return; }
      if (what === 'reverse') { await api.reverseMessage(message.id); onClose(true); return; }
      const suggestion = await api.suggestFields(message.id);
      setForm({ ...form, ...suggestion.fields } as FinanceSmsCorrection);
      setNote(suggestion.note);
    } catch (e) { setError(describeError(e)); }
    setBusy(false);
  };
  const field = (key: keyof FinanceSmsCorrection, label: string, extra?: { inputMode?: 'decimal' | 'numeric'; type?: string }) =>
    <label className="field"><span>{label}</span><input value={String(form[key] ?? '')} type={extra?.type ?? 'text'} inputMode={extra?.inputMode} dir="auto"
      onChange={(e) => set(key, e.target.value as never)} /></label>;
  return <Modal title={t('fsms.correct.title')} onClose={() => onClose(false)} className="formDialog finSmsCorrect">
    {message.body_masked ? <p className="finSmsBody" dir="auto">{message.body_masked}</p> : <p className="muted">{t('fsms.bodyGone')}</p>}
    <p className="muted">{t(`fsms.status.${message.status}` as MessageKey)} · {reasonLabel(message.reason)}</p>
    <form className="finForm" onSubmit={(e) => void act('post', e)} aria-describedby={error ? errorId : undefined}>
      <div className="cmFormGrid">
        <label className="field"><span>{t('fsms.field.kind')}</span><select value={form.kind ?? ''} required onChange={(e) => set('kind', e.target.value as FinanceSmsCorrection['kind'])}>
          <option value="">{t('fsms.pick')}</option>{SMS_KINDS.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}</select></label>
        {field('amount', t('fsms.field.amount'), { inputMode: 'decimal' })}
        {field('currency', t('fsms.field.currency'))}
        {field('converted_amount', t('fsms.field.converted'), { inputMode: 'decimal' })}
        <label className="field"><span>{t('fsms.field.account')}</span><select value={form.account_id ?? ''} onChange={(e) => set('account_id', e.target.value)}>
          <option value="">{t('fsms.asRead')}</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}{a.last4 ? ` (${a.last4})` : ''}</option>)}</select></label>
        <label className="field"><span>{t('fsms.field.counter')}</span><select value={form.counter_account_id ?? ''} onChange={(e) => set('counter_account_id', e.target.value)}>
          <option value="">{t('fsms.none')}</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}{a.last4 ? ` (${a.last4})` : ''}</option>)}</select></label>
        {field('merchant', t('fsms.field.merchant'))}
        <label className="field"><span>{t('fsms.field.category')}</span><select value={form.category ?? ''} onChange={(e) => set('category', e.target.value)}>
          <option value="">{t('fsms.automatic')}</option>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
        {field('occurred_on', t('fsms.field.date'), { type: 'date' })}
        {field('reference', t('fsms.field.reference'))}
        {field('interest', t('fsms.field.interest'), { inputMode: 'decimal' })}
        {field('fee', t('fsms.field.fee'), { inputMode: 'decimal' })}
        {field('last4', t('fsms.field.last4'), { inputMode: 'numeric' })}
      </div>
      <label className="finCheck"><input type="checkbox" checked={!!form.confirm_format} onChange={(e) => set('confirm_format', e.target.checked)} />{t('fsms.confirmFormat')}</label>
      <label className="finCheck"><input type="checkbox" checked={!!form.teach} onChange={(e) => set('teach', e.target.checked)} disabled={!message.body_masked} />{t('fsms.teach')}</label>
      {message.status === 'duplicate' && <label className="finCheck"><input type="checkbox" checked={!!form.allow_duplicate} onChange={(e) => set('allow_duplicate', e.target.checked)} />{t('fsms.allowDuplicate')}</label>}
      <FormError id={errorId} text={error} />
      {note && <Notice>{note}</Notice>}
      <div className="actions">
        <button type="submit" disabled={busy}>{message.status === 'posted' ? t('fsms.recordCorrection') : t('fsms.record')}</button>
        {aiOn && message.body_masked && <button type="button" className="secondary" disabled={busy} onClick={() => void act('suggest')}>{t('fsms.suggest')}</button>}
        {message.status === 'posted'
          ? <button type="button" className="secondary" disabled={busy} onClick={() => void act('reverse')}><Undo2 aria-hidden="true" />{t('fsms.undo')}</button>
          : <button type="button" className="secondary" disabled={busy} onClick={() => void act('dismiss')}>{t('fsms.dismiss')}</button>}
        <button type="button" className="secondary" disabled={busy} onClick={() => onClose(false)}>{t('common.cancel')}</button>
      </div>
    </form>
  </Modal>;
}

function Rules({ accounts }: { accounts: FinanceAccount[] }) {
  const [rules, reload] = useLoad(api.smsRules);
  const [error, setError] = useState<string | null>(null);
  const names = Object.fromEntries(accounts.map((a) => [a.id, a.name]));
  const act = async (rule: FinanceSmsRule, what: 'activate' | 'disable' | 'delete') => {
    setError(null);
    try { if (what === 'delete') await api.deleteRule(rule.id); else await api.changeRule(rule.id, what); reload(); }
    catch (e) { setError(describeError(e)); }
  };
  return <section className="surface cmPanel" aria-labelledby="fsms-rules">
    <h2 id="fsms-rules">{t('fsms.rules')}</h2>
    <p className="muted">{t('fsms.rulesHelp')}</p>
    {rules.kind === 'loading' && <Notice busy>{t('fsms.loading')}</Notice>}
    {rules.kind === 'error' && <LoadError message={rules.message} retry={reload} />}
    {rules.kind === 'ready' && (rules.value.length === 0
      ? <Empty icon={<MessageSquareText aria-hidden="true" />} title={t('fsms.rules.none')} text={t('fsms.rules.noneText')} />
      : <ul className="cmList">{rules.value.map((r) => <li key={r.id}>
        <div><strong dir="ltr">{r.sender}</strong><small>{kindLabel(r.kind)} · {t(`fsms.rule.${r.status}` as MessageKey)} · {t('fsms.rule.hits', { count: r.hits })}{r.account_id ? ` · ${names[r.account_id] ?? ''}` : ''}</small>
          <code className="finSmsPattern" dir="ltr">{r.pattern_preview}</code></div>
        <div className="actions">
          {r.status !== 'active' ? <button type="button" onClick={() => void act(r, 'activate')}>{t('fsms.rule.activate')}</button>
            : <button type="button" className="secondary" onClick={() => void act(r, 'disable')}>{t('fsms.rule.disable')}</button>}
          <button type="button" className="secondary" onClick={() => void act(r, 'delete')}><Trash2 aria-hidden="true" />{t('fsms.rule.delete')}</button>
        </div></li>)}</ul>)}
    <FormError id="fsms-rule-error" text={error} />
  </section>;
}

function Banks() {
  const [banks, reload] = useLoad(api.smsBanks);
  return <section className="surface cmPanel" aria-labelledby="fsms-banks">
    <h2 id="fsms-banks">{t('fsms.banks')}</h2>
    <p className="notice">{t('fsms.banksHelp')}</p>
    {banks.kind === 'loading' && <Notice busy>{t('fsms.loading')}</Notice>}
    {banks.kind === 'error' && <LoadError message={banks.message} retry={reload} />}
    {banks.kind === 'ready' && <div className="cmTableWrap" tabIndex={0}><table className="cmTable">
      <thead><tr><th scope="col">{t('fsms.bank')}</th><th scope="col">{t('fsms.bank.country')}</th><th scope="col">{t('fsms.bank.senders')}</th><th scope="col">{t('fsms.bank.state')}</th></tr></thead>
      <tbody>{banks.value.map((b: FinanceSmsBank) => <tr key={b.key}><td>{b.name}</td><td>{b.country}</td><td dir="ltr">{b.senders.join(', ')}</td>
        <td>{b.confirmed_by_you ? t('fsms.bank.confirmed') : t('fsms.bank.unverified')}</td></tr>)}</tbody></table></div>}
  </section>;
}

function Reconcile() {
  const [imports, reload] = useLoad(useCallback(() => listImports(50, 0), []));
  const [chosen, setChosen] = useState('');
  const [view, setView] = useState<Load<FinanceReconcileView> | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const pick = (importId: string) => {
    setChosen(importId); setDone(null);
    if (!importId) { setView(null); return; }
    setView({ kind: 'loading' });
    api.reconcilePreview(importId).then((value) => setView({ kind: 'ready', value }), (e) => setView(failed(e)));
  };
  const confirm = async () => {
    if (view?.kind !== 'ready') return;
    setBusy(true);
    try { const r = await api.reconcileConfirm(chosen, view.value.matched.map((p) => p.statement_transaction_id)); setDone(t('fsms.reconcile.done', { count: r.reconciled })); pick(chosen); }
    catch (e) { setDone(describeError(e)); } finally { setBusy(false); }
  };
  return <section className="surface cmPanel" aria-labelledby="fsms-reconcile">
    <h2 id="fsms-reconcile">{t('fsms.reconcile')}</h2>
    <p className="muted">{t('fsms.reconcileHelp')}</p>
    {imports.kind === 'error' && <LoadError message={imports.message} retry={reload} />}
    {imports.kind === 'ready' && (imports.value.items.length === 0 ? <p className="muted">{t('fsms.reconcile.noImports')}</p>
      : <label className="cmInline"><span>{t('fsms.reconcile.import')}</span><select value={chosen} onChange={(e) => pick(e.target.value)}>
        <option value="">{t('fsms.pick')}</option>{imports.value.items.map((i: FinanceImport) => <option key={i.id} value={i.id}>{i.filename} ({when(i.created_at)})</option>)}</select></label>)}
    {view?.kind === 'loading' && <Notice busy>{t('fsms.loading')}</Notice>}
    {view?.kind === 'error' && <LoadError message={view.message} retry={() => pick(chosen)} />}
    {view?.kind === 'ready' && <>
      <p>{t('fsms.reconcile.summary', { matched: view.value.matched.length, statement: view.value.statement_only, sms: view.value.sms_only.length })}</p>
      {view.value.matched.length > 0 && <div className="cmTableWrap" tabIndex={0}><table className="cmTable">
        <thead><tr><th scope="col">{t('fsms.field.date')}</th><th scope="col">{t('fsms.reconcile.statementRow')}</th><th scope="col" className="num">{t('fsms.field.amount')}</th></tr></thead>
        <tbody>{view.value.matched.map((p) => <tr key={p.statement_transaction_id}><td dir="ltr">{p.posted_on}</td><td dir="auto">{p.description}</td>
          <td className="num"><Money value={p.amount} currency={p.currency} signed /></td></tr>)}</tbody></table></div>}
      <p className="muted finHelp">{view.value.note}</p>
      {view.value.matched.length > 0 && <div className="actions"><button type="button" disabled={busy} onClick={() => void confirm()}>{t('fsms.reconcile.confirm', { count: view.value.matched.length })}</button></div>}
    </>}
    {done && <Notice>{done}</Notice>}
  </section>;
}

// ---- Ledger ----------------------------------------------------------------------------------

export function LedgerPanel() {
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [accountId, setAccountId] = useState('');
  const [currency, setCurrency] = useState('');
  const [category, setCategory] = useState('');
  const [kind, setKind] = useState('');
  const [board, setBoard] = useState<Load<FinanceLedgerDashboard>>({ kind: 'loading' });
  const [accounts] = useLoad(listAccounts);
  const load = useCallback(() => {
    setBoard((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    api.ledgerDashboard({ month, account_id: accountId, currency, category, kind }).then((value) => setBoard({ kind: 'ready', value }), (e) => setBoard(failed(e)));
  }, [month, accountId, currency, category, kind]);
  useEffect(() => { load(); }, [load]);
  const names = useMemo(() => (accounts.kind === 'ready' ? Object.fromEntries(accounts.value.map((a) => [a.id, a.name])) : {}), [accounts]);
  if (board.kind === 'denied') return <Denied />;
  if (board.kind === 'error') return <LoadError message={t('fledger.error', { message: board.message })} retry={load} />;
  if (board.kind === 'loading') return <section className="surface cmPanel"><Notice busy>{t('fsms.loading')}</Notice></section>;
  const b = board.value;
  const figures: [string, string, string][] = [
    ['income', t('fledger.income'), formatAmounts(b.income)], ['expenses', t('fledger.expenses'), formatAmounts(b.expenses)],
    ['surplus', t('fledger.surplus'), formatAmounts(b.surplus)], ['assets', t('fledger.assets'), formatAmounts(b.assets)],
    ['liabilities', t('fledger.liabilities'), formatAmounts(b.liabilities)], ['worth', t('fledger.netWorth'), formatAmounts(b.net_worth)],
  ];
  return <div className="finSms">
    <section className="surface cmPanel" aria-labelledby="fledger-title">
      <div className="sectionHeader"><h2 id="fledger-title">{t('fledger.title')}</h2>
        <span className="statusBadge neutral">{t(`fsms.ledgerKind.${b.ledger_kind}` as MessageKey)}</span></div>
      <p className="muted">{t('fledger.intro')}</p>
      <div className="cmFilters finSmsFilters" role="group" aria-label={t('fledger.filters')}>
        <div className="finMonth"><button type="button" className="secondary" onClick={() => setMonth(shiftMonth(month, -1))} aria-label={t('finance.month.previous')}>‹</button>
          <strong aria-live="polite">{monthLabel(month)}</strong>
          <button type="button" className="secondary" onClick={() => setMonth(shiftMonth(month, 1))} aria-label={t('finance.month.next')}>›</button></div>
        <label className="cmInline"><span>{t('fledger.account')}</span><select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
          <option value="">{t('fledger.all')}</option>{Object.entries(names).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
        <label className="cmInline"><span>{t('fledger.currency')}</span><select value={currency} onChange={(e) => setCurrency(e.target.value)}>
          <option value="">{t('fledger.all')}</option>{[...new Set([...b.currencies, currency].filter(Boolean))].map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
        <label className="cmInline"><span>{t('fledger.category')}</span><select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">{t('fledger.all')}</option>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
        <label className="cmInline"><span>{t('fledger.kind')}</span><select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">{t('fledger.all')}</option>{SMS_KINDS.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}<option value="statement">{t('fledger.kind.statement')}</option></select></label>
      </div>
      <dl className="finSmsFigures">{figures.map(([id, label, value]) => <div key={id}><dt>{label}</dt><dd dir="ltr">{value === 'nothing' ? '-' : value || '-'}</dd></div>)}</dl>
      <h3 id="fledger-sync">{t('fledger.sync')}</h3>
      <dl className="finSmsFacts">
        <div><dt>{t('fsms.title')}</dt><dd>{b.sync.enabled ? t('fsms.on') : t('fsms.off')}</dd></div>
        <div><dt>{t('fsms.devicesActive')}</dt><dd>{b.sync.devices_active}</dd></div>
        <div><dt>{t('fsms.lastReceived')}</dt><dd>{when(b.sync.last_received_at)}</dd></div>
        <div><dt>{t('fsms.lastError')}</dt><dd>{b.sync.last_error ? reasonLabel(b.sync.last_error) : '-'}</dd></div>
        <div><dt>{t('fsms.section.review')}</dt><dd>{b.sync.review_count}</dd></div>
      </dl>
      {b.in_transit.length > 0 && <p className="notice">{t('fledger.inTransitText', { amount: formatAmounts(b.in_transit) })}</p>}
      {b.entry_count === 0 && <Empty icon={<MessageSquareText aria-hidden="true" />} title={t('fledger.empty')} text={t('fledger.emptyText')} />}
    </section>
    <div className="finSmsGrid">
      <AccountsCard title={t('fledger.balances')} rows={b.accounts} empty={t('fledger.none')} />
      <AccountsCard title={t('fledger.cards')} rows={b.cards} empty={t('fledger.none')} owed />
      <AccountsCard title={t('fledger.loans')} rows={b.loans} empty={t('fledger.none')} owed />
    </div>
    <div className="finSmsGrid">
      <Table title={t('fledger.byCategory')} head={[t('fledger.category'), t('fledger.count')]} empty={t('fledger.none')}
        rows={b.categories.map((c) => [(CATEGORIES as string[]).includes(c.category) ? categoryLabel(c.category as FinanceCategory) : c.category, String(c.count), c.amount, c.currency])} />
      <Table title={t('fledger.byMerchant')} head={[t('fledger.merchant'), t('fledger.count')]} empty={t('fledger.none')}
        rows={b.merchants.map((m) => [m.merchant, String(m.count), m.amount, m.currency])} />
    </div>
    <section className="surface cmPanel" aria-labelledby="fledger-trend"><h2 id="fledger-trend">{t('fledger.trend')}</h2>
      {b.trend.length === 0 ? <p className="muted">{t('fledger.none')}</p> : <div className="cmTableWrap" tabIndex={0}><table className="cmTable">
        <thead><tr><th scope="col">{t('fledger.month')}</th><th scope="col">{t('fledger.currency')}</th><th scope="col" className="num">{t('fledger.income')}</th><th scope="col" className="num">{t('fledger.expenses')}</th><th scope="col" className="num">{t('fledger.net')}</th></tr></thead>
        <tbody>{[...b.trend, ...b.year].map((row) => <tr key={`${row.month}-${row.currency}`}><td>{row.month.length === 4 ? t('fledger.yearTotal', { year: row.month }) : monthLabel(row.month)}</td><td>{row.currency}</td>
          <td className="num"><Money value={row.income} currency={row.currency} /></td><td className="num"><Money value={row.expenses} currency={row.currency} /></td>
          <td className="num"><Money value={row.net} currency={row.currency} signed /></td></tr>)}</tbody></table></div>}
    </section>
    <div className="finSmsGrid">
      <section className="surface cmPanel" aria-labelledby="fledger-bills"><h2 id="fledger-bills">{t('fledger.bills')}</h2>
        {b.bills_due.length === 0 ? <p className="muted">{t('fledger.none')}</p> : <ul className="cmList">{b.bills_due.map((bill) => <li key={`${bill.id}-${bill.due_on}`}>
          <div><strong dir="auto">{bill.name}</strong><small dir="ltr">{bill.due_on}</small></div><Money value={bill.amount} currency={bill.currency} /></li>)}</ul>}</section>
      <section className="surface cmPanel" aria-labelledby="fledger-debts"><h2 id="fledger-debts">{t('fledger.debts')}</h2>
        {b.debts.length === 0 ? <p className="muted">{t('fledger.none')}</p> : <ul className="cmList">{b.debts.map((d) => <li key={d.id}>
          <div><strong dir="auto">{d.name}</strong><small>{d.months_to_clear === null ? t('fledger.debt.never') : t('fledger.debt.months', { count: d.months_to_clear })}</small></div>
          <Money value={d.balance} currency={d.currency} /></li>)}</ul>}</section>
      <section className="surface cmPanel" aria-labelledby="fledger-goals"><h2 id="fledger-goals">{t('fledger.goals')}</h2>
        {b.goals.length === 0 ? <p className="muted">{t('fledger.none')}</p> : <ul className="cmList">{b.goals.map((g) => <li key={g.id}>
          <div><strong dir="auto">{g.name}</strong><small>{t('fledger.goal.percent', { percent: g.percent })}</small></div><Money value={g.saved_amount} currency={g.currency} /></li>)}</ul>}</section>
    </div>
    <Journal names={names} />
  </div>;
}

function AccountsCard({ title, rows, empty, owed }: { title: string; rows: FinanceLedgerDashboard['accounts']; empty: string; owed?: boolean }) {
  const titleId = useId();
  return <section className="surface cmPanel" aria-labelledby={titleId}><h2 id={titleId}>{title}</h2>
    {rows.length === 0 ? <p className="muted">{empty}</p> : <ul className="cmList">{rows.map((a) => <li key={a.id}>
      <div><strong dir="auto">{a.name}</strong>
        {a.utilization_percent !== null && <small>{t('fledger.utilization', { percent: a.utilization_percent })}</small>}
        {a.observed_balance !== null && <small>{t('fledger.observed', { amount: `${a.currency} ${a.observed_balance}` })}{a.difference && a.difference !== '0.00' ? ` · ${t('fledger.difference', { amount: a.difference })}` : ''}</small>}</div>
      <Money value={owed && a.owed !== null ? a.owed : a.balance} currency={a.currency} signed={!owed} /></li>)}</ul>}
  </section>;
}

function Table({ title, head, rows, empty }: { title: string; head: [string, string]; rows: [string, string, string, string][]; empty: string }) {
  const titleId = useId();
  return <section className="surface cmPanel" aria-labelledby={titleId}><h2 id={titleId}>{title}</h2>
    {rows.length === 0 ? <p className="muted">{empty}</p> : <div className="cmTableWrap" tabIndex={0}><table className="cmTable">
      <thead><tr><th scope="col">{head[0]}</th><th scope="col" className="num">{head[1]}</th><th scope="col" className="num">{t('fsms.field.amount')}</th></tr></thead>
      <tbody>{rows.map(([name, count, amount, cur]) => <tr key={`${name}-${cur}`}><td dir="auto">{name}</td><td className="num">{count}</td><td className="num"><Money value={amount} currency={cur} /></td></tr>)}</tbody>
    </table></div>}
  </section>;
}

function Journal({ names }: { names: Record<string, string> }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Load<FinanceJournalPage>>({ kind: 'loading' });
  const load = useCallback(() => { api.ledgerEntries(PAGE, offset).then((value) => setPage({ kind: 'ready', value }), (e) => setPage(failed(e))); }, [offset]);
  useEffect(() => { load(); }, [load]);
  return <section className="surface cmPanel" aria-labelledby="fledger-journal"><h2 id="fledger-journal">{t('fledger.journal')}</h2>
    <p className="muted">{t('fledger.journalHelp')}</p>
    {page.kind === 'loading' && <Notice busy>{t('fsms.loading')}</Notice>}
    {page.kind === 'error' && <LoadError message={page.message} retry={load} />}
    {page.kind === 'ready' && (page.value.items.length === 0 ? <p className="muted">{t('fledger.none')}</p> : <>
      <ul className="cmList">{page.value.items.map((e) => <li key={e.id} className="finSmsEntry">
        <div><strong dir="auto">{e.description}</strong>
          <small><bdi dir="ltr">{e.posted_on}</bdi> · {kindLabel(e.kind)} · {t(`fledger.source.${e.source}` as MessageKey)}{e.status === 'reversed' ? ` · ${t('fledger.reversed')}` : ''}{e.reconciled_at ? ` · ${t('fledger.reconciled')}` : ''}</small>
          {e.original_amount && <small dir="ltr">{e.original_currency} {e.original_amount} @ {e.fx_rate}</small>}
          <ul className="finSmsPostings">{e.postings.map((p, i) => <li key={i}><span>{ledgerAccountLabel(p.ledger_account, names)}</span><span>{p.amount.startsWith('-') ? t('fledger.credit') : t('fledger.debit')} <Money value={p.amount.replace(/^-/, '')} currency={p.currency} /></span></li>)}</ul></div>
      </li>)}</ul>
      <div className="cmPager"><button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>{t('fsms.prev')}</button>
        <button type="button" className="secondary" disabled={page.value.total <= offset + PAGE} onClick={() => setOffset(offset + PAGE)}>{t('fsms.next')}</button></div>
    </>)}
  </section>;
}
