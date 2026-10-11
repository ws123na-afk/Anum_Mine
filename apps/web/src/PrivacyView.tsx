import { useCallback, useEffect, useId, useState } from 'react';
import { CircleDot, Download, ExternalLink, RefreshCw, ShieldAlert, Trash2 } from 'lucide-react';
import {
  permissionMessage, confirmationMatches, deletedSentence, formatBytes, privacyErrorMessage, privateRows, providerSentence,
  retentionRows, sharedRows, totalOf, type DeleteResult, type PrivacySummary,
} from './lib/privacy';
import * as privacyApi from './lib/privacyApi';
import { t } from './lib/i18n';
import { rich } from './rich';
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
      setExported({ tone: 'ok', text: t('privacy.download.done', { name: file.name, size: formatBytes(file.bytes) }) });
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

  if (load.kind === 'loading') return <p className="notice" role="status" aria-live="polite"><CircleDot aria-hidden="true"/>{t('privacy.loading')}</p>;
  if (load.kind === 'denied') return <section className="surface settingsForm privacyBlock" aria-labelledby="privacy-denied"><h2 id="privacy-denied">{t('privacy.title')}</h2><p className="notice errorNotice" role="alert"><ShieldAlert aria-hidden="true"/>{permissionMessage()}</p></section>;
  if (load.kind === 'error') return <section className="surface settingsForm privacyBlock" aria-labelledby="privacy-error"><h2 id="privacy-error">{t('privacy.title')}</h2><p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true"/>{load.message}</p><div className="actions"><button className="secondary" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}><RefreshCw aria-hidden="true"/>{t('common.tryAgain')}</button></div></section>;

  const summary = load.summary, phrase = summary.delete_confirmation;
  const privateTotal = totalOf(summary.private), sharedTotal = totalOf(summary.shared);
  const ready = confirmationMatches(typed, phrase);

  return <div className="settingsStack privacyStack">
    <section className="surface settingsForm privacyBlock" aria-labelledby="privacy-stored">
      <div className="sectionHeader"><h2 id="privacy-stored">{t('privacy.stored.title')}</h2><span className="statusBadge neutral">{summary.role ? `${summary.role} · ` : ''}{summary.workspace_id}</span></div>
      <p className="muted">{rich('privacy.stored.signedInAs', { user: <code dir="ltr">{summary.user_id}</code> })}</p>
      <div className="privacyColumns">
        <div>
          <h3>{t('privacy.private.title')}</h3>
          <dl className="privacyCounts">{privateRows(summary.private).map(row => <div key={row.key}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>
          {privateTotal === 0 && <p className="muted">{t('privacy.private.empty')}</p>}
        </div>
        <div>
          <h3>{t('privacy.shared.title')}</h3>
          <dl className="privacyCounts">{sharedRows(summary.shared).map(row => <div key={row.key}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>
          <p className="muted" dir="auto">{summary.shared_note}</p>
        </div>
      </div>
    </section>

    <section className="surface settingsForm privacyBlock" aria-labelledby="privacy-retention">
      <h2 id="privacy-retention">{t('privacy.retention.title')}</h2>
      <dl className="privacyRetention">{retentionRows(summary).map(row => <div key={row.label}><dt>{row.label}</dt><dd dir="auto">{row.value}</dd></div>)}</dl>
      <p className="muted">{providerSentence(summary)}</p>
      <p className="privacyPolicy">{summary.privacy_policy_url
        ? <a href={summary.privacy_policy_url} target="_blank" rel="noreferrer noopener">{t('privacy.policyLink')}<ExternalLink aria-hidden="true" className="flipRtl"/></a>
        : <span className="muted">{t('privacy.policyMissing')}</span>}</p>
    </section>

    <div className="privacyActions">
    <section className="surface settingsForm privacyBlock" aria-labelledby="privacy-download">
      <h2 id="privacy-download">{t('privacy.download.title')}</h2>
      <p className="muted">{t('privacy.download.text', { size: formatBytes(summary.export_max_bytes) })}</p>
      <div className="actions"><button onClick={() => void download()} disabled={exporting} aria-busy={exporting}><Download aria-hidden="true"/>{exporting ? t('privacy.download.preparing') : t('privacy.download.title')}</button></div>
      {exported && <p className={`notice ${exported.tone === 'error' ? 'errorNotice' : ''}`} role={exported.tone === 'error' ? 'alert' : 'status'}><CircleDot aria-hidden="true"/>{exported.text}</p>}
    </section>

    <section className="surface settingsForm privacyBlock privacyDanger" aria-labelledby="privacy-delete">
      <h2 id="privacy-delete">{t('privacy.delete.title')}</h2>
      <p className="muted">{t('privacy.delete.text')}</p>
      <ul className="privacyKept">
        <li><strong>{t('privacy.delete.keptLabel')}</strong> {sharedTotal ? t('privacy.delete.keptShared', { count: sharedTotal }) : t('privacy.delete.keptNone')} {t('privacy.delete.ownerCanRemove')}</li>
        <li><strong>{t('privacy.delete.accountLabel')}</strong> <bdi>{summary.account_note}</bdi></li>
      </ul>
      <label className="field" htmlFor={confirmId}><span>{rich('privacy.delete.confirm', { phrase: <code dir="ltr">{phrase}</code> })}</span>
        <input id={confirmId} value={typed} onChange={e => setTyped(e.target.value)} autoComplete="off" spellCheck={false} aria-describedby={confirmHelp} disabled={deleting}/></label>
      <p id={confirmHelp} className="muted">{privateTotal ? t('privacy.delete.willDelete', { count: privateTotal }) : t('privacy.delete.nothing')}</p>
      <div className="actions"><button className="danger" disabled={!ready || deleting} aria-busy={deleting} onClick={() => void remove()}><Trash2 aria-hidden="true"/>{deleting ? t('privacy.delete.deleting') : t('privacy.delete.title')}</button></div>
      {deleted && <p className={`notice ${deleted.tone === 'error' ? 'errorNotice' : ''}`} role={deleted.tone === 'error' ? 'alert' : 'status'}><CircleDot aria-hidden="true"/>{deleted.text}{lastDelete && deleted.tone === 'ok' ? ` ${lastDelete.account_note}` : ''}</p>}
    </section>
    </div>
  </div>;
}
