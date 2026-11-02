/** A meeting opened inside the headquarters: the live meeting room component of the Meetings module (join, leave,
 * hand raise, agenda, transcript, notes and the summary, all its own API calls and rules), in a drawer over the scene.
 * Without an id it shows that module's picker of this workspace's meetings. */
import { lazy, Suspense } from 'react';
import * as api from '../lib/api';
import { t } from '../lib/i18n';
import { fullPageHash, openHqScenePanel } from '../lib/hqWorkPanels';
import { WorkDrawer } from './WorkDrawer';

// The meeting room is the module's own component and stylesheet: it loads only when a meeting is opened.
const MeetingRoomPanel = lazy(() => import('../MeetingRoomPanel').then((m) => ({ default: m.MeetingRoomPanel })));

export function MeetingPanel({ meetingId, roomId, onClose }: { meetingId?: string | null; roomId?: string | null; onClose: () => void }) {
  return <WorkDrawer region={t('hqWork.meet.region')} onClose={onClose} testId="hq-meeting-panel"
    fullPage={{ hash: fullPageHash({ kind: 'meeting', id: meetingId ?? null }), label: t('hqWork.fullPage') }}
    foot={!meetingId && <button type="button" onClick={() => { if (openHqScenePanel('meetings')) onClose(); }}>{t('hqWork.meet.start')}</button>}>
    <div className="hqWdMeeting">
      <Suspense fallback={<p className="hqAsNote" role="status">{t('hqWork.meet.loading')}</p>}>
        <MeetingRoomPanel workspaceId={api.currentScope().workspaceId} meetingId={meetingId ?? undefined} roomId={roomId ?? undefined} onClose={onClose} />
      </Suspense>
    </div>
  </WorkDrawer>;
}
