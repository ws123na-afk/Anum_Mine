// "Schedule meeting" on Calls & Meetings (docs/meetings.md#scheduling-a-meeting): the Calendar's own
// event form, opened as a meeting, so a new meeting is a calendar event with the same repeat, time
// zone and reminder choices, sent to the connected Google or Microsoft calendar when there is one.
// ANUM never joins a meeting: after it happens, its transcript or recording is imported here.
import { useEffect, useState } from 'react';
import type { CalendarEventView, SyncConnection } from './lib/calendar';
import { syncTarget } from './lib/calendar';
import * as calendarApi from './lib/calendarApi';
import { EventFormDialog } from './CalendarView';
import { LoadingSkeleton } from './PageParts';
import { Modal } from './Dialog';
import { t } from './lib/i18n';

export function ScheduleMeetingDialog({ onClose, onScheduled }: { onClose: () => void; onScheduled: (event: CalendarEventView) => void }) {
  // undefined while the sync status loads; null when no calendar is connected (or it could not be read).
  const [target, setTarget] = useState<SyncConnection | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    calendarApi.getSync().then((setup) => { if (live) setTarget(syncTarget(setup.connections)); }, () => { if (live) setTarget(null); });
    return () => { live = false; };
  }, []);
  if (target === undefined) return <Modal title={t('meetings.schedule.title')} className="formDialog" onClose={onClose}><LoadingSkeleton label={t('meetings.schedule.loading')} rows={2} /></Modal>;
  return <EventFormDialog event={null} day={new Date()} target={target} onClose={onClose} onSaved={(event) => onScheduled(event)} />;
}
