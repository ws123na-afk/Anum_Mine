import { useCallback, useEffect, useState } from 'react';
import { BadgeCheck, CircleDot, PackageCheck, Pencil, Plus, ShieldAlert, Store, Trash2 } from 'lucide-react';
import { describeError, isPermissionDenied } from './lib/errors';
import { emptyMarketFilter, emptyPackageDraft, filterPackages, installSummary, packageBody, packageDraft, pageOf, type MarketFilter, type MarketPackage, type PackageDraft, type PackageInstall } from './lib/catalog';
import * as catalog from './lib/catalogApi';
import { Dialog } from './SkillsView';
import './catalog.css';

const PAGE = 20;
const failure = (e: unknown, who: string) => isPermissionDenied(e) ? `Not allowed: ${describeError(e)} ${who}` : describeError(e);
const OWNER_ONLY = 'Only workspace owners can install or uninstall packages.';
const ADMIN_ONLY = 'Only organization administrators can publish, edit or delete catalog packages.';

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
    catch (e) { setLoadError(`Could not load the marketplace: ${describeError(e)} Check that the API is reachable, then Refresh.`); }
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
    <div className="sectionHeader"><div><p className="eyebrow">Verified distribution</p><h2 id="marketplace-title">Marketplace catalog</h2></div>
      {canManage && <button type="button" onClick={() => setEditing({ draft: emptyPackageDraft(), isNew: true })}><Plus size={16} />Publish package</button>}</div>
    {loading && !packages.length && <p className="notice" role="status"><CircleDot />Loading the marketplace...</p>}
    {loadError && <p className="notice errorNotice" role="alert"><CircleDot />{loadError}</p>}
    {owner === false && packages.length > 0 && <p className="featureMeta">{OWNER_ONLY} Ask an owner of this workspace.</p>}
    {flash && <p className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}><CircleDot />{flash.text}</p>}
    {packages.length > 0 && <div className="catalogToolbar catalogToolbarFlat" role="search">
      <label className="field"><span>Search packages</span><input type="search" value={filter.query} placeholder="Name, publisher or permission" onChange={(e) => set('query', e.target.value)} /></label>
      <label className="field"><span>Kind</span><select value={filter.kind} onChange={(e) => set('kind', e.target.value as MarketFilter['kind'])}><option value="all">All kinds</option><option value="integration">Integrations</option><option value="skill">Skills</option></select></label>
      <label className="integrationToggle"><input type="checkbox" checked={filter.verifiedOnly} onChange={(e) => set('verifiedOnly', e.target.checked)} />Verified only</label>
      <label className="integrationToggle"><input type="checkbox" checked={filter.installedOnly} onChange={(e) => set('installedOnly', e.target.checked)} />Installed here</label>
      <p className="featureMeta" aria-live="polite">{filtered.length} of {packages.length} package{packages.length === 1 ? '' : 's'} · {installs.length} installed</p>
    </div>}
    {!loading && !loadError && packages.length === 0 && <div className="emptyState"><Store /><h3>No packages yet</h3>
      {canManage ? <><p>Publish the first package; every workspace of the organization will see it.</p><button type="button" onClick={() => setEditing({ draft: emptyPackageDraft(), isNew: true })}><Plus size={16} />Publish package</button></>
        : <p>No packages have been published to this marketplace yet. An organization administrator publishes packages for every workspace.</p>}</div>}
    {packages.length > 0 && filtered.length === 0 && <div className="emptyState"><Store /><h3>No packages match</h3><button type="button" className="secondary" onClick={() => setFilter(emptyMarketFilter())}>Clear filters</button></div>}
    {shown.map((item) => {
      const installed = installedIds.has(item.id);
      return <div className="catalogRow marketRow" key={item.id}>
        <PackageCheck aria-hidden="true" />
        <div><strong>{item.name}</strong> {item.verified ? <span className="statusBadge success"><BadgeCheck size={13} aria-hidden="true" />verified</span> : <span className="statusBadge warning">unverified</span>} {installed && <span className="statusBadge success">installed</span>}
          <p>{item.kind} · v{item.version} · {item.publisher} · {item.permissions.length ? item.permissions.join(', ') : 'no permissions'}</p></div>
        <div className="marketActions">
          {owner && !installed && <button type="button" disabled={busy} aria-label={`Install ${item.name}`} onClick={() => setReviewing(item)}>Install</button>}
          {owner && installed && (confirm === `uninstall-${item.id}`
            ? <span className="featureConfirm"><button type="button" className="danger" disabled={busy} onClick={() => void act(async () => { await catalog.uninstallPackage(item.id); setConfirm(null); return `${item.name} uninstalled from this workspace.`; }, OWNER_ONLY)}>Uninstall</button><button type="button" className="secondary" onClick={() => setConfirm(null)}>Cancel</button></span>
            : <button type="button" className="secondary" disabled={busy} onClick={() => setConfirm(`uninstall-${item.id}`)}>Uninstall</button>)}
          {canManage && <button type="button" className="iconButton" aria-label={`Edit ${item.name}`} onClick={() => setEditing({ draft: packageDraft(item), isNew: false })}><Pencil /></button>}
          {canManage && (confirm === `delete-${item.id}`
            ? <span className="featureConfirm"><button type="button" className="danger" disabled={busy} onClick={() => void act(async () => { await catalog.deletePackage(item.id); setConfirm(null); return `${item.name} removed from the catalog.`; }, ADMIN_ONLY)}>Delete</button><button type="button" className="secondary" onClick={() => setConfirm(null)}>Cancel</button></span>
            : <button type="button" className="iconButton" aria-label={`Delete ${item.name}`} onClick={() => setConfirm(`delete-${item.id}`)}><Trash2 /></button>)}
        </div>
      </div>;
    })}
    {remaining > 0 && <div className="catalogMore"><button type="button" className="secondary" onClick={() => setPages(pages + 1)}>Show {Math.min(remaining, PAGE)} more</button></div>}
    {reviewing && <InstallReview item={reviewing} busy={busy} onCancel={() => setReviewing(null)} onConfirm={() => void act(async () => { await catalog.installPackage(reviewing.id, reviewing.version); setReviewing(null); return `${reviewing.name} installed in this workspace.`; }, OWNER_ONLY)} />}
    {editing && <PackageForm draft={editing.draft} isNew={editing.isNew} existing={packages} onClose={() => setEditing(null)} onSave={(pkg) => act(async () => { const saved = await catalog.savePackage(pkg); setEditing(null); return `${saved.name} v${saved.version} ${editing.isNew ? 'published' : 'updated'}.`; }, ADMIN_ONLY)} />}
  </section>;
}

function InstallReview({ item, busy, onConfirm, onCancel }: { item: MarketPackage; busy: boolean; onConfirm(): void; onCancel(): void }) {
  const summary = installSummary(item);
  return <Dialog title={`Install ${item.name}?`} onClose={onCancel}>
    {summary.lines.map((line) => <p key={line}>{line}</p>)}
    {summary.warning && <p className="featureChip failed"><ShieldAlert />{summary.warning}</p>}
    {item.permissions.length > 0 && <><h4>Permissions granted in this workspace</h4><ul className="catalogToolList">{item.permissions.map((p) => <li key={p}><code>{p}</code></li>)}</ul></>}
    <p className="featureMeta">The install is recorded in the audit log. Agent tool calls stay mediated by the runtime and approvals still apply.</p>
    <div className="actions"><button type="button" className="secondary" onClick={onCancel}>Cancel</button><button type="button" autoFocus disabled={busy} onClick={onConfirm}><PackageCheck />Install</button></div>
  </Dialog>;
}

function PackageForm({ draft: initial, isNew, existing, onClose, onSave }: { draft: PackageDraft; isNew: boolean; existing: MarketPackage[]; onClose(): void; onSave(pkg: MarketPackage): Promise<boolean> }) {
  const [draft, setDraft] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const text = (key: 'id' | 'name' | 'version' | 'publisher' | 'permissions' | 'regions', label: string, disabled = false) =>
    <label className="field"><span>{label}</span><input value={draft[key]} disabled={disabled} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} /></label>;
  const submit = async () => {
    const body = packageBody(draft);
    if (!body.ok) { setError(body.error); return; }
    if (isNew && existing.some((p) => p.id === body.value.id)) { setError(`A package with id ${body.value.id} already exists; edit it instead.`); return; }
    setError(null);
    await onSave(body.value);
  };
  return <Dialog title={isNew ? 'Publish a package' : `Edit ${initial.name}`} onClose={onClose} wide>
    <form className="catalogForm" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      {text('id', 'Package id', !isNew)}{text('name', 'Name')}{text('version', 'Version')}{text('publisher', 'Publisher')}
      <label className="field"><span>Kind</span><select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as PackageDraft['kind'] })}><option value="integration">Integration</option><option value="skill">Skill</option></select></label>
      <label className="integrationToggle"><input type="checkbox" checked={draft.verified} onChange={(e) => setDraft({ ...draft, verified: e.target.checked })} />Publisher verified by the organization</label>
      <div className="catalogFormWide">{text('permissions', 'Permissions it requests (comma separated)')}</div>
      <div className="catalogFormWide">{text('regions', 'Regions (comma separated)')}</div>
      {error && <p className="notice errorNotice catalogFormWide" role="alert"><CircleDot />{error}</p>}
      <div className="actions catalogFormWide"><button type="button" className="secondary" onClick={onClose}>Cancel</button><button type="submit">{isNew ? 'Publish' : 'Save'}</button></div>
    </form>
  </Dialog>;
}
