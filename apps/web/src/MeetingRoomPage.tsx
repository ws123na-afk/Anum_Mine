import { useEffect, useState } from 'react';
import * as api from './lib/api';
import { meetingIdFromHash } from './lib/liveMeeting';
import { MeetingRoomPanel } from './MeetingRoomPanel';

/** Full page for the deep link `#office/meeting/<id>` (opened from Meetings or shared). */
export function MeetingRoomPage() {
  const [id, setId] = useState(() => meetingIdFromHash(location.hash));
  useEffect(() => { const sync = () => setId(meetingIdFromHash(location.hash)); window.addEventListener('hashchange', sync); return () => window.removeEventListener('hashchange', sync); }, []);
  if (!id) return null;
  return <main className="mrPage" style={{ margin: '0 auto', maxWidth: 1180 }}>
    <MeetingRoomPanel workspaceId={api.currentScope().workspaceId} meetingId={id} onClose={() => { history.pushState(null, '', '#meetings'); window.dispatchEvent(new HashChangeEvent('hashchange')); }} />
  </main>;
}
