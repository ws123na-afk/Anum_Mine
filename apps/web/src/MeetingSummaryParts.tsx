import { useEffect, useState } from 'react';
import { FileText, Link2, Sparkles, X } from 'lucide-react';
import * as api from './lib/api';
import { describeError } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { citedMessages, liveSummary, linksOf, workIndex, type RoomOutput, type RoomState, type WorkKind, type WorkLink } from './lib/liveMeeting';
import { meetingRoomApi } from './lib/liveMeetingApi';

const jump = (seq: number) => { const el = document.getElementById(`mr-msg-${seq}`); el?.scrollIntoView({ block: 'nearest' }); el?.focus?.(); };

/** The governed summary: drafted by one metered model call after the meeting ends, confirmed by the owner with a click. */
export function SummaryCard({ state, terminal, busy, drafting, blocked, error, onDraft, onConfirm, onDismiss }: {
  state: RoomState; terminal: boolean; busy: boolean; drafting: boolean; blocked: boolean; error: string;
  onDraft: () => void; onConfirm: (o: RoomOutput) => void; onDismiss: (o: RoomOutput) => void;
}) {
  const summary = liveSummary(state.outputs);
  const noMessages = state.messages.length === 0;
  const lines = summary ? summary.body.split('\n').filter((l) => l.trim()) : [];
  const can = terminal && !state.transcriptDeleted && !noMessages;
  return <section aria-labelledby="mr-summary" className="mrCard" data-testid="meeting-summary">
    <h3 id="mr-summary"><Sparkles aria-hidden="true" />{t('meetingRoom.summary')}</h3>
    {error && <p className="notice errorNotice" role="alert">{t('meetingRoom.summaryError', { detail: error })}</p>}
    {drafting && <p className="muted" role="status">{t('meetingRoom.summaryDrafting')}</p>}
    {!summary && !drafting && <>
      {!terminal && <p className="muted">{t('meetingRoom.summaryWait')}</p>}
      {terminal && state.transcriptDeleted && <p className="muted">{t('meetingRoom.summaryDeleted')}</p>}
      {can && <p className="muted">{t('meetingRoom.summaryEmpty')}</p>}
      {can && blocked && <p className="notice errorNotice" role="status">{t('meetingRoom.summaryBudget')}</p>}
      {can && <div className="mrRow"><button type="button" disabled={busy || blocked} onClick={onDraft}><Sparkles aria-hidden="true" />{t(summaryDismissedBefore(state) ? 'meetingRoom.summaryRedraft' : 'meetingRoom.summaryDraft')}</button>
        <span className="muted mrHint">{t('meetingRoom.summaryCost')}</span></div>}
    </>}
    {summary && <div className={`mrOut mrOut-${summary.state}`}>
      <div className="mrRow"><span className={`mrChip mrState-${summary.state}`}>{t(`meetingRoom.state.${summary.state}` as MessageKey)}</span>
        <span className="mrChip">{t('meetingRoom.modelDraft')}</span>
        <span className="muted mrHint">{t('meetingRoom.summaryCovers', { count: state.messages.length })}</span></div>
      <ul className="mrSummaryLines" dir="auto">{lines.map((line, i) => <li key={i}>
        <span>{line.replace(/^-\s*/, '')}</span>
        {citedMessages(line).map((n) => <button key={n} type="button" className="secondary mrSrc" onClick={() => jump(n)}>{t('meetingRoom.source', { number: n })}</button>)}
      </li>)}</ul>
      <p className="muted mrHint">{summary.state === 'confirmed' ? t('meetingRoom.summaryConfirmed') : t('meetingRoom.summaryReview')}</p>
      {summary.state === 'draft' && <div className="mrRow">
        <button type="button" disabled={busy} onClick={() => onConfirm(summary)}>{t('meetingRoom.summaryConfirm')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => onDismiss(summary)}>{t('meetingRoom.dismiss')}</button></div>}
      {summary.state === 'confirmed' && <div className="mrRow"><button type="button" className="secondary" disabled={busy} onClick={() => onDismiss(summary)}>{t('meetingRoom.dismiss')}</button></div>}
    </div>}
  </section>;
}

const summaryDismissedBefore = (state: RoomState): boolean => state.outputs.some((o) => o.kind === 'summary' && o.state === 'dismissed');

type Option = { id: string; label: string };

/** Link a confirmed decision to a task, an approval or the latest run of a task. The list is read from the real API. */
export function LinkForm({ busy, onLink, onCancel }: { busy: boolean; onLink: (kind: WorkKind, id: string) => Promise<void>; onCancel: () => void }) {
  const [kind, setKind] = useState<WorkKind>('task');
  const [options, setOptions] = useState<Option[] | null>(null);
  const [choice, setChoice] = useState('');
  const [problem, setProblem] = useState('');
  useEffect(() => {
    let live = true;
    setOptions(null); setChoice(''); setProblem('');
    const read = kind === 'approval'
      ? api.getApprovalPage({ status: 'all', limit: 25 }).then((p) => p.items.map((a) => ({ id: a.id, label: a.action })))
      : api.getTasks().then((tasks) => tasks.slice(0, 50).map((x) => ({ id: x.id, label: x.title })));
    read.then((list) => live && setOptions(list), (e) => { if (live) { setOptions([]); setProblem(t('meetingRoom.linkLoadFailed', { detail: describeError(e) })); } });
    return () => { live = false; };
  }, [kind]);
  const submit = async () => {
    if (!choice) return;
    setProblem('');
    try {
      let target = choice;
      if (kind === 'run') {
        try { target = (await api.getLatestRun(choice)).id; } catch { setProblem(t('meetingRoom.linkRunNone')); return; }
      }
      await onLink(kind, target);
    } catch (e) { setProblem(describeError(e)); }
  };
  return <form className="mrDraft" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
    <label htmlFor="mr-link-kind">{t('meetingRoom.linkKind')}</label>
    <select id="mr-link-kind" value={kind} onChange={(e) => setKind(e.target.value as WorkKind)}>
      {(['task', 'approval', 'run'] as WorkKind[]).map((k) => <option key={k} value={k}>{t(`meetingRoom.linkKind.${k}` as MessageKey)}</option>)}</select>
    <label htmlFor="mr-link-target">{t('meetingRoom.linkChoose')}</label>
    {options === null ? <p className="muted" role="status">{t('meetingRoom.busy')}</p>
      : options.length === 0 ? <p className="muted">{t('meetingRoom.linkNone')}</p>
      : <select id="mr-link-target" value={choice} onChange={(e) => { setChoice(e.target.value); setProblem(''); }} dir="auto">
        <option value="" disabled>{t('meetingRoom.linkChoose')}</option>{options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}</select>}
    {problem && <p className="notice errorNotice" role="alert">{problem}</p>}
    <div className="mrRow"><button type="submit" disabled={busy || !choice}>{t('meetingRoom.linkSave')}</button>
      <button type="button" className="secondary" onClick={onCancel}>{t('meetingRoom.cancel')}</button></div>
  </form>;
}

/** The links of one decision, with the form to add one (owner, confirmed decisions only). */
export function DecisionLinks({ output, links, owner, busy, onLink, onUnlink }: {
  output: RoomOutput; links: WorkLink[]; owner: boolean; busy: boolean;
  onLink: (kind: WorkKind, id: string) => Promise<void>; onUnlink: (link: WorkLink) => void;
}) {
  const [adding, setAdding] = useState(false);
  const mine = linksOf(links, output.id);
  if (output.kind !== 'decision') return null;
  return <div className="mrLinks">
    {mine.length > 0 && <ul className="mrPlain" aria-label={t('meetingRoom.links')}>{mine.map((l) => <li key={l.id}>
      <span><Link2 aria-hidden="true" /><span className="mrChip">{t(`meetingRoom.linkKind.${l.target_kind}` as MessageKey)}</span> <a href={l.target_kind === 'approval' ? '#approvals' : '#tasks'} dir="auto">{l.target_label ?? l.target_id}</a>
        {l.target_status && <small className="muted"> {l.target_status}</small>}</span>
      {owner && <button type="button" className="secondary mrIcon" disabled={busy} aria-label={t('meetingRoom.linkRemove', { name: l.target_label ?? l.target_id })} onClick={() => onUnlink(l)}><X aria-hidden="true" /></button>}
    </li>)}</ul>}
    {owner && output.state === 'confirmed' && !adding && <div className="mrRow"><button type="button" className="secondary" disabled={busy} onClick={() => setAdding(true)}><Link2 aria-hidden="true" />{t('meetingRoom.linkWork')}</button></div>}
    {owner && output.state === 'draft' && <small className="muted">{t('meetingRoom.linkOnlyConfirmed')}</small>}
    {owner && adding && <LinkForm busy={busy} onCancel={() => setAdding(false)} onLink={async (kind, id) => { await onLink(kind, id); setAdding(false); }} />}
  </div>;
}

/** The other direction: the work this meeting led to, and the decision behind each piece. */
export function LinkedWorkCard({ state }: { state: RoomState }) {
  const index = workIndex(state.links, state.outputs);
  return <section aria-labelledby="mr-work" className="mrCard" data-testid="linked-work"><h3 id="mr-work"><FileText aria-hidden="true" />{t('meetingRoom.work')}</h3>
    {index.length === 0 ? <p className="muted">{t('meetingRoom.workEmpty')}</p> :
      <ul className="mrPlain">{index.map((w) => <li key={w.key}>
        <span><span className="mrChip">{t(`meetingRoom.linkKind.${w.kind}` as MessageKey)}</span> <a href={w.kind === 'approval' ? '#approvals' : '#tasks'} dir="auto">{w.label}</a></span>
        {w.decisions.map((d) => <small key={d.id} className="muted" dir="auto">{t('meetingRoom.workFrom', { text: d.body.length > 90 ? d.body.slice(0, 87) + '...' : d.body })}</small>)}
      </li>)}</ul>}
  </section>;
}
