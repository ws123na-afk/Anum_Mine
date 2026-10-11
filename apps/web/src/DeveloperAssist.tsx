// Developer mode from the Assistant chat and the voice view (docs/developer-mode.md).
// "developer, ...", "build ..." or "Anum build ..." asks for confirmation first ("I'll ask the
// developer to: ... Start?") and creates the request only after Start or a spoken "yes". When a
// pull request is ready it is announced; "approve" opens the merge approval with its payload hash,
// which still needs a tap on Confirm merge: nothing is ever approved by voice alone.
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { CheckCircle2, ExternalLink, Fingerprint, Hammer, ShieldCheck, X } from 'lucide-react';
import type { Approval } from '@anum/contracts';
import * as api from './lib/api';
import { shortHash } from './lib/approvals';
import {
  OWNER_ONLY_REPLY, confirmPrompt, isApproveIntent, mentionsProtected, newlyReady, parseConfirmation, parseDeveloperCommand, pollDelay, readyAnnouncement,
  type DevRequest,
} from './lib/developer';
import { createDeveloperRequest, getDeveloperRequest, listDeveloperRequests, requestMergeApproval } from './lib/developerApi';
import { describeError } from './lib/errors';
import { useIsOwner } from './useIsOwner';
import './developer.css';

export interface DeveloperAssist {
  /** Handles a developer command or an answer to a pending question; resolves with the reply, or null when the text is not for the developer. */
  intercept: (text: string) => Promise<string | null>;
  /** True while a question waits for a spoken or typed answer ("Start?"). */
  awaitingAnswer: boolean;
  /** The confirmation, status and merge cards to render near the composer. */
  panel: ReactNode;
}

export function useDeveloperAssist({ source, announce, onOpenDeveloper }: {
  source: 'text' | 'voice';
  /** Said (voice) or shown (chat) when a pull request becomes ready for review. */
  announce?: (text: string) => void;
  onOpenDeveloper?: () => void;
}): DeveloperAssist {
  const owner = useIsOwner();
  const [pending, setPending] = useState<string | null>(null);
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

  const start = useCallback(async (text: string): Promise<string> => {
    setPending(null);
    if (mentionsProtected(text)) {
      const reply = 'That touches CI workflows or secret handling. Open the Developer view to send it with the explicit allow.';
      setMessage(reply);
      return reply;
    }
    setBusy(true);
    try {
      const created = await createDeveloperRequest({ text, allow_protected_paths: false }, source);
      setRequests((current) => [created, ...(current ?? []).filter((item) => item.id !== created.id)]);
      previous.current = [created, ...(previous.current ?? [])];
      const reply = "Started. The developer is on it; I'll tell you when the pull request is ready for review.";
      setMessage(reply);
      return reply;
    } catch (e) {
      const reply = `The developer could not start: ${describeError(e)}`;
      setMessage(reply);
      return reply;
    } finally { setBusy(false); }
  }, [source]);

  const openReview = useCallback(async (request: DevRequest): Promise<string> => {
    setBusy(true);
    try {
      const result = await requestMergeApproval(request.id);
      setReview(result);
      const reply = `Here is the merge to approve: pull request #${result.request.pr_number}, payload hash ${shortHash(result.approval.payloadHash)}. Tap Confirm merge on screen; I never approve by voice.`;
      setMessage(reply);
      return reply;
    } catch (e) {
      const reply = `The merge is not ready: ${describeError(e)}`;
      setMessage(reply);
      return reply;
    } finally { setBusy(false); }
  }, []);

  const confirmMerge = useCallback(async () => {
    if (!review) return;
    setBusy(true);
    try {
      await api.approveTask(review.approval);
      const updated = await getDeveloperRequest(review.request.id, true);
      setReview(null);
      setReady(null);
      setMessage(updated.status === 'merged' ? `Merged pull request #${updated.pr_number}.` : updated.merge_error ?? 'The merge did not happen. Open the Developer view for details.');
    } catch (e) {
      setMessage(`Not merged: ${describeError(e)}`);
    } finally { setBusy(false); }
  }, [review]);

  const intercept = useCallback(async (text: string): Promise<string | null> => {
    if (pending) {
      const answer = parseConfirmation(text);
      if (answer === 'yes') return start(pending);
      if (answer === 'no') { setPending(null); const reply = "Okay, I won't start it."; setMessage(reply); return reply; }
      setPending(null);
    }
    const waiting = ready ?? requests?.find((item) => item.status === 'waiting_approval') ?? null;
    if (waiting && isApproveIntent(text)) {
      if (!owner) return OWNER_ONLY_REPLY;
      return openReview(waiting);
    }
    const command = parseDeveloperCommand(text);
    if (command === null) return null;
    if (!owner) { setMessage(OWNER_ONLY_REPLY); return OWNER_ONLY_REPLY; }
    setPending(command);
    const reply = confirmPrompt(command);
    setMessage(null);
    return reply;
  }, [pending, ready, requests, owner, start, openReview]);

  const panel = (pending || message || review) ? <section className="devAssist" aria-label="Developer" aria-live="polite">
    <span className="devAssistTitle"><Hammer aria-hidden="true" />Developer</span>
    {pending && <>
      <p>{confirmPrompt(pending)}</p>
      <div className="actions">
        <button type="button" onClick={() => void start(pending)} disabled={busy}><CheckCircle2 aria-hidden="true" />Start</button>
        <button type="button" className="secondary" onClick={() => { setPending(null); setMessage("Okay, I won't start it."); }} disabled={busy}><X aria-hidden="true" />Cancel</button>
      </div>
    </>}
    {!pending && message && <p>{message}</p>}
    {review && <div className="devReview" aria-label="Merge approval">
      <p><ShieldCheck aria-hidden="true" size={16} /> Merge pull request #{review.request.pr_number} ({review.request.pr_title ?? review.request.branch}) with a squash merge.</p>
      <small className="approvalHash" title={review.approval.payloadHash ?? undefined}><Fingerprint aria-hidden size={12} /> Payload hash <code>{shortHash(review.approval.payloadHash)}</code></small>
      <div className="actions">
        <button type="button" onClick={() => void confirmMerge()} disabled={busy || !review.approval.payloadHash}><CheckCircle2 aria-hidden="true" />Confirm merge</button>
        <button type="button" className="secondary" onClick={() => setReview(null)} disabled={busy}>Not now</button>
      </div>
    </div>}
    {!pending && !review && ready && ready.status === 'waiting_approval' && <div className="actions">
      <button type="button" onClick={() => void openReview(ready)} disabled={busy}><ShieldCheck aria-hidden="true" />Approve & merge</button>
      {ready.pr_url && <a href={ready.pr_url} target="_blank" rel="noreferrer" className="devAssistLink">Open pull request <ExternalLink aria-hidden="true" size={12} /></a>}
    </div>}
    {!pending && onOpenDeveloper && <div className="actions"><button type="button" className="secondary" onClick={onOpenDeveloper}>Open Developer</button></div>}
  </section> : null;

  return { intercept, awaitingAnswer: pending !== null, panel };
}
