// Supporting documents in the business books (docs/business-books.md#supporting-documents-and-evidence): the attachment panel of
// a bill, an adjustment or a manual journal entry, the documents shown in the journal drill-down, and the evidence pack export.
// Documents live in the Document vault; attaching never copies a file and never posts anything.
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent } from 'react';
import { Download, FileArchive, FileText, Paperclip, Search, X } from 'lucide-react';
import type { BooksEvidenceDocument, BooksEvidenceSource, BooksSetup, VaultDocumentRow } from '@anum/contracts';
import {
  attachEvidence, attachableDocuments, detachEvidence, documentHref, downloadPack, listEvidence, offerable, packProblem,
} from './lib/businessEvidence';
import { isPermissionDenied, describeError } from './lib/errors';
import { categoryLabel as vaultCategory } from './lib/vault';
import { t, type MessageKey } from './lib/i18n';
import { Modal } from './Dialog';
import { Badge, Note, day, failure } from './BusinessParts';

type PanelState = { kind: 'loading' } | { kind: 'denied'; message: string } | { kind: 'error'; message: string }
  | { kind: 'ready'; documents: BooksEvidenceDocument[]; missing: boolean; canAttach: boolean };

/** The documents attached to one record, with attach and remove (docs/business-books.md#supporting-documents-and-evidence). */
export function EvidencePanel({ sourceType, sourceId, onChanged }: { sourceType: BooksEvidenceSource; sourceId: string; onChanged?: () => void }) {
  const [state, setState] = useState<PanelState>({ kind: 'loading' });
  const [picking, setPicking] = useState(false), [busy, setBusy] = useState(''), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const headingId = useId();
  const load = useCallback(async () => {
    try {
      const page = await listEvidence(sourceType, [sourceId]);
      const item = page.items[sourceId] ?? { documents: [], evidence_missing: false };
      setState({ kind: 'ready', documents: item.documents, missing: item.evidence_missing, canAttach: page.can_attach });
    } catch (caught) {
      setState(isPermissionDenied(caught) ? { kind: 'denied', message: describeError(caught) } : { kind: 'error', message: describeError(caught) });
    }
  }, [sourceType, sourceId]);
  useEffect(() => { void load(); }, [load]);
  async function remove(document: BooksEvidenceDocument) {
    setBusy(document.link_id); setMessage(null);
    try { await detachEvidence(sourceType, sourceId, document.link_id); setMessage({ text: t('books.evidence.removed') }); await load(); onChanged?.(); }
    catch (caught) { setMessage({ text: failure(caught, t('books.evidence.remove')), error: true }); } finally { setBusy(''); }
  }
  return <section className="bzBlock bkEvidence" aria-labelledby={headingId}>
    <h3 id={headingId}><Paperclip aria-hidden="true" className="bkInlineIcon" /> {t('books.evidence.title')}
      {state.kind === 'ready' && state.missing && <> <Badge tone="warning">{t('books.evidence.missing')}</Badge></>}</h3>
    {state.kind === 'loading' && <p className="muted">{t('books.reading')}</p>}
    {state.kind === 'denied' && <p className="muted">{t('books.evidence.denied')}</p>}
    {state.kind === 'error' && <Note error>{state.message} <button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></Note>}
    {state.kind === 'ready' && <>
      {state.documents.length === 0
        ? <p className="muted">{t(state.missing ? 'books.evidence.missingText' : 'books.evidence.none')}</p>
        : <ul className="bkList">{state.documents.map((doc) => <li key={doc.link_id}>
          <span><a href={documentHref(doc.document_id)} dir="auto"><FileText aria-hidden="true" className="bkInlineIcon" /> {doc.title}</a>
            <small>{doc.category.startsWith('vcat_') ? t('books.evidence.ownCategory') : vaultCategory(doc.category)} · {day(doc.added_at)}</small></span>
          {state.canAttach && <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void remove(doc)}
            aria-label={t('books.evidence.removeLabel', { title: doc.title })}><X aria-hidden="true" />{t('books.evidence.remove')}</button>}
        </li>)}</ul>}
      {state.canAttach
        ? <div className="bzInline"><button type="button" className="secondary" onClick={() => setPicking(true)}><Paperclip aria-hidden="true" />{t('books.evidence.attach')}</button></div>
        : <p className="muted bzHint">{t(sourceType === 'bill' || sourceType === 'invoice' ? 'books.evidence.whoBills' : 'books.evidence.whoJournal')}</p>}
    </>}
    {message && <Note error={message.error}>{message.text}</Note>}
    {picking && state.kind === 'ready' && <PickDialog attached={state.documents.map((d) => d.document_id)} onClose={() => setPicking(false)}
      onPick={async (doc) => { await attachEvidence(sourceType, sourceId, doc.id); setPicking(false); setMessage({ text: t('books.evidence.attached', { title: doc.title }) }); await load(); onChanged?.(); }} />}
  </section>;
}

function PickDialog({ attached, onClose, onPick }: { attached: string[]; onClose: () => void; onPick: (doc: VaultDocumentRow) => Promise<void> }) {
  const [q, setQ] = useState(''), [applied, setApplied] = useState('');
  const [rows, setRows] = useState<VaultDocumentRow[] | null>(null), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState('');
  const searchId = useId();
  const load = useCallback(async () => {
    setError(null);
    try { setRows(offerable((await attachableDocuments(applied)).items, attached)); } catch (caught) { setError(failure(caught, t('books.evidence.attach'))); setRows([]); }
  }, [applied, attached]);
  useEffect(() => { void load(); }, [load]);
  const submit = (event: FormEvent) => { event.preventDefault(); setApplied(q); };
  async function pick(doc: VaultDocumentRow) {
    setBusy(doc.id); setError(null);
    try { await onPick(doc); } catch (caught) { setError(failure(caught, t('books.evidence.attach'))); } finally { setBusy(''); }
  }
  return <Modal title={t('books.evidence.pickTitle')} onClose={onClose}>
    <p className="muted">{t('books.evidence.pickHelp')}</p>
    <form className="bzSearch" role="search" onSubmit={submit}>
      <label htmlFor={searchId} className="visuallyHidden">{t('books.evidence.search')}</label>
      <input id={searchId} type="search" value={q} placeholder={t('books.evidence.search')} onChange={(event) => setQ(event.target.value)} />
      <button type="submit" className="secondary"><Search aria-hidden="true" />{t('books.evidence.searchAction')}</button>
    </form>
    {error && <Note error>{error}</Note>}
    {rows === null && <p className="muted">{t('books.reading')}</p>}
    {rows !== null && rows.length === 0 && !error && <p className="muted">{t(applied ? 'books.evidence.noMatch' : 'books.evidence.noDocuments')}{' '}
      <a href="#vault">{t('books.evidence.openVault')}</a></p>}
    {rows !== null && rows.length > 0 && <ul className="bkList bkPick">{rows.map((doc) => <li key={doc.id}>
      <span><span dir="auto">{doc.title}</span><small>{doc.file?.name ?? ''}</small></span>
      <button type="button" disabled={Boolean(busy)} onClick={() => void pick(doc)}>{busy === doc.id ? t('dialog.working') : t('books.evidence.choose')}</button></li>)}</ul>}
  </Modal>;
}

/** The documents of one journal entry in the drill-down (links into the vault). */
export function EntryEvidence({ documents, source }: { documents: BooksEvidenceDocument[] | undefined; source: { type: BooksEvidenceSource; id: string } | null | undefined }) {
  if (!source) return null;
  if (!documents || documents.length === 0) return <p className="muted bkEntryEvidence"><Paperclip aria-hidden="true" className="bkInlineIcon" /> {t('books.evidence.noneShort')}</p>;
  return <p className="bkEntryEvidence"><Paperclip aria-hidden="true" className="bkInlineIcon" /> <span className="visuallyHidden">{t('books.evidence.title')}: </span>
    {documents.map((doc, index) => <span key={doc.link_id}>{index > 0 ? ', ' : ''}<a href={documentHref(doc.document_id)} dir="auto">{doc.title}</a></span>)}</p>;
}

/** The evidence pack export: a CSV index of every entry with its documents, or a zip with the files you may read. */
export function EvidencePackCard({ setup }: { setup: BooksSetup }) {
  const [span, setSpan] = useState<{ from: string; to: string }>({ from: '', to: '' });
  const [busy, setBusy] = useState<'' | 'csv' | 'zip'>(''), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const ids = { from: useId(), to: useId(), heading: useId() };
  const allowed = Boolean(setup.permissions.account ?? setup.permissions.manage);
  const problem = packProblem(span);
  async function run(kind: 'csv' | 'zip') {
    setBusy(kind); setMessage(null);
    try { await downloadPack(kind, span); setMessage({ text: t('books.evidence.pack.done') }); }
    catch (caught) { setMessage({ text: failure(caught, t('books.evidence.pack.title')), error: true }); } finally { setBusy(''); }
  }
  return <section className="surface bzCard" aria-labelledby={ids.heading}>
    <h2 id={ids.heading}><FileArchive aria-hidden="true" className="bkInlineIcon" /> {t('books.evidence.pack.title')}</h2>
    <p className="muted bzHint">{t('books.evidence.pack.help')}</p>
    {!allowed ? <Note>{t('books.evidence.pack.who')}</Note> : <>
      <div className="bzToolbar">
        <label className="bzCompact" htmlFor={ids.from}><span>{t('books.from')}</span><input id={ids.from} type="date" value={span.from} max={span.to || undefined}
          onChange={(event) => setSpan({ ...span, from: event.target.value })} /></label>
        <label className="bzCompact" htmlFor={ids.to}><span>{t('books.to')}</span><input id={ids.to} type="date" value={span.to} min={span.from || undefined}
          onChange={(event) => setSpan({ ...span, to: event.target.value })} /></label>
        <div className="bzToolbarEnd bzInline">
          <button type="button" className="secondary" disabled={Boolean(busy) || Boolean(problem)} onClick={() => void run('csv')}><Download aria-hidden="true" />
            {busy === 'csv' ? t('dialog.working') : t('books.evidence.pack.csv')}</button>
          <button type="button" disabled={Boolean(busy) || Boolean(problem)} onClick={() => void run('zip')}><FileArchive aria-hidden="true" />
            {busy === 'zip' ? t('dialog.working') : t('books.evidence.pack.zip')}</button>
        </div>
      </div>
      {problem && <Note error>{t(problem as MessageKey)}</Note>}
      <p className="muted bzHint">{t('books.evidence.pack.default')}</p>
    </>}
    {message && <Note error={message.error}>{message.text}</Note>}
  </section>;
}

/** Which records of a page need a supporting document (above the threshold, nothing attached). Quietly empty when the caller
 * cannot read the vault or the request fails: the flag is a hint, and each record's panel says why. */
export function useEvidenceFlags(sourceType: 'bill' | 'adjustment', ids: string[], tick: number): Record<string, boolean> {
  const [flags, setFlags] = useState<Record<string, boolean>>({});
  const key = useMemo(() => ids.join(','), [ids]);
  useEffect(() => {
    let live = true;
    const wanted = key ? key.split(',') : [];
    if (!wanted.length) { setFlags({}); return; }
    const chunks: string[][] = [];
    for (let i = 0; i < wanted.length; i += 100) chunks.push(wanted.slice(i, i + 100));
    Promise.all(chunks.map((chunk) => listEvidence(sourceType, chunk)))
      .then((pages) => { if (live) setFlags(Object.fromEntries(pages.flatMap((page) => Object.entries(page.items).map(([id, item]) => [id, item.evidence_missing])))); })
      .catch(() => { if (live) setFlags({}); });
    return () => { live = false; };
  }, [sourceType, key, tick]);
  return flags;
}
