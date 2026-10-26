import { useEffect, useMemo, useState } from 'react';
import { t } from '../lib/i18n.ts';
import type { BuilderController } from './useBuilderState.ts';
import { DESK_KINDS } from './labels.ts';
import { round3 } from './geometry.ts';

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
