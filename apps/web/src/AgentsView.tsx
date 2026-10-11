import { Bot, Search } from 'lucide-react';
import { agentRows } from './lib/capabilities';
import { t } from './lib/i18n';
import { Feedback, useSkills } from './SkillsView';
import { useIsOwner } from './useIsOwner';
import './features.css';
import './catalog.css';

export function AgentsView({ onOpenSkills }: { onOpenSkills?(): void }) {
  const s = useSkills();
  const owner = useIsOwner();
  const rows = agentRows(s.installs, s.versions);
  return <>
    <section className="moduleIntro"><div><p className="eyebrow">{t('agents.eyebrow')}</p><h2>{t('agents.title')}</h2><p>{t('agents.intro')}</p></div></section>
    <Feedback error={s.error} note={s.note} loading={s.loading} label={t('agents.loadingLabel')} />
    <div className="agentList">{rows.map(r => <article className={`surface agentRow ${r.enabled ? '' : 'featureDisabled'}`} key={r.installId}>
      <div className="agentIcon" aria-hidden="true"><Bot /></div>
      <div className="agentMain">
        <div className="rowTitle"><h3>{r.name}</h3><span className={`statusBadge ${r.enabled ? 'success' : 'warning'}`}>{r.enabled ? t('agents.enabled') : t('agents.disabled')}</span></div>
        <p dir="auto">{r.description}</p>
        <div className="tagRow">{r.tools.map(x => <span key={x} dir="ltr">{x}</span>)}</div>
        <a className="featureLink" href={`#skills`} onClick={e => { if (!onOpenSkills) return; e.preventDefault(); onOpenSkills(); window.setTimeout(() => document.getElementById(r.detailsAnchor)?.scrollIntoView({ block: 'center' }), 400); }}>{t('agents.details')}</a>
      </div>
      <dl><div><dt>{t('agents.version')}</dt><dd>{r.version}</dd></div></dl>
      <label className="featureToggle"><input type="checkbox" role="switch" checked={r.enabled} disabled={s.busy || owner !== true} title={owner === false ? t('agents.ownersOnly') : undefined} onChange={e => void s.toggle(r.skillId, e.target.checked)} aria-label={t(r.enabled ? 'agents.disableNamed' : 'agents.enableNamed', { name: r.name })} /><span>{r.enabled ? t('agents.toggleOn') : t('agents.toggleOff')}</span></label>
    </article>)}</div>
    {!s.loading && !s.error && !rows.length && <div className="emptyState"><Search aria-hidden="true" /><h3>{t('agents.emptyTitle')}</h3><p>{t('agents.emptyText')}</p>{onOpenSkills && <button type="button" onClick={onOpenSkills}>{t('agents.browse')}</button>}</div>}
  </>;
}
