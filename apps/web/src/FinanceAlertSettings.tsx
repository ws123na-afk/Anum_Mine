// Settings, Notifications, Financial alerts (docs/finance.md#financial-alerts, #alert-channels): which money alerts the person
// wants and where (in the app, phone push, email), and what today's run would tell. A push or an email carries the alert's fixed
// words only, never an amount, a name or an account number. A person without finance access sees nothing here.
import { useCallback, useEffect, useId, useState } from 'react';
import { CircleDot, RefreshCw } from 'lucide-react';
import * as dx from './lib/financeDebtsApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import './financeAlerts.css';

export function FinanceAlertSettings() {
  const id = useId();
  const [kinds, setKinds] = useState<dx.AlertKind[] | null>(null);
  const [channels, setChannels] = useState<dx.AlertChannels | undefined>(undefined);
  const [preview, setPreview] = useState<dx.AlertPreviewItem[] | null>(null);
  const [hidden, setHidden] = useState(false);
  const [loading, setLoading] = useState(true), [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { const view = await dx.alertSettings(); setKinds(view.kinds); setChannels(view.channels); }
    catch (e) { if (isPermissionDenied(e)) setHidden(true); else setError(t('falert.loadFailed', { error: describeError(e) })); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  if (hidden) return null;

  async function save() {
    if (!kinds) return;
    setSaving(true); setError(null); setNote(null);
    try { const view = await dx.saveAlertSettings(dx.alertSettingsBody(kinds)); setKinds(view.kinds); if (view.channels) setChannels(view.channels); setNote(t('falert.saved')); }
    catch (e) { setError(t('falert.saveFailed', { error: describeError(e) })); } finally { setSaving(false); }
  }
  async function showPreview() {
    setError(null);
    try { setPreview((await dx.alertPreview()).items); } catch (e) { setError(t('falert.loadFailed', { error: describeError(e) })); }
  }
  const why = dx.ALERT_CHANNELS.map((c) => dx.channelReasonKey(channels, c)).filter((k): k is string => k !== null);
  return <section className="surface settingsForm" aria-labelledby={`${id}-title`} aria-busy={loading || saving}>
    <p className="eyebrow">{t('falert.eyebrow')}</p>
    <h2 id={`${id}-title`}>{t('falert.title')}</h2>
    <p className="muted">{t('falert.intro')}</p>
    {why.length > 0 && <div className="alertChannelNotes">{why.map((key) => <p key={key} className="notice"><CircleDot aria-hidden="true" />{t(key as MessageKey)}</p>)}</div>}
    {loading && !kinds && <p className="muted" role="status">{t('falert.loading')}</p>}
    {kinds?.map((k) => <div key={k.kind} className="alertChannelRow" role="group" aria-labelledby={`${id}-${k.kind}-name`} aria-describedby={`${id}-${k.kind}`}>
      <div className="alertChannelText"><strong id={`${id}-${k.kind}-name`}>{t(`falert.kind.${k.kind}` as MessageKey)}</strong>
        <p id={`${id}-${k.kind}`}>{t(`falert.hint.${k.kind}` as MessageKey)}</p></div>
      <div className="alertChannelChecks">{dx.ALERT_CHANNELS.map((c) => {
        const usable = dx.channelUsable(channels, c);
        return <label key={c} data-unavailable={usable ? undefined : ''}>
          <input type="checkbox" checked={dx.alertChannel(k, c)} disabled={saving || (!usable && !dx.alertChannel(k, c))}
            aria-label={`${t(`falert.channel.${c}` as MessageKey)}: ${t(`falert.kind.${k.kind}` as MessageKey)}`}
            onChange={(e) => { setNote(null); setKinds(kinds.map((x) => (x.kind === k.kind ? dx.withChannel(x, c, e.target.checked) : x))); }} />
          <span>{t(`falert.channel.${c}` as MessageKey)}</span>
        </label>;
      })}</div>
    </div>)}
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
