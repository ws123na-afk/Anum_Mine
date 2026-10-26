import { useEffect, useMemo, useState } from 'react';
import type { OfficeEquipmentItem, OfficeRoomColor, OfficeRoomKind, OfficeRoomTeam, OfficeRoomUpdate, OfficeTeamDepartment } from '@anum/contracts';
import { t, type MessageKey } from '../lib/i18n.ts';
import type { OfficeRoom } from '../lib/officeSpaceClient.ts';
import type { BuilderApi } from './builderApi.ts';
import { DeskPanel, type Person } from './DeskPanel.tsx';
import { EquipmentEditor } from './EquipmentEditor.tsx';
import { DEPARTMENTS, DOOR_SIDES, ROOM_COLORS, ROOM_KINDS } from './labels.ts';
import { round3 } from './geometry.ts';
import type { BuilderController } from './useBuilderState.ts';

interface Props { b: BuilderController; api: BuilderApi; roomId: string; people: readonly Person[]; peopleReady: boolean; editable: boolean }

type TeamView = { state: 'loading' } | { state: 'ready'; team: OfficeRoomTeam } | { state: 'unavailable' };

/** Real member and assistant counts for the linked department; "Not connected" with no link or no answer. */
function useTeam(api: BuilderApi, roomId: string, department: OfficeTeamDepartment | null | undefined): TeamView {
  const [view, setView] = useState<TeamView>({ state: 'loading' });
  useEffect(() => {
    let active = true;
    setView({ state: 'loading' });
    if (!department) { setView({ state: 'unavailable' }); return () => { active = false; }; }
    api.team(roomId).then((team) => { if (active) setView({ state: 'ready', team }); }).catch(() => { if (active) setView({ state: 'unavailable' }); });
    return () => { active = false; };
  }, [api, roomId, department]);
  return view;
}

export function RoomDetails({ b, api, roomId, people, peopleReady, editable }: Props) {
  const record = b.roomsById.get(roomId);
  const draft = b.present.rooms[roomId];
  if (!record || !draft) return <p className="hqbHint">{t('hqb.room.pick')}</p>;
  return <RoomForm key={`${record.id}:${record.revision}`} b={b} api={api} record={record} people={people} peopleReady={peopleReady} editable={editable} />;
}

function RoomForm({ b, api, record, people, peopleReady, editable }: { b: BuilderController; api: BuilderApi; record: OfficeRoom; people: readonly Person[]; peopleReady: boolean; editable: boolean }) {
  const draft = b.present.rooms[record.id];
  const [name, setName] = useState(record.name);
  const [nameAr, setNameAr] = useState(record.name_ar ?? '');
  const [kind, setKind] = useState<OfficeRoomKind>(record.kind);
  const [capacity, setCapacity] = useState(record.capacity);
  const [color, setColor] = useState<OfficeRoomColor | null>(record.color ?? null);
  const [description, setDescription] = useState(record.description ?? '');
  const [notes, setNotes] = useState(record.notes ?? '');
  const [department, setDepartment] = useState<OfficeTeamDepartment | null>(record.team_department ?? null);
  const [equipment, setEquipment] = useState<OfficeEquipmentItem[]>(record.equipment ?? []);
  const [copyDesks, setCopyDesks] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const deskCount = b.data.desks.filter((d) => d.room_id === record.id).length;
  const team = useTeam(api, record.id, record.team_department);

  const patch = useMemo<Omit<OfficeRoomUpdate, 'expected_revision'>>(() => {
    const out: Omit<OfficeRoomUpdate, 'expected_revision'> = {};
    if (name.trim() && name.trim() !== record.name) out.name = name.trim();
    if ((nameAr.trim() || null) !== (record.name_ar ?? null)) out.name_ar = nameAr.trim() || null;
    if (kind !== record.kind) out.kind = kind;
    if (capacity !== record.capacity) out.capacity = capacity;
    if (color !== (record.color ?? null)) out.color = color;
    if (description !== (record.description ?? '')) out.description = description;
    if (notes !== (record.notes ?? '')) out.notes = notes;
    if (department !== (record.team_department ?? null)) out.team_department = department;
    if (JSON.stringify(equipment) !== JSON.stringify(record.equipment ?? [])) out.equipment = equipment;
    return out;
  }, [name, nameAr, kind, capacity, color, description, notes, department, equipment, record]);
  const changed = Object.keys(patch).length > 0;
  const valid = name.trim().length > 0 && capacity >= 1 && capacity <= 5000;
  const off = !editable || b.busy;
  const kinds = b.catalogue?.equipment_kinds ?? [];

  const numberField = (field: 'x' | 'z' | 'width' | 'depth', label: string) => (
    <label className="hqbGrow"><span>{label}</span>
      <input
        type="number" step="0.1" disabled={!editable || !draft.placed} value={round3(draft[field])}
        min={field === 'width' || field === 'depth' ? 1 : 0} max={field === 'width' || field === 'depth' ? 1000 : 10000}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (!Number.isFinite(v)) return;
          if ((field === 'width' || field === 'depth') && v < 1) return;
          b.moveRoom(record.id, { x: draft.x, z: draft.z, width: draft.width, depth: draft.depth, [field]: v }, `field:${record.id}:${field}`);
        }}
      />
    </label>
  );

  return (
    <div className="hqbRoomForm">
      <section className="hqbSection" aria-labelledby="hqb-pos-title">
        <h3 id="hqb-pos-title">{t('hqb.room.position')}</h3>
        {!draft.placed ? (
          <div className="hqbCallout">
            <p>{t('hqb.room.unplaced')}</p>
            {editable && <button type="button" onClick={() => b.placeRoom(record.id)}>{t('hqb.room.placeOnPlan')}</button>}
          </div>
        ) : (
          <>
            <div className="hqbRow">{numberField('x', t('hqb.room.x'))}{numberField('z', t('hqb.room.z'))}</div>
            <div className="hqbRow">{numberField('width', t('hqb.room.width'))}{numberField('depth', t('hqb.room.depth'))}</div>
            <div className="hqbRow">
              <label className="hqbGrow"><span>{t('hqb.room.door')}</span>
                <select value={draft.doorSide} disabled={!editable} onChange={(e) => b.setDoor(record.id, e.target.value as typeof draft.doorSide)}>
                  {DOOR_SIDES.map((side) => <option key={side} value={side}>{t(`hqb.side.${side}` as MessageKey)}</option>)}
                </select>
              </label>
              {editable && <button type="button" onClick={() => b.rotate(record.id)}>{t('hqb.room.rotate')}</button>}
            </div>
            <p className="hqbHint">{t('hqb.room.rotationNow', { degrees: draft.rotation })}</p>
          </>
        )}
      </section>

      <form
        className="hqbSection" aria-labelledby="hqb-details-title"
        onSubmit={(e) => { e.preventDefault(); if (changed && valid) void b.saveRoomDetails(record.id, patch); }}
      >
        <h3 id="hqb-details-title">{t('hqb.room.details')}</h3>
        <label className="hqbField"><span>{t('hqb.room.name')}</span>
          <input value={name} maxLength={100} dir="auto" required disabled={off} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="hqbField"><span>{t('hqb.room.nameAr')}</span>
          <input value={nameAr} maxLength={100} dir="rtl" lang="ar" disabled={off} onChange={(e) => setNameAr(e.target.value)} />
        </label>
        {!nameAr.trim() && <p className="hqbHint" role="note">{t('hqb.nameAr.hint')}</p>}
        <div className="hqbRow">
          <label className="hqbGrow"><span>{t('hqb.room.kind')}</span>
            <select value={kind} disabled={off} onChange={(e) => setKind(e.target.value as OfficeRoomKind)}>
              {ROOM_KINDS.map((k) => <option key={k} value={k}>{t(`hqb.kind.${k}` as MessageKey)}</option>)}
            </select>
          </label>
          <label className="hqbGrow"><span>{t('hqb.room.capacity')}</span>
            <input type="number" min={1} max={5000} value={capacity} disabled={off} onChange={(e) => setCapacity(Math.round(Number(e.target.value)) || 1)} />
          </label>
        </div>
        <fieldset className="hqbFieldset" disabled={off}>
          <legend>{t('hqb.room.color')}</legend>
          <div className="hqbSwatches" role="radiogroup" aria-label={t('hqb.room.color')}>
            <button type="button" role="radio" aria-checked={color === null} className="hqbSwatch" data-none="true" onClick={() => setColor(null)}>{t('hqb.room.colorNone')}</button>
            {ROOM_COLORS.map((c) => (
              <button key={c} type="button" role="radio" aria-checked={color === c} aria-label={t(`hqb.color.${c}` as MessageKey)} className="hqbSwatch" data-color={c} onClick={() => setColor(c)} />
            ))}
          </div>
        </fieldset>
        <label className="hqbField"><span>{t('hqb.room.description')}</span>
          <textarea rows={2} maxLength={500} dir="auto" value={description} disabled={off} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className="hqbField"><span>{t('hqb.room.notes')}</span>
          <textarea rows={3} maxLength={2000} dir="auto" value={notes} disabled={off} onChange={(e) => setNotes(e.target.value)} />
        </label>
        <EquipmentEditor items={equipment} kinds={kinds} disabled={off} onChange={setEquipment} />

        <label className="hqbField"><span>{t('hqb.room.team')}</span>
          <select value={department ?? ''} disabled={off} onChange={(e) => setDepartment((e.target.value || null) as OfficeTeamDepartment | null)}>
            <option value="">{t('hqb.room.teamNone')}</option>
            {DEPARTMENTS.map((d) => <option key={d} value={d}>{t(`hqb.dept.${d}` as MessageKey)}</option>)}
          </select>
        </label>
        <p className="hqbTeam" role="status" data-testid="room-team">
          {team.state === 'loading' ? t('hqb.common.loading')
            : team.state === 'ready' && team.team.member_profiles != null && team.team.assistants != null
              ? t('hqb.room.teamCounts', { members: team.team.member_profiles, assistants: team.team.assistants })
              : t('hqb.room.notConnected')}
        </p>
        {department !== (record.team_department ?? null) && <p className="hqbHint">{t('hqb.room.teamSaveFirst')}</p>}

        {editable && (
          <div className="hqbRow">
            <button type="submit" className="hqbPrimary" disabled={b.busy || !changed || !valid}>{b.busy ? t('hqb.common.saving') : t('hqb.room.saveDetails')}</button>
            {changed && <button type="button" onClick={() => { setName(record.name); setNameAr(record.name_ar ?? ''); setKind(record.kind); setCapacity(record.capacity); setColor(record.color ?? null); setDescription(record.description ?? ''); setNotes(record.notes ?? ''); setDepartment(record.team_department ?? null); setEquipment(record.equipment ?? []); }}>{t('hqb.common.reset')}</button>}
          </div>
        )}
      </form>

      <DeskPanel b={b} roomId={record.id} people={people} peopleReady={peopleReady} editable={editable} />

      {editable && (
        <section className="hqbSection" aria-labelledby="hqb-actions-title">
          <h3 id="hqb-actions-title">{t('hqb.room.actions')}</h3>
          <label className="hqbCheck"><input type="checkbox" checked={copyDesks} onChange={(e) => setCopyDesks(e.target.checked)} /> <span>{t('hqb.room.copyDesks')}</span></label>
          <button type="button" disabled={b.busy} onClick={() => { void b.copyRoom(record.id, copyDesks); }}>{t('hqb.room.copy')}</button>
          {confirmDelete ? (
            <div className="hqbConfirm" role="alertdialog" aria-label={t('hqb.room.deleteTitle')}>
              <p>{deskCount > 0 ? t('hqb.room.deleteBlocked', { count: deskCount }) : t('hqb.room.deleteQuestion', { name: record.name })}</p>
              <div className="hqbRow">
                <button type="button" className="hqbDanger" disabled={b.busy || deskCount > 0} onClick={() => { void b.deleteRoom(record.id); }}>{t('hqb.room.delete')}</button>
                <button type="button" onClick={() => setConfirmDelete(false)}>{t('hqb.common.cancel')}</button>
              </div>
            </div>
          ) : <button type="button" className="hqbQuiet" onClick={() => setConfirmDelete(true)}>{t('hqb.room.delete')}</button>}
        </section>
      )}
    </div>
  );
}
