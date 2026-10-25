import { useMemo, useState } from 'react';
import type { OfficeRoomKind, OfficeRoomTemplate } from '@anum/contracts';
import { getLocale, t, type MessageKey } from '../lib/i18n.ts';
import { ROOM_KINDS } from './labels.ts';
import { roomSeverity } from './validate.ts';
import type { BuilderController } from './useBuilderState.ts';

const PAGE = 60;

interface Props { b: BuilderController; editable: boolean }

/** The floor's rooms (searchable, paged), rooms still waiting for a place, and the ways to add one. */
export function RoomsTab({ b, editable }: Props) {
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(PAGE);
  const [withDesks, setWithDesks] = useState(true);
  const [name, setName] = useState('');
  const [kind, setKind] = useState<OfficeRoomKind>('office');
  const [capacity, setCapacity] = useState(8);
  const severity = useMemo(() => roomSeverity(b.issues), [b.issues]);
  const rooms = useMemo(() => {
    const q = query.trim().toLowerCase();
    return b.floorRecords
      .filter((r) => !q || r.name.toLowerCase().includes(q) || (r.name_ar ?? '').toLowerCase().includes(q))
      .sort((a, c) => a.name.localeCompare(c.name));
  }, [b.floorRecords, query]);
  const unplaced = b.floorRecords.filter((r) => b.present.rooms[r.id] && !b.present.rooms[r.id].placed);
  const arabic = getLocale() === 'ar';
  const templateName = (tpl: OfficeRoomTemplate) => (arabic ? tpl.name_ar : tpl.name);

  return (
    <div className="hqbRoomsTab">
      <label className="hqbField"><span>{t('hqb.rooms.search')}</span>
        <input type="search" value={query} dir="auto" placeholder={t('hqb.rooms.searchPlaceholder')} onChange={(e) => { setQuery(e.target.value); setLimit(PAGE); }} />
      </label>

      {unplaced.length > 0 && (
        <section className="hqbCallout" aria-label={t('hqb.rooms.unplacedTitle')}>
          <h3>{t('hqb.rooms.unplacedTitle', { count: unplaced.length })}</h3>
          <ul className="hqbList">
            {unplaced.map((r) => (
              <li key={r.id}>
                <span dir="auto">{r.name}</span>
                {editable && <button type="button" onClick={() => b.placeRoom(r.id)}>{t('hqb.room.placeOnPlan')}</button>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <ul className="hqbList hqbRoomList" aria-label={t('hqb.rooms.listLabel', { count: rooms.length })}>
        {rooms.slice(0, limit).map((r) => {
          const tone = severity.get(r.id);
          return (
            <li key={r.id}>
              <button type="button" className="hqbRoomRow" aria-pressed={r.id === b.selectedRoomId} data-tone={tone} onClick={() => b.setSelectedRoomId(r.id)}>
                <strong dir="auto">{arabic && r.name_ar ? r.name_ar : r.name}</strong>
                <small>{t(`hqb.kind.${r.kind}` as MessageKey)} · {t('hqb.rooms.capacity', { count: r.capacity })}{tone === 'error' ? ` · ${t('hqb.canvas.problem')}` : ''}</small>
              </button>
            </li>
          );
        })}
      </ul>
      {rooms.length === 0 && <p className="hqbHint">{query ? t('hqb.rooms.noMatch') : t('hqb.rooms.empty')}</p>}
      {rooms.length > limit && <button type="button" onClick={() => setLimit(limit + PAGE)}>{t('hqb.rooms.more', { count: rooms.length - limit })}</button>}

      {editable && (
        <section className="hqbSection" aria-labelledby="hqb-add-title">
          <h3 id="hqb-add-title">{t('hqb.add.title')}</h3>
          {b.catalogueFailed && <p className="hqbHint" role="status">{t('hqb.add.templatesUnavailable')}</p>}
          {b.catalogue && (
            <>
              <p className="hqbHint">{t('hqb.add.templatesHint')}</p>
              <label className="hqbCheck"><input type="checkbox" checked={withDesks} onChange={(e) => setWithDesks(e.target.checked)} /> <span>{t('hqb.add.withDesks')}</span></label>
              <ul className="hqbTemplates">
                {b.catalogue.items.map((tpl) => (
                  <li key={tpl.id}>
                    <button type="button" disabled={b.busy || !b.floorId} data-template={tpl.id} onClick={() => { void b.addFromTemplate(tpl, withDesks && tpl.desks > 0); }}>
                      <strong>{templateName(tpl)}</strong>
                      <small>{t('hqb.add.templateMeta', { width: tpl.width, depth: tpl.depth, capacity: tpl.capacity })}{tpl.desks > 0 ? ` · ${t('hqb.add.templateDesks', { count: tpl.desks })}` : ''}</small>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
          <form className="hqbBlank" onSubmit={(e) => { e.preventDefault(); if (name.trim()) void b.addBlank({ name, kind, capacity }).then((ok) => { if (ok) setName(''); }); }}>
            <h4>{t('hqb.add.blankTitle')}</h4>
            <label className="hqbField"><span>{t('hqb.room.name')}</span><input value={name} maxLength={100} dir="auto" required onChange={(e) => setName(e.target.value)} /></label>
            <div className="hqbRow">
              <label className="hqbGrow"><span>{t('hqb.room.kind')}</span>
                <select value={kind} onChange={(e) => setKind(e.target.value as OfficeRoomKind)}>{ROOM_KINDS.map((k) => <option key={k} value={k}>{t(`hqb.kind.${k}` as MessageKey)}</option>)}</select>
              </label>
              <label className="hqbGrow"><span>{t('hqb.room.capacity')}</span>
                <input type="number" min={1} max={5000} value={capacity} onChange={(e) => setCapacity(Math.round(Number(e.target.value)) || 1)} />
              </label>
            </div>
            <button type="submit" disabled={b.busy || !name.trim() || !b.floorId}>{t('hqb.add.blank')}</button>
          </form>
        </section>
      )}
    </div>
  );
}
