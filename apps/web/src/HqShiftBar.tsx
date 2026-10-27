/** The shift toggle of a room with shift seats (docs/ANUM_MINE_GREEN_CONCRETE.md): which shift is shown, whether it is on now, its hours. */
import { Moon, Sun } from 'lucide-react';
import { windowOf, otherShift, type ShiftSnapshot, type ShiftView } from './lib/hqShifts.ts';
import { t } from './lib/i18n.ts';
import './hqShift.css';

export function HqShiftBar({ snapshot, view, onView }: Readonly<{ snapshot: ShiftSnapshot; view: ShiftView; onView: (view: ShiftView) => void }>) {
  const viewed = snapshot.viewed;
  const shiftName = (s: 'day' | 'night') => t(s === 'day' ? 'hqGc.shift.day' : 'hqGc.shift.night');
  const window = windowOf(viewed, snapshot.hours);
  const other = otherShift(viewed);
  const Icon = viewed === 'day' ? Sun : Moon;
  return (
    <section className="hqShiftBar" aria-label={t('hqGc.shift.bar')} data-testid="shift-bar" data-viewed={viewed} data-active={snapshot.active}>
      <p className="hqShiftNow" role="status" data-testid="shift-showing">
        <Icon aria-hidden="true" />
        <span><strong>{t('hqGc.shift.showing', { shift: shiftName(viewed), state: t(viewed === snapshot.active ? 'hqGc.shift.onNow' : 'hqGc.shift.offNow') })}</strong>
          <small>{t('hqGc.shift.hours', window)} · {t('hqGc.shift.zone', { zone: snapshot.timezone })}</small></span>
      </p>
      {snapshot.hours.isDefault && <p className="hqNote" data-testid="shift-default-note">{t('hqGc.shift.hoursDefault')}</p>}
      <button type="button" className="hqGhost" aria-pressed={view === 'other'} data-testid="shift-toggle" onClick={() => onView(view === 'other' ? 'active' : 'other')}>
        {view === 'other' ? t('hqGc.shift.back', { shift: shiftName(otherShift(viewed)) }) : t('hqGc.shift.toggle', { shift: shiftName(other) })}
      </button>
    </section>
  );
}
