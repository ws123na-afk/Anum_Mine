import type { OfficeLayoutDiff } from '@anum/contracts';
import { t } from '../lib/i18n.ts';
import { miniView } from './historyModel.ts';

interface Props { diff: OfficeLayoutDiff; floor: { width_m: number | null; depth_m: number | null }; label: string }

/**
 * A small plan of one revision with what changed marked on it. Colour is never the only signal: every
 * changed room also has its name drawn on it and is listed as text under the plan.
 */
export function MiniPlan({ diff, floor, label }: Props) {
  const view = miniView(diff, floor);
  const pad = Math.max(1, view.width / 40);
  const text = Math.max(1.1, view.width / 38);
  const roomsById = new Map(diff.rooms.map((r) => [r.id, r]));
  return (
    <svg
      className="hqhPlan" role="img" aria-label={label}
      viewBox={`${view.minX - pad} ${view.minZ - pad} ${view.width + pad * 2} ${view.depth + pad * 2}`}
      preserveAspectRatio="xMidYMid meet" data-testid="history-plan"
    >
      <title>{label}</title>
      {floor.width_m != null && floor.depth_m != null && <rect className="hqhFloor" x={0} y={0} width={floor.width_m} height={floor.depth_m} />}
      {diff.rooms.map((room) => room.from_box && (
        <rect key={`from-${room.id}`} className="hqhFrom" x={room.from_box.x} y={room.from_box.z} width={room.from_box.width} height={room.from_box.depth} />
      ))}
      {diff.rooms.map((room) => room.box && (
        <g key={room.id} className="hqhRoom" data-status={room.status} data-room-id={room.id}>
          <rect x={room.box.x} y={room.box.z} width={room.box.width} height={room.box.depth} />
          {room.status !== 'same' && (
            <text x={room.box.x + room.box.width / 2} y={room.box.z + room.box.depth / 2} fontSize={text} textAnchor="middle" dominantBaseline="middle">
              {room.name}
            </text>
          )}
        </g>
      ))}
      {diff.desks.map((desk) => {
        const room = roomsById.get(desk.room_id)?.box;
        if (!room || desk.x == null || desk.z == null) return null;
        return <circle key={desk.id} className="hqhDesk" cx={room.x + desk.x} cy={room.z + desk.z} r={Math.max(0.35, view.width / 190)} />;
      })}
      <desc>{t('hqh.plan.desc')}</desc>
    </svg>
  );
}
