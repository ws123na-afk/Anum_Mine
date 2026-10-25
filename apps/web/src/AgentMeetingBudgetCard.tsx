import { useCallback, useEffect, useState } from 'react';
import { CircleDot, Save, XCircle } from 'lucide-react';
import { describeError } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import type { MeetingBudget } from './lib/liveMeeting';
import { meetingRoomApi } from './lib/liveMeetingApi';
import { budgetTone, parseCapInput, usd } from './lib/meetingBudget';
import { formatResetDate } from './lib/admin';
import './admin.css';

type Flash = { text: string; error?: boolean } | null;

/**
 * The owner's setting for the workspace-wide monthly agent meeting budget (S-02). The API owns the
 * numbers (`GET/PUT /api/v1/agent-meetings/budget`); nothing is computed from the model budgets here.
 * Shown inside Budgets, which only owners can open, and the API refuses anyone else on save.
 */
export function AgentMeetingBudgetCard() {
  const [budget, setBudget] = useState<MeetingBudget | null>(null);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const load = useCallback(() => {
    setError('');
    void meetingRoomApi.budget().then((next) => { setBudget(next); setDraft(String(next.limit_usd)); }, (e: unknown) => setError(describeError(e)));
  }, []);
  useEffect(load, [load]);

  const save = (limit: number | null) => {
    setBusy(true); setFlash(null);
    void meetingRoomApi.setBudget(limit)
      .then((next) => { setBudget(next); setDraft(String(next.limit_usd)); setFlash({ text: t(limit === null ? 'meetingBudget.resetDone' : 'meetingBudget.saved') }); })
      .catch((e: unknown) => setFlash({ text: describeError(e), error: true }))
      .finally(() => setBusy(false));
  };
  const submit = () => {
    const parsed = parseCapInput(draft);
    if (!parsed.ok) { setFlash({ text: t('meetingBudget.invalid'), error: true }); return; }
    save(parsed.value);
  };

  const title = t('meetingBudget.title');
  if (!budget) return <section className="surface budgetCard" aria-label={t('meetingBudget.cardLabel')}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('meetingBudget.scope')}</p><h2>{title}</h2></div></div>
    {error ? <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('meetingBudget.error', { message: error })}</p>
      : <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('meetingBudget.loading')}</p>}
    {error && <div className="actions"><button type="button" className="secondary" onClick={load}>{t('meetingRoom.retry')}</button></div>}
  </section>;

  const tone = budgetTone(budget);
  const meterTone = tone === 'full' ? 'stop' : tone === 'near' ? 'warn' : '';
  return <section className={`surface budgetCard meetingBudgetCard ${tone === 'full' ? 'exceeded' : ''}`} aria-label={t('meetingBudget.cardLabel')}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('meetingBudget.scope')}</p><h2>{title}</h2><p className="muted">{t('meetingBudget.subtitle')}</p></div>
      <span className={`statusBadge ${tone === 'full' ? 'dangerTone' : tone === 'near' ? 'neutral' : 'success'}`}>{t(`meetingBudget.badge.${tone}`)}</span></div>
    <div className="meetingBudgetBody">
    <div className="meetingBudgetInfo">
    <div className="budgetMeter">
      <div className="budgetMeterTop"><span>{t('meetingBudget.spent')}</span><strong dir="ltr">${usd(budget.used_usd)}<small> / ${usd(budget.limit_usd)}</small></strong></div>
      <div className={`meterTrack ${meterTone}`} role="progressbar" aria-label={t('meetingBudget.meterLabel')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.floor(budget.percent)}
        aria-valuetext={t('meetingBudget.percent', { percent: Math.floor(budget.percent) })}><span style={{ width: `${Math.min(100, budget.percent)}%` }} /></div>
      <small className="muted">{t('meetingBudget.percent', { percent: Math.floor(budget.percent) })}</small>
    </div>
    <dl className="budgetStats">
      <div><dt>{t('meetingBudget.remaining')}</dt><dd dir="ltr">{usd(budget.remaining_usd)}</dd></div>
      <div><dt>{t('meetingBudget.turns')}</dt><dd>{budget.turns}</dd></div>
      <div><dt>{t('meetingBudget.resets')}</dt><dd dir="ltr">{formatResetDate(budget.resets_on, false)}</dd></div>
    </dl>
    {tone === 'full' && <p className="notice errorNotice" role="status"><CircleDot aria-hidden="true" />{t('meetingRoom.budgetReached', { used: usd(budget.used_usd), limit: usd(budget.limit_usd), date: formatResetDate(budget.resets_on, false) })}</p>}
    <p className="muted">{t('meetingBudget.explain')}</p>
    <p className="muted">{budget.is_default || !budget.updated_by ? t('meetingBudget.isDefault') : t('meetingBudget.lastChanged', { user: budget.updated_by, when: budget.updated_at ? formatDateTime(budget.updated_at) : '' })}</p>
    </div>
    {budget.can_manage
      ? <form className="budgetForm" aria-label={t('meetingBudget.cardLabel')} onSubmit={(event) => { event.preventDefault(); submit(); }}>
        <label className="field"><span>{t('meetingBudget.limitLabel')}</span><input dir="ltr" inputMode="decimal" value={draft} onChange={(event) => setDraft(event.target.value)} autoComplete="off" /></label>
        {flash && <p className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{flash.text}</p>}
        <p className="muted">{t('meetingBudget.hint', { default: usd(budget.default_limit_usd) })}</p>
        <div className="actions">
          <button type="submit" disabled={busy}><Save aria-hidden="true" />{busy ? t('admin.saving') : t('meetingBudget.save')}</button>
          {!budget.is_default && <button type="button" className="secondary" disabled={busy} onClick={() => save(null)}><XCircle aria-hidden="true" />{t('meetingBudget.useDefault', { default: usd(budget.default_limit_usd) })}</button>}
        </div>
      </form>
      : <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('meetingBudget.ownerOnly')}</p>}
    </div>
  </section>;
}
