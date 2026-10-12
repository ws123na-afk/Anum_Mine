import { useCallback, useEffect, useId, useState } from 'react';
import { CircleDot, RefreshCw, Smartphone, Trash2 } from 'lucide-react';
import * as api from './lib/api';
import { describeError } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import {
  notificationSwitches,
  deviceTitle,
  lastSentText,
  pushSummary,
  sortDevices,
  withDefaults,
  type NotificationPreferences,
  type PushDevice,
  type PushStatus,
} from './lib/push';

// Settings › Notifications (docs/notifications.md): what this user wants to hear about in this
// workspace (PUT /api/v1/notification-preferences) and phone push, whether the server can send it
// and which of the user's phones are registered. Phones register themselves from the mobile app;
// here they can only be removed.
export function NotificationSettings() {
  // The saved switches, so the phone section reflects "Phone push" as soon as it is saved.
  const [saved, setSaved] = useState<NotificationPreferences | null>(null);
  return <>
    <NotificationSwitches onSaved={setSaved} />
    <PhonePushDevices prefs={saved} />
  </>;
}

function NotificationSwitches({ onSaved }: { onSaved: (value: NotificationPreferences) => void }) {
  const [prefs, setPrefs] = useState<Required<NotificationPreferences> | null>(null);
  const [loading, setLoading] = useState(true), [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const id = useId();

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { const loaded = withDefaults(await api.getNotificationPreferences()); setPrefs(loaded); onSaved(loaded); }
    catch (e) { setError(t('settings.notifications.loadFailed', { error: describeError(e) })); }
    finally { setLoading(false); }
  }, [onSaved]);
  useEffect(() => { void load(); }, [load]);

  async function save() {
    if (!prefs) return;
    setSaving(true); setError(null); setNote(null);
    try { const stored = withDefaults(await api.saveNotificationPreferences(prefs)); setPrefs(stored); onSaved(stored); setNote(t('settings.notifications.saved')); }
    catch (e) { setError(t('settings.notifications.saveFailed', { error: describeError(e) })); }
    finally { setSaving(false); }
  }

  return <section className="surface settingsForm" aria-labelledby={`${id}-title`} aria-busy={loading || saving}>
    <p className="eyebrow">{t('settings.notifications.eyebrow')}</p>
    <h2 id={`${id}-title`}>{t('settings.notifications.title')}</h2>
    {loading && !prefs && <p className="muted" role="status">{t('settings.notifications.loading')}</p>}
    {prefs && notificationSwitches().map((item) => <label className="settingToggle" key={item.key}>
      <div><strong>{item.label}</strong><p id={`${id}-${item.key}`}>{item.description}</p></div>
      <input type="checkbox" checked={prefs[item.key]} disabled={saving} aria-describedby={`${id}-${item.key}`}
        onChange={(e) => { setNote(null); setPrefs({ ...prefs, [item.key]: e.target.checked }); }} />
    </label>)}
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
    {note && <p className="notice" role="status" aria-live="polite"><CircleDot aria-hidden="true" />{note}</p>}
    <div className="actions">
      {!prefs && !loading && <button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.retry')}</button>}
      {prefs && <button type="button" disabled={saving} onClick={() => void save()}>{saving ? t('settings.notifications.saving') : t('settings.notifications.save')}</button>}
    </div>
  </section>;
}

function PhonePushDevices({ prefs }: { prefs: NotificationPreferences | null }) {
  const [status, setStatus] = useState<PushStatus | null>(null);
  const [devices, setDevices] = useState<PushDevice[]>([]);
  const [loading, setLoading] = useState(true), [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const id = useId();

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [nextStatus, nextDevices] = await Promise.all([api.getPushStatus(), api.listMyDevices()]);
      setStatus(nextStatus); setDevices(sortDevices(nextDevices));
    } catch (e) { setError(t('push.loadFailed', { error: describeError(e) })); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function remove(device: PushDevice) {
    setRemoving(device.id); setError(null); setNote(null);
    try {
      await api.removeMyDevice(device.id);
      setDevices((current) => current.filter((item) => item.id !== device.id));
      setNote(t('push.removed', { device: deviceTitle(device) }));
    } catch (e) { setError(t('push.removeFailed', { device: deviceTitle(device), error: describeError(e) })); }
    finally { setRemoving(null); }
  }

  const summary = status ? pushSummary(status, devices, prefs) : null;
  return <section className="surface settingsForm phonePush" aria-labelledby={`${id}-title`} aria-busy={loading || removing !== null}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('push.eyebrow')}</p><h2 id={`${id}-title`}>{t('push.title')}</h2></div>
      {summary && <span className={`statusBadge ${summary.tone}`}>{summary.badge}</span>}</div>
    {loading && !status && <p className="muted" role="status">{t('push.loading')}</p>}
    {summary && <p className="phonePushSummary">{summary.text}</p>}
    {status && devices.length > 0 && <ul className="phoneList" aria-label={t('push.list')}>
      {devices.map((device) => <li className="phoneRow" key={device.id}>
        <Smartphone aria-hidden="true" />
        <div><strong>{deviceTitle(device)}</strong><p>{t('push.device.meta', { sent: lastSentText(device), date: formatDateTime(device.created_at, { dateStyle: 'medium' }) })}</p></div>
        <button type="button" className="secondary" disabled={removing !== null} aria-label={t('push.removeLabel', { device: deviceTitle(device) })} onClick={() => void remove(device)}>
          <Trash2 aria-hidden="true" />{removing === device.id ? t('push.removing') : t('push.remove')}
        </button>
      </li>)}
    </ul>}
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
    {note && <p className="notice" role="status" aria-live="polite"><CircleDot aria-hidden="true" />{note}</p>}
    {!loading && <div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{status ? t('push.refresh') : t('common.retry')}</button></div>}
  </section>;
}
