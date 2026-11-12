// What ANUM learned from the person's reviews of bank messages, and why messages wait (docs/finance-sms.md#learning).
// Part of the lazy FinanceSms chunk: the "Why is this waiting?" summary, the learned-notices list with undo, and the
// one-tap fix shown on each waiting message.
import { useCallback, useEffect, useId, useState } from 'react';
import { Lightbulb, Undo2 } from 'lucide-react';
import type { FinanceAccount, FinanceSmsFix, FinanceSmsLearning, FinanceSmsLearningNotice, FinanceSmsReviewSummary } from '@anum/contracts';
import * as api from './lib/financeSmsApi';
import { ONE_TAP_FIXES, fixText, noticeText, reasonLabel } from './lib/financeSms';
import { describeError } from './lib/errors';
import { t } from './lib/i18n';
import { Denied, FormError, LoadError, Notice, failed, type Load } from './FinanceParts';

/** Run one fix. ``messageId`` is needed only for ``undo_learning``; ``accountId`` overrides a map_card proposal. */
export async function applyFix(fix: FinanceSmsFix, messageId?: string, accountId?: string): Promise<void> {
  switch (fix.action) {
    case 'trust_sender': await api.trustSender(fix.sender ?? ''); return;
    case 'clear_hold': await api.clearHold(fix.sender ?? ''); return;
    case 'auto_post_on': await api.setAutoPost(true); return;
    case 'remember_kind': await api.activateKind(fix.ref ?? ''); return;
    case 'map_card': await api.mapCard(fix.last4 ?? '', accountId ?? fix.account_id ?? ''); return;
    case 'undo_learning': if (messageId) await api.undoLearning(messageId); return;
    default: return;
  }
}

/** The fix line on one message: the words, and the button when it can be done in one tap. */
export function FixLine({ fix, messageId, accounts, onDone, onOpenSettings }: {
  fix: FinanceSmsFix; messageId?: string; accounts: FinanceAccount[]; onDone: () => void; onOpenSettings: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [account, setAccount] = useState(fix.account_id ?? '');
  const errorId = useId();
  const pickId = useId();
  const text = fixText(fix);
  const run = async () => {
    setBusy(true); setError(null);
    try { await applyFix(fix, messageId, account); onDone(); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const needsAccount = fix.action === 'map_card' && !fix.account_id;
  const spoken = fix.action === 'undo_learning' || needsAccount || !ONE_TAP_FIXES.includes(fix.action);
  return <div className="finSmsFix">
    {spoken && <span className="finSmsFixText"><Lightbulb aria-hidden="true" />{text}</span>}
    {needsAccount && <label className="cmInline"><span id={pickId}>{t('fsms.fix.pickAccount', { last4: fix.last4 ?? '-' })}</span>
      <select value={account} aria-labelledby={pickId} onChange={(e) => setAccount(e.target.value)}>
        <option value="">{t('fsms.pick')}</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>}
    {fix.action === 'undo_learning'
      ? <button type="button" className="secondary" disabled={busy} aria-label={t('fsms.learn.undoLabel', { text })} onClick={() => void run()}><Undo2 aria-hidden="true" />{t('fsms.fix.undo')}</button>
      : ONE_TAP_FIXES.includes(fix.action)
        ? <button type="button" disabled={busy || (needsAccount && !account)} aria-describedby={error ? errorId : undefined}
          aria-label={needsAccount ? t('fsms.learn.mapLabel', { text }) : undefined} onClick={() => void run()}>
          {needsAccount ? t('fsms.fix.map') : <><Lightbulb aria-hidden="true" />{text}</>}</button>
        : fix.action === 'open_settings' ? <button type="button" className="secondary" onClick={onOpenSettings}>{t('fsms.section.setup')}</button> : null}
    <FormError id={errorId} text={error} />
  </div>;
}

/** "Why is this waiting?": counts per reason, split by the sender or card they share, each with its fix. */
export function WhyWaiting({ accounts, refresh, onChanged, onOpenSettings }: { accounts: FinanceAccount[]; refresh: number; onChanged: () => void; onOpenSettings: () => void }) {
  const [summary, setSummary] = useState<Load<FinanceSmsReviewSummary>>({ kind: 'loading' });
  const load = useCallback(() => { api.reviewSummary().then((value) => setSummary({ kind: 'ready', value }), (e) => setSummary(failed(e))); }, []);
  useEffect(() => { load(); }, [load, refresh]);
  if (summary.kind === 'loading') return <Notice busy>{t('fsms.loading')}</Notice>;
  if (summary.kind === 'denied') return <Denied />;
  if (summary.kind === 'error') return <LoadError message={summary.message} retry={load} />;
  const value = summary.value;
  if (value.total === 0) return null;
  return <section className="surface cmPanel" aria-labelledby="fsms-why">
    <h2 id="fsms-why">{t('fsms.why')}</h2>
    <p className="muted">{t('fsms.why.help', { count: value.total })}</p>
    {!value.complete && <p className="muted finHelp">{t('fsms.why.partial', { scanned: value.scanned, total: value.total })}</p>}
    <ul className="cmList finSmsWhy">{value.groups.map((group) => <li key={group.reason}>
      <div><strong>{reasonLabel(group.reason)}</strong><small>{t('fsms.why.count', { count: group.count })}</small>
        <ul className="finSmsWhyItems">{group.items.map((item) => <li key={`${group.reason}-${item.key}`}>
          {item.key !== '-' && <small dir="ltr">{item.key} ({item.count})</small>}
          {item.fix && <FixLine fix={item.fix} accounts={accounts} onDone={() => { load(); onChanged(); }} onOpenSettings={onOpenSettings} />}
        </li>)}</ul></div>
    </li>)}</ul>
  </section>;
}

/** "What ANUM learned": each notice with its undo (or Map and Not now for a card proposal). */
export function LearnedNotices({ refresh, onChanged }: { refresh: number; onChanged: () => void }) {
  const [view, setView] = useState<Load<FinanceSmsLearning>>({ kind: 'loading' });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { api.smsLearning().then((value) => setView({ kind: 'ready', value }), (e) => setView(failed(e))); }, []);
  useEffect(() => { load(); }, [load, refresh]);
  const act = async (work: () => Promise<FinanceSmsLearning>) => {
    setBusy(true); setError(null);
    try { setView({ kind: 'ready', value: await work() }); onChanged(); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  if (view.kind === 'loading' || view.kind === 'denied') return null;
  if (view.kind === 'error') return <LoadError message={view.message} retry={load} />;
  const notices = view.value.notices;
  if (notices.length === 0) return null;
  const undo = (n: FinanceSmsLearningNotice) => {
    if (n.kind === 'sender_trusted') return () => api.untrustSender(n.sender ?? '');
    if (n.kind === 'kind_remembered') return () => api.forgetKind(n.ref ?? '');
    if (n.kind === 'category_remembered') return () => api.forgetCategory(n.ref ?? '');
    return () => api.declineCard(n.last4 ?? '');
  };
  return <section className="surface cmPanel" aria-labelledby="fsms-learned">
    <h2 id="fsms-learned">{t('fsms.learn.title')}</h2>
    <p className="muted">{t('fsms.learn.help')}</p>
    <ul className="cmList">{notices.map((n) => {
      const text = noticeText(n);
      return <li key={n.id} className="finSmsMessage">
        <div><span>{text}</span></div>
        <div className="actions">
          {n.kind === 'card_proposed'
            ? <>
              <button type="button" disabled={busy || !n.account_id} aria-label={t('fsms.learn.mapLabel', { text })} onClick={() => void act(() => api.mapCard(n.last4 ?? '', n.account_id ?? ''))}>{t('fsms.fix.map')}</button>
              <button type="button" className="secondary" disabled={busy} aria-label={t('fsms.learn.notNowLabel', { text })} onClick={() => void act(undo(n))}>{t('fsms.learn.notNow')}</button>
            </>
            : <>
              <button type="button" className="secondary" disabled={busy} aria-label={t('fsms.learn.undoLabel', { text })} onClick={() => void act(undo(n))}><Undo2 aria-hidden="true" />{t('fsms.fix.undo')}</button>
              <button type="button" className="secondary" disabled={busy} aria-label={t('fsms.learn.okLabel', { text })} onClick={() => void act(() => api.dismissNotice(n.id))}>{t('fsms.learn.dismiss')}</button>
            </>}
        </div>
      </li>;
    })}</ul>
    <FormError id="fsms-learned-error" text={error} />
  </section>;
}
