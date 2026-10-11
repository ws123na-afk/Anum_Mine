// Developer mode from the Assistant chat and the voice view (docs/developer-mode.md).
// "developer, ...", "build ...", "Anum build ..." or "ANUM, in <project> ..." first resolves the
// project on the server (by name, by its description, or the only ready one). When it is clear the
// card always confirms "I'll ask the developer to: ... in <project>. Start?"; when it is not, it asks
// which project (buttons, or a spoken name) and then confirms. The request is created only after
// Start or a spoken "yes". When a pull request is ready it is announced; "approve" opens the merge
// approval with its payload hash, which still needs a tap on Confirm merge: nothing is ever approved
// by voice alone.
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { CheckCircle2, ExternalLink, Fingerprint, FolderGit2, Hammer, ShieldCheck, X } from 'lucide-react';
import type { Approval } from '@anum/contracts';
import * as api from './lib/api';
import { shortHash } from './lib/approvals';
import {
  confirmPrompt, isApproveIntent, matchProjectAnswer, mentionsProtected, newlyReady, orList, parseConfirmation, parseDeveloperCommand,
  pollDelay, readyAnnouncement, whichProjectPrompt, type DevRequest, type ProjectRef,
} from './lib/developer';
import { createDeveloperRequest, getDeveloperRequest, listDeveloperRequests, requestMergeApproval, resolveDeveloperProject } from './lib/developerApi';
import { describeError } from './lib/errors';
import { t } from './lib/i18n';
import { rich } from './rich';
import { useIsOwner } from './useIsOwner';
import './developer.css';

export interface DeveloperAssist {
  /** Handles a developer command or an answer to a pending question; resolves with the reply, or null when the text is not for the developer. */
  intercept: (text: string) => Promise<string | null>;
  /** True while a question waits for a spoken or typed answer ("Which project?", "Start?"). */
  awaitingAnswer: boolean;
  /** The confirmation, status and merge cards to render near the composer. */
  panel: ReactNode;
}

/** Waiting for Start (a project is known) or for the project to be named. */
type Pending =
  | { kind: 'confirm'; text: string; project: ProjectRef }
  | { kind: 'choose'; text: string; candidates: ProjectRef[] };

export function useDeveloperAssist({ source, announce, onOpenDeveloper }: {
  source: 'text' | 'voice';
  /** Said (voice) or shown (chat) when a pull request becomes ready for review. */
  announce?: (text: string) => void;
  onOpenDeveloper?: () => void;
}): DeveloperAssist {
  const owner = useIsOwner();
  const [pending, setPending] = useState<Pending | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [ready, setReady] = useState<DevRequest | null>(null);
  const [review, setReview] = useState<{ request: DevRequest; approval: Approval } | null>(null);
  const [busy, setBusy] = useState(false);
  const [requests, setRequests] = useState<DevRequest[] | null>(null);
  const previous = useRef<DevRequest[] | null>(null);
  const announceRef = useRef(announce);
  announceRef.current = announce;

  const poll = useCallback(async () => {
    try {
      const current = await listDeveloperRequests({ limit: 10 });
      for (const item of newlyReady(previous.current, current)) {
        const text = readyAnnouncement(item);
        setReady(item);
        setMessage(text);
        announceRef.current?.(text);
      }
      previous.current = current;
      setRequests(current);
    } catch {
      // Developer mode may be unconfigured or the API unreachable; the chat and voice keep working.
      setRequests((current) => current ?? []);
    }
  }, []);

  useEffect(() => { if (owner) void poll(); }, [owner, poll]);
  useEffect(() => {
    if (!owner || requests === null) return;
    const delay = pollDelay(requests);
    if (delay === null) return;
    const timer = window.setTimeout(() => void poll(), delay);
    return () => window.clearTimeout(timer);
  }, [owner, requests, poll]);

  const say = useCallback((reply: string) => { setMessage(reply); return reply; }, []);

  const confirmFor = useCallback((text: string, project: ProjectRef): string => {
    setPending({ kind: 'confirm', text, project });
    setMessage(null);
    return confirmPrompt(text, project.name);
  }, []);

  const start = useCallback(async (text: string, project: ProjectRef): Promise<string> => {
    setPending(null);
    if (mentionsProtected(text)) return say(t('dev.assist.protected'));
    setBusy(true);
    try {
      const created = await createDeveloperRequest({ text, allow_protected_paths: false, project_id: project.id }, source);
      setRequests((current) => [created, ...(current ?? []).filter((item) => item.id !== created.id)]);
      previous.current = [created, ...(previous.current ?? [])];
      return say(t('dev.assist.started', { project: project.name }));
    } catch (e) {
      return say(t('dev.assist.couldNotStart', { message: describeError(e) }));
    } finally { setBusy(false); }
  }, [source, say]);

  const decline = useCallback(() => { setPending(null); return say(t('dev.assist.declined')); }, [say]);

  const openReview = useCallback(async (request: DevRequest): Promise<string> => {
    setBusy(true);
    try {
      const result = await requestMergeApproval(request.id);
      setReview(result);
      return say(t('dev.assist.review', { number: result.request.pr_number ?? '', project: result.request.project_name ?? result.request.repository ?? '', hash: shortHash(result.approval.payloadHash) }));
    } catch (e) {
      return say(t('dev.assist.notReady', { message: describeError(e) }));
    } finally { setBusy(false); }
  }, [say]);

  const confirmMerge = useCallback(async () => {
    if (!review) return;
    setBusy(true);
    try {
      await api.approveTask(review.approval);
      const updated = await getDeveloperRequest(review.request.id, true);
      setReview(null);
      setReady(null);
      setMessage(updated.status === 'merged' ? t('dev.assist.merged', { number: updated.pr_number ?? '' }) : updated.merge_error ?? t('dev.assist.mergeNotDone'));
    } catch (e) {
      setMessage(t('dev.notMerged', { message: describeError(e) }));
    } finally { setBusy(false); }
  }, [review]);

  const intercept = useCallback(async (text: string): Promise<string | null> => {
    if (pending?.kind === 'confirm') {
      const answer = parseConfirmation(text);
      if (answer === 'yes') return start(pending.text, pending.project);
      if (answer === 'no') return decline();
      setPending(null);
    } else if (pending?.kind === 'choose') {
      if (parseConfirmation(text) === 'no') return decline();
      const chosen = matchProjectAnswer(text, pending.candidates);
      if (chosen) return confirmFor(pending.text, chosen);
      if (parseDeveloperCommand(text) === null) return t('dev.assist.whichAgain', { names: orList(pending.candidates.map((candidate) => candidate.name)) });
      setPending(null);
    }
    const waiting = ready ?? requests?.find((item) => item.status === 'waiting_approval') ?? null;
    if (waiting && isApproveIntent(text)) {
      if (!owner) return t('dev.assist.ownerOnly');
      return openReview(waiting);
    }
    const command = parseDeveloperCommand(text);
    if (command === null) return null;
    if (!owner) return say(t('dev.assist.ownerOnly'));
    setBusy(true);
    try {
      const resolution = await resolveDeveloperProject(command);
      if (resolution.status === 'none') return say(t('dev.assist.noProjects'));
      if (resolution.status === 'resolved' && resolution.project) return confirmFor(resolution.text, resolution.project);
      setPending({ kind: 'choose', text: resolution.text, candidates: resolution.candidates });
      setMessage(null);
      return whichProjectPrompt(resolution.candidates);
    } catch (e) {
      return say(t('dev.assist.couldNotResolve', { message: describeError(e) }));
    } finally { setBusy(false); }
  }, [pending, ready, requests, owner, start, decline, confirmFor, openReview, say]);

  const panel = (pending || message || review) ? <section className="devAssist" aria-label={t('dev.assist.title')} aria-live="polite">
    <span className="devAssistTitle"><Hammer aria-hidden="true" />{t('dev.assist.title')}</span>
    {pending?.kind === 'confirm' && <>
      <p dir="auto">{confirmPrompt(pending.text, pending.project.name)}</p>
      <small className="muted devAssistRepo"><FolderGit2 aria-hidden="true" size={12} /><code dir="ltr">{pending.project.repository}</code></small>
      <div className="actions">
        <button type="button" onClick={() => void start(pending.text, pending.project)} disabled={busy}><CheckCircle2 aria-hidden="true" />{t('dev.assist.start')}</button>
        <button type="button" className="secondary" onClick={() => decline()} disabled={busy}><X aria-hidden="true" />{t('common.cancel')}</button>
      </div>
    </>}
    {pending?.kind === 'choose' && <>
      <p dir="auto">{whichProjectPrompt(pending.candidates)}</p>
      <div className="actions" role="group" aria-label={t('dev.assist.chooseProject')}>
        {pending.candidates.map((candidate) => <button type="button" className="secondary" key={candidate.id} onClick={() => confirmFor(pending.text, candidate)} disabled={busy}><FolderGit2 aria-hidden="true" />{candidate.name}</button>)}
        <button type="button" className="secondary" onClick={() => decline()} disabled={busy}><X aria-hidden="true" />{t('common.cancel')}</button>
      </div>
    </>}
    {!pending && message && <p dir="auto">{message}</p>}
    {review && <div className="devReview" aria-label={t('dev.review.label')}>
      <p dir="auto"><ShieldCheck aria-hidden="true" size={16} /> {t('dev.assist.mergeSummary', { number: review.request.pr_number ?? '', title: review.request.pr_title ?? review.request.branch, project: review.request.project_name ?? review.request.repository ?? '' })}</p>
      <small className="approvalHash" title={review.approval.payloadHash ?? undefined}><Fingerprint aria-hidden size={12} /> {rich('dev.review.hash', { hash: <code dir="ltr">{shortHash(review.approval.payloadHash)}</code> })}</small>
      <div className="actions">
        <button type="button" onClick={() => void confirmMerge()} disabled={busy || !review.approval.payloadHash}><CheckCircle2 aria-hidden="true" />{t('dev.review.confirm')}</button>
        <button type="button" className="secondary" onClick={() => setReview(null)} disabled={busy}>{t('dev.review.notNow')}</button>
      </div>
    </div>}
    {!pending && !review && ready && ready.status === 'waiting_approval' && <div className="actions">
      <button type="button" onClick={() => void openReview(ready)} disabled={busy}><ShieldCheck aria-hidden="true" />{t('dev.action.approveMerge')}</button>
      {ready.pr_url && <a href={ready.pr_url} target="_blank" rel="noreferrer" className="devAssistLink">{t('dev.assist.openPr')} <ExternalLink aria-hidden="true" size={12} className="flipRtl" /></a>}
    </div>}
    {!pending && onOpenDeveloper && <div className="actions"><button type="button" className="secondary" onClick={onOpenDeveloper}>{t('dev.assist.openDeveloper')}</button></div>}
  </section> : null;

  return { intercept, awaitingAnswer: pending !== null, panel };
}
