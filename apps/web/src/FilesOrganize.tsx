import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ChevronRight, CircleDot, Download, Folder, FolderInput, FolderPlus, Lock, MoreHorizontal, Pencil, Plus, Tag as TagIcon, Trash2, X } from 'lucide-react';
import * as api from './lib/api';
import { describeError, isPermissionDenied } from './lib/errors';
import {
  BULK_MAX_FILES, folderChildren, folderChoices, folderNameProblem, folderTrail, tagNameProblem, tagState,
  type ApiFile, type ApiFolder, type ApiTag, type FolderContents, type FolderScope,
} from './lib/files';
import { formatNumber, t } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';

/**
 * Folders, tags and bulk actions on the Documents page (docs/files.md#folders, #tags, #bulk-actions):
 * the folder trail with the subfolders of the open folder and their actions, the tag filter chips and
 * the tag manager, the bar over a selection (move, tag, download, delete), the move and tag dialogs
 * and the tags on one file in the detail pane. Every write goes to the API; the page reloads after.
 */

export function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

const files = (count: number) => t('files.bulk.files', { count });

// --------------------------------------------------------------------------- folders

export function FolderBar({ folders, current, canUpload, onOpen, onChanged, onNote, onProblem, searching }: {
  folders: ApiFolder[]; current: FolderScope; canUpload: boolean; onOpen(folder: FolderScope): void; onChanged(): Promise<void> | void;
  onNote(text: string): void; onProblem(text: string): void; searching: boolean;
}) {
  const [dialog, setDialog] = useState<null | { kind: 'new' } | { kind: 'rename'; folder: ApiFolder } | { kind: 'move'; folder: ApiFolder } | { kind: 'delete'; folder: ApiFolder }>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const trail = folderTrail(folders, current);
  const here = trail.at(-1) ?? null;
  const children = folderChildren(folders, current);
  const hereName = here ? here.name : t('files.folder.root');

  async function run(work: () => Promise<unknown>, done: string, failedKey: 'files.folder.createFailed' | 'files.folder.renameFailed' | 'files.folder.moveFailed' | 'files.folder.deleteFailed') {
    // The API's refusal names who may act and what stayed unchanged, so it is shown as it is.
    try { await work(); setDialog(null); onNote(done); await onChanged(); }
    catch (e) { onProblem(t(failedKey, { message: describeError(e) })); setDialog(null); }
  }

  return <div className="folderBar">
    <nav className="folderTrail" aria-label={t('files.folder.trail')}>
      <ol>
        <li>{current === null ? <span aria-current="location"><Folder aria-hidden="true" />{t('files.folder.root')}</span> : <button type="button" className="linkButton" onClick={() => onOpen(null)}><Folder aria-hidden="true" />{t('files.folder.root')}</button>}</li>
        {trail.map((folder, index) => <li key={folder.id}><ChevronRight aria-hidden="true" className="trailSep" />{index === trail.length - 1
          ? <span aria-current="location" dir="auto">{folder.name}</span>
          : <button type="button" className="linkButton" dir="auto" onClick={() => onOpen(folder.id)}>{folder.name}</button>}</li>)}
      </ol>
      {canUpload && <button type="button" className="secondary small" onClick={() => setDialog({ kind: 'new' })}><FolderPlus aria-hidden="true" />{t('files.folder.new')}</button>}
    </nav>
    {searching && <p className="muted folderNote">{t('files.folder.searchEverywhere')}</p>}
    {!searching && children.length > 0 && <ul className="folderList" aria-label={t('files.folder.subfolders', { name: hereName })}>
      {children.map((folder) => <li key={folder.id} className={menu === folder.id ? 'menuOpen' : undefined}>
        <button type="button" className="folderChip" aria-label={`${folder.name}: ${t('files.folder.open', { name: folder.name })}`} onClick={() => onOpen(folder.id)}>
          <Folder aria-hidden="true" /><span dir="auto">{folder.name}</span><small>{t('files.folder.count', { count: folder.file_count })}</small>
        </button>
        {folder.can_manage && <div className="folderActions">
          <button type="button" className="iconButton small" aria-expanded={menu === folder.id} aria-label={t('files.folder.actions', { name: folder.name })} title={t('files.folder.actions', { name: folder.name })} onClick={() => setMenu((open) => open === folder.id ? null : folder.id)}><MoreHorizontal aria-hidden="true" /></button>
          {menu === folder.id && <div className="folderMenu" role="group" aria-label={t('files.folder.actions', { name: folder.name })}>
            <button type="button" className="secondary small" onClick={() => { setMenu(null); setDialog({ kind: 'rename', folder }); }}><Pencil aria-hidden="true" />{t('files.folder.rename')}</button>
            <button type="button" className="secondary small" onClick={() => { setMenu(null); setDialog({ kind: 'move', folder }); }}><FolderInput aria-hidden="true" />{t('files.folder.move')}</button>
            <button type="button" className="secondary small dangerText" onClick={() => { setMenu(null); setDialog({ kind: 'delete', folder }); }}><Trash2 aria-hidden="true" />{t('files.folder.delete')}</button>
          </div>}
        </div>}
      </li>)}
    </ul>}
    {dialog?.kind === 'new' && <FolderNameDialog title={t('files.folder.newIn', { name: hereName })} confirm={t('files.folder.create')} siblings={children} onCancel={() => setDialog(null)}
      onSubmit={(name) => run(() => api.createFolder(name, current), t('files.folder.created', { name }), 'files.folder.createFailed')} />}
    {dialog?.kind === 'rename' && <FolderNameDialog title={t('files.folder.renameOf', { name: dialog.folder.name })} confirm={t('files.folder.rename')} initial={dialog.folder.name} siblings={folderChildren(folders, dialog.folder.parent_id)} exceptId={dialog.folder.id} onCancel={() => setDialog(null)}
      onSubmit={(name) => run(() => api.updateFolder(dialog.folder.id, { name }), t('files.folder.renamed', { name }), 'files.folder.renameFailed')} />}
    {dialog?.kind === 'move' && <MoveDialog title={t('files.folder.moveOf', { name: dialog.folder.name })} confirm={t('files.bulk.moveConfirm')} folders={folders} exclude={dialog.folder.id} initial={dialog.folder.parent_id} onCancel={() => setDialog(null)}
      onConfirm={(target) => run(() => api.updateFolder(dialog.folder.id, { parent_id: target }), t('files.folder.moved', { name: dialog.folder.name, target: targetName(folders, target) }), 'files.folder.moveFailed')} />}
    {dialog?.kind === 'delete' && <FolderDeleteDialog folder={dialog.folder} folders={folders} onCancel={() => setDialog(null)}
      onConfirm={(contents) => run(async () => { await api.deleteFolder(dialog.folder.id, contents); if (current === dialog.folder.id || folderTrail(folders, current).some((f) => f.id === dialog.folder.id)) onOpen(dialog.folder.parent_id); }, t('files.folder.deleted', { name: dialog.folder.name }), 'files.folder.deleteFailed')} />}
  </div>;
}

export function targetName(folders: readonly ApiFolder[], target: FolderScope): string {
  return folders.find((f) => f.id === target)?.name ?? t('files.folder.root');
}

function FolderNameDialog({ title, confirm, initial = '', siblings, exceptId, onSubmit, onCancel }: {
  title: string; confirm: string; initial?: string; siblings: readonly ApiFolder[]; exceptId?: string; onSubmit(name: string): Promise<void>; onCancel(): void;
}) {
  const [name, setName] = useState(initial);
  const [busy, setBusy] = useState(false);
  const id = useId();
  const problem = folderNameProblem(name, siblings, exceptId);
  const unchanged = initial !== '' && name.trim() === initial;
  const submit = (event: FormEvent) => { event.preventDefault(); if (problem || unchanged) return; setBusy(true); void onSubmit(name.trim()).finally(() => setBusy(false)); };
  return <Modal title={title} onClose={onCancel}>
    <form onSubmit={submit} className="dialogBody">
      <label className="field"><span>{t('files.folder.nameField')}</span><input id={id} value={name} maxLength={255} autoFocus data-autofocus dir="auto" aria-invalid={Boolean(problem && name)} aria-describedby={`${id}-hint`} onChange={(e) => setName(e.target.value)} /></label>
      <small id={`${id}-hint`} className={problem && name ? 'fieldError' : 'muted'}>{(name ? problem : null) ?? t('files.folder.nameHint')}</small>
      <div className="actions"><button type="submit" disabled={Boolean(problem) || unchanged || busy}>{busy ? t('dialog.working') : confirm}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

/** Pick a folder (or the root) from a flat, indented list: for a moved folder, everything below it is left out. */
export function MoveDialog({ title, confirm, folders, exclude, initial, onConfirm, onCancel }: {
  title: string; confirm: string; folders: readonly ApiFolder[]; exclude?: string | null; initial?: FolderScope; onConfirm(target: FolderScope): Promise<void>; onCancel(): void;
}) {
  const [target, setTarget] = useState<string>(initial ?? '');
  const [busy, setBusy] = useState(false);
  const id = useId();
  const choices = folderChoices(folders, exclude);
  return <Modal title={title} onClose={onCancel}>
    <form className="dialogBody" onSubmit={(event) => { event.preventDefault(); setBusy(true); void onConfirm(target || null).finally(() => setBusy(false)); }}>
      <label className="field"><span>{t('files.folder.pickLabel')}</span>
        <select id={id} value={target} size={Math.min(10, Math.max(4, choices.length + 1))} className="folderPick" autoFocus data-autofocus onChange={(e) => setTarget(e.target.value)}>
          <option value="">{t('files.folder.pickRoot')}</option>
          {choices.map((folder) => <option key={folder.id} value={folder.id} dir="auto">{`${'   '.repeat(Math.max(0, folder.depth - 1))}${folder.name}`}</option>)}
        </select>
      </label>
      <div className="actions"><button type="submit" disabled={busy || (initial !== undefined && (target || null) === (initial ?? null))}>{busy ? t('dialog.working') : confirm}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

function FolderDeleteDialog({ folder, folders, onConfirm, onCancel }: { folder: ApiFolder; folders: readonly ApiFolder[]; onConfirm(contents: FolderContents): Promise<void>; onCancel(): void }) {
  const below = folderChildren(folders, folder.id);
  const inside = folder.file_count + countBelow(folders, folder.id);
  const empty = inside === 0 && below.length === 0;
  const [choice, setChoice] = useState<FolderContents>('move_up');
  const [busy, setBusy] = useState(false);
  const id = useId();
  const parent = targetName(folders, folder.parent_id);
  return <ConfirmDialog title={t('files.folder.deleteTitle', { name: folder.name })} confirmLabel={t('files.folder.delete')} danger busy={busy}
    onConfirm={() => { setBusy(true); void onConfirm(empty ? 'refuse' : choice).finally(() => setBusy(false)); }} onCancel={onCancel}>
    {empty ? <p>{t('files.folder.deleteEmpty')}</p> : <>
      <p>{t('files.folder.deleteFull', { files: files(inside), folders: t('files.folder.subfolderCount', { count: below.length }) })}</p>
      <div className="radioList" role="radiogroup" aria-labelledby={`${id}-q`}>
        <span id={`${id}-q`} className="visuallyHidden">{t('files.folder.deleteTitle', { name: folder.name })}</span>
        <label><input type="radio" name={id} checked={choice === 'move_up'} onChange={() => setChoice('move_up')} />{t('files.folder.keepContents', { parent })}</label>
        <label><input type="radio" name={id} checked={choice === 'delete'} onChange={() => setChoice('delete')} aria-describedby={`${id}-hint`} />{t('files.folder.deleteContents')}</label>
        <small id={`${id}-hint`} className="muted">{t('files.folder.deleteContentsHint')}</small>
      </div>
    </>}
  </ConfirmDialog>;
}

function countBelow(folders: readonly ApiFolder[], folderId: string): number {
  return folderChildren(folders, folderId).reduce((sum, child) => sum + child.file_count + countBelow(folders, child.id), 0);
}

// --------------------------------------------------------------------------- tags

export function TagBar({ tags, active, canUpload, onToggle, onChanged, onNote, onProblem }: {
  tags: ApiTag[]; active: string | null; canUpload: boolean; onToggle(tagId: string | null): void; onChanged(): Promise<void> | void; onNote(text: string): void; onProblem(text: string): void;
}) {
  const [managing, setManaging] = useState(false);
  if (!tags.length && !canUpload) return null;
  return <div className="tagBar" role="group" aria-label={t('files.tag.filter')}>
    <TagIcon aria-hidden="true" className="tagBarIcon" />
    {tags.map((tag) => <button key={tag.id} type="button" className={`tagChipButton ${active === tag.id ? 'on' : ''}`} aria-pressed={active === tag.id} aria-label={`${tag.name}: ${t('files.tag.filterBy', { name: tag.name })}`} onClick={() => onToggle(active === tag.id ? null : tag.id)}><span dir="auto">{tag.name}</span><small>{formatNumber(tag.file_count)}</small></button>)}
    {active && <button type="button" className="secondary small" onClick={() => onToggle(null)}><X aria-hidden="true" />{t('files.tag.clearFilter')}</button>}
    {canUpload && <button type="button" className="secondary small tagManage" onClick={() => setManaging(true)}><Pencil aria-hidden="true" />{t('files.tag.manage')}</button>}
    {managing && <ManageTagsDialog tags={tags} canUpload={canUpload} onClose={() => setManaging(false)} onChanged={onChanged} onNote={onNote} onProblem={onProblem} />}
  </div>;
}

function ManageTagsDialog({ tags, canUpload, onClose, onChanged, onNote, onProblem }: {
  tags: ApiTag[]; canUpload: boolean; onClose(): void; onChanged(): Promise<void> | void; onNote(text: string): void; onProblem(text: string): void;
}) {
  const [draft, setDraft] = useState('');
  const [renaming, setRenaming] = useState<ApiTag | null>(null), [renameTo, setRenameTo] = useState('');
  const [confirming, setConfirming] = useState<ApiTag | null>(null);
  const [busy, setBusy] = useState(false), [local, setLocal] = useState<string | null>(null);
  const id = useId();
  const problem = tagNameProblem(draft, tags);
  const renameProblemText = renaming ? tagNameProblem(renameTo, tags, renaming.id) : null;
  async function run(work: () => Promise<unknown>, done: string, failedKey: 'files.tag.createFailed' | 'files.tag.renameFailed' | 'files.tag.deleteFailed') {
    setBusy(true); setLocal(null);
    try { await work(); onNote(done); await onChanged(); return true; }
    catch (e) { setLocal(isPermissionDenied(e) ? t('files.tag.manageDenied') : t(failedKey, { message: describeError(e) })); return false; }
    finally { setBusy(false); }
  }
  return <Modal title={t('files.tag.manageTitle')} className="tagsDialog" onClose={onClose}>
    <div className="dialogBody">
      <p className="muted">{t('files.tag.manageHelp')}</p>
      {local && <Notice error>{local}</Notice>}
      {canUpload && <form className="tagNewForm" onSubmit={(e) => { e.preventDefault(); if (problem) return; void run(() => api.createFileTag(draft.trim()), t('files.tag.created', { name: draft.trim() }), 'files.tag.createFailed').then((ok) => { if (ok) setDraft(''); }); }}>
        <label className="field"><span>{t('files.tag.nameField')}</span><input id={id} value={draft} maxLength={64} dir="auto" autoFocus data-autofocus aria-invalid={Boolean(problem && draft)} aria-describedby={`${id}-hint`} onChange={(e) => setDraft(e.target.value)} /></label>
        <button type="submit" disabled={Boolean(problem) || busy}><Plus aria-hidden="true" />{t('files.tag.create')}</button>
        <small id={`${id}-hint`} className={problem && draft ? 'fieldError' : 'muted'}>{draft ? problem ?? '' : ''}</small>
      </form>}
      {tags.length === 0
        ? <div className="fileVersionsEmpty"><strong>{t('files.tag.none')}</strong><p>{canUpload ? t('files.tag.noneText') : t('files.tag.noneReadOnly')}</p></div>
        : <ul className="tagManageList">{tags.map((tag) => <li key={tag.id}>
          {renaming?.id === tag.id
            ? <form className="tagRenameForm" onSubmit={(e) => { e.preventDefault(); if (renameProblemText) return; void run(() => api.renameFileTag(tag.id, renameTo.trim()), t('files.tag.renamed', { name: renameTo.trim() }), 'files.tag.renameFailed').then((ok) => { if (ok) setRenaming(null); }); }}>
              <label className="field"><span className="visuallyHidden">{t('files.tag.nameField')}</span><input value={renameTo} maxLength={64} dir="auto" autoFocus aria-invalid={Boolean(renameProblemText)} onChange={(e) => setRenameTo(e.target.value)} onKeyDown={(e) => { if (e.key === 'Escape') setRenaming(null); }} /></label>
              {renameProblemText && <small className="fieldError">{renameProblemText}</small>}
              <button type="submit" disabled={Boolean(renameProblemText) || busy || renameTo.trim() === tag.name}>{t('files.detail.saveName')}</button><button type="button" className="secondary" onClick={() => setRenaming(null)}>{t('common.cancel')}</button>
            </form>
            : <>
              <span className="tagChipButton static"><span dir="auto">{tag.name}</span><small>{t('files.tag.usage', { count: tag.file_count })}</small></span>
              {tag.can_manage && (confirming?.id === tag.id
                ? <span className="cellActions"><span className="visuallyHidden" role="status">{t('files.tag.confirmDelete', { name: tag.name, count: files(tag.file_count) })}</span><button type="button" className="danger" disabled={busy} aria-label={t('files.tag.confirmDeleteOf', { name: tag.name })} onClick={() => void run(() => api.deleteFileTag(tag.id), t('files.tag.deleted', { name: tag.name }), 'files.tag.deleteFailed').then(() => setConfirming(null))}>{t('files.tag.delete')}</button><button type="button" className="secondary" onClick={() => setConfirming(null)}>{t('files.keep')}</button></span>
                : <span className="cellActions"><button type="button" className="iconButton small" title={t('files.tag.rename')} aria-label={t('files.tag.renameOf', { name: tag.name })} onClick={() => { setRenaming(tag); setRenameTo(tag.name); }}><Pencil aria-hidden="true" /></button><button type="button" className="iconButton small" title={t('files.tag.delete')} aria-label={t('files.tag.deleteOf', { name: tag.name })} onClick={() => setConfirming(tag)}><Trash2 aria-hidden="true" /></button></span>)}
              {!tag.can_manage && <span className="muted tagLock"><Lock aria-hidden="true" /></span>}
            </>}
        </li>)}</ul>}
      <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('dialog.close')}</button></div>
    </div>
  </Modal>;
}

/** The tags on one file in the detail pane: chips with remove, and a picker that adds an existing tag or creates one. */
export function FileTags({ file, tags, canUpload, onChange, onTagsChanged }: {
  file: ApiFile; tags: ApiTag[]; canUpload: boolean; onChange(file: ApiFile): void; onTagsChanged(): Promise<void> | void;
}) {
  const [adding, setAdding] = useState(false), [pick, setPick] = useState(''), [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const id = useId();
  const on = new Set((file.tags ?? []).map((x) => x.id));
  const free = tags.filter((x) => !on.has(x.id));
  const draftProblem = draft ? tagNameProblem(draft, tags) : null;
  async function apply(work: () => Promise<ApiFile>) {
    setBusy(true); setProblem(null);
    try { onChange(await work()); setAdding(false); setPick(''); setDraft(''); await onTagsChanged(); }
    catch (e) { setProblem(isPermissionDenied(e) ? t('files.uploadRoleDenied') : t('files.tag.changeFailed', { message: describeError(e) })); }
    finally { setBusy(false); }
  }
  async function add() {
    if (pick) return apply(() => api.tagFile(file.id, [pick]));
    const name = draft.trim();
    if (!name || draftProblem) return;
    return apply(async () => { const created = await api.createFileTag(name); return api.tagFile(file.id, [created.id]); });
  }
  return <div className="fileTags" role="group" aria-labelledby={`${id}-label`}>
    <div className="fileTagsHead"><h3 id={`${id}-label`}>{t('files.tag.onFile')}</h3>{canUpload && !adding && <button type="button" className="secondary small" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('files.tag.add')}</button>}</div>
    {problem && <Notice error>{problem}</Notice>}
    <ul className="fileTagList">
      {(file.tags ?? []).map((tag) => <li key={tag.id}><span className="tagChipButton static"><span dir="auto">{tag.name}</span>{canUpload && <button type="button" className="tagRemove" disabled={busy} aria-label={t('files.tag.remove', { tag: tag.name, name: file.name })} title={t('files.tag.remove', { tag: tag.name, name: file.name })} onClick={() => void apply(() => api.untagFile(file.id, tag.id))}><X aria-hidden="true" /></button>}</span></li>)}
      {!(file.tags ?? []).length && !adding && <li className="muted">{t('files.tag.noneOnFile')}</li>}
    </ul>
    {adding && <form className="tagAddForm" onSubmit={(e) => { e.preventDefault(); void add(); }}>
      {free.length > 0 && <label className="field"><span>{t('files.tag.addPick')}</span><select value={pick} autoFocus onChange={(e) => { setPick(e.target.value); if (e.target.value) setDraft(''); }}><option value="">-</option>{free.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>}
      <label className="field"><span>{t('files.tag.new')}</span><input value={draft} maxLength={64} dir="auto" autoFocus={free.length === 0} aria-invalid={Boolean(draftProblem)} aria-describedby={draftProblem ? `${id}-draft` : undefined} disabled={Boolean(pick)} onChange={(e) => setDraft(e.target.value)} /></label>
      {draftProblem && <small id={`${id}-draft`} className="fieldError">{draftProblem}</small>}
      <div className="actions"><button type="submit" disabled={busy || (!pick && (!draft.trim() || Boolean(draftProblem)))}>{busy ? t('dialog.working') : pick ? t('files.tag.add') : t('files.tag.addNew', { name: draft.trim() })}</button><button type="button" className="secondary" onClick={() => { setAdding(false); setPick(''); setDraft(''); }}>{t('common.cancel')}</button></div>
    </form>}
  </div>;
}

// --------------------------------------------------------------------------- bulk actions

export function BulkBar({ selected, shown, folders, tags, folder, canUpload, onClear, onChanged, onNote, onProblem }: {
  selected: ApiFile[]; shown: number; folders: ApiFolder[]; tags: ApiTag[]; folder: ApiFolder | null; canUpload: boolean;
  onClear(): void; onChanged(): Promise<void> | void; onNote(text: string): void; onProblem(text: string): void;
}) {
  const [dialog, setDialog] = useState<null | 'move' | 'tag' | 'delete'>(null);
  const [busy, setBusy] = useState<null | 'download' | 'delete'>(null);
  const bar = useRef<HTMLDivElement>(null);
  useEffect(() => { bar.current?.focus(); }, []);
  const count = selected.length;
  const ids = selected.map((f) => f.id);
  const notMine = selected.filter((f) => !f.can_manage).length;
  const canChange = canUpload && notMine === 0 && count <= BULK_MAX_FILES;
  const label = files(count);

  async function run(work: () => Promise<unknown>, done: () => string, failed: (message: string) => string, keep = false) {
    try { await work(); setDialog(null); onNote(done()); if (!keep) { onClear(); await onChanged(); } }
    catch (e) { setDialog(null); onProblem(failed(describeError(e))); }
    finally { setBusy(null); }
  }
  const download = () => {
    setBusy('download'); onNote(t('files.bulk.downloading'));
    const name = `${folder ? folder.name.replace(/[^\p{L}\p{N}._-]+/gu, '-').replace(/^-+|-+$/g, '') || 'files' : 'files'}-${new Date().toISOString().slice(0, 10).replaceAll('-', '')}.zip`;
    void run(() => api.bulkDownloadFiles(ids, name), () => t('files.bulk.downloaded', { count: label, name }), (m) => t('files.bulk.downloadFailed', { message: m }), true);
  };

  return <div className="bulkBar surface" role="region" aria-label={t('files.sel.bar')} ref={bar} tabIndex={-1}>
    <strong role="status">{t('files.sel.count', { count })}</strong>
    <small className="muted">{shown > count ? t('files.sel.hint') : ''}</small>
    <div className="bulkActions">
      <button type="button" className="secondary" disabled={!canChange || busy !== null} aria-describedby={!canChange ? 'bulk-denied' : undefined} onClick={() => setDialog('move')}><FolderInput aria-hidden="true" />{t('files.bulk.move')}</button>
      <button type="button" className="secondary" disabled={!canUpload || busy !== null || count > BULK_MAX_FILES} onClick={() => setDialog('tag')}><TagIcon aria-hidden="true" />{t('files.bulk.tag')}</button>
      <button type="button" className="secondary" disabled={busy !== null || count > BULK_MAX_FILES} onClick={download}><Download aria-hidden="true" />{busy === 'download' ? t('files.bulk.downloading') : t('files.bulk.download')}</button>
      <button type="button" className="secondary dangerText" disabled={!canChange || busy !== null} aria-describedby={!canChange ? 'bulk-denied' : undefined} onClick={() => setDialog('delete')}><Trash2 aria-hidden="true" />{t('files.bulk.delete')}</button>
      <button type="button" className="secondary" onClick={onClear}><X aria-hidden="true" />{t('files.sel.clear')}</button>
    </div>
    {!canChange && <small id="bulk-denied" className="muted bulkNote"><Lock aria-hidden="true" />{count > BULK_MAX_FILES ? t('files.sel.max', { max: BULK_MAX_FILES }) : !canUpload ? t('files.bulk.denied') : t('files.bulk.manageOnly', { count: notMine })}</small>}
    {dialog === 'move' && <MoveDialog title={t('files.bulk.moveTitle', { count: label })} confirm={t('files.bulk.moveConfirm')} folders={folders} initial={folder?.id ?? null} onCancel={() => setDialog(null)}
      onConfirm={(target) => run(() => api.bulkMoveFiles(ids, target), () => t('files.bulk.moved', { count: label, target: targetName(folders, target) }), (m) => t('files.bulk.moveFailed', { message: m }))} />}
    {dialog === 'tag' && <BulkTagDialog selected={selected} tags={tags} onCancel={() => setDialog(null)} onChanged={onChanged}
      onApply={(add, remove) => run(async () => { if (add.length) await api.bulkTagFiles(ids, add); if (remove.length) await api.bulkUntagFiles(ids, remove); }, () => t('files.bulk.tagged', { count: label }), (m) => t('files.bulk.tagFailed', { message: m }))} />}
    {dialog === 'delete' && <ConfirmDialog title={t('files.bulk.deleteTitle', { count: label })} confirmLabel={t('files.bulk.deleteConfirm', { count: label })} danger busy={busy === 'delete'} onCancel={() => setDialog(null)}
      onConfirm={() => { setBusy('delete'); void run(() => api.bulkDeleteFiles(ids), () => t('files.bulk.deleted', { count: label }), (m) => t('files.bulk.deleteFailed', { message: m })); }}>
      <p>{t('files.bulk.deleteText')}</p>
      <ul className="bulkNames">{selected.slice(0, 8).map((f) => <li key={f.id} dir="auto">{f.name}</li>)}{count > 8 && <li className="muted">+{formatNumber(count - 8)}</li>}</ul>
    </ConfirmDialog>}
  </div>;
}

function BulkTagDialog({ selected, tags, onApply, onCancel, onChanged }: {
  selected: ApiFile[]; tags: ApiTag[]; onApply(add: string[], remove: string[]): Promise<void>; onCancel(): void; onChanged(): Promise<void> | void;
}) {
  const [choices, setChoices] = useState<Record<string, boolean>>({});
  const [draft, setDraft] = useState(''), [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const [extra, setExtra] = useState<ApiTag[]>([]);
  const id = useId();
  const all = [...tags, ...extra.filter((x) => !tags.some((y) => y.id === x.id))];
  const draftProblem = draft ? tagNameProblem(draft, all) : null;
  const state = (tag: ApiTag) => choices[tag.id] ?? (tagState(selected, tag.id) === 'all');
  const apply = () => {
    const add = all.filter((x) => (choices[x.id] ?? false) === true && tagState(selected, x.id) !== 'all').map((x) => x.id);
    const remove = all.filter((x) => choices[x.id] === false && tagState(selected, x.id) !== 'none').map((x) => x.id);
    setBusy(true); void onApply(add, remove).finally(() => setBusy(false));
  };
  const create = async () => {
    const name = draft.trim(); if (!name || draftProblem) return;
    setBusy(true); setProblem(null);
    try { const created = await api.createFileTag(name); setExtra((x) => [...x, created]); setChoices((c) => ({ ...c, [created.id]: true })); setDraft(''); await onChanged(); }
    catch (e) { setProblem(isPermissionDenied(e) ? t('files.uploadRoleDenied') : t('files.tag.createFailed', { message: describeError(e) })); }
    finally { setBusy(false); }
  };
  return <Modal title={t('files.bulk.tagTitle', { count: files(selected.length) })} className="tagsDialog" onClose={onCancel}>
    <div className="dialogBody">
      <p className="muted">{t('files.bulk.tagHelp')}</p>
      {problem && <Notice error>{problem}</Notice>}
      {all.length > 0 && <ul className="tagCheckList">{all.map((tag) => {
        const mixed = choices[tag.id] === undefined && tagState(selected, tag.id) === 'some';
        return <li key={tag.id}><label><input type="checkbox" checked={state(tag)} ref={(el) => { if (el) el.indeterminate = mixed; }} onChange={(e) => setChoices((c) => ({ ...c, [tag.id]: e.target.checked }))} /><span dir="auto">{tag.name}</span></label></li>;
      })}</ul>}
      <form className="tagNewForm" onSubmit={(e) => { e.preventDefault(); void create(); }}>
        <label className="field"><span>{t('files.tag.new')}</span><input id={id} value={draft} maxLength={64} dir="auto" autoFocus={all.length === 0} aria-invalid={Boolean(draftProblem)} aria-describedby={`${id}-hint`} onChange={(e) => setDraft(e.target.value)} /></label>
        <button type="submit" className="secondary" disabled={busy || !draft.trim() || Boolean(draftProblem)}><Plus aria-hidden="true" />{t('files.tag.create')}</button>
        <small id={`${id}-hint`} className={draftProblem ? 'fieldError' : 'muted'}>{draftProblem ?? ''}</small>
      </form>
      <div className="actions"><button type="button" disabled={busy || Object.keys(choices).length === 0} data-autofocus={all.length > 0 ? true : undefined} onClick={apply}>{busy ? t('dialog.working') : t('files.bulk.tagApply')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    </div>
  </Modal>;
}
