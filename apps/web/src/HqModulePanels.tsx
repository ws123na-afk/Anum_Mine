/**
 * The commerce and tyre module panels under a building scene: real numbers only for a connected module, otherwise why not
 * ("Not connected", "No access", "Unavailable") with a link to the setup. Split out of the building view.
 */
import type { CommerceLive, ModuleState, TyreLive } from './lib/hqModules.ts';
import { commerceFlow } from './lib/hqModules.ts';
import { t, type MessageKey } from './lib/i18n.ts';

function NotConnected(props: { title: string; state: ModuleState<unknown>; setupHash: string }) {
  const s = props.state;
  return (
    <section className="hqPanel hqModule" aria-label={props.title}>
      <h2>{props.title}</h2>
      {s.state === 'loading' && <p className="hqMuted" role="status">{t('hq.module.loading')}</p>}
      {s.state === 'not_connected' && <div className="hqUnavailable"><strong>{t('hq.module.notConnected')}</strong><a href={props.setupHash}>{t('hq.module.setup')}</a></div>}
      {s.state === 'forbidden' && <div className="hqUnavailable"><strong>{t('hq.module.forbidden')}</strong><span>{t('hq.module.forbiddenHint')}</span></div>}
      {s.state === 'error' && <div className="hqUnavailable" role="alert"><strong>{t('hq.module.error')}</strong><span>{s.message}</span></div>}
    </section>
  );
}

export function CommercePanels({ state }: { state: ModuleState<CommerceLive> }) {
  if (state.state !== 'ready') return <NotConnected title={t('hq.commerce.title')} state={state} setupHash="#commerce" />;
  const c = state.data;
  return (
    <>
      <section className="hqPanel hqModule" aria-label={t('hq.commerce.title')}>
        <h2>{t('hq.commerce.title')}</h2>
        <ul className="hqStats">
          <li><strong>{c.orders}</strong><span>{t('hq.commerce.orders')}</span></li>
          <li><strong>{c.waiting}</strong><span>{t('hq.commerce.waiting')}</span></li>
          <li><strong>{c.late}</strong><span>{t('hq.commerce.late')}</span></li>
          {c.lowStock !== null && <li><strong>{c.lowStock}</strong><span>{t('hq.commerce.lowStock')}</span></li>}
          <li><strong>{c.openAlerts}</strong><span>{t('hq.commerce.alerts')}</span></li>
        </ul>
      </section>
      <section className="hqPanel hqModule" aria-label={t('hq.flow.title')}>
        <h2>{t('hq.flow.title')}</h2>
        <ol className="hqFlow">
          {commerceFlow(c).map((step) => (
            <li key={step.key}><strong>{step.count}</strong><span>{t(`hq.flow.${step.key}` as MessageKey)}</span></li>
          ))}
        </ol>
      </section>
    </>
  );
}

export function TyrePanel({ state }: { state: ModuleState<TyreLive> }) {
  if (state.state !== 'ready') return <NotConnected title={t('hq.tyre.title')} state={state} setupHash="#tyre" />;
  const d = state.data;
  return (
    <section className="hqPanel hqModule" aria-label={t('hq.tyre.title')}>
      <h2>{t('hq.tyre.title')}</h2>
      <ul className="hqStats">
        <li><strong>{d.vehicles}</strong><span>{t('hq.tyre.vehicles')}</span></li>
        <li><strong>{d.fitted}</strong><span>{t('hq.tyre.fitted')}</span></li>
        {d.inStock !== null && <li><strong>{d.inStock}</strong><span>{t('hq.tyre.stock')}</span></li>}
        <li><strong>{d.belowMinimum}</strong><span>{t('hq.tyre.below')}</span></li>
        <li><strong>{d.overdueInspections}</strong><span>{t('hq.tyre.overdue')}</span></li>
        <li><strong>{d.dueNext30}</strong><span>{t('hq.tyre.due')}</span></li>
      </ul>
    </section>
  );
}
