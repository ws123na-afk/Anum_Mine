// Email Center phase 6 (docs/email.md#mailbox-settings), in the Mailbox settings drawer:
// - RetentionPanel: how long ANUM keeps sent messages, drafts and AI labels, background sync and
//   the last background check. A change is only asked for; an owner approves it with its payload hash.
// - ForwardingRulesPanel: the mailbox owner's forwarding rules. A new or edited rule (and resuming
//   one) waits for an approval of exactly that rule; pausing and removing need none.
import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from 'react';
import { CircleDot, Forward, Pause, PenLine, Play, Plus, RefreshCw, ShieldCheck, Timer, Trash2 } from 'lucide-react';
import * as api from './lib/api';
import { mapApproval, type ApiApproval } from './lib/api';
import { expiryLabel, shortHash } from './lib/approvals';
import { ApiError, describeError } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import type { MailAccount } from './lib/mail';
import {
  EMPTY_RULE, MAX_FORWARD_TO, parseRetention, retentionOptions, ruleInput, ruleProblem, sameSettings, settingsValues, sortRules, splitAddresses,
  type MailRule, type MailRuleInput, type MailSettings, type MailSettingsValues, type MailSync,
} from './lib/mailRules';
import {
  askMailSettings, createMailRule, deleteMailRule, getMailSettings, listMailRules, pauseMailRule, resumeMailRule, updateMailRule, withdrawMailSettings,
} from './lib/mailRulesApi';
import './mailAutomation.css';

type Note = { text: string; error?: boolean } | null;
const when = (value: string) => formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' });

function Notice({ note }: { note: Note }) {
  if (!note) return null;
  return <p className={`notice ${note.error ? 'errorNotice' : ''}`} role={note.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{note.text}</p>;
}

function useRun() {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<Note>(null);
  const run = useCallback(async (kind: string, fn: () => Promise<void>) => {
    setBusy(kind); setNote(null);
    try { await fn(); } catch (e) { setNote({ text: describeError(e), error: true }); } finally { setBusy(null); }
  }, []);
  return { busy, note, setNote, run };
}

/** One approval card: what waits, its payload hash and expiry; owners approve in place. */
function ApprovalCard({ title, text, approval, owner, busy, onApprove, onOpenApprovals, children }: {
  title: string; text: string; approval: ApiApproval; owner: boolean; busy: boolean; onApprove: () => void; onOpenApprovals?: () => void; children?: ReactNode;
}) {
  const mapped = mapApproval(approval);
  const expiry = expiryLabel(mapped);
  return <div className="mailApproval mailrApproval" role="group" aria-label={title}>
    <p><ShieldCheck aria-hidden="true" /> <strong>{title}</strong></p>
    <p className="muted" dir="auto">{text}</p>
    <p className="mailHash"><span className="muted">{t('mailr.approval.hash')}</span> <code dir="ltr" title={mapped.payloadHash ?? ''}>{shortHash(mapped.payloadHash)}</code>{expiry && <span className="muted"> · {expiry}</span>}</p>
    <div className="actions">
      {owner ? <button type="button" disabled={busy} onClick={onApprove}><ShieldCheck aria-hidden="true" />{busy ? t('mailr.approval.approving') : t('mailr.approval.approve')}</button>
        : <p className="muted">{t('mailr.approval.waitOwner')}</p>}
      {onOpenApprovals && <button type="button" className="secondary" onClick={onOpenApprovals}>{t('mailr.approval.open')}</button>}
      {children}
    </div>
  </div>;
}

// -- retention and sync -----------------------------------------------------------------------------

function retentionLabel(days: number | null): string {
  return days === null ? t('mailr.keep.forever') : t('mailr.keep.days', { count: days });
}

export function syncLine(sync: MailSync | null): string {
  if (!sync || sync.status === 'pending' || !sync.checked_at && sync.status === 'ok') return t('mailr.sync.pending');
  const at = when(sync.checked_at ?? sync.next_sync_at ?? new Date().toISOString());
  if (sync.status === 'ok') return t('mailr.sync.ok', { when: at, unread: formatNumber(sync.inbox_unread ?? 0), total: formatNumber(sync.inbox_total ?? 0) });
  if (sync.status === 'error') return t('mailr.sync.error', { when: at, reason: sync.last_error ?? '' });
  return t(`mailr.sync.${sync.status}` as MessageKey, { when: at });
}

export function RetentionPanel({ account, owner, onOpenApprovals }: { account: MailAccount; owner: boolean; onOpenApprovals?: () => void }) {
  const [settings, setSettings] = useState<MailSettings | null>(null);
  const [values, setValues] = useState<MailSettingsValues | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { busy, note, setNote, run } = useRun();
  const ids = { title: useId(), sent: useId(), drafts: useId(), labels: useId(), sync: useId(), syncHint: useId() };

  const load = useCallback(async () => {
    setLoadError(null);
    try { const next = await getMailSettings(account.id); setSettings(next); setValues(settingsValues(next)); }
    catch (e) { setLoadError(describeError(e)); }
  }, [account.id]);
  useEffect(() => { void load(); }, [load]);

  const header = <h3 id={ids.title}><Timer aria-hidden="true" />{t('mailr.keep.title')}</h3>;
  if (loadError) return <section className="mailsSection" aria-labelledby={ids.title}>{header}
    <Notice note={{ text: t('mailr.keep.failed', { reason: loadError }), error: true }} />
    <div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('mail.retry')}</button></div>
  </section>;
  if (!settings || !values) return <section className="mailsSection" aria-labelledby={ids.title}>{header}<p className="muted" role="status" aria-busy="true">{t('mailr.keep.loading')}</p></section>;

  const editable = settings.can_change && !busy;
  const unchanged = sameSettings(values, settingsValues(settings));
  const select = (key: 'sent_retention_days' | 'draft_retention_days' | 'label_retention_days', id: string, label: string) =>
    <label className="field" htmlFor={id}><span>{label}</span>
      <select id={id} value={values[key] === null ? '' : String(values[key])} disabled={!editable} onChange={(e) => setValues({ ...values, [key]: parseRetention(e.target.value) })}>
        {retentionOptions(settings.retention_choices, settings[key]).map((days) => <option key={days ?? 'keep'} value={days === null ? '' : String(days)}>{retentionLabel(days)}</option>)}
      </select>
    </label>;

  async function ask(event: FormEvent) {
    event.preventDefault();
    if (!settings || !values) return;
    if (unchanged) { setNote({ text: t('mailr.keep.nothing'), error: true }); return; }
    await run('ask', async () => {
      try { await askMailSettings(account.id, values, settings.version); }
      catch (e) {
        if (e instanceof ApiError && e.status === 409) { await load(); setNote({ text: t('mailr.keep.changed'), error: true }); return; }
        throw e;
      }
      await load();
    });
  }
  const pending = settings.approval && settings.pending ? settings.pending : null;
  return <section className="mailsSection" aria-labelledby={ids.title}>
    {header}
    <p className="muted">{t('mailr.keep.text')}</p>
    {!settings.can_change && <p className="notice" role="note"><CircleDot aria-hidden="true" />{t('mailr.keep.readOnly')}</p>}
    <form className="mailrKeep" onSubmit={(e) => void ask(e)}>
      {select('sent_retention_days', ids.sent, t('mailr.keep.sent'))}
      {select('draft_retention_days', ids.drafts, t('mailr.keep.drafts'))}
      {select('label_retention_days', ids.labels, t('mailr.keep.labels'))}
      <label className="mailsCheck mailrSync" htmlFor={ids.sync}>
        <input id={ids.sync} type="checkbox" checked={values.background_sync} disabled={!editable} aria-describedby={ids.syncHint} onChange={(e) => setValues({ ...values, background_sync: e.target.checked })} />
        <span>{t('mailr.keep.sync')}</span>
      </label>
      <small id={ids.syncHint} className="muted mailrWide">{settings.sync_running ? t('mailr.keep.syncHint', { minutes: Math.round(settings.sync_interval_seconds / 60) }) : t('mailr.keep.syncOff')}</small>
      {settings.can_change && <div className="actions mailrWide"><button type="submit" disabled={busy !== null || unchanged}>{busy === 'ask' ? t('mailr.keep.asking') : t('mailr.keep.ask')}</button></div>}
    </form>
    <Notice note={note} />
    {pending && settings.approval && <ApprovalCard title={t('mailr.keep.pendingTitle')} owner={owner} busy={busy === 'approve'} onOpenApprovals={onOpenApprovals} approval={settings.approval}
      text={t('mailr.keep.pendingText', { sent: retentionLabel(pending.sent_retention_days), drafts: retentionLabel(pending.draft_retention_days), labels: retentionLabel(pending.label_retention_days), sync: pending.background_sync ? t('mailr.keep.on') : t('mailr.keep.off') })}
      onApprove={() => void run('approve', async () => { await api.approveTask(mapApproval(settings.approval!)); await load(); setNote({ text: t('mailr.keep.applied') }); })}>
      <button type="button" className="secondary danger" disabled={busy !== null} onClick={() => void run('withdraw', async () => { await withdrawMailSettings(account.id); await load(); setNote({ text: t('mailr.keep.withdrawn') }); })}>{t('mailr.keep.withdraw')}</button>
    </ApprovalCard>}
    <p className="muted mailrSyncLine" role="status"><strong>{t('mailr.sync.title')}:</strong> {syncLine(settings.sync)}</p>
  </section>;
}

// -- forwarding rules ------------------------------------------------------------------------------

function RuleForm({ account, initial, editing, busy, onCancel, onSubmit }: {
  account: MailAccount; initial: MailRuleInput; editing: boolean; busy: boolean; onCancel: () => void; onSubmit: (input: MailRuleInput) => void;
}) {
  const [name, setName] = useState(initial.name);
  const [from, setFrom] = useState(initial.from_contains);
  const [subject, setSubject] = useState(initial.subject_contains);
  const [to, setTo] = useState(initial.forward_to.join(', '));
  const [problem, setProblem] = useState<string | null>(null);
  const ids = { title: useId(), name: useId(), from: useId(), fromHint: useId(), subject: useId(), to: useId(), toHint: useId(), problem: useId() };
  function submit(event: FormEvent) {
    event.preventDefault();
    const input = { name: name.trim(), from_contains: from.trim(), subject_contains: subject.trim(), forward_to: splitAddresses(to) };
    const found = ruleProblem(input, account.address);
    if (found) { setProblem(t(`mailr.problem.${found.problem}` as MessageKey, { max: MAX_FORWARD_TO, address: found.address ?? '' })); return; }
    setProblem(null);
    onSubmit(input);
  }
  return <form className="mailrRuleForm" aria-labelledby={ids.title} onSubmit={submit} noValidate>
    <h4 id={ids.title}>{editing ? t('mailr.form.editTitle') : t('mailr.form.title')}</h4>
    <label className="field" htmlFor={ids.name}><span>{t('mailr.form.name')}</span><input id={ids.name} dir="auto" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} /></label>
    <label className="field" htmlFor={ids.from}><span>{t('mailr.form.from')}</span><input id={ids.from} dir="auto" maxLength={200} aria-describedby={ids.fromHint} value={from} onChange={(e) => setFrom(e.target.value)} /></label>
    <small id={ids.fromHint} className="muted">{t('mailr.form.fromHint')}</small>
    <label className="field" htmlFor={ids.subject}><span>{t('mailr.form.subject')}</span><input id={ids.subject} dir="auto" maxLength={200} value={subject} onChange={(e) => setSubject(e.target.value)} /></label>
    <label className="field" htmlFor={ids.to}><span>{t('mailr.form.to')}</span><input id={ids.to} dir="ltr" inputMode="email" autoComplete="off" aria-describedby={ids.toHint} aria-invalid={problem ? true : undefined} value={to} onChange={(e) => setTo(e.target.value)} /></label>
    <small id={ids.toHint} className="muted">{t('mailr.form.toHint', { max: MAX_FORWARD_TO })}</small>
    {problem && <p id={ids.problem} className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{problem}</p>}
    <div className="actions">
      <button type="submit" disabled={busy}><ShieldCheck aria-hidden="true" />{t('mailr.form.ask')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={onCancel}>{t('mailr.form.cancel')}</button>
    </div>
  </form>;
}

function conditions(rule: MailRule): string[] {
  return [
    ...(rule.from_contains ? [t('mailr.rules.fromIs', { text: rule.from_contains })] : []),
    ...(rule.subject_contains ? [t('mailr.rules.subjectIs', { text: rule.subject_contains })] : []),
  ];
}

const TONE: Record<MailRule['status'], string> = { pending_approval: 'warning', active: 'success', paused: 'neutral' };

export function ForwardingRulesPanel({ account, owner, onOpenApprovals }: { account: MailAccount; owner: boolean; onOpenApprovals?: () => void }) {
  const [rules, setRules] = useState<MailRule[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<{ rule: MailRule | null } | null>(null);
  const { busy, note, setNote, run } = useRun();
  const title = useId();

  const load = useCallback(async () => {
    setLoadError(null);
    try { setRules(sortRules(await listMailRules(account.id))); } catch (e) { setLoadError(describeError(e)); }
  }, [account.id]);
  useEffect(() => { void load(); }, [load]);

  const header = <h3 id={title}><Forward aria-hidden="true" />{t('mailr.rules.title')}</h3>;
  async function save(input: MailRuleInput) {
    const editing = form?.rule ?? null;
    await run('save', async () => {
      if (editing) await updateMailRule(editing.id, input); else await createMailRule(account.id, input);
      setForm(null);
      await load();
      setNote({ text: t('mailr.rules.asked', { name: input.name }) });
    });
  }
  return <section className="mailsSection" aria-labelledby={title}>
    {header}
    <p className="muted">{t('mailr.rules.text')}</p>
    {loadError && <><Notice note={{ text: t('mailr.rules.failed', { reason: loadError }), error: true }} /><div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('mail.retry')}</button></div></>}
    {!rules && !loadError && <p className="muted" role="status" aria-busy="true">{t('mailr.rules.loading')}</p>}
    {rules && <>
      {rules.length === 0 ? <p className="muted">{t('mailr.rules.none')}</p> : <p className="muted" aria-live="polite">{t('mailr.rules.count', { count: rules.length })}</p>}
      {rules.length > 0 && <ul className="mailsList">{rules.map((rule) => {
        const named = { name: rule.name };
        return <li key={rule.id} className="mailsRow mailrRule">
          <div className="mailsWho">
            <span dir="auto"><strong>{rule.name}</strong> <span className={`statusBadge ${TONE[rule.status]}`}>{t(`mailr.rules.status.${rule.status}` as MessageKey)}</span></span>
            <small dir="auto">{conditions(rule).join(' · ')}</small>
            <small><span>{t('mailr.rules.to', { to: '' })}</span><bdi dir="ltr">{rule.forward_to.join(', ')}</bdi></small>
            <small>{t('mailr.rules.forwarded', { count: rule.forwarded_count })}{rule.last_run_at ? ` ${t('mailr.rules.lastRun', { when: when(rule.last_run_at) })}` : ''}</small>
            {rule.last_error && <small className="mailrError" dir="auto">{rule.last_error}</small>}
            {rule.approval_note && <small className="mailrError">{rule.approval_note}</small>}
          </div>
          <div className="mailsRowActions">
            <button type="button" className="secondary" disabled={busy !== null} aria-label={t('mailr.rules.editFor', named)} onClick={() => setForm({ rule })}><PenLine aria-hidden="true" />{t('mailr.rules.edit')}</button>
            {rule.status === 'paused' || (rule.status === 'pending_approval' && !rule.approval)
              ? <button type="button" className="secondary" disabled={busy !== null} aria-label={t('mailr.rules.resumeFor', named)} onClick={() => void run(`resume:${rule.id}`, async () => { await resumeMailRule(rule.id); await load(); setNote({ text: t('mailr.rules.asked', named) }); })}><Play aria-hidden="true" />{t('mailr.rules.resume')}</button>
              : <button type="button" className="secondary" disabled={busy !== null} aria-label={t('mailr.rules.pauseFor', named)} onClick={() => void run(`pause:${rule.id}`, async () => { await pauseMailRule(rule.id); await load(); setNote({ text: t('mailr.rules.paused', named) }); })}><Pause aria-hidden="true" />{t('mailr.rules.pause')}</button>}
            <button type="button" className="secondary danger" disabled={busy !== null} aria-label={t('mailr.rules.removeFor', named)} onClick={() => {
              if (!window.confirm(t('mailr.rules.removeConfirm', named))) return;
              void run(`remove:${rule.id}`, async () => { await deleteMailRule(rule.id); await load(); setNote({ text: t('mailr.rules.removed', named) }); });
            }}><Trash2 aria-hidden="true" />{t('mailr.rules.remove')}</button>
          </div>
          {rule.status === 'pending_approval' && rule.approval && <ApprovalCard title={t('mailr.rules.status.pending_approval')} owner={owner} busy={busy === `approve:${rule.id}`} onOpenApprovals={onOpenApprovals}
            approval={rule.approval} text={`${conditions(rule).join(' · ')}. ${t('mailr.rules.to', { to: rule.forward_to.join(', ') })}`}
            onApprove={() => void run(`approve:${rule.id}`, async () => { await api.approveTask(mapApproval(rule.approval!)); await load(); setNote({ text: t('mailr.rules.approved', named) }); })} />}
        </li>;
      })}</ul>}
    </>}
    <Notice note={note} />
    {form ? <RuleForm key={form.rule?.id ?? 'new'} account={account} editing={form.rule !== null} busy={busy !== null} initial={form.rule ? ruleInput(form.rule) : EMPTY_RULE}
      onCancel={() => setForm(null)} onSubmit={(input) => void save(input)} />
      : rules && <div className="actions"><button type="button" className="secondary" disabled={busy !== null} onClick={() => setForm({ rule: null })}><Plus aria-hidden="true" />{t('mailr.rules.new')}</button></div>}
  </section>;
}

/** What people a mailbox is shared with see instead of the rules. */
export function RulesOwnerOnly() {
  const title = useId();
  return <section className="mailsSection" aria-labelledby={title}>
    <h3 id={title}><Forward aria-hidden="true" />{t('mailr.rules.title')}</h3>
    <p className="muted">{t('mailr.rules.ownerOnly')}</p>
  </section>;
}
