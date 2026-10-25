import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Check, Download, FileText, ListChecks, Send, Trash2, X } from 'lucide-react';
import { MessageMarkdown } from './MessageMarkdown';
import * as api from './lib/api';
import { t, type MessageKey } from './lib/i18n';
import { isModelDraft, type OutputKind, type RoomMessage, type RoomOutput, type RoomState, type WorkKind, type WorkLink } from './lib/liveMeeting';
import { DecisionLinks } from './MeetingSummaryParts';

export interface Draft { kind: OutputKind; body: string; seq: number | null; label?: string }

export function endedNote(reason: string | null): string {
  if (!reason) return '';
  const key = `meetingRoom.ended.${reason}` as MessageKey;
  const text = t(key);
  return text === key ? '' : text;
}

export function EndButton({ onEnd, disabled }: { onEnd: () => void; disabled: boolean }) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <button type="button" className="secondary" disabled={disabled} onClick={() => setAsking(true)}>{t('meetingRoom.end')}</button>;
  return <span className="mrRow"><button type="button" className="danger" disabled={disabled} onClick={() => { setAsking(false); onEnd(); }}>{t('meetingRoom.endConfirm')}</button>
    <button type="button" className="secondary" onClick={() => setAsking(false)}>{t('meetingRoom.cancel')}</button></span>;
}

export function Meter({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return <div className="mrMeter"><span>{label}</span><progress value={pct} max={100} aria-label={label} className={pct >= 90 ? 'mrNear' : undefined} /></div>;
}

export function Transcript({ state, who, time, deleted, isOwner, live, speakingNames, notices, canDraft, onDraft }: {
  state: RoomState; who: (id: string) => string; time: (iso: string) => string; deleted: boolean; isOwner: boolean; live: boolean;
  speakingNames: string[]; notices: string[]; canDraft: boolean; onDraft: (kind: OutputKind, seq: number, body: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => { const el = box.current; if (el && stick.current) el.scrollTop = el.scrollHeight; }, [state.messages.length, speakingNames.length]);
  const onScroll = () => { const el = box.current; if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; };
  if (deleted) return <p className="notice" role="note">{t('meetingRoom.transcriptDeleted')}</p>;
  return <div className="mrLog" ref={box} onScroll={onScroll} role="log" aria-live="polite" aria-relevant="additions" tabIndex={0} aria-label={t('meetingRoom.transcript')}>
    {state.messages.length === 0 && speakingNames.length === 0 && <p className="muted mrEmpty">{isOwner && live ? t('meetingRoom.transcriptEmptyOwner') : t('meetingRoom.transcriptEmpty')}</p>}
    {state.messages.map((m: RoomMessage) => <article key={m.sequence} id={`mr-msg-${m.sequence}`} tabIndex={-1} className={`mrMsg mrMsg-${m.role}`}>
      <header><strong dir="auto">{who(m.speaker_id)}</strong>
        <span className="mrRole">{m.role === 'human' ? t('meetingRoom.human') : t('meetingRoom.agent')}</span>
        <span className="muted">#{m.sequence} {time(m.created_at)}</span></header>
      <MessageMarkdown text={m.body} />
      {m.redacted && <small className="muted">{t('meetingRoom.redacted')}</small>}
      {canDraft && <div className="mrRow mrMsgActions">
        <button type="button" className="secondary" onClick={() => onDraft('decision', m.sequence, m.body)}>{t('meetingRoom.draftDecision')}</button>
        <button type="button" className="secondary" onClick={() => onDraft('action_item', m.sequence, m.body)}>{t('meetingRoom.draftAction')}</button></div>}
    </article>)}
    {speakingNames.map((name) => <p key={name} className="mrTyping" role="status"><span className="mrPulse" aria-hidden="true" />{t('meetingRoom.speaking', { name })}</p>)}
    {notices.map((reason, i) => <p key={i} className="notice errorNotice" role="status">{t('meetingRoom.turnFailed', { reason })}</p>)}
  </div>;
}

export function Composer({ onSend, disabled }: { onSend: (text: string) => Promise<void>; disabled: boolean }) {
  const [text, setText] = useState('');
  const send = async () => { const body = text.trim(); if (!body) return; await onSend(body); setText(''); };
  const key = (e: KeyboardEvent) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void send(); } };
  return <form className="mrComposer" onSubmit={(e) => { e.preventDefault(); void send(); }}>
    <label htmlFor="mr-composer">{t('meetingRoom.composer')}</label>
    <textarea id="mr-composer" rows={2} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={key} dir="auto" />
    <div className="mrRow"><button type="submit" disabled={disabled || !text.trim()}><Send aria-hidden="true" />{t('meetingRoom.send')}</button><span className="muted mrHint">{t('meetingRoom.composerHint')}</span></div>
  </form>;
}

export function AgendaCard({ state, owner, busy, locked, onAdd, onState, onRemove }: {
  state: RoomState; owner: boolean; busy: boolean; locked: boolean; onAdd: (title: string) => Promise<void>;
  onState: (id: string, s: 'open' | 'current' | 'done') => Promise<void>; onRemove: (id: string) => Promise<void>;
}) {
  const [title, setTitle] = useState('');
  const edit = owner && !locked;
  return <section aria-labelledby="mr-agenda" className="mrCard"><h3 id="mr-agenda"><ListChecks aria-hidden="true" />{t('meetingRoom.agenda')}</h3>
    {state.agenda.length === 0 ? <p className="muted">{t('meetingRoom.agendaEmpty')}</p> :
      <ol className="mrAgenda">{state.agenda.map((a) => <li key={a.id} className={`mrAgenda-${a.state}`}>
        <span dir="auto">{a.title}</span> <span className="mrChip">{t(a.state === 'current' ? 'meetingRoom.agendaCurrent' : a.state === 'done' ? 'meetingRoom.agendaDone' : 'meetingRoom.agendaOpen')}</span>
        {edit && <span className="mrRow">
          {a.state !== 'current' && <button type="button" className="secondary" disabled={busy} onClick={() => void onState(a.id, 'current')}>{t('meetingRoom.agendaMarkCurrent')}</button>}
          {a.state !== 'done' && <button type="button" className="secondary" disabled={busy} onClick={() => void onState(a.id, 'done')}>{t('meetingRoom.agendaMarkDone')}</button>}
          <button type="button" className="secondary mrIcon" aria-label={t('meetingRoom.agendaRemove', { title: a.title })} disabled={busy} onClick={() => void onRemove(a.id)}><X aria-hidden="true" /></button></span>}
      </li>)}</ol>}
    {edit && <form className="mrRow" onSubmit={(e) => { e.preventDefault(); const v = title.trim(); if (v) void onAdd(v).then(() => setTitle('')); }}>
      <label className="mrSr" htmlFor="mr-agenda-new">{t('meetingRoom.agendaAdd')}</label>
      <input id="mr-agenda-new" value={title} maxLength={200} placeholder={t('meetingRoom.agendaPlaceholder')} onChange={(e) => setTitle(e.target.value)} dir="auto" />
      <button type="submit" disabled={busy || !title.trim()}>{t('meetingRoom.agendaAdd')}</button></form>}
  </section>;
}

export function OutputsCard({ state, owner, busy, draft, setDraft, onSave, onDecide, onTask, onLink, onUnlink }: {
  state: RoomState; owner: boolean; busy: boolean; draft: Draft | null; setDraft: (d: Draft | null) => void;
  onSave: (d: Draft) => Promise<void>; onDecide: (o: RoomOutput, s: 'confirmed' | 'dismissed') => Promise<void>; onTask: (o: RoomOutput) => Promise<void>;
  onLink: (o: RoomOutput, kind: WorkKind, id: string) => Promise<void>; onUnlink: (link: WorkLink) => void;
}) {
  const items = state.outputs.filter((o) => o.kind !== 'summary');
  const jump = (seq: number) => { const el = document.getElementById(`mr-msg-${seq}`); el?.scrollIntoView({ block: 'nearest' }); el?.focus?.(); };
  return <section aria-labelledby="mr-outputs" className="mrCard"><h3 id="mr-outputs"><Check aria-hidden="true" />{t('meetingRoom.outputs')}</h3>
    {items.length === 0 && !draft && <p className="muted">{t('meetingRoom.outputsEmpty')}</p>}
    <ul className="mrOutputs">{items.map((o) => <li key={o.id} className={`mrOut mrOut-${o.state}`}>
      <div className="mrRow"><span className="mrChip">{t(`meetingRoom.kind.${o.kind}` as MessageKey)}</span><span className={`mrChip mrState-${o.state}`}>{t(`meetingRoom.state.${o.state}` as MessageKey)}</span>
        {isModelDraft(o) && <span className="mrChip">{t('meetingRoom.modelDraft')}</span>}</div>
      <p dir="auto">{o.body}</p>
      {o.owner_label && <small className="muted" dir="auto">{o.owner_label}</small>}
      {o.source_sequences.length > 0 && <div className="mrRow">{o.source_sequences.map((n) => <button key={n} type="button" className="secondary mrSrc" onClick={() => jump(n)}>{t('meetingRoom.source', { number: n })}</button>)}</div>}
      {owner && !o.task_id && <div className="mrRow">
        {o.state === 'draft' && <><button type="button" disabled={busy} onClick={() => void onDecide(o, 'confirmed')}>{t('meetingRoom.confirm')}</button>
          <button type="button" className="secondary" disabled={busy} onClick={() => void onDecide(o, 'dismissed')}>{t('meetingRoom.dismiss')}</button></>}
        {o.kind === 'action_item' && o.state === 'confirmed' && <button type="button" disabled={busy} onClick={() => void onTask(o)}>{t('meetingRoom.createTask')}</button>}</div>}
      {o.task_id && <p className="mrRow"><span className="mrChip mrState-confirmed">{t('meetingRoom.taskCreated')}</span><a href="#tasks">{t('meetingRoom.openTasks')}</a></p>}
      <DecisionLinks output={o} links={state.links} owner={owner} busy={busy} onLink={(kind, id) => onLink(o, kind, id)} onUnlink={onUnlink} />
    </li>)}</ul>
    {owner && items.some((o) => o.kind === 'action_item' && o.state === 'confirmed' && !o.task_id) && <p className="muted mrHint">{t('meetingRoom.taskNote')}</p>}
    {owner && !state.transcriptDeleted && !draft && <div className="mrRow">
      <button type="button" className="secondary" onClick={() => setDraft({ kind: 'decision', body: '', seq: null })}>{t('meetingRoom.draftDecision')}</button>
      <button type="button" className="secondary" onClick={() => setDraft({ kind: 'action_item', body: '', seq: null })}>{t('meetingRoom.draftAction')}</button></div>}
    {owner && draft && <form className="mrDraft" onSubmit={(e) => { e.preventDefault(); if (draft.body.trim()) void onSave(draft); }}>
      <h4>{t(draft.kind === 'decision' ? 'meetingRoom.draftDecision' : 'meetingRoom.draftAction')}</h4>
      {draft.seq && <small className="muted">{t('meetingRoom.draftSources', { numbers: `#${draft.seq}` })}</small>}
      <label htmlFor="mr-draft-text">{t('meetingRoom.draftText')}</label>
      <textarea id="mr-draft-text" rows={3} maxLength={2000} value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} dir="auto" required />
      {draft.kind === 'action_item' && <><label htmlFor="mr-draft-owner">{t('meetingRoom.draftOwner')}</label>
        <input id="mr-draft-owner" maxLength={160} value={draft.label ?? ''} onChange={(e) => setDraft({ ...draft, label: e.target.value })} dir="auto" /></>}
      <div className="mrRow"><button type="submit" disabled={busy || !draft.body.trim()}>{t('meetingRoom.draftSave')}</button>
        <button type="button" className="secondary" onClick={() => setDraft(null)}>{t('meetingRoom.cancel')}</button></div>
    </form>}
  </section>;
}

export function FilesCard({ state, owner, busy, onLink, onUnlink }: { state: RoomState; owner: boolean; busy: boolean; onLink: (id: string) => Promise<void>; onUnlink: (id: string) => Promise<void> }) {
  const [options, setOptions] = useState<{ id: string; name: string }[] | null>(null);
  const [picking, setPicking] = useState(false);
  useEffect(() => {
    if (!picking || options) return;
    let live = true;
    void api.getFiles({ limit: 50 }).then((files) => live && setOptions(files.map((f) => ({ id: f.id, name: f.name }))), () => live && setOptions([]));
    return () => { live = false; };
  }, [picking, options]);
  const linked = new Set(state.files.map((f) => f.file_id));
  const free = (options ?? []).filter((o) => !linked.has(o.id));
  return <section aria-labelledby="mr-files" className="mrCard"><h3 id="mr-files"><FileText aria-hidden="true" />{t('meetingRoom.files')}</h3>
    {state.files.length === 0 ? <p className="muted">{t('meetingRoom.filesEmpty')}</p> :
      <ul className="mrPlain">{state.files.map((f) => <li key={f.file_id}><a href="#files" dir="auto">{f.name ?? f.file_id}</a>
        {owner && <button type="button" className="secondary mrIcon" aria-label={t('meetingRoom.fileUnlink', { name: f.name ?? f.file_id })} disabled={busy} onClick={() => void onUnlink(f.file_id)}><X aria-hidden="true" /></button>}</li>)}</ul>}
    {owner && !picking && <button type="button" className="secondary" onClick={() => setPicking(true)}>{t('meetingRoom.fileLink')}</button>}
    {owner && picking && (options === null ? <p className="muted" role="status">{t('meetingRoom.busy')}</p> : free.length === 0 ? <p className="muted">{t('meetingRoom.fileNone')}</p> :
      <div className="mrRow"><label className="mrSr" htmlFor="mr-file-pick">{t('meetingRoom.fileChoose')}</label>
        <select id="mr-file-pick" defaultValue="" onChange={(e) => { if (e.target.value) void onLink(e.target.value).then(() => setPicking(false)); }}>
          <option value="" disabled>{t('meetingRoom.fileChoose')}</option>{free.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select></div>)}
  </section>;
}

export function RecordCard({ terminal, deleted, busy, onExport, onExportJson, onDelete }: { terminal: boolean; deleted: boolean; busy: boolean; onExport: () => Promise<void>; onExportJson: () => Promise<void>; onDelete: () => Promise<void> }) {
  const [asking, setAsking] = useState(false);
  return <section aria-labelledby="mr-record" className="mrCard"><h3 id="mr-record">{t('meetingRoom.record')}</h3>
    <div className="mrRow"><button type="button" className="secondary" disabled={busy} onClick={() => void onExport()}><Download aria-hidden="true" />{t('meetingRoom.export')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => void onExportJson()}><Download aria-hidden="true" />{t('meetingRoom.exportJson')}</button></div>
    <p className="muted mrHint">{t('meetingRoom.exportJsonHint')}</p>
    {!deleted && (terminal
      ? (asking ? <div className="mrDraft"><p className="muted">{t('meetingRoom.deleteNote')}</p><div className="mrRow">
          <button type="button" className="danger" disabled={busy} onClick={() => { setAsking(false); void onDelete(); }}>{t('meetingRoom.deleteConfirm')}</button>
          <button type="button" className="secondary" onClick={() => setAsking(false)}>{t('meetingRoom.cancel')}</button></div></div>
        : <div className="mrRow"><button type="button" className="secondary" disabled={busy} onClick={() => setAsking(true)}><Trash2 aria-hidden="true" />{t('meetingRoom.deleteTranscript')}</button></div>)
      : <p className="muted mrHint">{t('meetingRoom.deleteLocked')}</p>)}
  </section>;
}
