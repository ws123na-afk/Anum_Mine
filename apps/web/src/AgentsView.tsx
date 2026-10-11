import { Bot, Search } from 'lucide-react';
import { agentRows } from './lib/capabilities';
import { Feedback, useSkills } from './SkillsView';
import { useIsOwner } from './useIsOwner';
import './features.css';
import './catalog.css';

export function AgentsView({ onOpenSkills }: { onOpenSkills?(): void }) {
  const s = useSkills();
  const owner = useIsOwner();
  const rows = agentRows(s.installs, s.versions);
  return <>
    <section className="moduleIntro"><div><p className="eyebrow">Runtime capability</p><h2>Installed agent skills</h2><p>Agents resolve only enabled, governed workspace skills.</p></div></section>
    <Feedback error={s.error} note={s.note} loading={s.loading} label="agents" />
    <div className="agentList">{rows.map(r => <article className={`surface agentRow ${r.enabled ? '' : 'featureDisabled'}`} key={r.installId}>
      <div className="agentIcon"><Bot /></div>
      <div className="agentMain">
        <div className="rowTitle"><h3>{r.name}</h3><span className={`statusBadge ${r.enabled ? 'success' : 'warning'}`}>{r.enabled ? 'enabled' : 'disabled'}</span></div>
        <p>{r.description}</p>
        <div className="tagRow">{r.tools.map(x => <span key={x}>{x}</span>)}</div>
        <a className="featureLink" href={`#skills`} onClick={e => { if (!onOpenSkills) return; e.preventDefault(); onOpenSkills(); window.setTimeout(() => document.getElementById(r.detailsAnchor)?.scrollIntoView({ block: 'center' }), 400); }}>Skill details</a>
      </div>
      <dl><div><dt>Version</dt><dd>{r.version}</dd></div></dl>
      <label className="featureToggle"><input type="checkbox" role="switch" checked={r.enabled} disabled={s.busy || owner !== true} title={owner === false ? 'Only workspace owners can enable or disable skills' : undefined} onChange={e => void s.toggle(r.skillId, e.target.checked)} aria-label={`${r.enabled ? 'Disable' : 'Enable'} ${r.name}`} /><span>{r.enabled ? 'On' : 'Off'}</span></label>
    </article>)}</div>
    {!s.loading && !s.error && !rows.length && <div className="emptyState"><Search /><h3>No agent skills installed</h3><p>Agents use the skills a workspace owner installs and enables.</p>{onOpenSkills && <button type="button" onClick={onOpenSkills}>Browse skills</button>}</div>}
  </>;
}
