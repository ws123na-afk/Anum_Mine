// Finance, Trips and projects (docs/finance.md#trips-and-projects): the person's own trips and projects, each with what is
// tagged to it per currency (never added across currencies). Tagging a payment is a finance link on the real ledger
// transaction; a tag is a view only, so the payment still counts by its own category. A section of Debts and goals
// (#finance-goals-trips); suggestions propose tags and nothing is tagged until the person accepts.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { MapPin, Pencil, Plus, Search, Tags, Trash2 } from 'lucide-react';
import type { FinancePaymentCandidate, FinanceTag, FinanceTagPage } from '@anum/contracts';
import * as pm from './lib/financePersonalApi';
import { formatAmount, isolate } from './lib/finance';
import { PERSONAL_PAGE, pages } from './lib/financePersonal';
import { checkTag, tagBody, tripNeedsDates, type TagForm } from './lib/financeDepth2';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, StatusChip } from './FinanceShell';
import './financePersonal.css';
import './financeDepth2.css';

const day = (value: string) => formatDateTime(`${value}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const totals = (tag: FinanceTag) => Object.entries(tag.totals).map(([currency, value]) => isolate(formatAmount(value, currency))).join(' · ');

export function TagsSection() {
  const [kind, setKind] = useState<'' | 'trip' | 'project'>(''), [q, setQ] = useState(''), [archived, setArchived] = useState(false), [offset, setOffset] = useState(0);
  const [state, setState] = useState<Load<FinanceTagPage>>({ kind: 'loading' });
  const [editing, setEditing] = useState<FinanceTag | 'new' | null>(null), [open, setOpen] = useState<FinanceTag | null>(null);
  const [removing, setRemoving] = useState<FinanceTag | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const searchId = useId(), kindId = useId();
  const load = useCallback(() => {
    setState((c) => (c.kind === 'ready' ? c : { kind: 'loading' }));
    pm.listTags({ kind: kind || null, q, archived, limit: PERSONAL_PAGE, offset }).then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
  }, [kind, q, archived, offset]);
  useEffect(() => { load(); }, [load]);
  const remove = async () => {
    if (!removing) return;
    try { await pm.deleteTag(removing.id); setNotice({ text: t('fdepth2.tags.deleted', { name: removing.name }) }); load(); }
    catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setRemoving(null); }
  };
  const filtered = Boolean(kind || q || archived);
  const count = state.kind === 'ready' ? pages(state.value.total) : 0, current = Math.floor(offset / PERSONAL_PAGE) + 1;
  return <div className="fpmPage fdepth2Tags">
    <p className="muted fpmIntro">{t('fdepth2.tags.subtitle')}</p>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <FinCard title={t('fplan.debts.tab.trips')} icon={<Tags />} actions={<button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('fdepth2.tags.add')}</button>}>
      <div className="fpmFilters">
        <label className="field" htmlFor={searchId}><span><Search aria-hidden="true" />{t('fdepth2.tags.search')}</span>
          <input id={searchId} value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} dir="auto" /></label>
        <label className="field" htmlFor={kindId}><span>{t('fdepth2.tags.kind')}</span>
          <select id={kindId} value={kind} onChange={(e) => { setKind(e.target.value as '' | 'trip' | 'project'); setOffset(0); }}>
            <option value="">{t('fdepth2.tags.all')}</option><option value="trip">{t('fdepth2.tags.kind.trip')}</option><option value="project">{t('fdepth2.tags.kind.project')}</option></select></label>
        <label className="finAdvisorCheck"><input type="checkbox" checked={archived} onChange={(e) => { setArchived(e.target.checked); setOffset(0); }} />{t('fdepth2.tags.showArchived')}</label>
      </div>
      {state.kind === 'loading' && <Notice busy>{t('fpm.loading')}</Notice>}
      {state.kind === 'denied' && <Denied />}
      {state.kind === 'error' && <LoadError message={t('fdepth2.tags.error', { message: state.message })} retry={load} />}
      {state.kind === 'ready' && (state.value.total === 0
        ? (filtered ? <p className="muted">{t('fdepth2.tags.emptyFiltered')}</p>
          : <Empty icon={<MapPin aria-hidden="true" />} title={t('fdepth2.tags.emptyTitle')} text={t('fdepth2.tags.empty')}
            action={<button type="button" onClick={() => setEditing('new')}>{t('fdepth2.tags.add')}</button>} />)
        : <>
          <div className="finTableWrap" tabIndex={0}><table className="finGrid">
            <thead><tr><th scope="col">{t('fdepth2.tags.name')}</th><th scope="col">{t('fdepth2.tags.kind')}</th><th scope="col">{t('fdepth2.tags.when')}</th>
              <th scope="col" className="num">{t('fdepth2.tags.spent')}</th><th scope="col"><span className="visuallyHidden">{t('fpm.actions')}</span></th></tr></thead>
            <tbody>{state.value.items.map((tag) => <tr key={tag.id}>
              <th scope="row" dir="auto">{tag.name}{tag.archived && <small className="muted fpmBlock">{t('fdepth2.tags.archived')}</small>}</th>
              <td><StatusChip tone="muted">{t(`fdepth2.tags.kind.${tag.kind}` as MessageKey)}</StatusChip></td>
              <td>{tag.starts_on && tag.ends_on ? t('fdepth2.tags.dates', { from: day(tag.starts_on), to: day(tag.ends_on) }) : tag.starts_on ? day(tag.starts_on) : t('fdepth2.tags.noDates')}</td>
              <td className="num">{tag.links ? totals(tag) : <span className="muted">{t('fdepth2.tags.nothing')}</span>}</td>
              <td><div className="fpmRowActions">
                <button type="button" className="secondary" onClick={() => setOpen(tag)} aria-label={t('fdepth2.tags.openAria', { name: tag.name })}><Tags aria-hidden="true" />{t('fdepth2.tags.open')}</button>
                <button type="button" className="secondary iconOnly" onClick={() => setEditing(tag)} aria-label={t('fdepth2.tags.editAria', { name: tag.name })}><Pencil aria-hidden="true" /></button>
                <button type="button" className="secondary iconOnly" onClick={() => setRemoving(tag)} aria-label={t('fdepth2.tags.deleteAria', { name: tag.name })}><Trash2 aria-hidden="true" /></button>
              </div></td>
            </tr>)}</tbody>
          </table></div>
          {count > 1 && <div className="actions fpmPager">
            <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PERSONAL_PAGE))}>{t('fpm.previous')}</button>
            <span>{t('fpm.page', { page: current, pages: count })}</span>
            <button type="button" className="secondary" disabled={current >= count} onClick={() => setOffset(offset + PERSONAL_PAGE)}>{t('fpm.next')}</button>
          </div>}
        </>)}
    </FinCard>
    {editing && <Modal title={editing === 'new' ? t('fdepth2.tags.add') : t('fdepth2.tags.edit', { name: editing.name })} onClose={() => setEditing(null)}>
      <TagFormView tag={editing === 'new' ? null : editing} onDone={(text) => { setEditing(null); if (text) { setNotice({ text }); load(); } }} /></Modal>}
    {open && <TagDetailView tag={open} onClose={() => { setOpen(null); load(); }} />}
    {removing && <ConfirmDialog title={t('fdepth2.tags.deleteTitle', { name: removing.name })} confirmLabel={t('fpm.delete')} danger onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>
      <p>{t('fdepth2.tags.deleteText')}</p></ConfirmDialog>}
  </div>;
}

function TagFormView({ tag, onDone }: { tag: FinanceTag | null; onDone: (text: string | null) => void }) {
  const [form, setForm] = useState<TagForm>({ kind: tag?.kind ?? 'trip', name: tag?.name ?? '', starts_on: tag?.starts_on ?? '', ends_on: tag?.ends_on ?? '',
    keywords: tag?.keywords ?? '', notes: tag?.notes ?? '', archived: tag?.archived ?? false });
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const set = <K extends keyof TagForm>(key: K, value: TagForm[K]) => setForm((f) => ({ ...f, [key]: value }));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const problem = checkTag(form);
    if (problem) { setError(t(`fdepth2.tags.problem.${problem}` as MessageKey)); return; }
    setBusy(true); setError(null);
    try {
      const saved = tag ? await pm.updateTag(tag.id, tagBody(form)) : await pm.createTag(tagBody(form));
      onDone(t('fdepth2.tags.saved', { name: saved.name }));
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <form className="finForm" onSubmit={(e) => void submit(e)} noValidate>
    <fieldset className="fpmChoice"><legend>{t('fdepth2.tags.kind')}</legend>
      {(['trip', 'project'] as const).map((k) => <label key={k}><input type="radio" name="tagKind" value={k} checked={form.kind === k}
        disabled={Boolean(tag && tag.links > 0 && tag.kind !== k)} onChange={() => set('kind', k)} />{t(`fdepth2.tags.kind.${k}` as MessageKey)}</label>)}
    </fieldset>
    <label className="field"><span>{t('fdepth2.tags.name')}</span>
      <input value={form.name} onChange={(e) => set('name', e.target.value)} maxLength={120} required dir="auto" placeholder={t('fdepth2.tags.namePlaceholder')} /></label>
    <div className="fdepth2TagDates">
      <label className="field"><span>{t('fdepth2.tags.starts')}</span><input type="date" value={form.starts_on} onChange={(e) => set('starts_on', e.target.value)} /></label>
      <label className="field"><span>{t('fdepth2.tags.ends')}</span><input type="date" value={form.ends_on} min={form.starts_on || undefined} onChange={(e) => set('ends_on', e.target.value)} /></label>
    </div>
    {tripNeedsDates(form) && <p className="muted">{t('fdepth2.tags.tripDates')}</p>}
    <label className="field"><span>{t('fdepth2.tags.keywords')}</span>
      <input value={form.keywords} onChange={(e) => set('keywords', e.target.value)} maxLength={200} dir="auto" /><small className="muted">{t('fdepth2.tags.keywordsHelp')}</small></label>
    <label className="field"><span>{t('fpm.notes')}</span><textarea value={form.notes} onChange={(e) => set('notes', e.target.value)} maxLength={1000} rows={2} dir="auto" /></label>
    {tag && <label className="finAdvisorCheck"><input type="checkbox" checked={form.archived} onChange={(e) => set('archived', e.target.checked)} />{t('fdepth2.tags.archived')}</label>}
    {error && <Notice error>{error}</Notice>}
    <div className="actions">
      <button type="button" className="secondary" onClick={() => onDone(null)}>{t('fpm.cancel')}</button>
      <button type="submit" disabled={busy}>{busy ? t('finance.saving') : t('fpm.save')}</button>
    </div>
  </form>;
}

function TagDetailView({ tag, onClose }: { tag: FinanceTag; onClose: () => void }) {
  const [state, setState] = useState<Load<Awaited<ReturnType<typeof pm.getTag>>>>({ kind: 'loading' });
  const [linking, setLinking] = useState(false), [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setState((c) => (c.kind === 'ready' ? c : { kind: 'loading' }));
    pm.getTag(tag.id).then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
  }, [tag.id]);
  useEffect(() => { load(); }, [load]);
  const untag = (linkId: string) => { pm.untagTransaction(tag.id, linkId).then(load).catch((e) => setError(describeError(e))); };
  return <Modal title={tag.name} onClose={onClose} className="fpmDialog">
    {state.kind === 'loading' && <Notice busy>{t('fpm.loading')}</Notice>}
    {state.kind === 'denied' && <Denied />}
    {state.kind === 'error' && <LoadError message={t('fdepth2.tags.error', { message: state.message })} retry={load} />}
    {state.kind === 'ready' && <>
      <FinBanner tone="info">{t('fdepth2.tags.money')}</FinBanner>
      <dl className="finStatement">
        <div><dt>{t('fdepth2.tags.spent')}</dt><dd>{state.value.links ? totals(state.value) : t('fdepth2.tags.nothing')}</dd></div>
        {state.value.starts_on && state.value.ends_on && <div><dt>{t('fdepth2.tags.when')}</dt><dd>{t('fdepth2.tags.dates', { from: day(state.value.starts_on), to: day(state.value.ends_on) })}</dd></div>}
      </dl>
      {error && <Notice error>{error}</Notice>}
      <h3 className="finAdvisorSub">{t('fpm.movements.title')}</h3>
      {state.value.movements.length === 0 ? <p className="muted">{t('fpm.movements.none')}</p>
        : <ul className="finMoveList">{state.value.movements.map((m) => <li key={m.link.id}>
          <div><span dir="auto">{m.description ?? '-'}</span><small>{m.posted_on ? day(m.posted_on) : '-'} · {t(`fpm.role.${m.link.role}` as MessageKey)}</small></div>
          {m.link.amount && m.link.currency ? <Money value={m.link.amount} currency={m.link.currency} /> : <span>-</span>}
          <button type="button" className="secondary" onClick={() => untag(m.link.id)}>{t('fdepth2.tags.untag')}</button>
        </li>)}</ul>}
      <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('fpm.close')}</button>
        <button type="button" onClick={() => setLinking(true)}>{t('fdepth2.tags.tag')}</button></div>
      {linking && <TagPicker tag={tag} onClose={() => setLinking(false)} onTagged={() => { setLinking(false); load(); }} />}
    </>}
  </Modal>;
}

function TagPicker({ tag, onClose, onTagged }: { tag: FinanceTag; onClose: () => void; onTagged: () => void }) {
  const [q, setQ] = useState(''), [direction, setDirection] = useState<'out' | 'in'>('out');
  const [rows, setRows] = useState<Load<FinancePaymentCandidate[]>>({ kind: 'loading' });
  const [busy, setBusy] = useState<string | null>(null), [error, setError] = useState<string | null>(null);
  const searchId = useId(), directionId = useId();
  const load = useCallback(() => {
    setRows({ kind: 'loading' });
    pm.candidates(tag.kind, direction, { around: tag.ends_on ?? tag.starts_on, q: q || null }).then((r) => setRows({ kind: 'ready', value: r.items })).catch((e) => setRows(failed(e)));
  }, [tag, direction, q]);
  useEffect(() => { load(); }, [load]);
  const pick = async (canonical: string) => {
    setBusy(canonical); setError(null);
    try { await pm.tagTransaction(tag.id, canonical); onTagged(); } catch (e) { setError(describeError(e)); } finally { setBusy(null); }
  };
  return <Modal title={t('fdepth2.tags.tagTitle', { name: tag.name })} onClose={onClose} className="fpmDialog">
    <div className="fpmFilters">
      <label className="field" htmlFor={searchId}><span>{t('fpm.link.search')}</span><input id={searchId} value={q} onChange={(e) => setQ(e.target.value)} dir="auto" /></label>
      <label className="field" htmlFor={directionId}><span>{t('fpm.people.direction')}</span>
        <select id={directionId} value={direction} onChange={(e) => setDirection(e.target.value as 'out' | 'in')}>
          <option value="out">{t('fpm.role.payment')}</option><option value="in">{t('fpm.role.refund')}</option></select></label>
    </div>
    {error && <Notice error>{error}</Notice>}
    {rows.kind === 'loading' && <Notice busy>{t('fpm.loading')}</Notice>}
    {rows.kind === 'denied' && <Denied />}
    {rows.kind === 'error' && <LoadError message={t('fpm.error', { message: rows.message })} retry={load} />}
    {rows.kind === 'ready' && (rows.value.length === 0 ? <p className="muted">{t('fpm.link.none')}</p>
      : <ul className="finMoveList fpmCandidates">{rows.value.map((c) => <li key={`${c.canonical_id}-${c.currency}`}>
        <div><span dir="auto">{c.merchant}</span><small>{day(c.posted_on)} · {t('fpm.link.left', { amount: isolate(formatAmount(c.remaining, c.currency)) })}</small></div>
        <Money value={c.amount} currency={c.currency} />
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void pick(c.canonical_id)}
          aria-label={t('fpm.link.useAria', { name: c.merchant, date: day(c.posted_on) })}>{t('fpm.link.use')}</button>
      </li>)}</ul>)}
    <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('fpm.close')}</button></div>
  </Modal>;
}
