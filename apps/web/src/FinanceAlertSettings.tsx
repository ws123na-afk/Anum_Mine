// Settings, Notifications, Financial alerts (docs/finance.md#financial-alerts): which money alerts the person wants, and
// what today's run would tell. In-app only; each item is told once. A person without finance access sees nothing here.
import { useCallback, useEffect, useId, useState } from 'react';
import { CircleDot, RefreshCw } from 'lucide-react';
import * as dx from './lib/financeDebtsApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';

export function FinanceAlertSettings() {
  const id = useId();
  const [kinds, setKinds] = useState<dx.AlertKind[] | null>(null);
  const [preview, setPreview] = useState<dx.AlertPreviewItem[] | null>(null);
  const [hidden, setHidden] = useState(false);
  const [loading, setLoading] = useState(true), [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { setKinds((await dx.alertSettings()).kinds); }
    catch (e) { if (isPermissionDenied(e)) setHidden(true); else setError(t('falert.loadFailed', { error: describeError(e) })); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  if (hidden) return null;

  async function save() {
    if (!kinds) return;
    setSaving(true); setError(null); setNote(null);
    try { setKinds((await dx.saveAlertSettings(Object.fromEntries(kinds.map((k) => [k.kind, k.on])))).kinds); setNote(t('falert.saved')); }
    catch (e) { setError(t('falert.saveFailed', { error: describeError(e) })); } finally { setSaving(false); }
  }
  async function showPreview() {
    setError(null);
    try { setPreview((await dx.alertPreview()).items); } catch (e) { setError(t('falert.loadFailed', { error: describeError(e) })); }
  }
  return <section className="surface settingsForm" aria-labelledby={`${id}-title`} aria-busy={loading || saving}>
    <p className="eyebrow">{t('falert.eyebrow')}</p>
    <h2 id={`${id}-title`}>{t('falert.title')}</h2>
    <p className="muted">{t('falert.intro')}</p>
    {loading && !kinds && <p className="muted" role="status">{t('falert.loading')}</p>}
    {kinds?.map((k) => <label key={k.kind} className="settingToggle">
      <div><strong>{t(`falert.kind.${k.kind}` as MessageKey)}</strong><p id={`${id}-${k.kind}`}>{t(`falert.hint.${k.kind}` as MessageKey)}</p></div>
      <input type="checkbox" checked={k.on} disabled={saving} aria-describedby={`${id}-${k.kind}`}
        onChange={(e) => { setNote(null); setKinds(kinds.map((x) => (x.kind === k.kind ? { ...x, on: e.target.checked } : x))); }} />
    </label>)}
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
    {note && <p className="notice" role="status" aria-live="polite"><CircleDot aria-hidden="true" />{note}</p>}
    <div className="actions">
      {!kinds && !loading && <button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.retry')}</button>}
      {kinds && <button type="button" disabled={saving} onClick={() => void save()}>{saving ? t('falert.saving') : t('falert.save')}</button>}
      {kinds && <button type="button" className="secondary" onClick={() => void showPreview()}>{t('falert.preview')}</button>}
    </div>
    {preview && (preview.length === 0 ? <p className="muted" role="status">{t('falert.preview.none')}</p> : <ul className="phoneList" aria-label={t('falert.preview.list')}>
      {preview.map((a, i) => <li key={`${a.kind}:${i}`} className="phoneRow"><div><strong>{t(`falert.kind.${a.kind}` as MessageKey)}</strong>
        <p>{a.body}</p><small className="muted">{formatDateTime(`${a.on}T12:00:00Z`, { dateStyle: 'medium', timeZone: 'UTC' })}
          {a.on_for_you ? '' : ` · ${t('falert.preview.off')}`}</small></div></li>)}
    </ul>)}
  </section>;
}
