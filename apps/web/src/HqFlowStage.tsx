/**
 * Live flow over the 3D building: one badge per room that has stored work, and a marker for an item
 * that the server moved between two answers (`useOfficeFlow` diffs consecutive answers). Both are stage
 * labels, so they follow the camera and share the label layout of the room and agent cards.
 * A marker flies between two real rooms only; with reduced motion it simply appears in the new room.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AlertTriangle, Hourglass } from 'lucide-react';
import type { OfficeFlowSnapshot } from '@anum/contracts';
import type { Vec3 } from './lib/headquartersProjection.ts';
import type { FlowChange } from './lib/officeFlow.ts';
import type { ChangeBatch } from './useOfficeFlow';
import { t } from './lib/i18n.ts';

const TRAVEL_MS = 900;
const MARKER_LIFE_MS = 5000;

type Marker = { id: string; title: string; kind: FlowChange['kind']; from: string | null; to: string; start: number };
export type FlowLabel = Readonly<{ id: string; anchor: Vec3; node: ReactNode; maxDistance?: number; priority?: number }>;

export function useFlowLabels(props: Readonly<{
  rooms: ReadonlyArray<{ id: string; label: string }>;
  anchorOf: (roomId: string) => Vec3 | undefined;
  snapshot: OfficeFlowSnapshot | null | undefined;
  batch: ChangeBatch | undefined;
  selectedRoomId: string | null;
  reducedMotion: boolean;
  onPickRoom: (roomId: string) => void;
}>): FlowLabel[] {
  const { rooms, anchorOf, snapshot, batch, selectedRoomId, reducedMotion, onPickRoom } = props;
  const [markers, setMarkers] = useState<Marker[]>([]);
  const [now, setNow] = useState(0);
  const anchorRef = useRef(anchorOf);
  anchorRef.current = anchorOf;
  const roomIds = useMemo(() => new Set(rooms.map((r) => r.id)), [rooms]);
  const roomIdsRef = useRef(roomIds);
  roomIdsRef.current = roomIds;
  const batchId = batch?.id ?? 0;

  useEffect(() => {
    if (!batch || !batch.changes.length) return;
    const start = performance.now();
    const next: Marker[] = [];
    for (const change of batch.changes.slice(0, 8)) {
      if (!change.toRoomId || !roomIdsRef.current.has(change.toRoomId)) continue;
      const from = change.kind === 'moved' && change.fromRoomId && roomIdsRef.current.has(change.fromRoomId) ? change.fromRoomId : null;
      next.push({ id: `${batch.id}:${change.key}`, title: change.title, kind: change.kind, from, to: change.toRoomId, start });
    }
    if (!next.length) return;
    setMarkers((old) => [...old, ...next].slice(-12));
    const timer = setTimeout(() => setMarkers((old) => old.filter((m) => !next.includes(m))), MARKER_LIFE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId]);

  // Drive the flight: re-render each frame only while a marker is travelling.
  const travelling = !reducedMotion && markers.some((m) => m.from && now - m.start < TRAVEL_MS);
  const flying = !reducedMotion && markers.some((m) => m.from);
  useEffect(() => {
    if (!flying) return;
    let frame = 0;
    const tick = () => {
      const time = performance.now();
      setNow(time);
      if (markers.some((m) => m.from && time - m.start < TRAVEL_MS)) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [flying, markers]);
  void travelling;

  return useMemo(() => {
    const out: FlowLabel[] = [];
    const rollup = snapshot?.rooms ?? {};
    for (const room of rooms) {
      const r = rollup[room.id];
      const base = anchorOf(room.id);
      if (!r || !base || r.items === 0) continue;
      const label = t('hqFlow.badge', { room: room.label, items: r.items, stuck: r.stuck, waiting: r.waiting });
      out.push({
        id: 'flow:' + room.id, anchor: { ...base, y: base.y + 0.4 }, priority: 1.5, maxDistance: 160,
        node: (
          <button type="button" className={`hqFlowBadge hqFlowBadgeStage${room.id === selectedRoomId ? ' selected' : ''}`} data-worst={r.worst ?? 'none'} data-room={room.id}
            aria-label={label} title={label} onClick={() => onPickRoom(room.id)}>
            <span>{t('hqFlow.badgeOpen', { count: r.items })}</span>
            {r.waiting > 0 && <span className="hqFlowBadgeWait"><Hourglass size={11} aria-hidden="true" />{r.waiting}</span>}
            {r.stuck > 0 && <span className="hqFlowBadgeStuck"><AlertTriangle size={11} aria-hidden="true" />{r.stuck}</span>}
          </button>
        ),
      });
    }
    for (const m of markers) {
      const to = anchorOf(m.to);
      if (!to) continue;
      const from = m.from ? anchorOf(m.from) : undefined;
      const travel = !!from && !reducedMotion;
      const p = travel ? Math.min(1, Math.max(0, (now - m.start) / TRAVEL_MS)) : 1;
      const e = p * p * (3 - 2 * p);
      const at = travel && from ? { x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e + 1.2, z: from.z + (to.z - from.z) * e } : { ...to, y: to.y + 1.2 };
      out.push({
        id: 'mk:' + m.id, anchor: at, priority: 0.5,
        node: <span className="hqFlowMarker hqFlowMarkerStage" data-kind={m.kind} data-travel={travel ? 'yes' : 'no'} aria-hidden="true"><i /><b>{m.title}</b></span>,
      });
    }
    return out;
  }, [rooms, anchorOf, snapshot, markers, now, selectedRoomId, reducedMotion, onPickRoom]);
}
