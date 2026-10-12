import { useCallback, useEffect, useState } from 'react';
import { BadgeCheck, CircleDot, PackageCheck, Pencil, Plus, ShieldAlert, Store, Trash2 } from 'lucide-react';
import { describeError, isPermissionDenied } from './lib/errors';
import { emptyMarketFilter, emptyPackageDraft, filterPackages, installSummary, packageBody, packageDraft, pageOf, type MarketFilter, type MarketPackage, type PackageDraft, type PackageInstall } from './lib/catalog';
import * as catalog from './lib/catalogApi';
import { joinList, t } from './lib/i18n';
import { Dialog } from './SkillsView';
import './catalog.css';

const PAGE = 20;
const failure = (e: unknown, who: string) => isPermissionDenied(e) ? t('market.notAllowed', { error: describeError(e), who }) : describeError(e);
const ownersOnly = () => t('market.ownersOnly');
const adminsOnly = () => t('market.adminsOnly');

/**
 * The tenant's marketplace: browse with search and filters, install with a permission summary,
 * uninstall, and (organization administrators) publish, edit and delete packages.
 */
export function MarketplaceCatalog({ owner, canManage, onChanged }: { owner: boolean | null; canManage: boolean; onChanged(): void }) {
  const [packages, setPackages] = useState<MarketPackage[]>([]);
  const [installs, setInstalls] = useState<PackageInstall[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filter, setFilter] = useState<MarketFilter>(emptyMarketFilter());
  const [pages, setPages] = useState(1);
  const [reviewing, setReviewing] = useState<MarketPackage | null>(null);
  const [editing, setEditing] = useState<{ draft: PackageDraft; isNew: boolean } | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const load = useCallback(async () => {
    setLoading(true);
    try { const [p, i] = await Promise.all([catalog.getPackages(), catalog.getPackageInstalls()]); setPackages(p); setInstalls(i); setLoadError(null); }
    catch (e) { setLoadError(t('market.loadFailed', { error: describeError(e) })); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => setPages(1), [filter]);
  const act = async (action: () => Promise<string>, who: string) => {
    setBusy(true); setFlash(null);
    try { setFlash({ text: await action() }); await load(); onChanged(); return true; }
    catch (e) { setFlash({ text: failure(e, who), error: true }); return false; } finally { setBusy(false); }
  };
  const installedIds = new Set(installs.map((i) => i.package_id));
  const filtered = filterPackages(packages, installs, filter);
  const { shown, remaining } = pageOf(filtered, pages, PAGE);
  const set = <K extends keyof MarketFilter>(key: K, value: MarketFilter[K]) => setFilter({ ...filter, [key]: value });
  return <section className="surface approvalTable enterpriseCatalog" aria-labelledby="marketplace-title">
    <div className="sectionHeader"><div><p className="eyebrow">{t('market.eyebrow')}</p><h2 id="marketplace-title">{t('market.title')}</h2></div>
      {canManage && <button type="button" onClick={() => setEditing({ draft: emptyPackageDraft(), isNew: true })}><Plus size={16} aria-hidden="true" />{t('market.publish')}</button>}</div>
    {loading && !packages.length && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('market.loading')}</p>}
    {loadError && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{loadError}</p>}
    {owner === false && packages.length > 0 && <p className="featureMeta">{t('market.ownersOnlyAsk')}</p>}
    {flash && <p className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{flash.text}</p>}
    {packages.length > 0 && <div className="catalogToolbar catalogToolbarFlat" role="search">
      <label className="field"><span>{t('market.search')}</span><input type="search" value={filter.query} placeholder={t('market.searchPlaceholder')} onChange={(e) => set('query', e.target.value)} /></label>
      <label className="field"><span>{t('market.kind')}</span><select value={filter.kind} onChange={(e) => set('kind', e.target.value as MarketFilter['kind'])}><option value="all">{t('market.kind.all')}</option><option value="integration">{t('market.kind.integration')}</option><option value="skill">{t('market.kind.skill')}</option></select></label>
      <label className="integrationToggle"><input type="checkbox" checked={filter.verifiedOnly} onChange={(e) => set('verifiedOnly', e.target.checked)} />{t('market.verifiedOnly')}</label>
      <label className="integrationToggle"><input type="checkbox" checked={filter.installedOnly} onChange={(e) => set('installedOnly', e.target.checked)} />{t('market.installedHere')}</label>
      <p className="featureMeta" aria-live="polite">{t('market.count', { shown: filtered.length, count: packages.length, installed: installs.length })}</p>
    </div>}
    {!loading && !loadError && packages.length === 0 && <div className="emptyState"><Store aria-hidden="true" /><h3>{t('market.emptyTitle')}</h3>
      {canManage ? <><p>{t('market.emptyAdmin')}</p><button type="button" onClick={() => setEditing({ draft: emptyPackageDraft(), isNew: true })}><Plus size={16} aria-hidden="true" />{t('market.publish')}</button></>
        : <p>{t('market.emptyOther')}</p>}</div>}
    {packages.length > 0 && filtered.length === 0 && <div className="emptyState"><Store aria-hidden="true" /><h3>{t('market.noMatch')}</h3><button type="button" className="secondary" onClick={() => setFilter(emptyMarketFilter())}>{t('market.clearFilters')}</button></div>}
    {shown.map((item) => {
      const installed = installedIds.has(item.id);
      return <div className="catalogRow marketRow" key={item.id}>
        <PackageCheck aria-hidden="true" />
        <div><strong>{item.name}</strong> {item.verified ? <span className="statusBadge success"><BadgeCheck size={13} aria-hidden="true" />{t('market.verified')}</span> : <span className="statusBadge warning">{t('market.unverified')}</span>} {installed && <span className="statusBadge success">{t('market.installed')}</span>}
          <p>{t('market.rowMeta', { kind: t(`market.kindName.${item.kind}`), version: item.version, publisher: item.publisher, permissions: item.permissions.length ? joinList(item.permissions) : t('market.noPermissions') })}</p></div>
        <div className="marketActions">
          {owner && !installed && <button type="button" disabled={busy} aria-label={t('market.installNamed', { name: item.name })} onClick={() => setReviewing(item)}>{t('market.install')}</button>}
          {owner && installed && (confirm === `uninstall-${item.id}`
            ? <span className="featureConfirm"><button type="button" className="danger" disabled={busy} onClick={() => void act(async () => { await catalog.uninstallPackage(item.id); setConfirm(null); return t('market.uninstalled', { name: item.name }); }, ownersOnly())}>{t('market.uninstall')}</button><button type="button" className="secondary" onClick={() => setConfirm(null)}>{t('market.cancel')}</button></span>
            : <button type="button" className="secondary" disabled={busy} onClick={() => setConfirm(`uninstall-${item.id}`)}>{t('market.uninstall')}</button>)}
          {canManage && <button type="button" className="iconButton" aria-label={t('market.editNamed', { name: item.name })} onClick={() => setEditing({ draft: packageDraft(item), isNew: false })}><Pencil aria-hidden="true" /></button>}
          {canManage && (confirm === `delete-${item.id}`
            ? <span className="featureConfirm"><button type="button" className="danger" disabled={busy} onClick={() => void act(async () => { await catalog.deletePackage(item.id); setConfirm(null); return t('market.deleted', { name: item.name }); }, adminsOnly())}>{t('market.delete')}</button><button type="button" className="secondary" onClick={() => setConfirm(null)}>{t('market.cancel')}</button></span>
            : <button type="button" className="iconButton" aria-label={t('market.deleteNamed', { name: item.name })} onClick={() => setConfirm(`delete-${item.id}`)}><Trash2 aria-hidden="true" /></button>)}
        </div>
      </div>;
    })}
    {remaining > 0 && <div className="catalogMore"><button type="button" className="secondary" onClick={() => setPages(pages + 1)}>{t('skills.showMore', { count: Math.min(remaining, PAGE) })}</button></div>}
    {reviewing && <InstallReview item={reviewing} busy={busy} onCancel={() => setReviewing(null)} onConfirm={() => void act(async () => { await catalog.installPackage(reviewing.id, reviewing.version); setReviewing(null); return t('market.installedDone', { name: reviewing.name }); }, ownersOnly())} />}
    {editing && <PackageForm draft={editing.draft} isNew={editing.isNew} existing={packages} onClose={() => setEditing(null)} onSave={(pkg) => act(async () => { const saved = await catalog.savePackage(pkg); setEditing(null); return t(editing.isNew ? 'market.savedPublished' : 'market.savedUpdated', { name: saved.name, version: saved.version }); }, adminsOnly())} />}
  </section>;
}

function InstallReview({ item, busy, onConfirm, onCancel }: { item: MarketPackage; busy: boolean; onConfirm(): void; onCancel(): void }) {
  const summary = installSummary(item);
  return <Dialog title={t('market.installTitle', { name: item.name })} onClose={onCancel}>
    {summary.lines.map((line) => <p key={line}>{line}</p>)}
    {summary.warning && <p className="featureChip failed"><ShieldAlert aria-hidden="true" />{summary.warning}</p>}
    {item.permissions.length > 0 && <><h4>{t('market.permissionsTitle')}</h4><ul className="catalogToolList">{item.permissions.map((p) => <li key={p}><code dir="ltr">{p}</code></li>)}</ul></>}
    <p className="featureMeta">{t('market.installNote')}</p>
    <div className="actions"><button type="button" className="secondary" onClick={onCancel}>{t('market.cancel')}</button><button type="button" autoFocus disabled={busy} onClick={onConfirm}><PackageCheck aria-hidden="true" />{t('market.install')}</button></div>
  </Dialog>;
}

function PackageForm({ draft: initial, isNew, existing, onClose, onSave }: { draft: PackageDraft; isNew: boolean; existing: MarketPackage[]; onClose(): void; onSave(pkg: MarketPackage): Promise<boolean> }) {
  const [draft, setDraft] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const text = (key: 'id' | 'name' | 'version' | 'publisher' | 'permissions' | 'regions', disabled = false, dir: 'ltr' | 'auto' = 'auto') =>
    <label className="field"><span>{t(`market.field.${key}`)}</span><input dir={dir} value={draft[key]} disabled={disabled} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} /></label>;
  const submit = async () => {
    const body = packageBody(draft);
    if (!body.ok) { setError(body.error); return; }
    if (isNew && existing.some((p) => p.id === body.value.id)) { setError(t('market.exists', { id: body.value.id })); return; }
    setError(null);
    await onSave(body.value);
  };
  return <Dialog title={isNew ? t('market.publishTitle') : t('market.editTitle', { name: initial.name })} onClose={onClose} wide>
    <form className="catalogForm" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      {text('id', !isNew, 'ltr')}{text('name')}{text('version', false, 'ltr')}{text('publisher')}
      <label className="field"><span>{t('market.field.kind')}</span><select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as PackageDraft['kind'] })}><option value="integration">{t('market.kindOption.integration')}</option><option value="skill">{t('market.kindOption.skill')}</option></select></label>
      <label className="integrationToggle"><input type="checkbox" checked={draft.verified} onChange={(e) => setDraft({ ...draft, verified: e.target.checked })} />{t('market.field.verified')}</label>
      <div className="catalogFormWide">{text('permissions', false, 'ltr')}</div>
      <div className="catalogFormWide">{text('regions', false, 'ltr')}</div>
      {error && <p className="notice errorNotice catalogFormWide" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
      <div className="actions catalogFormWide"><button type="button" className="secondary" onClick={onClose}>{t('market.cancel')}</button><button type="submit">{isNew ? t('market.publishButton') : t('market.saveButton')}</button></div>
    </form>
  </Dialog>;
}
