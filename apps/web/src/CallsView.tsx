import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { ClipboardPaste, Delete, Lock, Mic, MicOff, Phone, PhoneCall, PhoneOff, Plus, UserPlus } from 'lucide-react';
import { Modal } from './Dialog';
import { CallLink, ErrorNotice, LogCallDialog, Notice, type DialTarget } from './CallsParts';
import { ContactForm, ContactsTab, RecentsTab } from './CallsLists';
import { AiCallsTab, ProviderTab } from './CallsAdmin';
import { PageHeader, PageSummary } from './PageParts';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatNumber, getLocale, t, type MessageKey } from './lib/i18n';
import {
  backspace, callsHash, costText, durationText, isDialable, longPressZero, parseCallsHash, pasteNumber, pressKey, TABS,
  type ApiCall, type ApiCallsSetup, type ApiContact, type CallsTab,
} from './lib/calls';
import * as callsApi from './lib/callsApi';
import type { LiveCall, LiveState } from './lib/callsDevice';
import { figure, type Load } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import './admin.css';
import './meetings.css';
import './calls.css';

const fail = <T,>(e: unknown): Load<T> => isPermissionDenied(e) ? { state: 'denied', message: describeError(e) } : { state: 'error', message: describeError(e) };

/**
 * Phone calls (docs/calls.md): Recents (the call log), Contacts, a Dial pad, AI calls and the voice
 * Provider. Free by default: "Call on your phone" opens the phone's own dialer (a tel: link) and the
 * log dialog opens at once, so the call is logged with its outcome, duration and notes, then the
 * workspace model can summarise it into a follow-up task. With a provider, calls go through ANUM.
 */
export function CallsView({ onOpenTasks, onOpenApprovals, onOpenSettings }: { onOpenTasks?(): void; onOpenApprovals?(): void; onOpenSettings?(): void }) {
  const [setup, setSetup] = useState<Load<ApiCallsSetup>>({ state: 'loading' });
  const [route, setRoute] = useState(() => parseCallsHash(location.hash));
  const [counts, setCounts] = useState<Load<{ calls: number; contacts: number }>>({ state: 'loading' });
  const [contacts, setContacts] = useState<ApiContact[]>([]);
  const [note, setNote] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null);
  const [logging, setLogging] = useState<{ target: DialTarget | null; startedAt: number | null } | null>(null);
  const [live, setLive] = useState<DialTarget | null>(null);
  const [adding, setAdding] = useState(false);
  const [tick, setTick] = useState(0);
  const changed = useCallback(() => setTick((n) => n + 1), []);

  const loadSetup = useCallback(() => { callsApi.getSetup().then((value) => setSetup({ state: 'ready', value }), (e) => setSetup(fail(e))); }, []);
  useEffect(() => { loadSetup(); }, [loadSetup]);
  useEffect(() => {
    Promise.all([callsApi.listCalls({ limit: 1 }), callsApi.listContacts({ limit: 100 })]).then(
      ([calls, people]) => { setCounts({ state: 'ready', value: { calls: calls.total, contacts: people.total } }); setContacts(people.items); },
      (e) => setCounts(fail(e)),
    );
  }, [tick]);
  useEffect(() => {
    const follow = () => setRoute(parseCallsHash(location.hash));
    window.addEventListener('popstate', follow);
    window.addEventListener('hashchange', follow);
    return () => { window.removeEventListener('popstate', follow); window.removeEventListener('hashchange', follow); };
  }, []);

  const go = (tab: CallsTab, open: { call?: string | null; contact?: string | null } = {}) => {
    const next = callsHash(tab, open);
    if (location.hash !== next) history.pushState(null, '', next);
    setRoute(parseCallsHash(next));
  };
  const dial = (target: DialTarget) => { setNote(null); setLogging({ target, startedAt: Date.now() }); };

  const info = setup.state === 'ready' ? setup.value : null;
  const caps = info?.capabilities;
  const canLog = info?.can_log ?? false;
  const tabs = TABS;
  const countValue = counts.state === 'ready' ? counts.value : null;
  const figures = [
    figure('calls', t('calls.fig.calls'), counts, () => ({ value: formatNumber(countValue!.calls), hint: t('calls.fig.callsHint') })),
    figure('contacts', t('calls.fig.contacts'), counts, () => ({ value: formatNumber(countValue!.contacts), hint: t('calls.fig.contactsHint') })),
    figure('provider', t('calls.fig.provider'), setup, () => caps?.browser_calls
      ? { value: t('calls.fig.on'), hint: t('calls.fig.rateHint', { cost: costText(60, caps.cost_per_minute_cents) }), tone: 'ok' }
      : { value: t('calls.fig.off'), hint: t('calls.fig.freeHint') }),
  ];

  if (setup.state === 'denied') return <>
    <PageHeader title={viewLabel('calls')} subtitle={t('calls.subtitle')} />
    <section className="surface emptyState" aria-labelledby="calls-denied"><Lock aria-hidden="true" /><h3 id="calls-denied">{t('calls.deniedTitle')}</h3><p>{setup.message}</p></section>
  </>;

  return <>
    <PageHeader title={viewLabel('calls')} subtitle={t('calls.subtitle')} actions={canLog ? <>
      <button type="button" className="secondary" onClick={() => setAdding(true)}><UserPlus aria-hidden="true" />{t('calls.action.addContact')}</button>
      <button type="button" onClick={() => setLogging({ target: null, startedAt: null })}><Plus aria-hidden="true" />{t('calls.action.log')}</button>
    </> : undefined} />
    {setup.state === 'error' && <ErrorNotice message={t('calls.setupFailed', { message: setup.message })} onRetry={loadSetup} />}
    {note && <Notice>{note}</Notice>}
    {problem && <Notice error>{problem}</Notice>}
    <PageSummary label={t('calls.summary')} figures={figures} onRetry={changed} />
    <div className="segmented callsTabs" role="tablist" aria-label={t('calls.tabs')}>
      {tabs.map((tab) => <button key={tab} type="button" role="tab" id={`calls-tab-${tab}`} aria-selected={route.tab === tab} aria-controls="calls-panel" onClick={() => go(tab)}>{t(`calls.tab.${tab}` as MessageKey)}</button>)}
    </div>
    <div id="calls-panel" role="tabpanel" aria-labelledby={`calls-tab-${route.tab}`} className="callsPanel">
      {route.tab === 'recents' && <RecentsTab refresh={tick} setup={info} selected={route.call} onSelect={(id) => go('recents', { call: id })} onDial={dial} onCallInAnum={setLive} onChanged={changed} onOpenTasks={onOpenTasks} onOpenSettings={onOpenSettings} onOpenDial={() => go('dial')} />}
      {route.tab === 'contacts' && <ContactsTab refresh={tick} setup={info} selected={route.contact} onSelect={(id) => go('contacts', { contact: id })} onDial={dial} onCallInAnum={setLive} onChanged={changed} onAdd={() => setAdding(true)} onOpenCall={(id) => go('recents', { call: id })} onAskAi={() => go('ai')} />}
      {route.tab === 'dial' && <DialTab setup={info} contacts={contacts} onDial={dial} onCallInAnum={setLive} />}
      {route.tab === 'ai' && <AiCallsTab refresh={tick} setup={info} contacts={contacts} onOpenApprovals={onOpenApprovals} onOpenCall={(id) => go('recents', { call: id })} onOpenContacts={() => go('contacts')} />}
      {route.tab === 'provider' && <ProviderTab setup={info} onChanged={() => { loadSetup(); changed(); }} />}
    </div>
    {logging && <LogCallDialog target={logging.target} startedAt={logging.startedAt} contacts={contacts} onClose={() => setLogging(null)} onLogged={(call, summarize) => {
      setLogging(null); setNote(t('calls.log.saved')); changed(); go('recents', { call: call.id });
      if (summarize) callsApi.summarize(call.id).then(() => changed(), (e) => setProblem(describeError(e)));
    }} />}
    {adding && <Modal title={t('calls.contact.newTitle')} className="formDialog" onClose={() => setAdding(false)}>
      <ContactForm onCancel={() => setAdding(false)} onSaved={(contact) => { setAdding(false); setNote(t('calls.contact.added', { name: contact.name })); changed(); go('contacts', { contact: contact.id }); }} />
    </Modal>}
    {live && caps && <BrowserCallDialog target={live} setup={info!} onClose={() => setLive(null)} onEnded={(call) => { setLive(null); changed(); go('recents', { call: call.id }); setNote(t('calls.live.ended')); }} />}
  </>;
}

// ---------------------------------------------------------------------------------------- dialpad

const KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#'] as const;
const LONG_PRESS_MS = 550;

function DialTab({ setup, contacts, onDial, onCallInAnum }: { setup: ApiCallsSetup | null; contacts: ApiContact[]; onDial(target: DialTarget): void; onCallInAnum(target: DialTarget): void }) {
  const ids = useId();
  const [number, setNumber] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const timer = useRef<number | null>(null), longPressed = useRef(false);
  const known = contacts.find((item) => item.phone === number || item.phone_normalized === number) ?? null;
  const target: DialTarget = { phone: number, contact: known };

  const down = (key: string) => {
    longPressed.current = false;
    if (key !== '0') return;
    timer.current = window.setTimeout(() => { longPressed.current = true; setNumber((value) => longPressZero(value)); }, LONG_PRESS_MS);
  };
  const up = (key: string) => {
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null; }
    if (!longPressed.current) setNumber((value) => pressKey(value, key));
    longPressed.current = false;
  };
  async function paste() {
    setProblem(null);
    try { const text = await navigator.clipboard.readText(); setNumber((value) => pasteNumber(value, text)); }
    catch { setProblem(t('calls.dial.pasteFailed')); }
  }

  return <section className="pane callsDial" aria-labelledby={`${ids}-title`}>
    <div className="paneHead"><h2 id={`${ids}-title`}>{t('calls.dial.title')}</h2></div>
    <div className="paneBody">
      <div className="callsDialNumber">
        <label className="visuallyHidden" htmlFor={`${ids}-number`}>{t('calls.dial.number')}</label>
        <input id={`${ids}-number`} value={number} dir="ltr" inputMode="tel" autoComplete="off" maxLength={24} placeholder="+971"
          onChange={(event) => setNumber(pasteNumber('', event.target.value))}
          onKeyDown={(event) => { if (event.key === 'Enter' && isDialable(number)) { event.preventDefault(); onDial(target); window.location.href = `tel:${number.replace(/#/g, '%23')}`; } }} />
        <button type="button" className="iconButton" aria-label={t('calls.dial.paste')} title={t('calls.dial.paste')} onClick={() => void paste()}><ClipboardPaste aria-hidden="true" /></button>
        <button type="button" className="iconButton" aria-label={t('calls.dial.delete')} title={t('calls.dial.delete')} disabled={!number} onClick={() => setNumber(backspace)}><Delete aria-hidden="true" className="flipRtl" /></button>
      </div>
      {known && <p className="muted callsDialKnown">{t('calls.dial.known', { name: known.name })}</p>}
      <div className="callsKeypad" role="group" aria-label={t('calls.dial.keypad')} dir="ltr">
        {KEYS.map((key) => <button key={key} type="button" className="callsKey" aria-label={key === '0' ? `0 +` : key}
          onPointerDown={() => down(key)} onPointerUp={() => up(key)} onPointerLeave={() => { if (timer.current) { window.clearTimeout(timer.current); timer.current = null; } }}
          onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setNumber((value) => pressKey(value, key)); } }}>
          <span>{key}</span>{key === '0' && <small aria-hidden="true">+</small>}
        </button>)}
      </div>
      <p className="muted">{t('calls.dial.plusHint')} {t('calls.dial.desktopHint')}</p>
      {problem && <Notice error>{problem}</Notice>}
      <div className="actions callsDialActions">
        {isDialable(number)
          ? <CallLink target={target} onDial={onDial} className="buttonLink primaryLink" />
          : <button type="button" disabled aria-describedby={`${ids}-invalid`}><Phone aria-hidden="true" />{t('calls.callOnPhone')}</button>}
        {setup?.capabilities.browser_calls && setup.can_log && <button type="button" className="secondary" disabled={!isDialable(number)} onClick={() => onCallInAnum(target)}><PhoneCall aria-hidden="true" />{t('calls.callInAnum')}</button>}
      </div>
      {!isDialable(number) && <p id={`${ids}-invalid`} className="muted">{t('calls.dial.invalid')}</p>}
      {setup && !setup.capabilities.browser_calls && setup.capabilities.problem && <p className="muted">{t('calls.live.off', { reason: setup.capabilities.problem })}</p>}
    </div>
  </section>;
}

// ------------------------------------------------------------------------------- browser (provider) call

function BrowserCallDialog({ target, setup, onClose, onEnded }: { target: DialTarget; setup: ApiCallsSetup; onClose(): void; onEnded(call: ApiCall): void }) {
  const ids = useId();
  const caps = setup.capabilities;
  const [record, setRecord] = useState(false), [consent, setConsent] = useState(false);
  const [state, setState] = useState<LiveState | 'idle'>('idle');
  const [detail, setDetail] = useState<string | null>(null);
  const [muted, setMuted] = useState(false), [seconds, setSeconds] = useState(0);
  const call = useRef<LiveCall | null>(null), entry = useRef<ApiCall | null>(null), opened = useRef<number | null>(null);
  const language = getLocale() === 'ar' ? 'ar' : 'en';

  useEffect(() => {
    if (state !== 'open') return undefined;
    opened.current = opened.current ?? Date.now();
    const timer = window.setInterval(() => setSeconds(Math.round((Date.now() - (opened.current ?? Date.now())) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [state]);
  useEffect(() => () => call.current?.hangUp(), []);

  async function start() {
    setDetail(null);
    try {
      const prepared = await callsApi.prepareBrowserCall({ ...(target.contact ? { contact_id: target.contact.id } : { phone: target.phone }), record, recording_consent: record && consent, language });
      entry.current = prepared.call;
      const { startBrowserCall } = await import('./lib/callsDevice');
      call.current = await startBrowserCall(prepared.token, prepared.call.id, {
        onState(next, message) {
          setState(next);
          if (next === 'failed') setDetail(message ?? '');
          if (next === 'ended' && entry.current) onEnded(entry.current);
        },
      });
    } catch (e) { setState('failed'); setDetail(describeError(e)); }
  }

  const working = state === 'connecting' || state === 'ringing' || state === 'open';
  const status = state === 'connecting' ? t('calls.live.connecting') : state === 'ringing' ? t('calls.live.ringing') : state === 'open' ? t('calls.live.open', { time: durationText(seconds) }) : null;
  return <Modal title={t('calls.live.title')} className="formDialog callsLiveDialog" onClose={() => { if (!working) onClose(); }}>
    <p className="callsLiveWho"><strong dir="auto">{target.contact?.name ?? target.phone}</strong>{target.contact && <bdi dir="ltr">{target.phone}</bdi>}</p>
    <p className="muted">{t('calls.live.costNote', { cost: costText(60, caps.cost_per_minute_cents) })}</p>
    {state === 'idle' && <>
      {caps.recording && <label className="checkRow"><input type="checkbox" checked={record} onChange={(event) => { setRecord(event.target.checked); if (!event.target.checked) setConsent(false); }} />{t('calls.live.record')}</label>}
      {record && caps.recording_notice && <div className="callsConsent">
        <label className="checkRow" htmlFor={`${ids}-consent`}><input id={`${ids}-consent`} type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />{t('calls.live.recordConsent')}</label>
        <blockquote dir="auto">{caps.recording_notice[language]}</blockquote>
      </div>}
      <div className="actions">
        <button type="button" disabled={record && !consent} onClick={() => void start()} data-autofocus><PhoneCall aria-hidden="true" />{t('calls.live.start')}</button>
        <button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button>
      </div>
    </>}
    {status && <p className="notice" role="status" aria-live="polite"><Phone aria-hidden="true" /><span>{status}</span></p>}
    {working && <div className="actions">
      <button type="button" className="secondary" aria-pressed={muted} onClick={() => { const next = !muted; setMuted(next); call.current?.setMuted(next); }}>{muted ? <MicOff aria-hidden="true" /> : <Mic aria-hidden="true" />}{muted ? t('calls.live.unmute') : t('calls.live.mute')}</button>
      <button type="button" className="danger" onClick={() => call.current?.hangUp()}><PhoneOff aria-hidden="true" />{t('calls.live.hangUp')}</button>
    </div>}
    {state === 'failed' && <>
      <Notice error>{t('calls.live.failed', { message: detail || t('common.operationFailed') })}</Notice>
      <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('dialog.close')}</button></div>
    </>}
  </Modal>;
}
