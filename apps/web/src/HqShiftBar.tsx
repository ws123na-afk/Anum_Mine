/** Working hours of a room whose seats have hours (docs/ANUM_MINE_GREEN_CONCRETE.md): how many people are on shift now, and the hours of the room's people, grouped. */
import { Clock } from 'lucide-react';
import type { SceneDesk } from './lib/headquartersProjection.ts';
import { roomWindows, type ShiftSnapshot } from './lib/hqShifts.ts';
import { t } from './lib/i18n.ts';
import './hqShift.css';

export function HqShiftBar({ snapshot, desks }: Readonly<{ snapshot: ShiftSnapshot; desks: readonly SceneDesk[] }>) {
  const seated = desks.filter((d) => d.agentId && d.shiftInfo);
  const on = seated.filter((d) => d.shiftInfo?.onShift).length;
  const windows = roomWindows(desks);
  const defaults = seated.some((d) => d.shiftInfo?.hoursAreDefault);
  return (
    <section className="hqShiftBar" aria-label={t('hqGc.hours.bar')} data-testid="shift-bar" data-on={on} data-total={seated.length}>
      <p className="hqShiftNow" role="status" data-testid="shift-showing">
        <Clock aria-hidden="true" />
        <span><strong>{t('hqGc.hours.now', { on, total: seated.length })}</strong>
          <small>{t('hqGc.shift.zone', { zone: snapshot.timezone })}</small></span>
      </p>
      {windows.length > 0 && <ul className="hqShiftWindows" aria-label={t('hqGc.hours.bar')}>
        {windows.map((w) => (
          <li key={`${w.start}-${w.end}`} data-testid="shift-window" data-on={w.on}>
            <span>{t('hqGc.shift.hours', { start: w.start, end: w.end })}</span>
            <small>{t('hqGc.hours.people', { count: w.people })}</small>
            <span className={`hqPill tone-${w.on ? 'ok' : 'faint'}`}>{t(w.on ? 'hqGc.hours.onNow' : 'hqGc.hours.offNow')}</span>
          </li>
        ))}
      </ul>}
      {defaults && <p className="hqNote" data-testid="shift-default-note">{t('hqGc.shift.hoursDefault')}</p>}
      <p className="hqNote" data-testid="shift-edit">{t('hqGc.hours.edit')}</p>
    </section>
  );
}
