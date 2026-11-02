/** "Assign to a person" inside the office's assign dialog. People take work as Team work items: the same
 * `POST /api/v1/work-items` the Team page sends, built by its own `draftBody`, so the API checks the role, that the
 * person may be assigned, and audits it. Only people the API marks assignable are offered. */
import { useEffect, useId, useState } from 'react';
import { CheckCircle2, Loader2 } from 'lucide-react';
import type { TeamPerson, WorkItemDetail, WorkPriority } from '@anum/contracts';
import { describeError, isPermissionDenied } from '../lib/errors';
import { t, type MessageKey } from '../lib/i18n';
import { draftBody, emptyDraft, PRIORITIES, type WorkDraft } from '../lib/team';
import { createWork, getPeople } from '../lib/teamApi';
import type { AssignPrefill } from '../lib/hqAssign';
import { LocalDateInput } from '../hq-date/LocalDateInput.tsx';

export function AssignPerson({ prefill, onClose }: { prefill: AssignPrefill; onClose: () => void }) {
  const ids = { who: useId(), title: useId(), desc: useId(), pri: useId(), due: useId() };
  const [people, setPeople] = useState<TeamPerson[] | null>(null);
  const [peopleError, setPeopleError] = useState<{ denied: boolean; detail: string } | null>(null);
  const [draft, setDraft] = useState<WorkDraft>(() => ({ ...emptyDraft(), title: prefill.title ?? '', description: prefill.instructions ?? '' }));
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [done, setDone] = useState<{ item: WorkItemDetail; name: string } | null>(null);

  const loadPeople = () => {
    setPeopleError(null); setPeople(null);
    getPeople().then((list) => setPeople(list.filter((p) => p.assignable)), (e) => setPeopleError({ denied: isPermissionDenied(e), detail: describeError(e) }));
  };
  useEffect(loadPeople, []);

  const { body, errors } = draftBody(draft);
  const noPerson = !draft.assignee;
  const set = <K extends keyof WorkDraft>(k: K, v: WorkDraft[K]) => { setDraft((d) => ({ ...d, [k]: v })); setFailure(null); };
  const send = async () => {
    setTouched(true);
    if (!body || noPerson || busy) return;
    setBusy(true); setFailure(null);
    try {
      const item = await createWork(body);
      const who = people?.find((p) => `user:${p.user_id}` === draft.assignee)?.display_name ?? '';
      setDone({ item, name: who });
    } catch (e) {
      setFailure(isPermissionDenied(e) ? t('hqWork.person.denied') : t('hqWork.person.failed', { detail: describeError(e) }));
    } finally { setBusy(false); }
  };

  if (done) return <div className="hqAsBody hqAsDone" role="status">
    <p><CheckCircle2 size={15} aria-hidden="true" /> {t('hqWork.person.done', { title: done.item.title, name: done.name })}</p>
    <p className="hqAsNote">{t('hqWork.person.doneNote')}</p>
    <div className="hqAsFoot">
      <button type="button" className="hqAsPrimary" onClick={onClose} data-autofocus>{t('hqAssign.close')}</button>
      <a className="hqAsLink" href="#team" onClick={onClose}>{t('hqWork.person.openTeam')}</a>
    </div>
  </div>;

  return <form className="hqAsBody" noValidate onSubmit={(e) => { e.preventDefault(); void send(); }}>
    <p className="hqAsNote">{t('hqWork.person.intro')}</p>
    <section className="hqAsSection">
      {people === null && !peopleError && <p className="hqAsNote" role="status"><Loader2 size={14} className="hqAsSpin" aria-hidden="true" /> {t('hqWork.person.loading')}</p>}
      {peopleError && <div className="hqAsBox hqAsWarn" role="alert"><p>{peopleError.denied ? t('hqWork.person.peopleDenied') : t('hqWork.person.peopleError', { detail: peopleError.detail })}</p>
        {!peopleError.denied && <button type="button" onClick={loadPeople}>{t('hqAssign.retry')}</button>}</div>}
      {people && people.length === 0 && <div className="hqAsBox" role="status"><p>{t('hqWork.person.empty')}</p><a className="hqAsLink" href="#members" onClick={onClose}>{t('hqWork.person.emptyAction')}</a></div>}
      {people && people.length > 0 && <div className="hqAsField">
        <label htmlFor={ids.who} className="hqAsLabel">{t('hqWork.person.who')}</label>
        <select id={ids.who} value={draft.assignee} onChange={(e) => set('assignee', e.target.value)} aria-invalid={touched && noPerson} aria-describedby={touched && noPerson ? `${ids.who}-e` : undefined}>
          <option value="">{t('hqWork.person.pick')}</option>
          {people.map((p) => <option key={p.user_id} value={`user:${p.user_id}`}>{p.display_name}{p.job_title ? ` (${p.job_title})` : ''}</option>)}
        </select>
        {touched && noPerson && <p id={`${ids.who}-e`} className="hqAsErr" role="alert">{t('hqWork.person.errWho')}</p>}
      </div>}
    </section>
    <section className="hqAsSection">
      <div className="hqAsField">
        <label htmlFor={ids.title} className="hqAsLabel">{t('hqAssign.field.title')}</label>
        <input id={ids.title} value={draft.title} maxLength={200} onChange={(e) => set('title', e.target.value)} dir="auto" required aria-invalid={touched && Boolean(errors.title)} aria-describedby={touched && errors.title ? `${ids.title}-e` : undefined} />
        {touched && errors.title && <p id={`${ids.title}-e`} className="hqAsErr" role="alert">{t('hqAssign.err.title')}</p>}
      </div>
      <div className="hqAsField">
        <label htmlFor={ids.desc} className="hqAsLabel">{t('hqWork.person.details')}</label>
        <textarea id={ids.desc} rows={4} value={draft.description} maxLength={6000} onChange={(e) => set('description', e.target.value)} dir="auto" />
      </div>
      <div className="hqAsRow">
        <div className="hqAsField">
          <label htmlFor={ids.pri} className="hqAsLabel">{t('hqAssign.field.priority')}</label>
          <select id={ids.pri} value={draft.priority} onChange={(e) => set('priority', e.target.value as WorkPriority)}>
            {PRIORITIES.map((p) => <option key={p} value={p}>{t(`team.priority.${p}` as MessageKey)}</option>)}
          </select>
        </div>
        <div className="hqAsField">
          <label htmlFor={ids.due} className="hqAsLabel">{t('hqAssign.field.due')}</label>
          <LocalDateInput id={ids.due} type="datetime-local" value={draft.due} onChange={(e) => set('due', e.target.value)} aria-invalid={touched && Boolean(errors.due)} />
          {touched && errors.due && <p className="hqAsErr" role="alert">{t('hqAssign.err.dueInvalid')}</p>}
        </div>
      </div>
    </section>
    {failure && <div className="hqAsBox hqAsFail" role="alert"><p>{failure}</p><p>{t('hqAssign.failedKeep')}</p></div>}
    <footer className="hqAsFoot">
      <button type="submit" className="hqAsPrimary" disabled={busy || !people?.length} data-autofocus>{busy ? t('hqAssign.sending') : t('hqWork.person.submit')}</button>
      <button type="button" onClick={onClose} disabled={busy}>{t('hqAssign.cancel')}</button>
    </footer>
  </form>;
}
