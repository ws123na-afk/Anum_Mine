// Settings > Workspace: this workspace's name, status and lifecycle (rename, archive, restore)
// and creating another workspace in the organization (docs/identity.md, Workspace lifecycle).
// What the caller may do is the API's answer (GET /workspaces/current/permissions).
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Archive, ArrowRightLeft, CircleDot, Lock, Pencil, Plus, RefreshCw, Undo2 } from 'lucide-react';
import type { WorkspaceInfo, WorkspacePermissions } from '@anum/contracts';
import * as admin from './lib/adminApi';
import { rememberWorkspace } from './lib/api';
import { archiveConfirmed, workspaceIdInput, workspaceName } from './lib/audit';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { rich } from './rich';
import { ConfirmDialog } from './Dialog';
import './admin.css';
import './audit.css';

type Load = { kind: 'loading' } | { kind: 'ready'; workspace: WorkspaceInfo; permissions: WorkspacePermissions } | { kind: 'error'; detail: string };
type Flash = { text: string; error?: boolean } | null;

const when = (value: string) => formatDateTime(value);

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

export function WorkspaceAdmin({ onSwitchWorkspace, onArchivedChange }: { onSwitchWorkspace?: (workspaceId: string) => Promise<void>; onArchivedChange?: (archived: boolean) => void }) {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [flash, setFlash] = useState<Flash>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [typed, setTyped] = useState('');
  const [newName, setNewName] = useState('');
  const [newId, setNewId] = useState('');
  const [created, setCreated] = useState<WorkspaceInfo | null>(null);
  const [formError, setFormError] = useState<{ form: 'rename' | 'create'; text: string } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [workspace, permissions] = await Promise.all([admin.getCurrentWorkspace(), admin.getWorkspacePermissions()]);
      setLoad({ kind: 'ready', workspace, permissions });
      setName(workspace.name);
      onArchivedChange?.(workspace.archivedAt !== null);
    } catch (error) {
      setLoad({ kind: 'error', detail: describeError(error) });
    }
  }, [onArchivedChange]);
  useEffect(() => { void refresh(); }, [refresh]);

  async function act(key: string, action: () => Promise<string>): Promise<boolean> {
    setBusy(key);
    setFlash(null);
    try {
      setFlash({ text: await action() });
      await refresh();
      return true;
    } catch (error) {
      setFlash({ text: isPermissionDenied(error) ? t('workspaceAdmin.notAllowed', { message: describeError(error) }) : describeError(error), error: true });
      return false;
    } finally {
      setBusy(null);
    }
  }

  if (load.kind === 'loading') return <section className="surface workspaceAdmin" aria-label={t('workspaceAdmin.title')}><Notice>{t('workspaceAdmin.loading')}</Notice></section>;
  if (load.kind === 'error') return <section className="surface workspaceAdmin" aria-label={t('workspaceAdmin.title')}>
    <Notice error>{t('workspaceAdmin.loadError', { message: load.detail })}</Notice>
    <div className="actions"><button type="button" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>
  </section>;

  const { workspace, permissions } = load;
  const archived = workspace.archivedAt !== null;
  const canManage = permissions.canManageWorkspace;
  const role = permissions.role ? t(`workspaceAdmin.role.${permissions.role}` as MessageKey) : t('workspaceAdmin.role.none');

  const rename = (event: FormEvent) => {
    event.preventDefault();
    const result = workspaceName(name);
    if (!result.ok) { setFormError({ form: 'rename', text: result.error }); return; }
    setFormError(null);
    void act('rename', async () => { const next = await admin.renameWorkspace(result.value); return t('workspaceAdmin.renamed', { name: next.name }); });
  };
  const create = (event: FormEvent) => {
    event.preventDefault();
    const checkedName = workspaceName(newName);
    if (!checkedName.ok) { setFormError({ form: 'create', text: checkedName.error }); return; }
    const checkedId = workspaceIdInput(newId);
    if (!checkedId.ok) { setFormError({ form: 'create', text: checkedId.error }); return; }
    setFormError(null);
    void act('create', async () => {
      const next = await admin.createWorkspace(checkedName.value, checkedId.value);
      rememberWorkspace(next.id);
      setCreated(next);
      setNewName('');
      setNewId('');
      return t('workspaceAdmin.created', { name: next.name });
    });
  };

  return <section className="surface workspaceAdmin" aria-label={t('workspaceAdmin.title')}>
    <div className="sectionHeader">
      <div><h2>{t('workspaceAdmin.title')}</h2><p className="muted">{permissions.organizationAdmin ? t('workspaceAdmin.yourRoleAdmin', { role }) : t('workspaceAdmin.yourRole', { role })}</p></div>
      <span className={`statusBadge ${archived ? 'warning' : 'success'}`}>{archived ? t('workspaceAdmin.status.archived') : t('workspaceAdmin.status.active')}</span>
    </div>
    {archived && <p className="notice archivedBanner" role="status"><Archive aria-hidden="true" />{t(workspace.archivedBy ? 'workspaceAdmin.archivedBy' : 'workspaceAdmin.archivedAt', { when: workspace.archivedAt ? when(workspace.archivedAt) : '', who: workspace.archivedBy ?? '' })}</p>}
    <dl className="workspaceFacts">
      <div><dt>{t('workspaceAdmin.name')}</dt><dd>{workspace.name}</dd></div>
      <div><dt>{t('workspaceAdmin.id')}</dt><dd><code>{workspace.id}</code></dd></div>
      <div><dt>{t('workspaceAdmin.organization')}</dt><dd><code>{workspace.tenantId}</code></dd></div>
      <div><dt>{t('workspaceAdmin.createdAt')}</dt><dd>{when(workspace.createdAt)}</dd></div>
    </dl>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {created && onSwitchWorkspace && <div className="actions">
      <button type="button" onClick={() => void onSwitchWorkspace(created.id).catch((error: unknown) => setFlash({ text: describeError(error), error: true }))}><ArrowRightLeft aria-hidden="true" />{t('workspaceAdmin.switchTo', { name: created.name })}</button>
    </div>}
    {!canManage && <p className="notice" role="note"><Lock aria-hidden="true" />{t('workspaceAdmin.readOnly')}</p>}
    <div className="workspaceForms">
      {canManage && !archived && <form aria-label={t('workspaceAdmin.renameForm')} noValidate onSubmit={rename}>
        <h3>{t('workspaceAdmin.rename')}</h3>
        <label className="field"><span>{t('workspaceAdmin.nameLabel')}</span><input value={name} maxLength={160} dir="auto" onChange={(event) => setName(event.target.value)} /></label>
        {formError?.form === 'rename' && <Notice error>{formError.text}</Notice>}
        <div className="actions"><button type="submit" disabled={busy !== null || name.trim() === workspace.name}><Pencil aria-hidden="true" />{t('workspaceAdmin.saveName')}</button></div>
      </form>}
      {canManage && <form aria-label={archived ? t('workspaceAdmin.restoreForm') : t('workspaceAdmin.archiveForm')} onSubmit={(event) => event.preventDefault()}>
        <h3>{archived ? t('workspaceAdmin.restore') : t('workspaceAdmin.archive')}</h3>
        <p className="muted">{archived ? t('workspaceAdmin.restoreText') : t('workspaceAdmin.archiveText')}</p>
        <div className="actions">
          {archived
            ? <button type="button" disabled={busy !== null} onClick={() => void act('restore', async () => { await admin.restoreWorkspace(); return t('workspaceAdmin.restored'); })}><Undo2 aria-hidden="true" />{t('workspaceAdmin.restoreButton')}</button>
            : <button type="button" className="danger" disabled={busy !== null} onClick={() => { setTyped(''); setConfirmArchive(true); }}><Archive aria-hidden="true" />{t('workspaceAdmin.archiveButton')}</button>}
        </div>
      </form>}
      {permissions.canCreateWorkspace && <form aria-label={t('workspaceAdmin.createForm')} noValidate onSubmit={create}>
        <h3>{t('workspaceAdmin.createTitle')}</h3>
        <p className="muted">{t('workspaceAdmin.createText')}</p>
        <label className="field"><span>{t('workspaceAdmin.name')}</span><input value={newName} maxLength={160} dir="auto" onChange={(event) => setNewName(event.target.value)} placeholder={t('workspaceAdmin.namePlaceholder')} autoComplete="off" /></label>
        <label className="field"><span>{t('workspaceAdmin.idOptional')}</span><input value={newId} maxLength={80} dir="ltr" onChange={(event) => setNewId(event.target.value)} placeholder={t('workspaceAdmin.idPlaceholder')} autoComplete="off" spellCheck={false} /></label>
        {formError?.form === 'create' && <Notice error>{formError.text}</Notice>}
        <div className="actions"><button type="submit" disabled={busy !== null}><Plus aria-hidden="true" />{t('workspaceAdmin.createButton')}</button></div>
      </form>}
    </div>
    {confirmArchive && <ConfirmDialog title={t('workspaceAdmin.archiveConfirmTitle', { name: workspace.name })} confirmLabel={t('workspaceAdmin.archiveButton')} danger busy={busy === 'archive'}
      confirmDisabled={!archiveConfirmed(typed, workspace.name)}
      onCancel={() => setConfirmArchive(false)}
      onConfirm={() => {
        if (!archiveConfirmed(typed, workspace.name)) return;
        void act('archive', async () => { await admin.archiveWorkspace(typed.trim()); return t('workspaceAdmin.archived', { name: workspace.name }); }).then(() => setConfirmArchive(false));
      }}>
      <p>{rich('workspaceAdmin.archiveConfirmText', { name: <strong dir="auto">{workspace.name}</strong> })}</p>
      <label className="field"><span>{t('workspaceAdmin.nameLabel')}</span><input value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" dir="auto" data-autofocus aria-invalid={typed.length > 0 && !archiveConfirmed(typed, workspace.name)} /></label>
      {typed.length > 0 && !archiveConfirmed(typed, workspace.name) && <p className="muted">{t('workspaceAdmin.nameMismatch')}</p>}
    </ConfirmDialog>}
  </section>;
}
