import { useEffect, useState } from 'react';
import { t, type MessageKey } from './lib/i18n';
import { meetingHash } from './lib/liveMeeting';
import { meetingRoomApi, type MeetingSummary } from './lib/liveMeetingApi';
import { useLocale } from './useLocale';
import './meetingRoom.css';

/** Live agent meetings listed above the imported meetings in Calls & Meetings, each opening its room. */
export function MeetingRoomLauncher() {
  useLocale();
  const [items, setItems] = useState<MeetingSummary[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => { let live = true; meetingRoomApi.list().then((r) => live && setItems(r.items.slice(0, 5)), () => live && setFailed(true)); return () => { live = false; }; }, []);
  return <section className="mrLauncher" aria-labelledby="mr-launcher">
    <h3 id="mr-launcher">{t('meetingRoom.launcher')}</h3>
    <p className="muted">{t('meetingRoom.launcherHint')}</p>
    {failed && <p className="notice errorNotice" role="alert">{t('meetingRoom.launcherLoadFailed')}</p>}
    {items && items.length === 0 && <p className="muted">{t('meetingRoom.launcherEmpty')}</p>}
    {items && items.length > 0 && <ul>{items.map((m) => <li key={m.id}>
      <span dir="auto"><strong>{m.title}</strong> <span className={`mrChip mr-${m.status}`}>{t(`meetingRoom.status.${m.status}` as MessageKey)}</span></span>
      <a href={meetingHash(m.id)}>{t('meetingRoom.pickerOpen')}</a>
    </li>)}</ul>}
  </section>;
}
