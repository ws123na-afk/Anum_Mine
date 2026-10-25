// The 2D plan of one floor, drawn as SVG in metres: grid, floor outline, rooms, desks, resize
// handles. Mouse, touch and keyboard all edit the same staged draft; nothing is saved here.
import { memo, useCallback, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import type { OfficeRoomKind } from '@anum/contracts';
import { t } from '../lib/i18n.ts';
import { useLocale } from '../useLocale';
import type { DraftDesk, DraftRoom } from './draftModel.ts';
import {
  HANDLES, clampDesk, planView, moveRect, overlaps, resizeByKey, resizeRect, withinBounds, round3,
  type Bounds, type Handle, type Rect,
} from './geometry.ts';

export interface CanvasRoom { id: string; name: string; kind: OfficeRoomKind; color: string | null; draft: DraftRoom }
export interface CanvasDesk { id: string; label: string; draft: DraftDesk; seated: boolean }

interface Props {
  rooms: readonly CanvasRoom[];
  desks: readonly CanvasDesk[];
  bounds: Bounds | null;
  severity: ReadonlyMap<string, 'error' | 'warning'>;
  selectedRoomId: string;
  selectedDeskId: string;
  /** Grid step in metres; 0 = snapping off. */
  snapStep: number;
  pxPerMetre: number;
  editable: boolean;
  onSelectRoom: (id: string) => void;
  onSelectDesk: (id: string) => void;
  onMoveRoom: (id: string, rect: Rect, key?: string | null) => void;
  onMoveDesk: (id: string, x: number, z: number, key?: string | null) => void;
  onRotate: (id: string) => void;
  onCycle: (direction: 1 | -1) => void;
  onAnnounce: (text: string) => void;
}

interface RoomDrag { id: string; handle?: Handle; start: Rect; rect: Rect; originX: number; originY: number; moved: boolean }
interface DeskDrag { kind: 'desk'; id: string; roomId: string; startX: number; startZ: number; x: number; z: number; originX: number; originY: number; moved: boolean }
type Drag = (RoomDrag & { kind: 'move' }) | (RoomDrag & { kind: 'resize' }) | DeskDrag;

const DRAG_THRESHOLD_PX = 3;
export const PLAN_MARGIN_M = 1;
const MARGIN_M = PLAN_MARGIN_M;

const fmt = (n: number): string => String(round3(n));
const rectOfDraft = (d: DraftRoom): Rect => ({ id: d.id, x: d.x, z: d.z, width: d.width, depth: d.depth });

interface ShapeProps {
  room: CanvasRoom;
  rect: Rect;
  ppm: number;
  selected: boolean;
  tone: 'error' | 'warning' | 'ok';
  editable: boolean;
  roving: boolean;
  dragging: boolean;
  /** Only here so a language switch redraws the memoised shapes. */
  locale: string;
  onPointerDown: (e: PointerEvent<SVGGElement>, room: CanvasRoom) => void;
  onKeyDown: (e: KeyboardEvent<SVGGElement>, room: CanvasRoom) => void;
  onFocus: (id: string) => void;
}

/** A shape redraws only when something it shows changed, so moving one of 500 rooms redraws one. Handlers are stable (see `latest`). */
const sameShape = (a: ShapeProps, b: ShapeProps): boolean =>
  a.room.id === b.room.id && a.room.name === b.room.name && a.room.kind === b.room.kind && a.room.color === b.room.color
  && a.rect.x === b.rect.x && a.rect.z === b.rect.z && a.rect.width === b.rect.width && a.rect.depth === b.rect.depth
  && a.ppm === b.ppm && a.selected === b.selected && a.tone === b.tone && a.editable === b.editable && a.roving === b.roving && a.dragging === b.dragging && a.locale === b.locale;

const RoomShape = memo(function RoomShape({ room, rect, ppm, selected, tone, editable, roving, dragging, locale: _locale, onPointerDown, onKeyDown, onFocus }: ShapeProps) {
  const fontSize = 12 / ppm;
  const chars = Math.max(0, Math.floor((rect.width * ppm - 10) / 7));
  const label = room.name.length > chars ? `${room.name.slice(0, Math.max(0, chars - 1))}…` : room.name;
  const problem = tone === 'error' ? t('hqb.canvas.problem') : tone === 'warning' ? t('hqb.canvas.warning') : '';
  const aria = t('hqb.canvas.roomLabel', { name: room.name, kind: t(`hqb.kind.${room.kind}`), x: fmt(rect.x), z: fmt(rect.z), width: fmt(rect.width), depth: fmt(rect.depth) }) + (problem ? `. ${problem}` : '');
  return (
    <g
      className="hqbRoom" data-color={room.color ?? ''} data-kind={room.kind} data-tone={tone} data-selected={selected ? 'true' : undefined}
      data-dragging={dragging ? 'true' : undefined} data-editable={editable ? 'true' : undefined} data-room-id={room.id}
      role="button" tabIndex={roving ? 0 : -1} aria-label={aria} aria-pressed={selected}
      onPointerDown={(e) => onPointerDown(e, room)} onKeyDown={(e) => onKeyDown(e, room)} onFocus={() => onFocus(room.id)}
    >
      <title>{room.name}</title>
      <rect className="hqbRoomBody" x={rect.x} y={rect.z} width={rect.width} height={rect.depth} />
      {rect.width * ppm > 34 && rect.depth * ppm > 20 && (
        <text className="hqbRoomName" x={rect.x + rect.width / 2} y={rect.z + rect.depth / 2} fontSize={fontSize} strokeWidth={3 / ppm} textAnchor="middle" dominantBaseline="middle">{label}</text>
      )}
    </g>
  );
}, sameShape);

export function PlanCanvas({
  rooms, desks, bounds, severity, selectedRoomId, selectedDeskId, snapStep, pxPerMetre: ppm, editable,
  onSelectRoom, onSelectDesk, onMoveRoom, onMoveDesk, onRotate, onCycle, onAnnounce,
}: Props) {
  const locale = useLocale();
  const [drag, setDrag] = useState<Drag | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const coarse = typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches;

  const placed = useMemo(() => rooms.filter((r) => r.draft.placed), [rooms]);
  const placedById = useMemo(() => new Map(placed.map((r) => [r.id, r])), [placed]);
  const view = useMemo(() => planView(placed.map((r) => rectOfDraft(r.draft)), bounds), [placed, bounds]);

  const step = snapStep > 0 ? snapStep : 0;
  const keyStep = step > 0 ? step : 0.1;
  const gridStep = step > 0 ? Math.max(step, 0.5) : 1;
  const selectedIndex = placed.findIndex((r) => r.id === selectedRoomId);
  const rovingId = selectedIndex >= 0 ? selectedRoomId : placed[0]?.id ?? '';

  const liveRect = (room: CanvasRoom): Rect => (drag && drag.kind !== 'desk' && drag.id === room.id ? drag.rect : rectOfDraft(room.draft));

  // While a room is dragged, the rooms it would overlap turn red with it, before anything is saved.
  const dragTone = useMemo(() => {
    if (!drag || drag.kind === 'desk') return null;
    const bad = new Set<string>();
    if (!withinBounds(drag.rect, bounds)) bad.add(drag.id);
    for (const other of placed) {
      if (other.id !== drag.id && overlaps(drag.rect, rectOfDraft(other.draft))) { bad.add(drag.id); bad.add(other.id); }
    }
    return bad;
  }, [drag, placed, bounds]);

  const toneOf = (id: string): 'error' | 'warning' | 'ok' => {
    if (dragTone) return dragTone.has(id) ? 'error' : id === (drag as { id: string }).id ? 'ok' : severity.get(id) ?? 'ok';
    return severity.get(id) ?? 'ok';
  };

  const begin = (next: Drag, e: PointerEvent<Element>) => {
    dragRef.current = next;
    setDrag(next);
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  };

  const onRoomPointerDown = (e: PointerEvent<SVGGElement>, room: CanvasRoom) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    e.stopPropagation();
    const wasSelected = room.id === selectedRoomId;
    onSelectRoom(room.id);
    // A touch on a room that is not selected only selects it, so the page can still scroll with a finger.
    if (!editable || (e.pointerType === 'touch' && !wasSelected)) return;
    const start = rectOfDraft(room.draft);
    begin({ kind: 'move', id: room.id, start, rect: start, originX: e.clientX, originY: e.clientY, moved: false }, e);
  };

  const onHandleDown = (e: PointerEvent<SVGRectElement>, handle: Handle, room: CanvasRoom) => {
    e.stopPropagation();
    const start = rectOfDraft(room.draft);
    begin({ kind: 'resize', id: room.id, handle, start, rect: start, originX: e.clientX, originY: e.clientY, moved: false }, e);
  };

  const onDeskDown = (e: PointerEvent<SVGGElement>, desk: CanvasDesk) => {
    e.stopPropagation();
    onSelectRoom(desk.draft.roomId);
    onSelectDesk(desk.id);
    if (!editable || desk.draft.posX == null || desk.draft.posZ == null) return;
    if (e.pointerType === 'touch' && desk.id !== selectedDeskId) return;
    begin({ kind: 'desk', id: desk.id, roomId: desk.draft.roomId, startX: desk.draft.posX, startZ: desk.draft.posZ, x: desk.draft.posX, z: desk.draft.posZ, originX: e.clientX, originY: e.clientY, moved: false }, e);
  };

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const current = dragRef.current;
    if (!current) return;
    const px = e.clientX - current.originX;
    const py = e.clientY - current.originY;
    if (!current.moved && Math.hypot(px, py) < DRAG_THRESHOLD_PX) return;
    const dx = px / ppm;
    const dz = py / ppm;
    let next: Drag;
    if (current.kind === 'move') next = { ...current, moved: true, rect: moveRect(current.start, dx, dz, step) };
    else if (current.kind === 'resize') next = { ...current, moved: true, rect: resizeRect(current.start, current.handle as Handle, dx, dz, step) };
    else {
      const room = placedById.get(current.roomId)?.draft;
      const point = room ? clampDesk(room, current.startX + dx, current.startZ + dz, step > 0 ? 0.25 : 0) : { x: current.startX, z: current.startZ };
      next = { ...current, moved: true, x: point.x, z: point.z };
    }
    dragRef.current = next;
    setDrag(next);
  };

  const finish = (commitChange: boolean) => {
    const current = dragRef.current;
    dragRef.current = null;
    setDrag(null);
    if (!current || !current.moved || !commitChange) return;
    if (current.kind === 'desk') {
      onMoveDesk(current.id, current.x, current.z);
      onAnnounce(t('hqb.live.deskMoved', { x: fmt(current.x), z: fmt(current.z) }));
    } else {
      onMoveRoom(current.id, current.rect);
      const name = placedById.get(current.id)?.name ?? '';
      onAnnounce(current.kind === 'move'
        ? t('hqb.live.moved', { name, x: fmt(current.rect.x), z: fmt(current.rect.z) })
        : t('hqb.live.resized', { name, width: fmt(current.rect.width), depth: fmt(current.rect.depth) }));
    }
  };

  const onRoomKeyDown = (e: KeyboardEvent<SVGGElement>, room: CanvasRoom) => {
    if (e.key === 'Escape') { onSelectRoom(''); return; }
    if (e.key === '[' || e.key === ']') { e.preventDefault(); onCycle(e.key === ']' ? 1 : -1); return; }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelectRoom(room.id); return; }
    if (!editable) return;
    const rect = rectOfDraft(room.draft);
    if (e.key === 'r' || e.key === 'R') { e.preventDefault(); onRotate(room.id); onAnnounce(t('hqb.live.rotated', { name: room.name })); return; }
    const arrows = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'] as const;
    const arrow = arrows.find((a) => a === e.key);
    if (!arrow) return;
    e.preventDefault();
    if (e.shiftKey) {
      const next = resizeByKey(rect, arrow, keyStep);
      onMoveRoom(room.id, next, `resize:${room.id}`);
      onAnnounce(t('hqb.live.resized', { name: room.name, width: fmt(next.width), depth: fmt(next.depth) }));
      return;
    }
    const dx = arrow === 'ArrowLeft' ? -keyStep : arrow === 'ArrowRight' ? keyStep : 0;
    const dz = arrow === 'ArrowUp' ? -keyStep : arrow === 'ArrowDown' ? keyStep : 0;
    const next = moveRect(rect, dx, dz, step > 0 ? step : 0.1);
    onMoveRoom(room.id, next, `move:${room.id}`);
    onAnnounce(t('hqb.live.moved', { name: room.name, x: fmt(next.x), z: fmt(next.z) }));
  };

  const latest = useRef({ down: onRoomPointerDown, key: onRoomKeyDown, focus: onSelectRoom });
  latest.current = { down: onRoomPointerDown, key: onRoomKeyDown, focus: onSelectRoom };
  const stableDown = useCallback((e: PointerEvent<SVGGElement>, room: CanvasRoom) => latest.current.down(e, room), []);
  const stableKey = useCallback((e: KeyboardEvent<SVGGElement>, room: CanvasRoom) => latest.current.key(e, room), []);
  const stableFocus = useCallback((id: string) => latest.current.focus(id), []);

  const selected = placedById.get(selectedRoomId);
  const selectedRect = selected ? liveRect(selected) : null;
  const handleSize = (coarse ? 18 : 10) / ppm;
  const widthPx = Math.ceil((view.width + MARGIN_M * 2) * ppm);
  const heightPx = Math.ceil((view.depth + MARGIN_M * 2) * ppm);

  return (
    <svg
      ref={svgRef} className="hqbSvg" role="group" aria-label={t('hqb.canvas.label')} aria-describedby="hqb-keys" data-touch-lock={editable && (selectedRoomId || selectedDeskId) ? 'true' : undefined}
      width={widthPx} height={heightPx} viewBox={`${-MARGIN_M} ${-MARGIN_M} ${view.width + MARGIN_M * 2} ${view.depth + MARGIN_M * 2}`}
      onPointerMove={onMove} onPointerUp={() => finish(true)} onPointerCancel={() => finish(false)}
      onKeyDown={(e) => { if (e.key === 'Escape' && dragRef.current) { e.stopPropagation(); finish(false); } }}
    >
      <defs>
        <pattern id="hqbGrid" width={gridStep} height={gridStep} patternUnits="userSpaceOnUse">
          <path d={`M ${gridStep} 0 L 0 0 0 ${gridStep}`} className="hqbGridLine" />
        </pattern>
        <pattern id="hqbGridMajor" width={5} height={5} patternUnits="userSpaceOnUse">
          <path d="M 5 0 L 0 0 0 5" className="hqbGridMajor" />
        </pattern>
      </defs>
      <rect className="hqbBackdrop" x={-MARGIN_M} y={-MARGIN_M} width={view.width + MARGIN_M * 2} height={view.depth + MARGIN_M * 2} onPointerDown={() => { onSelectRoom(''); onSelectDesk(''); }} />
      <rect className="hqbFloor" x={0} y={0} width={bounds?.width ?? view.width} height={bounds?.depth ?? view.depth} data-bounded={bounds ? 'true' : 'false'} pointerEvents="none" />
      <rect x={0} y={0} width={bounds?.width ?? view.width} height={bounds?.depth ?? view.depth} fill="url(#hqbGrid)" pointerEvents="none" />
      <rect x={0} y={0} width={bounds?.width ?? view.width} height={bounds?.depth ?? view.depth} fill="url(#hqbGridMajor)" pointerEvents="none" />
      {bounds && <text className="hqbFloorLabel" x={bounds.width - 0.3} y={bounds.depth - 0.3} textAnchor="end" fontSize={11 / ppm}>{t('hqb.canvas.floorSize', { width: fmt(bounds.width), depth: fmt(bounds.depth) })}</text>}

      <g className="hqbRooms">
        {placed.map((room) => (
          <RoomShape
            key={room.id} room={room} rect={liveRect(room)} ppm={ppm} selected={room.id === selectedRoomId} tone={toneOf(room.id)}
            editable={editable} roving={room.id === rovingId} dragging={drag?.kind !== 'desk' && drag?.id === room.id} locale={locale}
            onPointerDown={stableDown} onKeyDown={stableKey} onFocus={stableFocus}
          />
        ))}
      </g>

      <g className="hqbDesks">
        {desks.map((desk) => {
          const room = placedById.get(desk.draft.roomId);
          if (!room || desk.draft.posX == null || desk.draft.posZ == null) return null;
          const moving = drag?.kind === 'desk' && drag.id === desk.id;
          const rect = liveRect(room);
          const x = rect.x + (moving ? drag.x : desk.draft.posX);
          const z = rect.z + (moving ? drag.z : desk.draft.posZ);
          const outside = desk.draft.posX > rect.width + 1e-6 || desk.draft.posZ > rect.depth + 1e-6;
          return (
            <g key={desk.id} className="hqbDesk" aria-hidden="true" data-seated={desk.seated ? 'true' : 'false'} data-selected={desk.id === selectedDeskId ? 'true' : undefined} data-outside={outside ? 'true' : undefined} onPointerDown={(e) => onDeskDown(e, desk)}>
              <circle cx={x} cy={z} r={Math.max(0.28, 5 / ppm)} />
              {ppm >= 14 && <title>{desk.label}</title>}
            </g>
          );
        })}
      </g>

      {selected && selectedRect && editable && (
        <g className="hqbHandles">
          {HANDLES.map((handle) => {
            const left = selectedRect.x;
            const top = selectedRect.z;
            const right = selectedRect.x + selectedRect.width;
            const bottom = selectedRect.z + selectedRect.depth;
            const cx = handle.includes('w') ? left : handle.includes('e') ? right : (left + right) / 2;
            const cz = handle.includes('n') ? top : handle.includes('s') ? bottom : (top + bottom) / 2;
            return (
              <rect
                key={handle} className="hqbHandle" data-handle={handle} x={cx - handleSize / 2} y={cz - handleSize / 2}
                width={handleSize} height={handleSize} onPointerDown={(e) => onHandleDown(e, handle, selected)}
              />
            );
          })}
        </g>
      )}
    </svg>
  );
}
