// One lead list (docs/business.md#whatsapp-leads): the WhatsApp Sales CRM leads that are not in the
// Business list yet, and open leads that may be the same person. Nothing is merged on its own: every
// suggestion waits for a person's answer (link to that lead, or keep them apart), and only owners
// merge two leads, because a merge closes one of them.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { GitMerge, MessageCircle, Search, UserPlus, Users } from 'lucide-react';
import type { BusinessSetup, LeadDuplicatePair, WhatsAppWaitingLead } from '@anum/contracts';
import { describeError, isPermissionDenied } from './lib/errors';
import { t } from './lib/i18n';
import * as b from './lib/businessApi';
import { conversationHref, stageText, waitingName } from './lib/leadSources';
import { stateLabel } from './lib/business';
import { Badge, Note, when } from './BusinessParts';

const PAGE = 25;
type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'hidden' } | { kind: 'error'; text: string };

export function SourceBadge({ channel }: { channel: string | null | undefined }) {
  if (channel !== 'whatsapp') return null;
  return <span className="bzSourceBadge"><MessageCircle aria-hidden="true" />{t('bizw.source.whatsapp')}</span>;
}

/** WhatsApp leads waiting to join the list; hidden when there are none or the person may not read WhatsApp. */
export function WhatsAppWaiting({ setup, onChanged, onOpenLead }: { setup: BusinessSetup; onChanged: () => void; onOpenLead: (id: string) => void }) {
  const [items, setItems] = useState<WhatsAppWaitingLead[]>([]), [total, setTotal] = useState(0), [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [q, setQ] = useState(''), [search, setSearch] = useState('');
  const [busy, setBusy] = useState<string | null>(null), [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const [moreError, setMoreError] = useState<string | null>(null);
  const searchId = useId(), titleId = useId();
  // Many waiting leads would push the lead list below the fold: the list opens on request.
  const [open, setOpen] = useState<boolean | null>(null);

  const fetchFirst = useCallback(async () => {
    setLoad((current) => current.kind === 'hidden' ? current : { kind: 'loading' });
    try {
      const page = await b.listWaitingWhatsApp({ q: search || undefined, limit: PAGE, offset: 0 });
      setItems(page.items); setTotal(page.total);
      setLoad(page.total === 0 && !search ? { kind: 'hidden' } : { kind: 'ready' });
    } catch (error) {
      // Viewers, and people who may not read WhatsApp, simply do not see this part of the list.
      setLoad(isPermissionDenied(error) ? { kind: 'hidden' } : { kind: 'error', text: describeError(error) });
    }
  }, [search]);
  useEffect(() => { void fetchFirst(); }, [fetchFirst]);

  async function more() {
    setBusy('more'); setMoreError(null);
    try { const page = await b.listWaitingWhatsApp({ q: search || undefined, limit: PAGE, offset: items.length }); setItems((c) => [...c, ...page.items]); setTotal(page.total); }
    catch (error) { setMoreError(describeError(error)); } finally { setBusy(null); }
  }
  async function act(key: string, run: () => Promise<string>) {
    setBusy(key); setNote(null);
    try { setNote({ text: await run() }); await fetchFirst(); onChanged(); }
    catch (error) { setNote({ text: describeError(error), error: true }); } finally { setBusy(null); }
  }
  const mapOne = (item: WhatsAppWaitingLead, body: { link_to_lead_id?: string; separate_from?: string[] }) => act(item.id, async () => {
    const result = await b.mapWhatsAppLead(item.id, body);
    return result.linked ? t('bizw.linked', { name: waitingName(item) }) : t('bizw.added', { name: waitingName(item) });
  });

  // After the last waiting lead joined the list, its outcome still shows.
  if (load.kind === 'hidden') return note ? <Note error={note.error}>{note.text}</Note> : null;
  const shown = open ?? (total <= 3 || Boolean(search));
  const canWrite = setup.permissions.write, canManage = setup.permissions.manage;
  return <section className="surface bzWaiting" aria-labelledby={titleId}>
    <div className="bzWaitingHead">
      <div><h3 id={titleId}><MessageCircle aria-hidden="true" className="bzInlineIcon" />{t('bizw.title', { count: total })}</h3><p className="muted">{t('bizw.text')}</p></div>
      {canManage && load.kind === 'ready' && total > 0 && <button type="button" disabled={busy !== null} onClick={() => void act('all', async () => {
        const result = await b.mapAllWhatsApp();
        return t('bizw.mappedAll', { mapped: result.mapped, waiting: result.waiting_for_owner });
      })}><UserPlus aria-hidden="true" />{t('bizw.mapAll')}</button>}
    </div>
    {note && <Note error={note.error}>{note.text}</Note>}
    {total > 3 && <div className="bzInline"><button type="button" className="secondary" aria-expanded={shown} onClick={() => setOpen(!shown)}>{shown ? t('bizw.hide') : t('bizw.review', { count: total })}</button></div>}
    {shown && <>
    <form className="bzSearch" role="search" onSubmit={(e: FormEvent) => { e.preventDefault(); setSearch(q.trim()); }}>
      <label className="visuallyHidden" htmlFor={searchId}>{t('bizw.search')}</label>
      <input id={searchId} type="search" value={q} placeholder={t('bizw.search')} onChange={(e) => { setQ(e.target.value); if (!e.target.value) setSearch(''); }} />
      <button type="submit" className="secondary"><Search aria-hidden="true" />{t('biz.find')}</button>
    </form>
    {load.kind === 'loading' && <Note>{t('bizw.loading')}</Note>}
    {load.kind === 'error' && <Note error>{t('bizw.failed', { message: load.text })} <button type="button" className="secondary" onClick={() => void fetchFirst()}>{t('common.tryAgain')}</button></Note>}
    {load.kind === 'ready' && items.length === 0 && <Note>{t('bizw.noMatch')} <button type="button" className="linkButton" onClick={() => { setQ(''); setSearch(''); }}>{t('biz.clearFilters')}</button></Note>}
    {items.length > 0 && <ul className="bzWaitingList">{items.map((item) => <li key={item.id} className="bzWaitingItem">
      <div className="bzWaitingText">
        <strong dir="auto">{waitingName(item)}</strong>
        <span className="bzWaitingMeta">{item.phone && <bdi dir="ltr">{item.phone}</bdi>}<span>{t('bizw.stage', { stage: stageText(item.stage) })}</span><span>{when(item.created_at)}</span>
          <a href={conversationHref(item.conversation_id)}>{t('bizw.conversation')}</a></span>
        {item.requirements && <span className="muted" dir="auto">{item.requirements}</span>}
      </div>
      {item.suggestions.length > 0 && <div className="bzSuggest" role="group" aria-label={t('bizw.maybeSame')}>
        <p><Users aria-hidden="true" className="bzInlineIcon" />{t('bizw.maybeSame')}</p>
        {item.suggestions.map((s) => <div key={s.lead_id} className="bzSuggestRow">
          <button type="button" className="linkButton" onClick={() => onOpenLead(s.lead_id)}><span dir="auto">{s.title}</span></button><small className="muted">{s.reason}</small>
          {canWrite && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void mapOne(item, { link_to_lead_id: s.lead_id })}><GitMerge aria-hidden="true" />{t('bizw.sameLink')}</button>}
        </div>)}
        {canWrite && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void mapOne(item, { separate_from: item.suggestions.map((s) => s.lead_id) })}>{t('bizw.different')}</button>}
      </div>}
      {item.suggestions.length === 0 && canWrite && <div className="bzInline"><button type="button" className="secondary" disabled={busy !== null} onClick={() => void mapOne(item, {})}><UserPlus aria-hidden="true" />{t('bizw.add')}</button></div>}
    </li>)}</ul>}
    {items.length < total && <div className="bzMore"><button type="button" className="secondary" disabled={busy === 'more'} onClick={() => void more()}>{t('biz.showMore', { shown: items.length, total })}</button>{moreError && <Note error>{moreError}</Note>}</div>}
    </>}
  </section>;
}

/** Open leads that share a phone number or e-mail address: merge (owners) or keep apart. */
export function DuplicatePairs({ setup, onChanged, onOpenLead }: { setup: BusinessSetup; onChanged: () => void; onOpenLead: (id: string) => void }) {
  const [pairs, setPairs] = useState<LeadDuplicatePair[]>([]), [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [busy, setBusy] = useState(false), [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const titleId = useId();
  const fetchPairs = useCallback(async () => {
    try { const found = await b.listLeadDuplicates(); setPairs(found); setLoad(found.length ? { kind: 'ready' } : { kind: 'hidden' }); }
    catch (error) { setLoad(isPermissionDenied(error) ? { kind: 'hidden' } : { kind: 'error', text: describeError(error) }); }
  }, []);
  useEffect(() => { void fetchPairs(); }, [fetchPairs]);
  async function act(run: () => Promise<string>) {
    setBusy(true); setNote(null);
    try { setNote({ text: await run() }); await fetchPairs(); onChanged(); }
    catch (error) { setNote({ text: describeError(error), error: true }); } finally { setBusy(false); }
  }
  if (load.kind === 'loading') return null;
  if (load.kind === 'hidden') return note ? <Note error={note.error}>{note.text}</Note> : null;
  return <section className="surface bzWaiting" aria-labelledby={titleId}>
    <div className="bzWaitingHead"><div><h3 id={titleId}><Users aria-hidden="true" className="bzInlineIcon" />{t('bizd.title', { count: pairs.length })}</h3><p className="muted">{t('bizd.text')}</p></div></div>
    {note && <Note error={note.error}>{note.text}</Note>}
    {load.kind === 'error' && <Note error>{t('bizd.failed', { message: load.text })} <button type="button" className="secondary" onClick={() => void fetchPairs()}>{t('common.tryAgain')}</button></Note>}
    {pairs.length > 0 && <ul className="bzWaitingList">{pairs.map((pair) => <li key={`${pair.first.id}|${pair.second.id}`} className="bzWaitingItem">
      <div className="bzWaitingText"><span className="muted">{pair.reason}</span>
        {[pair.first, pair.second].map((side) => <span key={side.id} className="bzPairSide">
          <button type="button" className="linkButton" onClick={() => onOpenLead(side.id)}><span dir="auto">{side.title}</span></button>
          <Badge tone="neutral">{stateLabel(side.state)}</Badge><SourceBadge channel={side.channel} /></span>)}
      </div>
      <div className="bzInline">
        {setup.permissions.manage && [pair.first, pair.second].map((keep) => {
          const other = keep === pair.first ? pair.second : pair.first;
          return <button key={keep.id} type="button" className="secondary" disabled={busy} onClick={() => {
            if (!window.confirm(t('bizd.mergeConfirm', { keep: keep.title, close: other.title }))) return;
            void act(async () => { await b.mergeLeads(keep.id, other.id); return t('bizd.merged', { keep: keep.title }); });
          }}><GitMerge aria-hidden="true" />{t('bizd.keep', { title: keep.title })}</button>;
        })}
        {setup.permissions.write && <button type="button" className="secondary" disabled={busy} onClick={() => void act(async () => { await b.keepLeadsSeparate(pair.first.id, pair.second.id); return t('bizd.separated'); })}>{t('bizd.different')}</button>}
        {!setup.permissions.manage && <small className="muted">{t('bizd.ownerMerges')}</small>}
      </div>
    </li>)}</ul>}
  </section>;
}
