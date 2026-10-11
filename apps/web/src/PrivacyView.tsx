import { useCallback, useEffect, useId, useState } from 'react';
import { CircleDot, Download, ExternalLink, RefreshCw, ShieldAlert, Trash2 } from 'lucide-react';
import {
  PERMISSION_MESSAGE, confirmationMatches, deletedSentence, formatBytes, privacyErrorMessage, privateRows, providerSentence,
  retentionRows, sharedRows, totalOf, type DeleteResult, type PrivacySummary,
} from './lib/privacy';
import * as privacyApi from './lib/privacyApi';
import './privacy.css';

type Load = { kind: 'loading' } | { kind: 'ready'; summary: PrivacySummary } | { kind: 'denied' } | { kind: 'error'; message: string };
type Outcome = { tone: 'ok' | 'error'; text: string } | null;

// Settings -> Privacy (docs/privacy.md): what ANUM stores about the caller in this workspace,
// how long it is kept, where messages are sent, and the two rights actions: download and delete.
export function PrivacyView() {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [exporting, setExporting] = useState(false), [exported, setExported] = useState<Outcome>(null);
  const [typed, setTyped] = useState(''), [deleting, setDeleting] = useState(false), [deleted, setDeleted] = useState<Outcome>(null);
  const [lastDelete, setLastDelete] = useState<DeleteResult | null>(null);
  const confirmId = useId(), confirmHelp = useId();

  const refresh = useCallback(async () => {
    try { setLoad({ kind: 'ready', summary: await privacyApi.getPrivacySummary() }); }
    catch (e) {
      const status = (e as { status?: number }).status;
      setLoad(status === 403 ? { kind: 'denied' } : { kind: 'error', message: privacyErrorMessage(e, 'load') });
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  async function download() {
    setExporting(true); setExported(null);
    try {
      const file = await privacyApi.downloadMyData();
      setExported({ tone: 'ok', text: `Downloaded ${file.name} (${formatBytes(file.bytes)}).` });
    } catch (e) { setExported({ tone: 'error', text: privacyErrorMessage(e, 'export') }); }
    finally { setExporting(false); }
  }

  async function remove() {
    setDeleting(true); setDeleted(null);
    try {
      const result = await privacyApi.deleteMyData(typed.trim());
      setLastDelete(result); setTyped('');
      setDeleted({ tone: 'ok', text: deletedSentence(result) });
      await refresh();
    } catch (e) { setDeleted({ tone: 'error', text: privacyErrorMessage(e, 'delete') }); }
    finally { setDeleting(false); }
  }

  if (load.kind === 'loading') return <p className="notice" role="status" aria-live="polite"><CircleDot aria-hidden="true"/>Loading your privacy summary...</p>;
  if (load.kind === 'denied') return <section className="surface settingsForm privacyBlock" aria-labelledby="privacy-denied"><h2 id="privacy-denied">Privacy</h2><p className="notice errorNotice" role="alert"><ShieldAlert aria-hidden="true"/>{PERMISSION_MESSAGE}</p></section>;
  if (load.kind === 'error') return <section className="surface settingsForm privacyBlock" aria-labelledby="privacy-error"><h2 id="privacy-error">Privacy</h2><p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true"/>{load.message}</p><div className="actions"><button className="secondary" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}><RefreshCw aria-hidden="true"/>Try again</button></div></section>;

  const summary = load.summary, phrase = summary.delete_confirmation;
  const privateTotal = totalOf(summary.private), sharedTotal = totalOf(summary.shared);
  const ready = confirmationMatches(typed, phrase);

  return <div className="settingsStack privacyStack">
    <section className="surface settingsForm privacyBlock" aria-labelledby="privacy-stored">
      <div className="sectionHeader"><h2 id="privacy-stored">What ANUM stores about you</h2><span className="statusBadge neutral">{summary.role ? `${summary.role} · ` : ''}{summary.workspace_id}</span></div>
      <p className="muted">Signed in as <code>{summary.user_id}</code>. Everything below is in this workspace only; other workspaces keep their own data.</p>
      <div className="privacyColumns">
        <div>
          <h3>Private to you</h3>
          <dl className="privacyCounts">{privateRows(summary.private).map(row => <div key={row.key}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>
          {privateTotal === 0 && <p className="muted">Nothing private is stored for you here yet. Chats, voice sessions and memories from your chats will appear here.</p>}
        </div>
        <div>
          <h3>Shared with the workspace</h3>
          <dl className="privacyCounts">{sharedRows(summary.shared).map(row => <div key={row.key}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>
          <p className="muted">{summary.shared_note}</p>
        </div>
      </div>
    </section>

    <section className="surface settingsForm privacyBlock" aria-labelledby="privacy-retention">
      <h2 id="privacy-retention">How long it is kept, and where it goes</h2>
      <dl className="privacyRetention">{retentionRows(summary).map(row => <div key={row.label}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>
      <p className="muted">{providerSentence(summary)}</p>
      <p className="privacyPolicy">{summary.privacy_policy_url
        ? <a href={summary.privacy_policy_url} target="_blank" rel="noreferrer noopener">Read the privacy policy<ExternalLink aria-hidden="true"/></a>
        : <span className="muted">Your organization has not published a privacy policy link yet. Ask the workspace owner for it.</span>}</p>
    </section>

    <div className="privacyActions">
    <section className="surface settingsForm privacyBlock" aria-labelledby="privacy-download">
      <h2 id="privacy-download">Download my data</h2>
      <p className="muted">A JSON file with your profile in this workspace, conversations, private memories, voice transcripts, tasks you created, approvals you decided, notification preferences and the list of files you uploaded (with download links). Up to {formatBytes(summary.export_max_bytes)}.</p>
      <div className="actions"><button onClick={() => void download()} disabled={exporting} aria-busy={exporting}><Download aria-hidden="true"/>{exporting ? 'Preparing download...' : 'Download my data'}</button></div>
      {exported && <p className={`notice ${exported.tone === 'error' ? 'errorNotice' : ''}`} role={exported.tone === 'error' ? 'alert' : 'status'}><CircleDot aria-hidden="true"/>{exported.text}</p>}
    </section>

    <section className="surface settingsForm privacyBlock privacyDanger" aria-labelledby="privacy-delete">
      <h2 id="privacy-delete">Delete my data</h2>
      <p className="muted">Permanently deletes your conversations, private memories (and their search index entries), voice sessions and transcripts, and notification preferences in this workspace. This cannot be undone. Download your data first if you want a copy.</p>
      <ul className="privacyKept">
        <li><strong>Kept:</strong> {sharedTotal ? `${sharedTotal} shared workspace ${sharedTotal === 1 ? 'record' : 'records'} you created or decided (tasks, approvals, shared memories, files).` : 'shared workspace records (none created by you).'} A workspace owner can remove them.</li>
        <li><strong>Your account:</strong> {summary.account_note}</li>
      </ul>
      <label className="field" htmlFor={confirmId}><span>Type <code>{phrase}</code> to confirm</span>
        <input id={confirmId} value={typed} onChange={e => setTyped(e.target.value)} autoComplete="off" spellCheck={false} aria-describedby={confirmHelp} disabled={deleting}/></label>
      <p id={confirmHelp} className="muted">{privateTotal ? `${privateTotal} private ${privateTotal === 1 ? 'item' : 'items'} will be deleted.` : 'Nothing private is stored for you right now.'}</p>
      <div className="actions"><button className="danger" disabled={!ready || deleting} aria-busy={deleting} onClick={() => void remove()}><Trash2 aria-hidden="true"/>{deleting ? 'Deleting...' : 'Delete my data'}</button></div>
      {deleted && <p className={`notice ${deleted.tone === 'error' ? 'errorNotice' : ''}`} role={deleted.tone === 'error' ? 'alert' : 'status'}><CircleDot aria-hidden="true"/>{deleted.text}{lastDelete && deleted.tone === 'ok' ? ` ${lastDelete.account_note}` : ''}</p>}
    </section>
    </div>
  </div>;
}
