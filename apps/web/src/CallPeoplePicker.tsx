// Choosing teammates for a group call (docs/in-app-calls.md#group-calls): "add" during a call
// (each pick rings that person at once), "start" from Team calls (two or three people, then
// voice or video). The list is the workspace's people (GET /team/people), searchable, without
// you and without anyone already in the call; the server checks membership again.
import { useEffect, useId, useMemo, useState } from 'react';
import { Phone, RefreshCw, UserPlus, Video, X } from 'lucide-react';
import type { AppCallMedia, TeamPerson } from '@anum/contracts';
import { getPeople } from './lib/teamApi';
import { currentUserId } from './lib/api';
import { describeError, isPermissionDenied } from './lib/errors';
import { t } from './lib/i18n';
import { useModal } from './Dialog';

interface Person { user_id: string; display_name: string }
type Load = { state: 'loading' } | { state: 'ready'; people: TeamPerson[] } | { state: 'error'; message: string; denied: boolean };

export function CallPeoplePicker({ mode, exclude, slots, onPick, onClose, videoEnabled = true }: {
  mode: 'add' | 'start';
  exclude: Set<string>;
  /** How many more people fit in the call. */
  slots: number;
  onPick(people: Person[], media: AppCallMedia): void;
  onClose(): void;
  videoEnabled?: boolean;
}) {
  const panel = useModal(onClose);
  const titleId = useId(), searchId = useId(), hintId = useId();
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [query, setQuery] = useState('');
  const [chosen, setChosen] = useState<Person[]>([]);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoad({ state: 'loading' });
    getPeople().then(
      (people) => { if (!cancelled) setLoad({ state: 'ready', people }); },
      (error: unknown) => { if (!cancelled) setLoad({ state: 'error', message: describeError(error), denied: isPermissionDenied(error) }); },
    );
    return () => { cancelled = true; };
  }, [attempt]);

  const me = currentUserId();
  const candidates = useMemo(() => {
    if (load.state !== 'ready') return [];
    const words = query.trim().toLocaleLowerCase();
    return load.people
      .filter((p) => p.user_id !== me && !exclude.has(p.user_id))
      .filter((p) => !words || [p.display_name, p.email ?? '', p.job_title ?? ''].some((v) => v.toLocaleLowerCase().includes(words)))
      .sort((a, b) => a.display_name.localeCompare(b.display_name));
  }, [load, query, me, exclude]);
  const available = load.state === 'ready' ? load.people.filter((p) => p.user_id !== me && !exclude.has(p.user_id)).length : 0;

  const toggle = (person: TeamPerson) => {
    const picked = chosen.some((c) => c.user_id === person.user_id);
    if (picked) setChosen(chosen.filter((c) => c.user_id !== person.user_id));
    else if (chosen.length < slots) setChosen([...chosen, { user_id: person.user_id, display_name: person.display_name }]);
  };
  const enough = mode === 'add' ? chosen.length >= 1 : chosen.length >= 2;

  return <section className="surface appCallPanel appCallPicker" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={hintId} ref={panel as never}>
    <button type="button" className="iconButton small appCallMinimize" aria-label={t('appCalls.close')} title={t('appCalls.close')} onClick={onClose}><X aria-hidden="true" /></button>
    <h2 id={titleId}>{t(mode === 'add' ? 'appCalls.group.addTitle' : 'appCalls.group.startTitle')}</h2>
    <p id={hintId} className="appCallDetail">{mode === 'start' ? t('appCalls.group.startHint') : slots > 0 ? t('appCalls.group.pickHint', { count: slots }) : t('appCalls.group.full', { count: 4 })}</p>
    {load.state === 'loading' && <p className="muted" role="status">{t('appCalls.group.loadingPeople')}</p>}
    {load.state === 'error' && <div className="notice errorNotice" role="alert">
      <span>{load.denied ? t('appCalls.group.peopleDenied') : t('appCalls.group.peopleFailed', { message: load.message })}</span>
      {!load.denied && <button type="button" className="secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw aria-hidden="true" />{t('appCalls.history.retry')}</button>}
    </div>}
    {load.state === 'ready' && available === 0 && <p className="appCallDetail" role="status">{t('appCalls.group.nobodyLeft')}</p>}
    {load.state === 'ready' && available > 0 && <>
      <label className="visuallyHidden" htmlFor={searchId}>{t('appCalls.group.search')}</label>
      <input id={searchId} type="search" className="appCallSearch" placeholder={t('appCalls.group.search')} value={query} onChange={(e) => setQuery(e.target.value)} data-autofocus />
      {candidates.length === 0
        ? <p className="muted" role="status">{t('appCalls.group.noMatch')}</p>
        : <ul className="appCallPickList" aria-label={t('appCalls.group.people')}>
            {candidates.map((person) => {
              const picked = chosen.some((c) => c.user_id === person.user_id);
              return <li key={person.user_id}>
                <button type="button" className="appCallPickRow" aria-pressed={picked} disabled={!picked && chosen.length >= slots} onClick={() => toggle(person)}>
                  <span className="appCallPickName"><bdi>{person.display_name}</bdi></span>
                  {person.job_title && <small><bdi>{person.job_title}</bdi></small>}
                </button>
              </li>;
            })}
          </ul>}
    </>}
    <div className="appCallControls">
      {mode === 'add'
        ? <button type="button" className="appCallButton accept" disabled={!enough} onClick={() => onPick(chosen, 'audio')}><UserPlus aria-hidden="true" /><span>{t('appCalls.group.ring', { count: chosen.length })}</span></button>
        : <>
            <button type="button" className="appCallButton accept" disabled={!enough} onClick={() => onPick(chosen, 'audio')}><Phone aria-hidden="true" /><span>{t('appCalls.group.startVoice')}</span></button>
            {videoEnabled && <button type="button" className="appCallButton accept" disabled={!enough} onClick={() => onPick(chosen, 'video')}><Video aria-hidden="true" /><span>{t('appCalls.group.startVideo')}</span></button>}
          </>}
      <button type="button" className="appCallButton" onClick={onClose}><X aria-hidden="true" /><span>{t('appCalls.group.cancel')}</span></button>
    </div>
  </section>;
}
