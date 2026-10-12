// An agent's Skills tab (docs/agents.md#skills): what the agent really loads, from
// `GET /api/v1/agents/{id}/skills`, with each skill's state, version, tools and how it loads, the
// one-step fixes the caller may make (allow a missing tool, remove the skill), and a searchable
// picker to add skills. Every change is a `PATCH /api/v1/agents/{id}` with the version the page
// read, so the API checks permissions, tools and skills and refuses a stale edit.
import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { AlertTriangle, Check, CircleDot, PackageCheck, Plus, RefreshCw, Search, Sparkles, Wrench, X } from 'lucide-react';
import * as api from './lib/api';
import {
  addableSkills, agentsApi, ALWAYS_ON_TOOL, allowableMissingTools, allowToolsChanges, attachSkillChanges, detachSkillChanges,
  filterLoadout, sortLoadout, skillStateTone, TOOL_CATEGORIES,
  type AgentCatalog, type AgentCatalogSkill, type AgentProfile, type AgentProfileUpdate, type AgentSkillLoadout, type AgentSkillState,
} from './lib/agents';
import { budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { riskLabel } from './lib/labels';

const agents = agentsApi(api.apiRequest, api.apiRequestVoid);
const SEARCH_FROM = 6;

function explain(error: unknown, action: MessageKey): string {
  if (isPermissionDenied(error)) return describeError(error);
  if (isBudgetExceeded(error)) return t('common.budgetReached', { message: budgetMessage(error) });
  return t(action, { reason: describeError(error) });
}

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

function toolNote(tool: AgentSkillState['tools'][number]): MessageKey {
  if (!tool.registered) return 'agentSkills.tool.missing';
  if (tool.granted === false) return 'agentSkills.tool.notGranted';
  if (!tool.allowed) return 'agentSkills.tool.notAllowed';
  return 'agentSkills.tool.ready';
}

/** Why a skill does not load, in the interface language; the API's sentence when no case fits. */
function reasonText(item: AgentSkillState): string | null {
  const names = (pick: (tool: AgentSkillState['tools'][number]) => boolean) => item.tools.filter(pick).map((tool) => tool.name).join(', ');
  const missing = names((tool) => !tool.registered);
  const ungranted = names((tool) => tool.registered && tool.granted === false);
  const notAllowed = names((tool) => tool.registered && tool.granted !== false && !tool.allowed);
  switch (item.state) {
    case 'ready': return null;
    case 'disabled': return t('agentSkills.reason.disabled');
    case 'not_installed': return t('agentSkills.reason.notInstalled');
    case 'blocked': return t('agentSkills.reason.blocked');
    case 'needs_tools':
      if (ungranted) return t('agentSkills.reason.notGranted', { tools: ungranted });
      if (notAllowed) return t('agentSkills.reason.notAllowed', { tools: notAllowed });
      break;
    case 'unavailable':
      if (missing) return t('agentSkills.reason.missingTool', { tools: missing });
      if (item.source === 'unknown') return t('agentSkills.reason.unknown');
      break;
  }
  return item.reason;
}

function SkillRow({ item, canEdit, busy, catalog, onAllow, onRemove, onOpenSkills }: {
  item: AgentSkillState; canEdit: boolean; busy: boolean; catalog: AgentCatalog | null;
  onAllow: (tools: string[]) => void; onRemove: () => void; onOpenSkills?: () => void;
}) {
  const allowable = canEdit ? allowableMissingTools(item, catalog) : [];
  const toSkills = item.state === 'disabled' || item.state === 'not_installed' || item.upgrade_available || (item.state === 'needs_tools' && item.tools.some((tool) => tool.granted === false));
  const titleId = useId();
  const Icon = item.source === 'builtin' ? Sparkles : PackageCheck;
  return <li className={`skillLoad is-${item.state}`} aria-labelledby={titleId}>
    <span className="skillLoadIcon" aria-hidden="true"><Icon /></span>
    <div className="skillLoadBody">
      <div className="skillLoadHead">
        <h4 id={titleId} dir="auto">{item.name}</h4>
        <span className={`statusBadge ${skillStateTone(item.state)}`}>{t(`agentSkills.state.${item.state}` as MessageKey)}</span>
      </div>
      <p className="skillLoadMeta">
        <span>{t(`agentSkills.source.${item.source}` as MessageKey)}</span>
        {item.version && <span dir="ltr">{t('agentSkills.version', { version: item.version })}</span>}
        {item.risk_level && <span>{t('agentSkills.risk', { risk: riskLabel(item.risk_level) })}</span>}
        <code dir="ltr">{item.id}</code>
      </p>
      {item.description && <p className="skillLoadText" dir="auto">{item.description}</p>}
      <p className="skillLoadHow">
        {item.activation === 'always' ? t('agentSkills.loadsAlways')
          : item.triggers.length ? t('agentSkills.loadsOnRequest', { words: item.triggers.slice(0, 6).join(t('common.listSeparator')) })
            : t('agentSkills.loadsOnRequestAny')}
      </p>
      {reasonText(item) && <p className="skillLoadReason" dir="auto">{reasonText(item)}</p>}
      {item.upgrade_available && item.latest_version && <p className="skillLoadReason subtle">{t('agentSkills.upgrade', { version: item.latest_version })}</p>}
      <div className="skillLoadFoot">
        {item.tools.length > 0 && <ul className="skillToolChips" aria-label={t('agentSkills.toolsOf', { name: item.name })}>
          {item.tools.map((tool) => {
            const ok = tool.registered && tool.allowed && tool.granted !== false;
            return <li key={tool.name} className={ok ? 'ok' : 'missing'}>
              {ok ? <Check aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
              <code dir="ltr">{tool.name}</code><span className="visuallyHidden">{t(toolNote(tool))}</span>
            </li>;
          })}
        </ul>}
        {(allowable.length > 0 || (toSkills && onOpenSkills) || canEdit) && <div className="skillLoadActions">
          {allowable.length > 0 && <button type="button" className="secondary" disabled={busy} onClick={() => onAllow(allowable)}><Wrench aria-hidden="true" />{t('agentSkills.allow', { tools: allowable.join(', ') })}</button>}
          {toSkills && onOpenSkills && <button type="button" className="secondary" onClick={onOpenSkills}>{t('agentSkills.openSkills')}</button>}
          {canEdit && <button type="button" className="secondary quiet" disabled={busy} onClick={onRemove} aria-label={t('agentSkills.removeNamed', { name: item.name })}><X aria-hidden="true" />{t('agentSkills.remove')}</button>}
        </div>}
      </div>
    </div>
  </li>;
}

function AddSkills({ catalog, catalogError, loaded, busy, onAdd, onClose, onOpenSkills }: {
  catalog: AgentCatalog | null; catalogError: string | null; loaded: string[]; busy: boolean;
  onAdd: (skill: AgentCatalogSkill) => void; onClose: () => void; onOpenSkills?: () => void;
}) {
  const [query, setQuery] = useState('');
  const searchId = useId();
  const search = useRef<HTMLInputElement>(null);
  // Opening the picker puts the cursor in its search, so a keyboard user can type at once.
  useEffect(() => { search.current?.focus(); }, [catalog]);
  const options = addableSkills(catalog, loaded, query);
  const anyLeft = addableSkills(catalog, loaded, '').length > 0;
  const usable = new Set((catalog?.tools ?? []).filter((tool) => tool.available_to_you && !tool.always_on).map((tool) => tool.name));
  return <section className="skillPicker" aria-labelledby={`${searchId}-h`}>
    <div className="sectionHeader"><h4 id={`${searchId}-h`}>{t('agentSkills.addTitle')}</h4>
      <button type="button" className="linkButton" onClick={onClose}>{t('agentSkills.done')}</button></div>
    {catalogError && <Notice error>{catalogError}</Notice>}
    {!catalog && !catalogError && <p className="muted" role="status">{t('agents.loadingCatalog')}</p>}
    {catalog && <>
      <div className="agentSearch"><label className="visuallyHidden" htmlFor={searchId}>{t('agentSkills.searchAdd')}</label><Search aria-hidden="true" />
        <input ref={search} id={searchId} type="search" value={query} placeholder={t('agentSkills.searchPlaceholder')} onChange={(e) => setQuery(e.target.value)} /></div>
      {catalog.skills.length === 0 && <p className="muted">{t('agentSkills.addNone')} {onOpenSkills && <button type="button" className="linkButton" onClick={onOpenSkills}>{t('agentSkills.openSkills')}</button>}</p>}
      {catalog.skills.length > 0 && !anyLeft && <p className="muted">{t('agentSkills.addEmpty')}</p>}
      {anyLeft && options.length === 0 && <p className="muted">{t('agentSkills.addNoMatch', { query })}</p>}
      {options.length > 0 && <ul className="skillPickerList">{options.map((skill) => {
        const extra = skill.source === 'builtin' ? skill.required_tools.filter((tool) => tool !== ALWAYS_ON_TOOL && usable.has(tool)) : [];
        return <li key={skill.id}>
          <div><strong dir="auto">{skill.name}</strong>
            <small>{t(`agentSkills.source.${skill.source}` as MessageKey)} <code dir="ltr">{skill.id}</code></small>
            {skill.description && <small dir="auto">{skill.description}</small>}
            {extra.length > 0 && <small>{t('agentSkills.addTools', { tools: extra.join(', ') })}</small>}</div>
          <button type="button" disabled={busy} onClick={() => onAdd(skill)} aria-label={t('agentSkills.addNamed', { name: skill.name })}><Plus aria-hidden="true" />{t('agentSkills.addButton')}</button>
        </li>;
      })}</ul>}
    </>}
  </section>;
}

export function AgentSkillsPanel({ agent, catalog, catalogError, onAgentChanged, onOpenSkills }: {
  agent: AgentProfile; catalog: AgentCatalog | null; catalogError: string | null;
  onAgentChanged: (agent: AgentProfile) => void; onOpenSkills?: () => void;
}) {
  const [loadout, setLoadout] = useState<AgentSkillLoadout | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState('');
  const searchId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const load = useCallback(async () => {
    setState((old) => (old === 'ready' ? 'ready' : 'loading')); setError(null);
    try { setLoadout(await agents.skills(agent.id)); setState('ready'); }
    catch (e) { setError(explain(e, 'agentSkills.loadFailed')); setState('error'); }
  }, [agent.id]);
  useEffect(() => { void load(); }, [load, agent.version]);

  const canEdit = agent.can_edit && (loadout?.can_edit ?? true);
  async function change(body: AgentProfileUpdate, done: string, refocus = false) {
    setBusy(true); setFlash(null);
    try {
      onAgentChanged(await agents.update(agent.id, body)); setFlash({ text: done });
      // The removed row and its button are gone: keep focus in the panel, not on the page body.
      if (refocus) window.requestAnimationFrame(() => heading.current?.focus());
    }
    catch (e) { setFlash({ text: explain(e, 'agentSkills.saveFailed'), error: true }); }
    finally { setBusy(false); }
  }
  const items = useMemo(() => sortLoadout(loadout?.items ?? []), [loadout]);
  const shown = filterLoadout(items, query);
  const tools = useMemo(() => new Map((catalog?.tools ?? []).map((tool) => [tool.name, tool])), [catalog]);

  return <div className="agentColumns">
    <section className="surface agentPanel" aria-labelledby="agent-skills-h">
      <div className="sectionHeader">
        <div><h3 id="agent-skills-h" ref={heading} tabIndex={-1}>{t('agentSkills.title')}</h3>
          {loadout && items.length > 0 && <p className="muted skillSummary" role="status">{t('agentSkills.summary', { ready: loadout.ready, attention: loadout.attention })}</p>}</div>
        <div className="skillPanelActions">
          <button type="button" className="iconButton" onClick={() => void load()} aria-label={t('agentSkills.refresh')} title={t('agentSkills.refresh')}><RefreshCw aria-hidden="true" /></button>
          {canEdit && !adding && <button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('agentSkills.add')}</button>}
        </div>
      </div>
      <p className="muted skillIntro">{t('agentSkills.intro')}</p>
      {!agent.can_edit && <Notice>{t('agentSkills.readOnly')}</Notice>}
      {flash && <Notice error={flash.error}>{flash.text}</Notice>}
      {adding && canEdit && <AddSkills catalog={catalog} catalogError={catalogError} loaded={agent.allowed_skills} busy={busy} onClose={() => { setAdding(false); window.requestAnimationFrame(() => heading.current?.focus()); }} onOpenSkills={onOpenSkills}
        onAdd={(skill) => { if (catalog) void change(attachSkillChanges(agent, skill, catalog), t('agentSkills.added', { name: skill.name })); }} />}
      {state === 'loading' && <ul className="skillLoadList" aria-busy="true" aria-label={t('agentSkills.loading')}>
        {[0, 1].map((n) => <li key={n} className="skillLoad isSkeleton" aria-hidden="true"><span className="skillLoadIcon" /><div className="skillLoadBody"><span className="skeletonLine wide" /><span className="skeletonLine" /></div></li>)}
      </ul>}
      {state === 'error' && <div role="alert"><Notice error>{error}</Notice><div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>}
      {state === 'ready' && items.length === 0 && <div className="emptyState compactEmpty">
        <Sparkles aria-hidden="true" /><h4>{t('agentSkills.emptyTitle')}</h4>
        <p>{canEdit ? t('agentSkills.emptyText') : t('agentSkills.emptyReadOnly')}</p>
        {canEdit && !adding && <button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('agentSkills.add')}</button>}
      </div>}
      {state === 'ready' && items.length >= SEARCH_FROM && <div className="agentSearch skillSearch">
        <label className="visuallyHidden" htmlFor={searchId}>{t('agentSkills.search')}</label><Search aria-hidden="true" />
        <input id={searchId} type="search" value={query} placeholder={t('agentSkills.searchPlaceholder')} onChange={(e) => setQuery(e.target.value)} />
      </div>}
      {state === 'ready' && items.length > 0 && shown.length === 0 && <p className="muted">{t('agentSkills.noMatch', { query })}</p>}
      {state === 'ready' && shown.length > 0 && <ul className="skillLoadList" aria-label={t('agentSkills.title')}>
        {shown.map((item) => <SkillRow key={item.id} item={item} canEdit={canEdit} busy={busy} catalog={catalog} onOpenSkills={onOpenSkills}
          onAllow={(names) => void change(allowToolsChanges(agent, names), t('agentSkills.allowed', { tools: names.join(', ') }))}
          onRemove={() => void change(detachSkillChanges(agent, item.id), t('agentSkills.removed', { name: item.name }), true)} />)}
      </ul>}
    </section>
    <section className="surface agentPanel" aria-labelledby="agent-tools-h">
      <h3 id="agent-tools-h">{t('agents.field.tools')}</h3>
      <ul className="agentCapList">{[ALWAYS_ON_TOOL, ...agent.allowed_tools].map((name) => {
        const tool = tools.get(name);
        const category = tool?.category;
        return <li key={name}><strong dir="ltr">{name}</strong>
          <small>{tool && category && TOOL_CATEGORIES.includes(category) ? `${t(`agents.toolCategory.${category}` as MessageKey)} · ${t(`agents.level.${name === ALWAYS_ON_TOOL ? 'auto' : agent.autonomy[category]}` as MessageKey)}` : t('agents.toolUnavailable')}</small></li>;
      })}</ul>
      <p className="muted">{t('agents.toolsNote')}</p>
    </section>
  </div>;
}
