import { MonitorUp, RefreshCcw } from 'lucide-react';
import { STATUS_ORDER, STATUS_TONE } from './lib/campusModel.ts';
import type { WallModel, WallTile } from './lib/hqCommandWall.ts';
import { t, formatNumber, type MessageKey } from './lib/i18n.ts';
import { statusLabel } from './HqChrome';

function Tile({ tile }: { tile: WallTile }) {
  const label = t(`hq.wall.${tile.id}` as MessageKey);
  const text = tile.state === 'ready' ? `${tile.atLeast ? '≥ ' : ''}${formatNumber(tile.value ?? 0)}`
    : tile.state === 'loading' ? t('hq.wall.loading') : tile.state === 'no_access' ? t('hq.wall.noAccess') : t('hq.wall.notConnected');
  return (
    <li className="hqWallTile" data-state={tile.state} data-tile={tile.id}>
      <span className="hqWallLabel">{label}</span>
      <strong className="hqWallValue" dir="auto" title={tile.atLeast ? t('hq.wall.atLeast') : undefined}>{text}</strong>
    </li>
  );
}

/**
 * A wall of dashboards for the operations room. Every number is read from a stored record through an existing endpoint;
 * a source that is down shows "Not connected" and no number.
 */
export function HqCommandWall(props: Readonly<{ model: WallModel; variant: 'panel' | 'scene'; onReload?: () => void }>) {
  const { model } = props;
  const a = model.agents;
  const broken = model.tiles.some((x) => x.state === 'not_connected') || a.state === 'not_connected';
  return (
    <section className={`hqWall hqWall-${props.variant}`} aria-label={t('hq.wall.title')} data-testid={`hq-wall-${props.variant}`}>
      <header className="hqWallHead">
        <MonitorUp aria-hidden="true" />
        <h3>{t('hq.wall.title')}</h3>
        {broken && props.onReload && (
          <button type="button" className="hqIconButton" onClick={props.onReload} aria-label={t('hq.wall.retry')} title={t('hq.wall.retry')}><RefreshCcw aria-hidden="true" /></button>
        )}
      </header>
      <ul className="hqWallGrid">
        {model.tiles.map((tile) => <Tile key={tile.id} tile={tile} />)}
        <li className="hqWallTile hqWallAgents" data-state={a.state} data-tile="agents">
          <span className="hqWallLabel">{t('hq.wall.agents')}</span>
          {a.state === 'ready' ? (
            <>
              <strong className="hqWallValue">{formatNumber(a.total)}</strong>
              <ul className="hqWallStates" aria-label={t('hq.wall.agentsByState')}>
                {STATUS_ORDER.filter((k) => a.byState[k] > 0).map((k) => (
                  <li key={k} className={`tone-${STATUS_TONE[k]}`}><span className="dot" aria-hidden="true" />{statusLabel(k)} <b>{formatNumber(a.byState[k])}</b></li>
                ))}
              </ul>
            </>
          ) : <strong className="hqWallValue" dir="auto">{a.state === 'loading' ? t('hq.wall.loading') : t('hq.wall.notConnected')}</strong>}
        </li>
      </ul>
      {model.asOf && <p className="hqNote">{t('hq.wall.asOf', { time: new Date(model.asOf).toLocaleTimeString() })}</p>}
    </section>
  );
}
