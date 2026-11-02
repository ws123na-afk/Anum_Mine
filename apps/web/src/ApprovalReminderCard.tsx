// The approval-ageing reminder (docs/approvals-and-risk.md#approval-ageing-reminder): after how many
// hours a pending approval reminds this workspace's owners in the app, or off. Any member reads it;
// only owners change it (the API's 403 says so).
import { useCallback, useEffect, useId, useState } from 'react';
import { BellRing, CircleDot, Save } from 'lucide-react';
import { apiRequest } from './lib/api';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import { reminderChoices, type ApprovalReminderSettings } from './lib/policy';

const URL = '/api/v1/approval-reminders/settings';

export function ApprovalReminderCard({ readOnly }: { readOnly: boolean }) {
  const [settings, setSettings] = useState<ApprovalReminderSettings | null>(null);
  const [draft, setDraft] = useState<string>('off');
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const id = useId();
  const load = useCallback(async () => {
    setError(null);
    try {
      const next = await apiRequest<ApprovalReminderSettings>(URL, { method: 'GET' });
      setSettings(next); setDraft(next.reminder_hours === null ? 'off' : String(next.reminder_hours));
    } catch (failure) { setError(describeError(failure)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const save = () => {
    setBusy(true); setFlash(null);
    const hours = draft === 'off' ? null : Number(draft);
    void apiRequest<ApprovalReminderSettings>(URL, { method: 'PUT', body: JSON.stringify({ reminder_hours: hours }) })
      .then((next) => { setSettings(next); setFlash({ text: hours === null ? t('policy.reminder.off') : t('policy.reminder.saved', { hours }) }); })
      .catch((failure: unknown) => setFlash({ text: isPermissionDenied(failure) ? t('policy.reminder.denied') : describeError(failure), error: true }))
      .finally(() => setBusy(false));
  };
  const current = settings?.reminder_hours ?? null;
  const changed = settings !== null && draft !== (current === null ? 'off' : String(current));
  return <section className="surface adminPanel policyPanel" aria-labelledby={`${id}-title`}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('policy.reminder.eyebrow')}</p><h2 id={`${id}-title`}>{t('policy.reminder.title')}</h2></div><BellRing className="sectionIcon" aria-hidden="true" /></div>
    <p className="muted">{t('policy.reminder.help')}</p>
    {error && <div className="actions"><p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('policy.reminder.error', { message: error })}</p>
      <button type="button" className="secondary" onClick={() => void load()}>{t('policy.reminder.retry')}</button></div>}
    {settings && <>
      <label className="field" htmlFor={`${id}-hours`}><span>{t('policy.reminder.label')}</span>
        <select id={`${id}-hours`} value={draft} disabled={readOnly || busy} onChange={(event) => setDraft(event.target.value)}>
          <option value="off">{t('policy.reminder.optionOff')}</option>
          {reminderChoices(current).map((hours) => <option key={hours} value={String(hours)}>{t('policy.reminder.option', { hours })}</option>)}
        </select></label>
      {settings.updated_at && <p className="muted">{t('policy.reminder.updated', { time: formatDateTime(settings.updated_at), user: settings.updated_by ?? '-' })}</p>}
      {flash && <p className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{flash.text}</p>}
      {!readOnly && <div className="actions"><button type="button" disabled={busy || !changed} onClick={save}><Save aria-hidden="true" />{busy ? t('admin.saving') : t('policy.reminder.save')}</button></div>}
    </>}
  </section>;
}
