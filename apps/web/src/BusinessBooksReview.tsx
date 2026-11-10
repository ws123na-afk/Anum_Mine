// Accountant review (docs/business-books.md#accountant-review): one list of everything in the books that waits for an owner
// or an accountant, with the close checklist of the previous and current month. The API decides who may see it (owners and
// accountants), who may approve each draft (never its maker) and what blocks a close; this page shows what it says and links
// to the section where each item is acted on. Approving a draft goes through POST /bills/{id}/approve, a pending adjustment through
// POST /adjustments/{id}/approve, and a proposed receipt is attached only when the person presses Attach. Dismiss stores the
// (bill, receipt) pair so it is never proposed again; Show dismissed receipts lists them with Restore.
import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, ClipboardList, EyeOff, Paperclip, RotateCcw } from 'lucide-react';
import type { BooksReceiptDismissal, BooksReview, BooksReviewItem, BooksReviewKind, BooksSetup } from '@anum/contracts';
import { BOOKS_REVIEW_KINDS } from '@anum/contracts';
import * as ledger from './lib/businessLedgerApi';
import * as books from './lib/businessBooksApi';
import { booksTabFromId, money, type BooksTab } from './lib/businessBooks';
import { describeError, isPermissionDenied } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { Badge, Denied, Empty, Failed, Note, Pager, day } from './BusinessParts';

const PAGE = 25;
type Load = { state: 'loading' } | { state: 'ready'; value: BooksReview } | { state: 'denied'; message: string } | { state: 'error'; message: string };
const label = (key: string, vars?: Record<string, string | number>) => t(key as MessageKey, vars);

const countryName = (code: string) => { const key = `gaps.country.${code}`; const text = label(key); return text === key ? code : text; };

/** What one item says, in the person's language (the API's English `detected` is for the assistant). */
export function reviewText(item: BooksReviewItem): string {
  const p = item.params;
  if (item.kind === 'external') return item.detected;
  if (item.kind === 'posting_issue') return label('breview.text.posting_issue', { what: label(`ledger.source.${p.source_kind}`), reason: label(`breview.reason.${p.reason}`) });
  if (item.kind === 'adjustment_approval') return label('fdepth.review.adjustment', { kind: label(`books.adj.${p.adjustment_kind}`), description: p.description ?? '' });
  if (item.kind === 'vat') return label(`fdepth.vat.${p.code}`, { from: p.from ? day(p.from) : '-', to: p.to ? day(p.to) : '-', due: p.due ? day(p.due) : '-',
    days: Number(p.days ?? 0), count: Number(p.count ?? p.days ?? 0), country: p.country ? countryName(p.country) : '-' });
  if (item.kind === 'receipt_match') return label(p.vendor_agrees === '1' ? 'fdepth.receipt.text' : 'fdepth.receipt.textNoMerchant', {
    document: p.document ?? '', vendor: p.vendor || '-', reference: p.reference || '-', date: p.receipt_date ? day(p.receipt_date) : '-' });
  return label(`breview.text.${item.kind}`, { vendor: p.vendor || '-', count: Number(p.count ?? 0), currency: item.currency ?? '', first: p.first ? day(p.first) : '-' });
}

/** The counts banner on the Books home: shown to owners and accountants when something waits. */
export function ReviewBanner({ setup, onSection }: { setup: BooksSetup; onSection: (tab: BooksTab) => void }) {
  const allowed = setup.permissions.manage || Boolean(setup.permissions.account);
  const [review, setReview] = useState<BooksReview | null>(null);
  useEffect(() => {
    if (!allowed) return;
    let live = true;
    ledger.getReview('', 0, 1).then((value) => { if (live) setReview(value); }).catch(() => { if (live) setReview(null); });
    return () => { live = false; };
  }, [allowed]);
  if (!allowed || !review) return null;
  const waiting = Object.values(review.counts).reduce((sum, n) => sum + (n ?? 0), 0);
  if (waiting === 0) return null;
  return <section className="surface bzCard bkReviewBanner" aria-labelledby="bk-review-banner">
    <h2 id="bk-review-banner"><ClipboardList aria-hidden="true" />{t('breview.banner.title', { count: waiting })}</h2>
    <p className="muted">{BOOKS_REVIEW_KINDS.filter((k) => review.counts[k]).map((k) => t(`breview.count.${k}` as MessageKey, { count: review.counts[k] ?? 0 })).join(' · ')}</p>
    <div className="bzInline"><button type="button" onClick={() => onSection('review')}>{t('breview.banner.open')}</button></div>
  </section>;
}

export function ReviewSection({ setup, onSection }: { setup: BooksSetup; onSection: (tab: BooksTab) => void }) {
  const [kind, setKind] = useState<BooksReviewKind | ''>('');
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [items, setItems] = useState<BooksReviewItem[]>([]);
  const [more, setMore] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [dismissedTick, setDismissedTick] = useState(0);
  // A quiet refresh (after Dismiss or Restore) keeps the page and the open dismissed list on screen while it reads.
  const run = useCallback(async (quiet = false) => {
    if (!quiet) setLoad({ state: 'loading' });
    try { const value = await ledger.getReview(kind, 0, PAGE); setLoad({ state: 'ready', value }); setItems(value.items); }
    catch (error) { setLoad(isPermissionDenied(error) ? { state: 'denied', message: describeError(error) } : { state: 'error', message: describeError(error) }); }
  }, [kind]);
  useEffect(() => { void run(); }, [run]);
  if (!setup.permissions.manage && !setup.permissions.account) return <Denied text={t('breview.denied')} />;
  if (load.state === 'loading') return <Note>{t('books.reading')}</Note>;
  if (load.state === 'denied') return <Denied text={load.message} />;
  if (load.state === 'error') return <Failed text={load.message} onRetry={() => void run()} />;
  const value = load.value;
  async function loadMore() {
    setMore({ busy: true, error: null });
    try { const next = await ledger.getReview(kind, items.length, PAGE); setItems([...items, ...next.items]); setMore({ busy: false, error: null }); }
    catch (error) { setMore({ busy: false, error: describeError(error) }); }
  }
  async function approve(item: BooksReviewItem) {
    if (!item.ref_id) return;
    setBusy(item.key); setMessage(null);
    try {
      if (item.kind === 'adjustment_approval') { await books.approveAdjustment(item.ref_id); setMessage({ text: t('fdepth.review.approved') }); }
      else { await books.approveBill(item.ref_id, Number(item.params.version ?? 1)); setMessage({ text: t('breview.approved', { vendor: item.params.vendor || '-' }) }); }
      await run();
    }
    catch (error) { setMessage({ text: describeError(error), error: true }); } finally { setBusy(null); }
  }
  async function attach(item: BooksReviewItem) {
    if (!item.ref_id || !item.params.document_id) return;
    setBusy(item.key); setMessage(null);
    try { await books.acceptReceiptMatch(item.ref_id, item.params.document_id); setMessage({ text: t('fdepth.receipt.attached', { document: item.params.document ?? '' }) }); await run(); }
    catch (error) { setMessage({ text: describeError(error), error: true }); } finally { setBusy(null); }
  }
  async function dismiss(item: BooksReviewItem) {
    if (!item.ref_id || !item.params.document_id) return;
    setBusy(item.key); setMessage(null);
    try { await books.dismissReceiptMatch(item.ref_id, item.params.document_id); setMessage({ text: t('gaps.receipt.dismissed', { document: item.params.document ?? '' }) }); setDismissedTick((n) => n + 1); await run(true); }
    catch (error) { setMessage({ text: describeError(error), error: true }); } finally { setBusy(null); }
  }
  const total = Object.values(value.counts).reduce((sum, n) => sum + (n ?? 0), 0);
  return <>
    <p className="muted bzHint">{t('breview.help')}</p>
    {message && <Note error={message.error}>{message.text}</Note>}
    {value.unavailable.length > 0 && <Note error>{t('breview.unavailable', { names: value.unavailable.join(', ') })}</Note>}
    <div className="bzOverview bkGrid">
      {value.close_months.map((month) => <section key={month.month} className="surface bzCard" aria-labelledby={`bk-review-${month.month}`}>
        <h2 id={`bk-review-${month.month}`}>{t('breview.close.title', { start: day(month.start), end: day(month.end) })}</h2>
        <p>{month.status === 'closed' ? <Badge tone="success">{t('breview.close.closed')}</Badge>
          : month.status === 'not_ended' ? <Badge tone="neutral">{t('breview.close.notEnded')}</Badge>
          : month.ready ? <Badge tone="success">{t('breview.close.ready')}</Badge> : <Badge tone="warning">{t('breview.close.waiting')}</Badge>}</p>
        <ul className="bkList" aria-label={t('ledger.close.checklist')}>
          {([['issues', month.issues, 'ledger'], ['drafts', month.drafts, 'bills'], ['allocations_in_review', month.allocations_in_review, 'allocations'],
            ['adjustments_pending', month.adjustments_pending ?? 0, 'review']] as const).map(([key, count, go]) => <li key={key}>
            <span>{t(`ledger.close.check.${key}` as MessageKey, { count })}</span>
            {count === 0 ? <Badge tone="success">{t('ledger.close.ok')}</Badge> : <button type="button" className="secondary" onClick={() => onSection(go)}>{t('ledger.close.fix')}</button>}
          </li>)}
          <li><span>{t('ledger.close.check.to_post', { count: value.to_post })}</span>
            {value.to_post === 0 ? <Badge tone="success">{t('ledger.close.ok')}</Badge> : <button type="button" className="secondary" onClick={() => onSection('ledger')}>{t('ledger.close.fix')}</button>}</li>
        </ul>
        {month.status === 'open' && <div className="bzInline"><button type="button" className="secondary" onClick={() => onSection('close')}>{t('breview.close.open')}</button></div>}
      </section>)}
    </div>
    <section className="surface bzCard bkWide" aria-labelledby="bk-review-list">
      <h2 id="bk-review-list">{t('breview.title', { count: total })}</h2>
      <div className="bzToolbar">
        <label className="bzCompact"><span>{t('breview.filter')}</span>
          <select value={kind} onChange={(event) => setKind(event.target.value as BooksReviewKind | '')}>
            <option value="">{t('breview.all')}</option>
            {BOOKS_REVIEW_KINDS.map((k) => <option key={k} value={k}>{t(`breview.kind.${k}` as MessageKey)} ({value.counts[k] ?? 0})</option>)}
          </select></label>
      </div>
      {items.length === 0 ? <Empty title={kind ? t('breview.emptyKind') : t('breview.emptyTitle')} text={t('breview.emptyText')}
        action={<button type="button" className="secondary" onClick={() => onSection('overview')}>{t('breview.backHome')}</button>} />
        : <ul className="bkReviewList">{items.map((item) => <li key={item.key}>
          <div className="bkReviewMain">
            <span className="bkReviewHead"><Badge tone={item.severity === 'blocking' ? 'warning' : 'neutral'}>{t(`breview.kind.${item.kind}` as MessageKey)}</Badge>
              {item.occurred_on && <small className="muted">{day(item.occurred_on)}</small>}</span>
            <strong dir="auto">{reviewText(item)}</strong>
            {item.amount && item.currency && <span className="bkMoney" dir="ltr">{money(item.amount, item.currency)}</span>}
            {item.severity === 'blocking' && <small className="muted">{t('breview.blocks')}</small>}
          </div>
          <div className="bzInline">
            {item.action === 'approve' && (item.can_act
              ? <button type="button" disabled={busy === item.key} onClick={() => void approve(item)}><CheckCircle2 aria-hidden="true" />{t('breview.approve')}</button>
              : <small className="muted">{t(`breview.whyNot.${item.why_not ?? 'own_draft'}` as MessageKey)}</small>)}
            {item.action === 'link' && <button type="button" disabled={busy === item.key} onClick={() => void attach(item)}><Paperclip aria-hidden="true" />{t('fdepth.receipt.attach')}</button>}
            {item.kind === 'receipt_match' && <button type="button" className="secondary" disabled={busy === item.key} aria-label={t('gaps.receipt.dismissLabel', { document: item.params.document ?? '' })}
              onClick={() => void dismiss(item)}><EyeOff aria-hidden="true" />{t('gaps.receipt.dismiss')}</button>}
            <button type="button" className="secondary" onClick={() => onSection(booksTabFromId(item.section))}>{t('breview.open', { section: t(`books.tab.${booksTabFromId(item.section)}` as MessageKey) })}</button>
          </div>
        </li>)}</ul>}
      <Pager shown={items.length} total={value.total} busy={more.busy} error={more.error} onMore={() => void loadMore()} />
    </section>
    <DismissedReceipts tick={dismissedTick} onRestored={(text) => { setMessage({ text }); void run(true); }} />
  </>;
}

type DismissedLoad = { state: 'loading' } | { state: 'ready'; items: BooksReceiptDismissal[]; total: number } | { state: 'error'; message: string };

/** Show dismissed receipts: the pairs never proposed again, newest first, each with Restore. */
function DismissedReceipts({ tick, onRestored }: { tick: number; onRestored: (text: string) => void }) {
  const [open, setOpen] = useState(false);
  const [load, setLoad] = useState<DismissedLoad>({ state: 'loading' });
  const [more, setMore] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const read = useCallback(async () => {
    setLoad({ state: 'loading' });
    try { const page = await books.listDismissedReceipts(0, PAGE); setLoad({ state: 'ready', items: page.items, total: page.total }); }
    catch (failure) { setLoad({ state: 'error', message: describeError(failure) }); }
  }, []);
  useEffect(() => { if (open) void read(); }, [open, read, tick]);
  async function loadMore() {
    if (load.state !== 'ready') return;
    setMore({ busy: true, error: null });
    try { const page = await books.listDismissedReceipts(load.items.length, PAGE); setLoad({ state: 'ready', items: [...load.items, ...page.items], total: page.total }); setMore({ busy: false, error: null }); }
    catch (failure) { setMore({ busy: false, error: describeError(failure) }); }
  }
  async function restore(row: BooksReceiptDismissal) {
    setBusy(row.id); setError(null);
    try { await books.restoreReceiptMatch(row.id); onRestored(t('gaps.receipt.restored')); await read(); }
    catch (failure) { setError(describeError(failure)); } finally { setBusy(null); }
  }
  const title = (row: BooksReceiptDismissal) => row.document_found && row.document_title ? row.document_title : t('gaps.receipt.documentGone');
  return <section className="surface bzCard bkWide" aria-labelledby="bk-review-dismissed">
    <div className="bzToolbar">
      <h2 id="bk-review-dismissed">{t('gaps.receipt.dismissedTitle')}</h2>
      <button type="button" className="secondary" aria-expanded={open} aria-controls="bk-review-dismissed-list" onClick={() => setOpen(!open)}>
        {open ? t('gaps.receipt.hideDismissed') : t('gaps.receipt.showDismissed')}</button>
    </div>
    {open && <div id="bk-review-dismissed-list">
      <p className="muted bzHint">{t('gaps.receipt.dismissedHelp')}</p>
      {error && <Note error>{error}</Note>}
      {load.state === 'loading' ? <Note>{t('books.reading')}</Note>
        : load.state === 'error' ? <Failed text={load.message} onRetry={() => void read()} />
        : load.items.length === 0 ? <p className="muted">{t('gaps.receipt.dismissedEmpty')}</p>
        : <><ul className="bkReviewList">{load.items.map((row) => <li key={row.id}>
          <div className="bkReviewMain">
            <strong dir="auto">{t('gaps.receipt.dismissedRow', { document: title(row), reference: row.bill_found ? row.bill_reference || '-' : t('gaps.receipt.billGone'),
              vendor: row.vendor || '-' })}</strong>
            {row.amount && row.currency && <span className="bkMoney" dir="ltr">{money(row.amount, row.currency)}</span>}
            <small className="muted">{t('gaps.receipt.dismissedOn', { date: day(row.dismissed_at.slice(0, 10)) })}</small>
          </div>
          <div className="bzInline"><button type="button" className="secondary" disabled={busy === row.id} aria-label={t('gaps.receipt.restoreLabel', { document: title(row) })}
            onClick={() => void restore(row)}><RotateCcw aria-hidden="true" />{t('gaps.receipt.restore')}</button></div>
        </li>)}</ul>
        <Pager shown={load.items.length} total={load.total} busy={more.busy} error={more.error} onMore={() => void loadMore()} /></>}
    </div>}
  </section>;
}
