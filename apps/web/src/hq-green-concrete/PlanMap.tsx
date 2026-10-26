import { t } from '../lib/i18n';
import { rotOfFacing } from '../lib/hqSeatTables';
import type { GreenSeat } from '@anum/contracts';

const W = 16, D = 12;

/** A small plan of the Command Center: the screen wall, the L shaped tables with their seat numbers, the manager desk and the door. */
export function PlanMap({ seats, width = W, depth = D }: { seats: readonly GreenSeat[]; width?: number; depth?: number }) {
  const spots = new Map<string, GreenSeat[]>();
  for (const s of seats) spots.set(`${s.pos_x}:${s.pos_z}`, [...(spots.get(`${s.pos_x}:${s.pos_z}`) ?? []), s]);
  return (
    <figure className="hqGcMap">
      <svg viewBox={`-0.5 -0.9 ${width + 1} ${depth + 2}`} role="img" aria-label={t('hqGc.layout.aria')}>
        <rect x="0" y="0" width={width} height={depth} className="room" />
        <rect x="3" y="0.05" width={width - 6} height="0.35" className="wall" />
        <text x={width / 2} y="-0.25" className="cap" textAnchor="middle">{t('hqGc.layout.wall')}</text>
        <rect x={width / 2 - 0.6} y={depth - 0.12} width="1.2" height="0.24" className="door" />
        <text x={width / 2} y={depth + 0.9} className="cap" textAnchor="middle">{t('hqGc.layout.door')}</text>
        {[...spots.values()].map((list) => {
          const s = list[0];
          const across = rotOfFacing(s.facing) % 2 === 0;
          const w = across ? 1.5 : 0.78, h = across ? 0.78 : 1.5;
          const day = list.find((x) => x.shift === 'day');
          const manager = list.find((x) => x.shift === null);
          const label = String(day ? day.seat_order : (list[0].seat_order));
          return (
            <g key={s.desk_id} className={manager ? 'seat manager' : 'seat'}>
              <rect x={s.pos_x - w / 2} y={s.pos_z - h / 2} width={w} height={h} rx="0.08" />
              <text x={s.pos_x} y={s.pos_z + 0.18} textAnchor="middle">{manager ? 'M' : label}</text>
            </g>
          );
        })}
      </svg>
      <figcaption>{t('hqGc.layout.caption')}</figcaption>
    </figure>
  );
}
