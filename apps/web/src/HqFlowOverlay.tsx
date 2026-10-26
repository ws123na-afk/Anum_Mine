import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Hourglass } from 'lucide-react';
import type { OfficeFlowSnapshot } from '@anum/contracts';
import type { CameraState } from './lib/headquartersSpatial';
import type { SceneFloor } from './lib/headquartersProjection';
import { projectToStage, roomBadgePoint, type StagePoint } from './lib/hqFlowProject';
import type { FlowChange } from './lib/officeFlow';
import type { ChangeBatch } from './useOfficeFlow';
import { t } from './lib/i18n';
import { roomName } from './lib/registryNames.ts';
import { useLocale } from './useLocale';
import './hqFlow.css';

const TRAVEL_MS = 900;
const MARKER_LIFE_MS = 5000;

interface Marker { id: string; title: string; kind: FlowChange['kind']; fromRoom: string | null; toRoom: string }

/** Badges per office and travelling markers over the 3D view. A badge exists because stored
 * records place work in that office. A marker appears only when two consecutive answers of the
 * server disagree about where an item is, and it travels only between two real offices. */
export function HqFlowOverlay({ floor, camera, snapshot, batch, selectedRoomId, onSelectRoom }: {
  floor: SceneFloor; camera: CameraState; snapshot: OfficeFlowSnapshot | null; batch: ChangeBatch;
  selectedRoomId: string | null; onSelectRoom: (roomId: string) => void;
}) {
  useLocale();
  const box = useRef<HTMLDivElement>(null);
  const [aspect, setAspect] = useState(1.6);
  const [markers, setMarkers] = useState<Marker[]>([]);
  const [flying, setFlying] = useState(false);
  const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => { const r = el.getBoundingClientRect(); if (r.height > 0) setAspect(r.width / r.height); });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const roomPoint = useMemo(() => {
    const map = new Map<string, StagePoint | null>();
    for (const room of floor.rooms) map.set(room.id, projectToStage(camera, roomBadgePoint(room), aspect));
    return map;
  }, [floor, camera, aspect]);
  const roomPointRef = useRef(roomPoint);
  roomPointRef.current = roomPoint;

  useEffect(() => {
    if (!batch.changes.length) return;
    const next: Marker[] = [];
    for (const change of batch.changes.slice(0, 8)) {
      if (!change.toRoomId || !roomPointRef.current.get(change.toRoomId)) continue;
      const fromRoom = change.kind === 'moved' && change.fromRoomId && roomPointRef.current.get(change.fromRoomId) ? change.fromRoomId : null;
      next.push({ id: `${batch.id}:${change.key}`, title: change.title, kind: change.kind, fromRoom, toRoom: change.toRoomId });
    }
    if (!next.length) return;
    setMarkers((old) => [...old, ...next].slice(-12));
    setFlying(false);
    const frame = requestAnimationFrame(() => requestAnimationFrame(() => setFlying(true)));
    const timer = setTimeout(() => setMarkers((old) => old.filter((m) => !next.includes(m))), MARKER_LIFE_MS);
    return () => { cancelAnimationFrame(frame); clearTimeout(timer); };
  }, [batch.id]);

  const rollup = snapshot?.rooms ?? {};
  return <div className="hqFlowOverlay" ref={box} data-testid="hq-flow-overlay">
    {floor.rooms.map((room) => {
      const r = rollup[room.id];
      const p = roomPoint.get(room.id);
      if (!r || !p || r.items === 0) return null;
      const label = t('hqFlow.badge', { room: roomName(room), items: r.items, stuck: r.stuck, waiting: r.waiting });
      return <button key={room.id} type="button" className={`hqFlowBadge${room.id === selectedRoomId ? ' selected' : ''}`}
        data-worst={r.worst ?? 'none'} data-room={room.id} style={{ left: `${p.left}%`, top: `${p.top}%` }}
        aria-label={label} title={label} onClick={() => onSelectRoom(room.id)}>
        <span>{t('hqFlow.badgeOpen', { count: r.items })}</span>
        {r.waiting > 0 && <span className="hqFlowBadgeWait"><Hourglass size={11} aria-hidden="true" />{r.waiting}</span>}
        {r.stuck > 0 && <span className="hqFlowBadgeStuck"><AlertTriangle size={11} aria-hidden="true" />{r.stuck}</span>}
      </button>;
    })}
    {markers.map((m) => {
      const to = roomPoint.get(m.toRoom);
      const from = m.fromRoom ? roomPoint.get(m.fromRoom) ?? null : null;
      if (!to) return null;
      const travelling = !!from && !reduced;
      const at = travelling && !flying ? from! : to;
      return <span key={m.id} className="hqFlowMarker" data-kind={m.kind} data-travel={travelling ? 'yes' : 'no'}
        style={{ left: `${at.left}%`, top: `${at.top}%`, transitionDuration: travelling ? `${TRAVEL_MS}ms` : '0ms' }} aria-hidden="true">
        <i /><b>{m.title}</b>
      </span>;
    })}
  </div>;
}
