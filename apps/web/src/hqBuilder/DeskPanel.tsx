import { useEffect, useMemo, useState } from 'react';
import { t } from '../lib/i18n.ts';
import type { BuilderController } from './useBuilderState.ts';
import { DESK_KINDS } from './labels.ts';
import { round3 } from './geometry.ts';
import type { OfficeDesk } from '../lib/officeSpaceClient.ts';
import type { OfficeDeskUpdate } from '@anum/contracts';
import { HOUR_PATTERN } from '../lib/hqShifts.ts';

export interface Person { id: string; name: string }
interface Props { b: BuilderController; roomId: string; people: readonly Person[]; peopleReady: boolean; editable: boolean }

/** Desks of the selected room: rename, place, seat an agent (real assignments), add and remove. */
export function DeskPanel({ b, roomId, people, peopleReady, editable }: Props) {
  const room = b.roomsById.get(roomId);
  const desks = useMemo(() => b.data.desks.filter((d) => d.room_id === roomId).sort((a, c) => a.label.localeCompare(c.label)), [b.data.desks, roomId]);
  const [newLabel, setNewLabel] = useState('');
  const [label, setLabel] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const selected = desks.find((d) => d.id === b.selectedDeskId);
  const draft = selected ? b.present.desks[selected.id] : undefined;
  const roomDraft = b.present.rooms[roomId];
  useEffect(() => { setLabel(selected?.label ?? ''); setConfirmDelete(false); }, [selected?.id, selected?.label]);
  if (!room) return null;
  const allowsDesks = DESK_KINDS.includes(room.kind);
  const full = desks.length >= room.capacity;
  const seatedAt = new Map(b.data.desks.filter((d) => d.agent_id).map((d) => [d.agent_id as string, d]));
  const nameOfAgent = (id: string | null): string => (id ? people.find((p) => p.id === id)?.name ?? t('hqb.desk.agentUnknown') : t('hqb.desk.vacant'));

  return (
    <section className="hqbSection" aria-labelledby="hqb-desks-title">
      <h3 id="hqb-desks-title">{t('hqb.desk.title', { count: desks.length, capacity: room.capacity })}</h3>
      {!allowsDesks && <p className="hqbHint">{t('hqb.desk.notAllowed', { kind: t(`hqb.kind.${room.kind}`) })}</p>}
      {allowsDesks && desks.length === 0 && <p className="hqbHint">{t('hqb.desk.empty')}</p>}
      <ul className="hqbDeskList">
        {desks.map((desk) => {
          const pos = b.present.desks[desk.id];
          return (
            <li key={desk.id}>
              <button type="button" className="hqbDeskRow" aria-pressed={desk.id === b.selectedDeskId} onClick={() => b.setSelectedDeskId(desk.id === b.selectedDeskId ? '' : desk.id)}>
                <strong dir="auto">{desk.label}</strong>
                <small dir="auto">{nameOfAgent(desk.agent_id)}{pos?.posX == null ? ` · ${t('hqb.desk.notPlaced')}` : ''}</small>
              </button>
            </li>
          );
        })}
      </ul>

      {selected && draft && (
        <div className="hqbDeskEditor" role="group" aria-label={t('hqb.desk.editor', { name: selected.label })}>
          <form className="hqbRow" onSubmit={(e) => { e.preventDefault(); if (label.trim() && label.trim() !== selected.label) void b.renameDesk(selected.id, label); }}>
            <label className="hqbGrow"><span>{t('hqb.desk.label')}</span>
              <input value={label} maxLength={100} dir="auto" disabled={!editable || b.busy} onChange={(e) => setLabel(e.target.value)} />
            </label>
            {editable && <button type="submit" disabled={b.busy || !label.trim() || label.trim() === selected.label}>{t('hqb.desk.rename')}</button>}
          </form>

          <label className="hqbField"><span>{t('hqb.desk.agent')}</span>
            <select
              value={selected.agent_id ?? ''} disabled={!editable || b.busy || !peopleReady}
              onChange={(e) => { void b.seatAgent(selected.id, e.target.value || null); }}
            >
              <option value="">{t('hqb.desk.vacant')}</option>
              {selected.agent_id && !people.some((p) => p.id === selected.agent_id) && <option value={selected.agent_id}>{t('hqb.desk.agentUnknown')}</option>}
              {people.map((p) => {
                const at = seatedAt.get(p.id);
                return <option key={p.id} value={p.id}>{at && at.id !== selected.id ? t('hqb.desk.agentMoves', { name: p.name, desk: at.label }) : p.name}</option>;
              })}
            </select>
          </label>
          {!peopleReady && <p className="hqbHint">{t('hqb.desk.peopleUnavailable')}</p>}

          {draft.posX != null && draft.posZ != null ? (
            <div className="hqbRow">
              {(['posX', 'posZ'] as const).map((field) => (
                <label key={field} className="hqbGrow"><span>{field === 'posX' ? t('hqb.desk.x') : t('hqb.desk.z')}</span>
                  <input
                    type="number" step="0.05" min={0} max={field === 'posX' ? roomDraft?.width : roomDraft?.depth} disabled={!editable}
                    value={round3(draft[field] as number)}
                    onChange={(e) => { const v = Number(e.target.value); if (Number.isFinite(v)) b.moveDesk(selected.id, field === 'posX' ? v : (draft.posX as number), field === 'posZ' ? v : (draft.posZ as number), `deskfield:${selected.id}:${field}`); }}
                  />
                </label>
              ))}
            </div>
          ) : (
            editable && roomDraft?.placed ? <button type="button" onClick={() => b.placeDesk(selected.id)}>{t('hqb.desk.place')}</button>
              : <p className="hqbHint">{t('hqb.desk.placeRoomFirst')}</p>
          )}

          <SeatFields desk={selected} editable={editable} busy={b.busy} onSave={(patch) => { void b.saveDeskSeat(selected.id, patch); }} />

          {editable && (
            confirmDelete ? (
              <div className="hqbConfirm" role="alertdialog" aria-label={t('hqb.desk.deleteTitle')}>
                <p>{selected.agent_id ? t('hqb.desk.deleteSeated') : t('hqb.desk.deleteQuestion', { name: selected.label })}</p>
                <div className="hqbRow">
                  <button type="button" className="hqbDanger" disabled={b.busy || !!selected.agent_id} onClick={() => { void b.deleteDesk(selected.id); }}>{t('hqb.desk.delete')}</button>
                  <button type="button" onClick={() => setConfirmDelete(false)}>{t('hqb.common.cancel')}</button>
                </div>
              </div>
            ) : <button type="button" className="hqbQuiet" onClick={() => setConfirmDelete(true)}>{t('hqb.desk.delete')}</button>
          )}
        </div>
      )}

      {editable && allowsDesks && (
        <form className="hqbRow hqbAddDesk" onSubmit={(e) => { e.preventDefault(); if (newLabel.trim()) void b.addDesk(roomId, newLabel).then((ok) => { if (ok) setNewLabel(''); }); }}>
          <label className="hqbGrow"><span>{t('hqb.desk.newLabel')}</span>
            <input value={newLabel} maxLength={100} dir="auto" placeholder={t('hqb.desk.newPlaceholder')} onChange={(e) => setNewLabel(e.target.value)} />
          </label>
          <button type="submit" disabled={b.busy || !newLabel.trim() || full}>{t('hqb.desk.add')}</button>
          {full && <p className="hqbHint">{t('hqb.desk.full')}</p>}
        </form>
      )}
    </section>
  );
}

const FACINGS = ['north', 'south', 'east', 'west'] as const;

/** Seat fields (docs/ANUM_MINE_GREEN_CONCRETE.md): the working hours of the person at the seat, its place at the table, the wall it looks at, the manager seat and the table group. */
function SeatFields({ desk, editable, busy, onSave }: { desk: OfficeDesk; editable: boolean; busy: boolean; onSave: (patch: Omit<OfficeDeskUpdate, 'expected_revision'>) => void }) {
  const [start, setStart] = useState(desk.work_start ?? '');
  const [end, setEnd] = useState(desk.work_end ?? '');
  const [order, setOrder] = useState(desk.seat_order == null ? '' : String(desk.seat_order));
  const [facing, setFacing] = useState(desk.facing ?? '');
  const [kind, setKind] = useState(desk.seat_kind ?? '');
  const [group, setGroup] = useState(desk.table_group ?? '');
  useEffect(() => {
    setStart(desk.work_start ?? ''); setEnd(desk.work_end ?? ''); setOrder(desk.seat_order == null ? '' : String(desk.seat_order)); setFacing(desk.facing ?? ''); setKind(desk.seat_kind ?? ''); setGroup(desk.table_group ?? '');
  }, [desk.id, desk.revision, desk.work_start, desk.work_end, desk.seat_order, desk.facing, desk.seat_kind, desk.table_group]);
  const patch: Omit<OfficeDeskUpdate, 'expected_revision'> = {};
  if ((start || null) !== (desk.work_start ?? null) || (end || null) !== (desk.work_end ?? null)) {
    patch.work_start = start || null; patch.work_end = end || null;
    // Own hours replace an older day or night shift.
    if (start && desk.shift) patch.shift = null;
  }
  if ((order === '' ? null : Number(order)) !== (desk.seat_order ?? null)) patch.seat_order = order === '' ? null : Number(order);
  if ((facing || null) !== (desk.facing ?? null)) patch.facing = (facing || null) as OfficeDesk['facing'];
  if ((kind || null) !== (desk.seat_kind ?? null)) patch.seat_kind = (kind || null) as OfficeDesk['seat_kind'];
  if ((group.trim() || null) !== (desk.table_group ?? null)) patch.table_group = group.trim() || null;
  const orderOk = order === '' || (Number.isInteger(Number(order)) && Number(order) >= 1 && Number(order) <= 999);
  const groupOk = group.trim() === '' || /^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/.test(group.trim());
  const hoursOk = (!start && !end) || (HOUR_PATTERN.test(start) && HOUR_PATTERN.test(end) && start !== end);
  const older = desk.shift && !desk.work_start ? desk.shift : null;
  const changed = Object.keys(patch).length > 0;
  const off = !editable || busy;
  return (
    <fieldset className="hqbSeat" data-testid="hqb-seat">
      <legend>{t('hqGc.builder.seat')}</legend>
      <div className="hqbRow" data-testid="hqb-seat-hours">
        <label className="hqbGrow"><span>{t('hqGc.builder.workStart')}</span>
          <input type="text" inputMode="numeric" placeholder="08:00" maxLength={5} dir="ltr" value={start} disabled={off} aria-invalid={!hoursOk} data-testid="hqb-seat-start" onChange={(e) => setStart(e.target.value.trim())} /></label>
        <label className="hqbGrow"><span>{t('hqGc.builder.workEnd')}</span>
          <input type="text" inputMode="numeric" placeholder="17:00" maxLength={5} dir="ltr" value={end} disabled={off} aria-invalid={!hoursOk} data-testid="hqb-seat-end" onChange={(e) => setEnd(e.target.value.trim())} /></label>
      </div>
      <p className="hqbHint" role="note">{t('hqGc.builder.workHelp')}</p>
      {!hoursOk && <p className="hqbHint" role="alert">{t('hqGc.builder.workInvalid')}</p>}
      {older && <p className="hqbHint" role="note" data-testid="hqb-seat-older">{t('hqGc.builder.olderShift', { shift: t(older === 'day' ? 'hqGc.shift.day' : 'hqGc.shift.night') })}</p>}
      <div className="hqbRow">
        <label className="hqbGrow"><span>{t('hqGc.builder.kind')}</span>
          <select value={kind} disabled={off} onChange={(e) => setKind(e.target.value as typeof kind)}>
            <option value="">{t('hqGc.builder.kindNone')}</option>
            <option value="team">{t('hqGc.builder.kindTeam')}</option>
            <option value="manager">{t('hqGc.builder.kindManager')}</option>
          </select></label>
        <label className="hqbGrow"><span>{t('hqGc.builder.order')}</span>
          <input type="number" min={1} max={999} step={1} value={order} disabled={off} aria-invalid={!orderOk} data-testid="hqb-seat-order" onChange={(e) => setOrder(e.target.value)} /></label>
      </div>
      <div className="hqbRow">
        <label className="hqbGrow"><span>{t('hqGc.builder.facing')}</span>
          <select value={facing} disabled={off} onChange={(e) => setFacing(e.target.value as typeof facing)}>
            <option value="">-</option>
            {FACINGS.map((f) => <option key={f} value={f}>{t(`hqGc.facing.${f}`)}</option>)}
          </select></label>
      </div>
      <label className="hqbField"><span>{t('hqGc.builder.group')}</span>
        <input value={group} maxLength={40} dir="ltr" disabled={off} aria-invalid={!groupOk} onChange={(e) => setGroup(e.target.value)} /></label>
      <p className="hqbHint">{t('hqGc.builder.groupHelp')}</p>
      {editable && <button type="button" disabled={busy || !changed || !orderOk || !groupOk || !hoursOk} onClick={() => onSave(patch)} data-testid="hqb-seat-save">{t('hqb.floor.save')}</button>}
    </fieldset>
  );
}
