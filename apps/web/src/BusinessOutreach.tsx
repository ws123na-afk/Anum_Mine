// Business, Outreach (docs/business.md#outreach): the outbox. Approved drafts are queued here (the
// deterministic gate runs, then an approval bound to the exact text is asked), owners decide them,
// follow-ups are scheduled, replies stop follow-ups, and the numbers carry their denominators.
// Sending is off until an owner turns it on; every message still waits for an approval.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { CheckCircle2, Fingerprint, PauseCircle, PlayCircle, ShieldAlert } from 'lucide-react';
import type { BusinessSetup, OutboxItem, OutboxStatus, OutreachOverview, PipelineSettings, ReadyDraft } from '@anum/contracts';
import * as api from './lib/api';
import { shortHash } from './lib/approvals';
import { describeError } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { channelLabel } from './lib/business';
import { OUTBOX_STATUSES, outboxStatusLabel, outboxTone, rateText } from './lib/businessPipeline';
import * as p from './lib/businessPipelineApi';
import { Badge, Empty, Failed, Note, Pager, ReasonDialog, day, failure, when } from './BusinessParts';

type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'error'; message: string };

export function OutreachTab({ setup, onOpenLead }: { setup: BusinessSetup; onOpenLead: (leadId: string) => void }) {
  const [overview, setOverview] = useState<Load<OutreachOverview>>({ state: 'loading' }), [tick, setTick] = useState(0);
  const load = useCallback(async () => {
    try { setOverview({ state: 'ready', value: await p.getOutreach(30) }); } catch (error) { setOverview({ state: 'error', message: describeError(error) }); }
  }, []);
  useEffect(() => { void load(); }, [load, tick]);
  const changed = () => setTick((value) => value + 1);
  if (overview.state === 'loading') return <Note>{t('biz.loading')}</Note>;
  if (overview.state === 'error') return <Failed text={overview.message} onRetry={() => void load()} />;
  const value = overview.value;
  return <div className="bzSection">
    {value.sending_enabled ? <Note>{t('biz.outreach.sendingOn')}</Note> : <Note>{t('biz.outreach.sendingOffNote')}</Note>}
    {value.paused && <Note error>{t('biz.outreach.paused', { reason: value.paused_reason || '-' })}</Note>}
    {value.metrics.opt_out_alarm && <Note error>{t('biz.outreach.optOutAlarm')}</Note>}
    <section className="surface bzCard" aria-labelledby="bz-o-numbers"><h2 id="bz-o-numbers">{t('biz.outreach.numbers', { days: value.metrics.days })}</h2>
      <dl className="bzFacts">
        <div><dt>{t('biz.outreach.sent')}</dt><dd>{value.metrics.sent_total}</dd></div>
        <div><dt>{t('biz.outreach.replies')}</dt><dd>{rateText(value.metrics.replies)}</dd></div>
        <div><dt>{t('biz.outreach.optOuts')}</dt><dd>{rateText(value.metrics.opt_outs)}</dd></div>
        <div><dt>{t('biz.outreach.bounces')}</dt><dd>{rateText(value.metrics.bounces)}</dd></div>
        <div><dt>{t('biz.outreach.today')}</dt><dd>{(['email', 'whatsapp'] as const).map((channel) => t('biz.outreach.quotaValue', { channel: channelLabel(channel), sent: value.sent_today[channel] ?? 0, quota: value.daily_quota[channel] ?? 0 })).join(' · ')}</dd></div>
        <div><dt>{t('biz.outreach.waiting')}</dt><dd>{t('biz.outreach.waitingValue', { approval: value.waiting.pending_approval ?? 0, held: value.waiting.held ?? 0, scheduled: value.waiting.scheduled ?? 0 })}</dd></div>
        <div><dt>{t('biz.outreach.channels')}</dt><dd>{value.channels_connected.length ? value.channels_connected.map((item) => channelLabel(item as never)).join(', ') : t('biz.outreach.noChannels')}</dd></div>
      </dl>
    </section>
    <ControlsCard setup={setup} overview={value} onChanged={changed} />
    {setup.permissions.write && <ReadyCard onChanged={changed} onOpenLead={onOpenLead} />}
    <OutboxCard refresh={tick} setup={setup} onChanged={changed} onOpenLead={onOpenLead} />
  </div>;
}

function ControlsCard({ setup, overview, onChanged }: { setup: BusinessSetup; overview: OutreachOverview; onChanged: () => void }) {
  const [settings, setSettings] = useState<PipelineSettings | null>(null), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [form, setForm] = useState({ email: '', whatsapp: '', bounce: '', followMax: '', followDays: '' }), [busy, setBusy] = useState(false), [pausing, setPausing] = useState(false);
  const base = useId();
  useEffect(() => {
    p.getPipelineSettings().then((value) => {
      setSettings(value);
      setForm({ email: String(value.daily_quota.email ?? 0), whatsapp: String(value.daily_quota.whatsapp ?? 0), bounce: String(value.max_bounce_rate_pct), followMax: String(value.follow_up_max), followDays: String(value.follow_up_min_days) });
    }).catch((error: unknown) => setMessage({ text: failure(error, t('biz.outreach.settings')), error: true }));
  }, [overview]);
  const manage = setup.permissions.manage;
  async function save(event: FormEvent, sending?: boolean) {
    event.preventDefault();
    const numbers = [form.email, form.whatsapp, form.followMax, form.followDays].map(Number), bounce = Number(form.bounce);
    if (numbers.some((item) => !Number.isInteger(item) || item < 0) || numbers[0] > 500 || numbers[1] > 500 || numbers[2] > 5 || numbers[3] < 1 || numbers[3] > 60 || !(bounce >= 0 && bounce <= 50)) {
      setMessage({ text: t('biz.outreach.invalid'), error: true }); return;
    }
    setBusy(true); setMessage(null);
    try {
      await p.savePipelineSettings({ daily_quota: { email: numbers[0], whatsapp: numbers[1] }, follow_up_max: numbers[2], follow_up_min_days: numbers[3], max_bounce_rate_pct: bounce,
        ...(sending === undefined ? {} : { sending_enabled: sending }) });
      setMessage({ text: t('biz.settings.saved') }); onChanged();
    } catch (error) { setMessage({ text: failure(error, t('biz.outreach.settings')), error: true }); } finally { setBusy(false); }
  }
  async function resume() {
    try { await p.setPause(false); onChanged(); } catch (error) { setMessage({ text: failure(error, t('biz.outreach.resume')), error: true }); }
  }
  const field = (name: keyof typeof form, label: string) => <label className="field" htmlFor={`${base}-${name}`}><span>{label}</span>
    <input id={`${base}-${name}`} inputMode="decimal" value={form[name]} disabled={!manage} onChange={(event) => setForm((current) => ({ ...current, [name]: event.target.value }))} /></label>;
  return <section className="surface bzCard" aria-labelledby="bz-o-settings"><h2 id="bz-o-settings">{t('biz.outreach.settings')}</h2>
    <form onSubmit={(event) => void save(event)} aria-label={t('biz.outreach.settings')}>
      <div className="bzGrid2">{field('email', t('biz.outreach.quotaEmail'))}{field('whatsapp', t('biz.outreach.quotaWhatsapp'))}{field('bounce', t('biz.outreach.bounceRate'))}
        {field('followMax', t('biz.outreach.followMax'))}{field('followDays', t('biz.outreach.followDays'))}</div>
      {settings && <p className="muted bzHint">{settings.sending_enabled ? t('biz.outreach.sendingIsOn') : t('biz.outreach.sendingIsOff')}</p>}
      {manage ? <div className="actions">
        <button type="submit" disabled={busy}>{t('biz.outreach.save')}</button>
        {settings && <button type="button" className="secondary" disabled={busy} onClick={(event) => void save(event as unknown as FormEvent, !settings.sending_enabled)}>
          {settings.sending_enabled ? t('biz.outreach.turnOff') : t('biz.outreach.turnOn')}</button>}
      </div> : <p className="muted">{t('biz.outreach.ownersOnly')}</p>}
    </form>
    <div className="bzInline">
      {overview.paused ? (manage && <button type="button" onClick={() => void resume()}><PlayCircle aria-hidden="true" />{t('biz.outreach.resume')}</button>)
        : (setup.permissions.write && <button type="button" className="secondary" onClick={() => setPausing(true)}><PauseCircle aria-hidden="true" />{t('biz.outreach.pause')}</button>)}
    </div>
    {message && <Note error={message.error}>{message.text}</Note>}
    {pausing && <ReasonDialog title={t('biz.outreach.pause')} label={t('biz.outreach.pauseWhy')} confirm={t('biz.outreach.pause')}
      onConfirm={async (reason) => { await p.setPause(true, reason); setPausing(false); onChanged(); }} onCancel={() => setPausing(false)} />}
  </section>;
}

function ReadyCard({ onChanged, onOpenLead }: { onChanged: () => void; onOpenLead: (leadId: string) => void }) {
  const [ready, setReady] = useState<Load<ReadyDraft[]>>({ state: 'loading' }), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null), [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setReady({ state: 'ready', value: (await p.listReady()).items }); } catch (error) { setReady({ state: 'error', message: failure(error, t('biz.outreach.ready')) }); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function queue(item: ReadyDraft, followUpOf: string | null) {
    setBusy(item.draft.id); setMessage(null);
    try {
      const queued = await p.queueDraft(item.draft.id, followUpOf ? { follow_up_of: followUpOf } : {});
      setMessage({ text: queued.status === 'scheduled' ? t('biz.outreach.scheduled', { day: day(queued.send_after) }) : t('biz.outreach.queued') });
      await load(); onChanged();
    } catch (error) { setMessage({ text: failure(error, t('biz.outreach.queue')), error: true }); } finally { setBusy(null); }
  }
  return <section className="surface bzCard" aria-labelledby="bz-o-ready"><h2 id="bz-o-ready">{t('biz.outreach.ready')}</h2>
    <p className="muted bzHint">{t('biz.outreach.readyHelp')}</p>
    {ready.state === 'loading' && <Note>{t('biz.loading')}</Note>}
    {ready.state === 'error' && <Note error>{ready.message}</Note>}
    {ready.state === 'ready' && ready.value.length === 0 && <Empty title={t('biz.outreach.readyEmpty')} text={t('biz.outreach.readyEmptyText')} />}
    {ready.state === 'ready' && ready.value.length > 0 && <ul className="bzList">{ready.value.map((item) => <li key={item.draft.id}>
      <strong dir="auto">{item.company}</strong> <small className="muted">{channelLabel(item.draft.channel)} · {item.contact_name || '-'}</small>
      <br /><small dir="auto">{item.draft.subject || item.draft.body.slice(0, 120)}</small>
      <span className="bzInline">
        <button type="button" disabled={busy === item.draft.id} onClick={() => void queue(item, null)}>{t('biz.outreach.queue')}</button>
        {item.queued_for.length > 0 && <button type="button" className="secondary" disabled={busy === item.draft.id} onClick={() => void queue(item, item.queued_for[0])}>{t('biz.outreach.queueFollowUp')}</button>}
        <button type="button" className="linkButton" onClick={() => onOpenLead(item.draft.lead_id)}>{t('biz.run.openLead')}</button>
      </span></li>)}</ul>}
    {message && <Note error={message.error}>{message.text}</Note>}
  </section>;
}

function OutboxCard({ refresh, setup, onChanged, onOpenLead }: { refresh: number; setup: BusinessSetup; onChanged: () => void; onOpenLead: (leadId: string) => void }) {
  const [status, setStatus] = useState<OutboxStatus | ''>(''), [items, setItems] = useState<OutboxItem[]>([]), [total, setTotal] = useState(0);
  const [state, setState] = useState<'loading' | 'ready'>('loading'), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState<OutboxItem | null>(null), [replying, setReplying] = useState<OutboxItem | null>(null), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const filterId = useId();
  const load = useCallback(async (offset: number) => {
    setBusy(true); setError(null);
    try {
      const page = await p.listOutbox({ status, limit: 25, offset });
      setItems((current) => (offset === 0 ? page.items : [...current, ...page.items])); setTotal(page.total);
    } catch (problem) { setError(failure(problem, t('biz.outreach.outbox'))); } finally { setBusy(false); setState('ready'); }
  }, [status]);
  useEffect(() => { void load(0); }, [load, refresh]);
  async function act(run: () => Promise<unknown>, done: string) {
    setMessage(null);
    try { await run(); setMessage({ text: done }); await load(0); onChanged(); } catch (problem) { setMessage({ text: failure(problem, t('biz.outreach.outbox')), error: true }); }
  }
  async function decide(item: OutboxItem, kind: 'approve' | 'reject') {
    if (!item.approval) return;
    const mapped = api.mapApproval(item.approval as unknown as Parameters<typeof api.mapApproval>[0]);
    await act(() => (kind === 'approve' ? api.approveTask(mapped) : api.rejectTask(mapped)), kind === 'approve' ? t('biz.outreach.approved') : t('biz.outreach.rejected'));
  }
  return <section className="surface bzCard" aria-labelledby="bz-o-outbox"><h2 id="bz-o-outbox">{t('biz.outreach.outbox')}</h2>
    <label className="bzCompact bzCompactField" htmlFor={filterId}><span>{t('biz.outreach.filter')}</span>
      <select id={filterId} value={status} onChange={(event) => setStatus(event.target.value as OutboxStatus | '')}><option value="">{t('biz.outreach.all')}</option>
        {OUTBOX_STATUSES.map((item) => <option key={item} value={item}>{outboxStatusLabel(item)}</option>)}</select></label>
    {state === 'loading' && <Note>{t('biz.loading')}</Note>}
    {state === 'ready' && items.length === 0 && !error && <Empty title={t('biz.outreach.empty')} text={t('biz.outreach.emptyText')} />}
    {items.length > 0 && <ul className="bzList bzOutbox">{items.map((item) => <li key={item.id}>
      <div className="bzLeadHead"><strong dir="auto">{item.company}</strong><Badge tone={outboxTone(item.status)}>{outboxStatusLabel(item.status)}</Badge>
        <small className="muted">{channelLabel(item.channel)} · {t('biz.outreach.step', { step: item.step })} · {item.recipient}</small></div>
      <p className="bzHint" dir="auto"><strong>{item.subject}</strong>{item.subject ? ' · ' : ''}{item.body.slice(0, 220)}{item.body.length > 220 ? '...' : ''}</p>
      {item.note && <p className="muted bzHint">{item.note}</p>}
      {item.gate_issues.length > 0 && <ul className="bzBullets bzBlocked">{item.gate_issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
      {item.send_after && item.status === 'scheduled' && <p className="muted bzHint">{t('biz.outreach.dueOn', { day: day(item.send_after) })}</p>}
      {item.sent_at && <p className="muted bzHint">{t('biz.outreach.sentAt', { when: when(item.sent_at) })}</p>}
      {item.approval && <div className="bzApproval" role="group" aria-label={t('biz.outreach.approvalGroup')}>
        <p className="bzApprovalTitle"><ShieldAlert aria-hidden="true" />{t('biz.outreach.waitingApproval')}</p>
        <p className="bzHash"><Fingerprint aria-hidden="true" />{t('biz.approval.hash')} <code title={item.approval.payload_hash ?? ''}>{shortHash(item.approval.payload_hash)}</code></p>
        {setup.permissions.decide ? <div className="bzInline"><button type="button" onClick={() => void decide(item, 'approve')}><CheckCircle2 aria-hidden="true" />{t('biz.outreach.approveSend')}</button>
          <button type="button" className="secondary" onClick={() => void decide(item, 'reject')}>{t('biz.approval.reject')}</button></div> : <p className="muted">{t('biz.approval.ownerDecides')}</p>}
      </div>}
      {setup.permissions.write && <div className="bzInline">
        {(item.status === 'held' || item.status === 'rejected' || (item.status === 'scheduled' && item.due)) && <button type="button" className="secondary"
          onClick={() => void act(() => p.requestApproval(item.id), t('biz.outreach.asked'))}>{t('biz.outreach.askAgain')}</button>}
        {(item.status === 'held' || item.status === 'sent') && <button type="button" className="secondary"
          onClick={() => void act(() => p.followUpDraft(item.id), t('biz.outreach.followUpWritten'))}>{t('biz.outreach.followUp')}</button>}
        {['scheduled', 'pending_approval', 'held'].includes(item.status) && <button type="button" className="secondary" onClick={() => setCancelling(item)}>{t('biz.outreach.cancel')}</button>}
        <button type="button" className="secondary" onClick={() => setReplying(item)}>{t('biz.outreach.recordReply')}</button>
        <button type="button" className="linkButton" onClick={() => onOpenLead(item.lead_id)}>{t('biz.run.openLead')}</button>
      </div>}
    </li>)}</ul>}
    <Pager shown={items.length} total={total} busy={busy} error={error} onMore={() => void load(items.length)} />
    {message && <Note error={message.error}>{message.text}</Note>}
    {cancelling && <ReasonDialog title={t('biz.outreach.cancel')} label={t('biz.outreach.cancelWhy')} confirm={t('biz.outreach.cancel')}
      onConfirm={async (reason) => { await p.cancelItem(cancelling.id, reason); setCancelling(null); await load(0); onChanged(); }} onCancel={() => setCancelling(null)} />}
    {replying && <ReplyForm item={replying} onDone={(text) => { setReplying(null); if (text) { setMessage({ text }); void load(0); onChanged(); } }} />}
  </section>;
}

function ReplyForm({ item, onDone }: { item: OutboxItem; onDone: (message: string | null) => void }) {
  const [kind, setKind] = useState<'reply' | 'bounce'>('reply'), [text, setText] = useState(''), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const base = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setProblem(null);
    try {
      const reply = await p.recordReply(item.lead_id, { channel: item.channel, kind, text });
      onDone(t(`biz.outreach.replyKind.${reply.kind}` as MessageKey, { count: reply.cancelled }));
    } catch (error) { setProblem(failure(error, t('biz.outreach.recordReply'))); setBusy(false); }
  }
  return <form className="bzBlock" onSubmit={(event) => void submit(event)} aria-label={t('biz.outreach.recordReply')}>
    <h3>{t('biz.outreach.replyFrom', { company: item.company })}</h3>
    <fieldset className="field"><legend>{t('biz.outreach.replyKind')}</legend><div className="bzInline">
      {(['reply', 'bounce'] as const).map((value) => <label key={value} className="bzCheck"><input type="radio" name={`${base}-kind`} checked={kind === value} onChange={() => setKind(value)} />{t(`biz.outreach.kind.${value}` as MessageKey)}</label>)}
    </div></fieldset>
    {kind === 'reply' && <label className="field" htmlFor={`${base}-text`}><span>{t('biz.outreach.replyText')}</span><textarea id={`${base}-text`} rows={3} dir="auto" value={text} onChange={(event) => setText(event.target.value)} /></label>}
    <p className="muted bzHint">{t('biz.outreach.replyPrivacy')}</p>
    {problem && <Note error>{problem}</Note>}
    <div className="actions"><button type="submit" disabled={busy}>{t('biz.outreach.recordReply')}</button><button type="button" className="secondary" onClick={() => onDone(null)}>{t('common.cancel')}</button></div>
  </form>;
}
