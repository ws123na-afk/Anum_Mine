/**
 * The billboard card of a building: an HTML overlay pinned above the board (docs/ANUM_MINE_HQ_BILLBOARDS.md). Everything it prints comes
 * from `billboardModel` (fixed messages and real record fields). The header is a button (select, press again to open), a focused card has
 * a Close button, the details are plain text in reading order, and the Open button enters the building. `lib/hqLabels.ts` decides the density through the wrapper's
 * `data-density`: full shows everything, compact the name and one line, dot only the board glyph; focus brings a parked card forward.
 */
import { ArrowRight, X } from 'lucide-react';
import { t } from '../lib/i18n.ts';
import type { BillboardModel, BillboardRow } from './billboardModel.ts';
import './hqBillboards.css';

export function BillboardCard(props: {
  model: BillboardModel; selected: boolean; focused: boolean; onActivate: () => void; onOpen: () => void; onHover: (on: boolean) => void; onClose: () => void;
}) {
  const m = props.model;
  // A row without a heading (the verified time) belongs to the row before it.
  const groups: Array<{ row: BillboardRow; times: BillboardRow[] }> = [];
  const LIVE = new Set(['presence', 'presenceTime', 'work', 'workTime']);
  for (const row of m.rows) { if (row.label === null && groups.length) groups[groups.length - 1].times.push(row); else groups.push({ row, times: [] }); }
  const aboutGroups = groups.filter((g) => !LIVE.has(g.row.field));
  const liveGroups = groups.filter((g) => LIVE.has(g.row.field));
  return (
    <section
      className="hqBillboard" role="group" aria-label={t('hqBillboard.group', { name: m.name })} data-testid="hq-billboard" data-building={m.id}
      data-selected={props.selected} data-focused={props.focused} data-status={m.status} onPointerEnter={(e) => { if (e.pointerType === 'mouse') props.onHover(true); }} onPointerLeave={(e) => { if (e.pointerType === 'mouse') props.onHover(false); }}
    >
      <div className="hqBbTop">
      <button
        type="button" className="hqBbHead" aria-pressed={props.selected} title={m.name}
        aria-label={t('hqBillboard.headAria', { name: m.name, summary: m.summary })} onClick={props.onActivate}
      >
        <span className="hqBbGlyph" aria-hidden="true" />
        <span className="hqBbTitle" aria-hidden="true"><strong dir="auto">{m.name}</strong><small dir="auto">{m.kindText ?? m.summary}</small></span>
        <span className="hqStatusDot" data-status={m.status} aria-hidden="true" />
      </button>
      {/* A focused card always has its own Close (Escape does the same): it lets go of the building, so the card and its details close.
          A press does not move focus to it: a focus unfolds a folded card (`:focus-within`), which would move the button away before the
          release and the click would be lost. The keyboard still reaches it with Tab. */}
      {props.focused && (
        <button type="button" className="hqBbClose" aria-label={t('hqBillboard.close')} title={t('hqBillboard.close')} onMouseDown={(e) => e.preventDefault()} onClick={props.onClose}>
          <X aria-hidden="true" />
        </button>
      )}
      </div>
      <div className="hqBbBody">
        <div className="hqBbCols">
          {([['about', aboutGroups], ['live', liveGroups]] as const).map(([name, list]) => (
            <dl key={name} className="hqBbRows" data-col={name}>
              {list.map(({ row, times }, i) => (
                <div key={i} className="hqBbRow" data-field={row.field} data-connected={row.connected ? 'true' : 'false'} data-stale={row.stale ? 'true' : 'false'}>
                  <dt>{row.label}</dt>
                  <dd dir="auto" title={row.parts ? undefined : row.value}>{row.parts ? <span className="hqBbTotal">{row.value}</span> : row.value}
                    {row.parts && (
                      <ul className="hqBbParts">{row.parts.map((p) => <li key={p.key} data-part={p.key}><span>{p.label}</span> <b>{p.value}</b></li>)}</ul>
                    )}
                  </dd>
                  {times.map((time, k) => <dd key={k} className="hqBbTime" data-field={time.field} data-stale={time.stale ? 'true' : 'false'}>{time.value}</dd>)}
                </div>
              ))}
            </dl>
          ))}
        </div>
        {m.accessible
          ? <button type="button" className="hqBbOpen" aria-label={t('hqBillboard.openAria', { name: m.name })} onClick={props.onOpen}><span dir="auto">{m.openLabel}</span><ArrowRight aria-hidden="true" className="flip" /></button>
          : <p className="hqBbNote">{m.unavailableText}</p>}
      </div>
    </section>
  );
}
