// Email campaigns through Brevo (docs/brevo.md): the workspace's Brevo connection, the campaigns in
// Brevo with their numbers and reports, and drafts written in ANUM. Sending or scheduling to contacts
// always waits for an owner's approval (payload hash shown); a test goes only to the Brevo account's
// own address. Brevo's names and subjects are data from outside ANUM: shown as text, never as HTML.
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { CheckCircle2, CircleDot, Eye, Fingerprint, KeyRound, Lock, Mail, Plus, RefreshCw, Search, Send, ShieldAlert, Trash2 } from 'lucide-react';
import type { Approval } from '@anum/contracts';
import * as api from './lib/api';
import { argumentRows, shortHash } from './lib/approvals';
import {
  CAMPAIGN_STATUSES, DRAFT_STATUSES, campaignStatusName, draftActions, draftProblems, draftStatusName, fromLocalInput, keyProblem, pageRange, percent, statusTone, toLocalInput,
  type Campaign, type ContactList, type Draft, type DraftBody, type DraftStatus, type Page, type Sender, type Status,
} from './lib/brevo';
import * as b from './lib/brevoApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { readerDocument } from './lib/mail';
import { Drawer } from './Dialog';
import './brevo.css';

type Section = 'campaigns' | 'drafts' | 'connection';
const SECTIONS: Section[] = ['campaigns', 'drafts', 'connection'];
const PAGE = 20;

function Note({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}
function Badge({ tone, children }: { tone: string; children: ReactNode }) { return <span className={`statusBadge ${tone}`}>{children}</span>; }
function Empty({ title, text, action }: { title: string; text: string; action?: ReactNode }) {
  return <div className="emptyState"><Search aria-hidden="true" /><h3>{title}</h3><p>{text}</p>{action && <div className="actions mkInline mkCenter">{action}</div>}</div>;
}
const failure = (e: unknown, what: string) => isPermissionDenied(e) ? t('brevo.denied', { message: describeError(e) }) : t('brevo.failed', { what, message: describeError(e) });

/** The Email campaigns tab of Marketing. */
export function BrevoCampaigns({ owner }: { owner: boolean }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'denied' | 'error'>('loading');
  const [problem, setProblem] = useState<string | null>(null);
  const [section, setSection] = useState<Section>('campaigns');
  const [notice, setNotice] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setStatus(await b.getStatus()); setState('ready'); setProblem(null); }
    catch (e) { setState(isPermissionDenied(e) ? 'denied' : 'error'); setProblem(describeError(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  if (state === 'loading') return <Note>{t('brevo.loading')}</Note>;
  if (state === 'denied') return <section className="surface mkPanel"><Lock aria-hidden="true" /><div><h2>{t('brevo.deniedTitle')}</h2><p className="muted">{t('brevo.deniedText')}</p></div></section>;
  if (state === 'error' || !status) return <section className="surface mkPanel"><ShieldAlert aria-hidden="true" /><div><h2>{t('brevo.errorTitle')}</h2><p className="muted">{t('brevo.errorText', { message: problem ?? '' })}</p><div className="actions"><button type="button" onClick={() => { setState('loading'); void load(); }}>{t('common.tryAgain')}</button></div></div></section>;

  const connected = status.connected;
  const current: Section = !connected && section !== 'drafts' ? 'connection' : section;
  return <section className="bvLayout" aria-labelledby="bv-title">
    <div className="mkSectionHead">
      <div><h2 id="bv-title"><Mail aria-hidden="true" className="mkInlineIcon" />{t('brevo.title')}</h2><p className="muted bvLead">{t('brevo.lead')}</p></div>
      <nav className="bvSections" aria-label={t('brevo.sections')}>{SECTIONS.map((id) => <button key={id} type="button" className="secondary" aria-current={current === id ? 'page' : undefined} onClick={() => setSection(id)}>{t(`brevo.section.${id}`)}</button>)}</nav>
    </div>
    {!connected && <Note>{status.connection?.status === 'needs_key' ? t('brevo.needsKey') : status.can_manage ? t('brevo.notConnectedOwner') : t('brevo.notConnectedMember')}</Note>}
    {notice && <Note>{notice}</Note>}
    {current === 'connection' && <ConnectionSection status={status} onChanged={(next, message) => { setStatus(next); setNotice(message); if (next.connected) setSection('campaigns'); }} />}
    {current === 'campaigns' && <CampaignsSection onWrite={() => setSection('drafts')} />}
    {current === 'drafts' && <DraftsSection status={status} owner={owner} onNotice={setNotice} />}
  </section>;
}

// ---- connection --------------------------------------------------------------------------------------

function ConnectionSection({ status, onChanged }: { status: Status; onChanged: (status: Status, message: string) => void }) {
  const [key, setKey] = useState(''), [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const keyId = useId(), hintId = useId();
  const connection = status.connection;
  async function run(fn: () => Promise<Status>, message: (next: Status) => string) {
    setBusy(true); setProblem(null);
    try { const next = await fn(); onChanged(next, message(next)); setKey(''); } catch (e) { setProblem(failure(e, t('brevo.connection.what'))); } finally { setBusy(false); }
  }
  function save(event: FormEvent) {
    event.preventDefault();
    const wrong = keyProblem(key);
    if (wrong) { setProblem(t(wrong)); return; }
    void run(() => b.saveKey(key.trim()), (next) => t('brevo.connection.saved', { email: next.connection?.account_email ?? '' }));
  }
  return <div className="bvConnection">
    {connection ? <div className="pane dataTableWrap"><table className="dataTable bvFacts">
      <caption className="visuallyHidden">{t('brevo.connection.title')}</caption>
      <tbody>
        <tr><th scope="row">{t('brevo.connection.state')}</th><td><Badge tone={statusTone(connection.status)}>{t(`brevo.connection.status.${connection.status}`)}</Badge>{connection.last_error && <small className="bvError">{connection.last_error}</small>}</td></tr>
        <tr><th scope="row">{t('brevo.connection.account')}</th><td><span dir="auto">{connection.company || connection.account_name || '-'}</span> <small dir="ltr">{connection.account_email}</small></td></tr>
        <tr><th scope="row">{t('brevo.connection.plan')}</th><td>{connection.plan || '-'}</td></tr>
        <tr><th scope="row">{t('brevo.connection.key')}</th><td><code dir="ltr">{t('brevo.connection.keyEnding', { last4: connection.key_last4 })}</code></td></tr>
        <tr><th scope="row">{t('brevo.connection.checked')}</th><td>{connection.last_checked_at ? formatDateTime(connection.last_checked_at) : '-'}</td></tr>
      </tbody>
    </table></div> : <Empty title={t('brevo.connection.none')} text={status.can_manage ? t('brevo.connection.noneOwner') : t('brevo.connection.noneMember')} />}
    {status.can_manage ? <form className="surface bvKeyForm" onSubmit={save}>
      <h3><KeyRound aria-hidden="true" className="mkInlineIcon" />{connection ? t('brevo.connection.replace') : t('brevo.connection.paste')}</h3>
      <label className="field" htmlFor={keyId}><span>{t('brevo.connection.keyLabel')}</span>
        <input id={keyId} type="password" autoComplete="off" spellCheck={false} dir="ltr" value={key} onChange={(event) => setKey(event.target.value)} aria-describedby={hintId} placeholder="xkeysib-..." /></label>
      <small id={hintId} className="muted">{t('brevo.connection.keyHint')}</small>
      <div className="actions mkInline">
        <button type="submit" disabled={busy || !key.trim()}>{busy ? t('common.loading') : t('brevo.connection.save')}</button>
        {connection && <button type="button" className="secondary" disabled={busy} onClick={() => void run(b.checkConnection, (next) => next.connected ? t('brevo.connection.checkedOk') : t('brevo.connection.checkedBad'))}><RefreshCw aria-hidden="true" />{t('brevo.connection.check')}</button>}
        {connection && <button type="button" className="secondary dangerText" disabled={busy} onClick={() => { if (confirm(t('brevo.connection.confirmRemove'))) void run(b.removeConnection, () => t('brevo.connection.removed')); }}><Trash2 aria-hidden="true" />{t('brevo.connection.remove')}</button>}
      </div>
      <p className="muted bvSmall">{t('brevo.connection.dns')}</p>
    </form> : <Note>{t('brevo.connection.ownerOnly')}</Note>}
    {problem && <Note error>{problem}</Note>}
  </div>;
}

// ---- campaigns in Brevo ----------------------------------------------------------------------------------

function CampaignsSection({ onWrite }: { onWrite: () => void }) {
  const [page, setPage] = useState<Page<Campaign> | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading'), [problem, setProblem] = useState<string | null>(null);
  const [filter, setFilter] = useState(''), [offset, setOffset] = useState(0), [opened, setOpened] = useState<number | null>(null);
  const filterId = useId();
  const load = useCallback(async () => {
    setState('loading');
    try { setPage(await b.listCampaigns({ limit: PAGE, offset, status: filter })); setState('ready'); }
    catch (e) { setState('error'); setProblem(failure(e, t('brevo.campaigns.what'))); }
  }, [filter, offset]);
  useEffect(() => { void load(); }, [load]);
  const range = page ? pageRange(page, page.items.length) : null;
  return <div className="bvCampaigns">
    <div className="mkInline bvToolbar">
      <label className="field bvFilter" htmlFor={filterId}><span>{t('brevo.campaigns.status')}</span>
        <select id={filterId} value={filter} onChange={(event) => { setFilter(event.target.value); setOffset(0); }}>
          <option value="">{t('brevo.all')}</option>{CAMPAIGN_STATUSES.map((value) => <option key={value} value={value}>{campaignStatusName(value)}</option>)}
        </select></label>
      <button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('brevo.refresh')}</button>
    </div>
    {state === 'loading' && <Note>{t('brevo.campaigns.loading')}</Note>}
    {state === 'error' && <div><Note error>{problem}</Note><div className="actions"><button type="button" onClick={() => void load()}>{t('common.tryAgain')}</button></div></div>}
    {state === 'ready' && page && (page.items.length === 0
      ? <Empty title={filter ? t('brevo.campaigns.noMatch') : t('brevo.campaigns.none')} text={filter ? t('brevo.campaigns.noMatchText') : t('brevo.campaigns.noneText')}
        action={filter ? <button type="button" className="secondary" onClick={() => setFilter('')}>{t('brevo.clearFilter')}</button> : <button type="button" onClick={onWrite}><Plus aria-hidden="true" />{t('brevo.campaigns.write')}</button>} />
      : <div className="pane dataTableWrap"><table className="dataTable bvTable">
        <caption className="visuallyHidden">{t('brevo.campaigns.caption')}</caption>
        <thead><tr><th scope="col">{t('brevo.col.campaign')}</th><th scope="col">{t('brevo.col.status')}</th><th scope="col" className="optional">{t('brevo.col.when')}</th><th scope="col" className="num">{t('brevo.col.delivered')}</th><th scope="col" className="num">{t('brevo.col.opens')}</th><th scope="col" className="num optional">{t('brevo.col.clicks')}</th><th scope="col"><span className="visuallyHidden">{t('brevo.col.actions')}</span></th></tr></thead>
        <tbody>{page.items.map((item) => <tr key={item.id}>
          <th scope="row"><strong dir="auto">{item.name || t('brevo.untitled')}</strong><small dir="auto">{item.subject}</small></th>
          <td><Badge tone={statusTone(item.status)}>{campaignStatusName(item.status)}</Badge></td>
          <td className="optional nowrap muted">{item.sent_at ? formatDateTime(item.sent_at) : item.scheduled_at ? formatDateTime(item.scheduled_at) : '-'}</td>
          <td className="num">{formatNumber(item.stats.delivered)}</td>
          <td className="num">{percent(item.stats.open_rate)}</td>
          <td className="num optional">{percent(item.stats.click_rate)}</td>
          <td><div className="cellActions"><button type="button" className="secondary" onClick={() => setOpened(item.id)} aria-label={t('brevo.campaigns.reportOf', { name: item.name })}>{t('brevo.campaigns.report')}</button></div></td>
        </tr>)}</tbody>
      </table></div>)}
    {range && page && page.total > 0 && <Pager range={range} total={page.total} onPrev={() => setOffset(Math.max(0, offset - PAGE))} onNext={() => setOffset(offset + PAGE)} />}
    {opened !== null && <Drawer title={t('brevo.report.title')} onClose={() => setOpened(null)}><CampaignReport campaignId={opened} /></Drawer>}
  </div>;
}

function Pager({ range, total, onPrev, onNext }: { range: ReturnType<typeof pageRange>; total: number; onPrev: () => void; onNext: () => void }) {
  return <nav className="mkInline bvPager" aria-label={t('brevo.paging')}>
    <span className="muted" role="status">{t('brevo.range', { from: range.from, to: range.to, total })}</span>
    <button type="button" className="secondary" disabled={!range.hasPrev} onClick={onPrev}>{t('brevo.prev')}</button>
    <button type="button" className="secondary" disabled={!range.hasNext} onClick={onNext}>{t('brevo.next')}</button>
  </nav>;
}

function CampaignReport({ campaignId }: { campaignId: number }) {
  const [campaign, setCampaign] = useState<Campaign | null>(null), [problem, setProblem] = useState<string | null>(null);
  useEffect(() => { void b.getCampaign(campaignId).then(setCampaign).catch((e) => setProblem(failure(e, t('brevo.report.what')))); }, [campaignId]);
  if (problem) return <Note error>{problem}</Note>;
  if (!campaign) return <Note>{t('brevo.report.loading')}</Note>;
  const s = campaign.stats;
  const figures: [string, string][] = [
    [t('brevo.report.sent'), formatNumber(s.sent)], [t('brevo.report.delivered'), formatNumber(s.delivered)],
    [t('brevo.report.opens'), `${formatNumber(s.unique_views)} (${percent(s.open_rate)})`], [t('brevo.report.clicks'), `${formatNumber(s.unique_clicks)} (${percent(s.click_rate)})`],
    [t('brevo.report.unsubscribed'), formatNumber(s.unsubscriptions)], [t('brevo.report.bounced'), formatNumber(s.hard_bounces + s.soft_bounces)],
    [t('brevo.report.complaints'), formatNumber(s.complaints)],
  ];
  return <div className="bvReport">
    <h3 dir="auto">{campaign.name}</h3>
    <p className="muted" dir="auto">{campaign.subject}</p>
    <p><Badge tone={statusTone(campaign.status)}>{campaignStatusName(campaign.status)}</Badge> <span className="muted">{campaign.sent_at ? t('brevo.report.sentAt', { when: formatDateTime(campaign.sent_at) }) : campaign.scheduled_at ? t('brevo.report.scheduledAt', { when: formatDateTime(campaign.scheduled_at) }) : ''}</span></p>
    <dl className="bvFigures">{figures.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <p className="muted bvSmall"><span dir="auto">{campaign.sender_name}</span> <span dir="ltr">{campaign.sender_email}</span></p>
    {campaign.list_stats.length > 0 && <div className="pane dataTableWrap"><table className="dataTable">
      <caption>{t('brevo.report.perList')}</caption>
      <thead><tr><th scope="col">{t('brevo.report.list')}</th><th scope="col" className="num">{t('brevo.report.delivered')}</th><th scope="col" className="num">{t('brevo.report.opens')}</th></tr></thead>
      <tbody>{campaign.list_stats.map((row) => <tr key={row.list_id}><th scope="row">#{row.list_id}</th><td className="num">{formatNumber(row.delivered ?? 0)}</td><td className="num">{percent(row.open_rate)}</td></tr>)}</tbody>
    </table></div>}
    {s.delivered === 0 && <p className="muted">{t('brevo.report.noNumbers')}</p>}
  </div>;
}

// ---- drafts ---------------------------------------------------------------------------------------------

function DraftsSection({ status, owner, onNotice }: { status: Status; owner: boolean; onNotice: (text: string) => void }) {
  const [page, setPage] = useState<Page<Draft> | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading'), [problem, setProblem] = useState<string | null>(null);
  const [typed, setTyped] = useState(''), [query, setQuery] = useState(''), [filter, setFilter] = useState<DraftStatus | ''>(''), [offset, setOffset] = useState(0);
  const [opened, setOpened] = useState<string | null>(null), [editing, setEditing] = useState<Draft | 'new' | null>(null), [version, setVersion] = useState(0);
  const searchId = useId(), statusId = useId();
  const load = useCallback(async () => {
    try { setPage(await b.listDrafts({ query, status: filter, limit: PAGE, offset })); setState('ready'); }
    catch (e) { setState('error'); setProblem(failure(e, t('brevo.drafts.what'))); }
  }, [query, filter, offset]);
  useEffect(() => { void load(); }, [load, version]);
  const range = page ? pageRange(page, page.items.length) : null;
  const filtered = Boolean(query || filter);
  return <div className="bvDrafts">
    <form className="mkInline bvToolbar" role="search" onSubmit={(event) => { event.preventDefault(); setOffset(0); setQuery(typed); }}>
      <label className="field bvSearch" htmlFor={searchId}><span>{t('brevo.drafts.search')}</span><input id={searchId} type="search" value={typed} onChange={(event) => setTyped(event.target.value)} /></label>
      <label className="field bvFilter" htmlFor={statusId}><span>{t('brevo.drafts.status')}</span>
        <select id={statusId} value={filter} onChange={(event) => { setFilter(event.target.value as DraftStatus | ''); setOffset(0); }}>
          <option value="">{t('brevo.all')}</option>{DRAFT_STATUSES.map((value) => <option key={value} value={value}>{draftStatusName(value)}</option>)}
        </select></label>
      <button type="submit" className="secondary"><Search aria-hidden="true" />{t('brevo.drafts.find')}</button>
      {status.can_write && <button type="button" disabled={!status.connected} onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('brevo.drafts.new')}</button>}
    </form>
    {state === 'loading' && <Note>{t('brevo.drafts.loading')}</Note>}
    {state === 'error' && <div><Note error>{problem}</Note><div className="actions"><button type="button" onClick={() => void load()}>{t('common.tryAgain')}</button></div></div>}
    {state === 'ready' && page && (page.items.length === 0
      ? <Empty title={filtered ? t('brevo.drafts.noMatch') : t('brevo.drafts.none')} text={filtered ? t('brevo.drafts.noMatchText') : status.can_write ? t('brevo.drafts.noneText') : t('brevo.drafts.noneViewer')}
        action={filtered ? <button type="button" className="secondary" onClick={() => { setTyped(''); setQuery(''); setFilter(''); }}>{t('brevo.clearFilter')}</button>
          : status.can_write && status.connected ? <button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('brevo.drafts.new')}</button> : undefined} />
      : <div className="pane dataTableWrap"><table className="dataTable bvTable">
        <caption className="visuallyHidden">{t('brevo.drafts.caption')}</caption>
        <thead><tr><th scope="col">{t('brevo.col.campaign')}</th><th scope="col" className="optional">{t('brevo.col.lists')}</th><th scope="col">{t('brevo.col.status')}</th><th scope="col" className="optional">{t('brevo.col.updated')}</th><th scope="col"><span className="visuallyHidden">{t('brevo.col.actions')}</span></th></tr></thead>
        <tbody>{page.items.map((draft) => <tr key={draft.id}>
          <th scope="row"><strong dir="auto">{draft.name}</strong><small dir="auto">{draft.subject}</small>{draft.source === 'assistant' && <small>{t('brevo.drafts.byAssistant')}</small>}</th>
          <td className="optional"><span dir="auto">{draft.list_names.join(', ') || draft.list_ids.join(', ')}</span></td>
          <td><Badge tone={statusTone(draft.status)}>{draftStatusName(draft.status)}</Badge>{draft.last_error && <small className="bvError">{draft.last_error.message}</small>}</td>
          <td className="optional nowrap muted">{formatDateTime(draft.updated_at)}</td>
          <td><div className="cellActions"><button type="button" className="secondary" onClick={() => setOpened(draft.id)} aria-label={t('brevo.drafts.openOf', { name: draft.name })}><Eye aria-hidden="true" />{t('brevo.drafts.open')}</button></div></td>
        </tr>)}</tbody>
      </table></div>)}
    {range && page && page.total > 0 && <Pager range={range} total={page.total} onPrev={() => setOffset(Math.max(0, offset - PAGE))} onNext={() => setOffset(offset + PAGE)} />}
    {opened && <Drawer title={t('brevo.drafts.drawer')} onClose={() => setOpened(null)}>
      <DraftDetail draftId={opened} status={status} owner={owner} onEdit={(draft) => { setOpened(null); setEditing(draft); }}
        onChanged={(message, gone) => { onNotice(message); setVersion((value) => value + 1); if (gone) setOpened(null); }} />
    </Drawer>}
    {editing && <Drawer title={editing === 'new' ? t('brevo.editor.newTitle') : t('brevo.editor.editTitle')} onClose={() => setEditing(null)}>
      <DraftEditor draft={editing === 'new' ? null : editing} onSaved={(draft, message) => { setEditing(null); onNotice(message); setVersion((value) => value + 1); setOpened(draft.id); }} />
    </Drawer>}
  </div>;
}

const HIDDEN_ARGS = new Set(['draft_id', 'content_hash', 'sender_id', 'sender_email', 'list_ids']);
const ARG_KEYS = ['campaign', 'subject', 'preview_text', 'sender', 'lists', 'recipients', 'scheduled_at', 'brevo_account', 'text', 'written_by'] as const;
const argLabel = (key: string): string => { const base = key.split('[')[0]; return (ARG_KEYS as readonly string[]).includes(base) ? t(`brevo.arg.${base}` as MessageKey) : base.replace(/_/g, ' '); };
function ApprovalBox({ approval, owner, onDecided }: { approval: Approval; owner: boolean; onDecided: (message: string) => void }) {
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const rows = argumentRows(approval.arguments ?? {}).filter((row) => !HIDDEN_ARGS.has(row.key.split('[')[0]) && row.value !== '' && !(row.key === 'written_by' && row.value === 'person'));
  async function decide(kind: 'approve' | 'reject') {
    setBusy(true); setProblem(null);
    try {
      const result = kind === 'approve' ? await api.approveTask(approval) : await api.rejectTask(approval);
      onDecided(kind === 'approve' ? (result.run?.result ?? t('brevo.approval.approved')) : t('brevo.approval.rejected'));
    } catch (e) { setProblem(describeError(e)); } finally { setBusy(false); }
  }
  return <div className="mkApproval" role="group" aria-label={t('brevo.approval.label')}>
    <p className="mkApprovalTitle"><ShieldAlert aria-hidden="true" />{t('brevo.approval.waiting')}</p>
    <dl className="mkArgs">{rows.map((row) => <div key={row.key}><dt>{argLabel(row.key)}</dt><dd dir="auto" className={row.multiline ? 'mkMultiline' : undefined}>{row.value}</dd></div>)}</dl>
    <p className="mkHash"><Fingerprint aria-hidden="true" />{t('brevo.approval.hash')} <code title={approval.payloadHash ?? ''}>{shortHash(approval.payloadHash)}</code></p>
    {owner ? <div className="actions mkInline"><button type="button" disabled={busy} onClick={() => void decide('approve')}><CheckCircle2 aria-hidden="true" />{t('brevo.approval.approve')}</button><button type="button" className="secondary" disabled={busy} onClick={() => void decide('reject')}>{t('brevo.approval.reject')}</button></div>
      : <p className="muted">{t('brevo.approval.ownerDecides')}</p>}
    {problem && <Note error>{problem}</Note>}
  </div>;
}

function DraftDetail({ draftId, status, owner, onEdit, onChanged }: { draftId: string; status: Status; owner: boolean; onEdit: (draft: Draft) => void; onChanged: (message: string, gone?: boolean) => void }) {
  const [draft, setDraft] = useState<Draft | null>(null), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false), [when, setWhen] = useState('');
  const whenId = useId();
  const reload = useCallback(() => { void b.getDraft(draftId).then(setDraft).catch((e) => setProblem(failure(e, t('brevo.drafts.what')))); }, [draftId]);
  useEffect(() => { reload(); }, [reload]);
  const doc = useMemo(() => draft?.preview_html ? readerDocument(draft.preview_html) : null, [draft]);
  if (!draft) return problem ? <Note error>{problem}</Note> : <Note>{t('brevo.drafts.loadingOne')}</Note>;
  const actions = draftActions(draft, status, status.connected);
  const approval = b.approvalOf(draft.approval);
  async function act(fn: () => Promise<Draft | void>, message: string, gone = false) {
    setBusy(true); setProblem(null);
    try { const next = await fn(); if (next) setDraft(next); onChanged(message, gone); } catch (e) { setProblem(failure(e, t('brevo.drafts.what'))); } finally { setBusy(false); }
  }
  return <div className="bvDetail">
    <div className="mkPostHead"><div><h3 dir="auto">{draft.name}</h3><small className="muted" dir="auto">{draft.subject}</small></div><Badge tone={statusTone(draft.status)}>{draftStatusName(draft.status)}</Badge></div>
    <dl className="bvFacts bvFactList">
      <div><dt>{t('brevo.editor.sender')}</dt><dd><span dir="auto">{draft.sender_name}</span> <span dir="ltr">{draft.sender_email}</span></dd></div>
      <div><dt>{t('brevo.editor.lists')}</dt><dd dir="auto">{draft.list_names.join(', ') || draft.list_ids.join(', ')}</dd></div>
      {draft.preview_text && <div><dt>{t('brevo.editor.preview')}</dt><dd dir="auto">{draft.preview_text}</dd></div>}
      {draft.scheduled_at && <div><dt>{t('brevo.drafts.scheduledFor')}</dt><dd>{formatDateTime(draft.scheduled_at)}</dd></div>}
      {draft.test_sent_at && <div><dt>{t('brevo.drafts.testSent')}</dt><dd>{formatDateTime(draft.test_sent_at)}</dd></div>}
      {draft.brevo_campaign_id && <div><dt>{t('brevo.drafts.inBrevo')}</dt><dd>#{draft.brevo_campaign_id}</dd></div>}
    </dl>
    {draft.source === 'assistant' && <Note>{t('brevo.drafts.assistantNote')}</Note>}
    {draft.last_error && <div className="mkError" role="note"><strong>{draft.last_error.message}</strong><span>{draft.last_error.fix}</span></div>}
    {approval && <ApprovalBox approval={approval} owner={owner} onDecided={(message) => { onChanged(message); reload(); }} />}
    <div className="actions mkPostActions">
      {actions.edit && <button type="button" className="secondary" disabled={busy} onClick={() => onEdit(draft)}>{t('brevo.drafts.edit')}</button>}
      {actions.test && <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => b.sendTest(draft.id), t('brevo.drafts.testDone', { email: status.connection?.account_email ?? '' }))}><Mail aria-hidden="true" />{t('brevo.drafts.test')}</button>}
      {actions.ask && !asking && <button type="button" disabled={busy} onClick={() => { setAsking(true); setWhen(toLocalInput(draft.scheduled_at)); }}><Send aria-hidden="true" />{t('brevo.drafts.ask')}</button>}
      {actions.withdraw && <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => b.withdraw(draft.id), t('brevo.drafts.withdrawn'))}>{t('brevo.drafts.withdraw')}</button>}
      {actions.remove && <button type="button" className="secondary dangerText mkDelete" disabled={busy} onClick={() => { if (confirm(t('brevo.drafts.confirmDelete'))) void act(async () => { await b.deleteDraft(draft.id); }, t('brevo.drafts.deleted'), true); }}><Trash2 aria-hidden="true" />{t('brevo.drafts.delete')}</button>}
    </div>
    {!status.can_manage && actions.ask && <p className="muted bvSmall">{t('brevo.drafts.testOwner')}</p>}
    {asking && <form className="mkScheduleForm" onSubmit={(event) => { event.preventDefault(); setAsking(false); void act(() => b.askToSend(draft.id, fromLocalInput(when)), t('brevo.drafts.asked')); }}>
      <label className="field" htmlFor={whenId}><span>{t('brevo.drafts.when')}</span><input id={whenId} type="datetime-local" value={when} onChange={(event) => setWhen(event.target.value)} /></label>
      <small className="muted">{t('brevo.drafts.whenHint')}</small>
      <div className="actions mkInline"><button type="submit" disabled={busy}>{when ? t('brevo.drafts.askSchedule') : t('brevo.drafts.askNow')}</button><button type="button" className="secondary" onClick={() => setAsking(false)}>{t('common.cancel')}</button></div>
    </form>}
    {problem && <Note error>{problem}</Note>}
    <h4>{t('brevo.drafts.previewTitle')}</h4>
    {draft.remote_images > 0 && <p className="muted bvSmall">{t('brevo.drafts.imagesHidden', { count: draft.remote_images })}</p>}
    {doc && <iframe className="bvFrame" title={t('brevo.drafts.frameTitle')} sandbox="allow-popups allow-popups-to-escape-sandbox" referrerPolicy="no-referrer" srcDoc={doc} />}
  </div>;
}

function DraftEditor({ draft, onSaved }: { draft: Draft | null; onSaved: (draft: Draft, message: string) => void }) {
  const [senders, setSenders] = useState<Sender[] | null>(null), [lists, setLists] = useState<ContactList[] | null>(null), [listTotal, setListTotal] = useState(0);
  const [loadProblem, setLoadProblem] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [body, setBody] = useState<DraftBody>(() => ({ name: draft?.name ?? '', subject: draft?.subject ?? '', preview_text: draft?.preview_text ?? '', sender_id: draft?.sender_id ?? 0, html: draft?.html ?? '', list_ids: draft?.list_ids ?? [] }));
  const [listFilter, setListFilter] = useState('');
  const ids = { name: useId(), subject: useId(), preview: useId(), sender: useId(), html: useId(), lists: useId(), listFilter: useId() };
  useEffect(() => {
    void Promise.all([b.listSenders(), b.listLists(50, 0)]).then(([s, l]) => {
      const active = s.items.filter((item) => item.active);
      setSenders(active); setLists(l.items); setListTotal(l.total);
      setBody((current) => current.sender_id ? current : { ...current, sender_id: active[0]?.id ?? 0 });
    }).catch((e) => setLoadProblem(failure(e, t('brevo.editor.what'))));
  }, []);
  async function moreLists() {
    if (!lists) return;
    try { const next = await b.listLists(50, lists.length); setLists([...lists, ...next.items]); setListTotal(next.total); } catch (e) { setProblem(failure(e, t('brevo.editor.what'))); }
  }
  const problems = draftProblems(body);
  const preview = useMemo(() => readerDocument(body.html), [body.html]);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (problems.length) { setProblem(problems.map((key) => t(key)).join(' ')); return; }
    setBusy(true); setProblem(null);
    try {
      const saved = draft ? await b.updateDraft(draft.id, body) : await b.createDraft(body);
      onSaved(saved, draft ? t('brevo.editor.updated') : t('brevo.editor.created'));
    } catch (e) { setProblem(failure(e, t('brevo.editor.what'))); } finally { setBusy(false); }
  }
  if (loadProblem) return <Note error>{loadProblem}</Note>;
  if (!senders || !lists) return <Note>{t('brevo.editor.loading')}</Note>;
  const shownLists = lists.filter((item) => !listFilter.trim() || item.name.toLowerCase().includes(listFilter.trim().toLowerCase()));
  const toggle = (id: number) => setBody({ ...body, list_ids: body.list_ids.includes(id) ? body.list_ids.filter((item) => item !== id) : [...body.list_ids, id] });
  return <form className="bvEditor" onSubmit={(event) => void save(event)} noValidate>
    {draft?.status === 'pending_approval' && <Note>{t('brevo.editor.retires')}</Note>}
    <label className="field" htmlFor={ids.name}><span>{t('brevo.editor.name')}</span><input id={ids.name} required maxLength={200} dir="auto" value={body.name} onChange={(event) => setBody({ ...body, name: event.target.value })} /></label>
    <label className="field" htmlFor={ids.subject}><span>{t('brevo.editor.subject')}</span><input id={ids.subject} required maxLength={200} dir="auto" value={body.subject} onChange={(event) => setBody({ ...body, subject: event.target.value })} /></label>
    <label className="field" htmlFor={ids.preview}><span>{t('brevo.editor.preview')}</span><input id={ids.preview} maxLength={200} dir="auto" value={body.preview_text} onChange={(event) => setBody({ ...body, preview_text: event.target.value })} /></label>
    {senders.length === 0 ? <Note error>{t('brevo.editor.noSenders')}</Note>
      : <label className="field" htmlFor={ids.sender}><span>{t('brevo.editor.sender')}</span><select id={ids.sender} value={body.sender_id} onChange={(event) => setBody({ ...body, sender_id: Number(event.target.value) })}>{senders.map((item) => <option key={item.id} value={item.id}>{item.name} &lt;{item.email}&gt;</option>)}</select></label>}
    <fieldset className="bvLists" aria-describedby={ids.lists}>
      <legend>{t('brevo.editor.lists')}</legend>
      <small id={ids.lists} className="muted">{t('brevo.editor.listsHint', { count: body.list_ids.length })}</small>
      {lists.length === 0 ? <Note error>{t('brevo.editor.noLists')}</Note> : <>
        {lists.length > 8 && <label className="field" htmlFor={ids.listFilter}><span className="visuallyHidden">{t('brevo.editor.findList')}</span><input id={ids.listFilter} type="search" placeholder={t('brevo.editor.findList')} value={listFilter} onChange={(event) => setListFilter(event.target.value)} /></label>}
        <ul className="bvListPick">{shownLists.map((item) => <li key={item.id}><label><input type="checkbox" checked={body.list_ids.includes(item.id)} onChange={() => toggle(item.id)} /> <span dir="auto">{item.name}</span> <small className="muted">{t('brevo.editor.subscribers', { count: formatNumber(item.subscribers) })}</small></label></li>)}</ul>
        {lists.length < listTotal && <button type="button" className="secondary" onClick={() => void moreLists()}>{t('brevo.editor.moreLists')}</button>}
      </>}
    </fieldset>
    <label className="field" htmlFor={ids.html}><span>{t('brevo.editor.html')}</span><textarea id={ids.html} rows={10} dir="ltr" spellCheck={false} value={body.html} onChange={(event) => setBody({ ...body, html: event.target.value })} /></label>
    <small className="muted">{t('brevo.editor.htmlHint')}</small>
    {body.html.trim() && <><h4>{t('brevo.drafts.previewTitle')}</h4><iframe className="bvFrame" title={t('brevo.drafts.frameTitle')} sandbox="" referrerPolicy="no-referrer" srcDoc={preview} /></>}
    {problem && <Note error>{problem}</Note>}
    <div className="actions mkInline"><button type="submit" disabled={busy || senders.length === 0}>{busy ? t('common.loading') : draft ? t('brevo.editor.save') : t('brevo.editor.create')}</button></div>
    <p className="muted bvSmall">{t('brevo.editor.nothingSent')}</p>
  </form>;
}
