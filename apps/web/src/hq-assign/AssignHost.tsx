/** Mounted once by the headquarters. Listens for "assign work" and "open this work" requests from any card, desk, room,
 * team panel, flow dock item, history record or the assistant, and shows the dialog, the confirmation toast and the
 * work drawers (task, approvals, meeting, agent work log) on the same screen. Nothing here navigates unless the person
 * clicks an "Open full page" link. */
import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import type { AssignResponse } from '@anum/contracts';
import { t } from '../lib/i18n';
import { ASSIGN_EVENT, setAssignHostMounted, TASK_PANEL_EVENT, type AssignPrefill } from '../lib/hqAssign';
import { HQ_PANEL_EVENT, validPanel, type HqWorkPanel } from '../lib/hqWorkPanels';
import { useLocale } from '../useLocale';

// Each opens only on a request, so each is its own chunk; the host itself (the listeners and the toast) stays mounted.
const AssignDialog = lazy(() => import('./AssignDialog').then((m) => ({ default: m.AssignDialog })));
const TaskPanel = lazy(() => import('./TaskPanel').then((m) => ({ default: m.TaskPanel })));
const ApprovalPanel = lazy(() => import('./ApprovalPanel').then((m) => ({ default: m.ApprovalPanel })));
const MeetingPanel = lazy(() => import('./MeetingPanel').then((m) => ({ default: m.MeetingPanel })));
const AgentWorkPanel = lazy(() => import('./AgentWorkPanel').then((m) => ({ default: m.AgentWorkPanel })));

export interface AssignedInfo { agentId: string; taskId: string }
export interface AssignHostProps {
  /** Agents seated in a room, for the dialog opened from that room. */
  seatedAgentIds?: (roomId: string) => ReadonlySet<string>;
  /** The live flow, the desks and the side panel read again so the new task shows at once. */
  onAssigned?: (info: AssignedInfo) => void;
  /** Something was decided or changed in a drawer (an approval, a task action). */
  onChanged?: () => void;
  /** Moves on every workspace event of the shell's stream; open drawers read their record again. */
  live?: number | string;
}

let opened = 0;

export function AssignHost({ seatedAgentIds, onAssigned, onChanged, live }: AssignHostProps) {
  const locale = useLocale();
  const [prefill, setPrefill] = useState<AssignPrefill | null>(null);
  const [panel, setPanel] = useState<(HqWorkPanel & { n: number; agentName?: string }) | null>(null);
  const [toast, setToast] = useState<{ title: string; name: string; taskId: string } | null>(null);
  const show = useCallback((p: HqWorkPanel, agentName?: string) => { opened += 1; setPanel({ ...p, n: opened, agentName }); }, []);

  useEffect(() => {
    setAssignHostMounted(true);
    const open = (e: Event) => setPrefill((e as CustomEvent<AssignPrefill>).detail ?? {});
    const task = (e: Event) => show({ kind: 'task', id: String((e as CustomEvent<string>).detail) });
    const work = (e: Event) => { const p = (e as CustomEvent<HqWorkPanel>).detail; if (p && validPanel(p)) show(p); };
    window.addEventListener(ASSIGN_EVENT, open); window.addEventListener(TASK_PANEL_EVENT, task); window.addEventListener(HQ_PANEL_EVENT, work);
    return () => {
      window.removeEventListener(ASSIGN_EVENT, open); window.removeEventListener(TASK_PANEL_EVENT, task); window.removeEventListener(HQ_PANEL_EVENT, work);
      setAssignHostMounted(false);
    };
  }, [show]);
  // The dialog shows the same confirmation with its own "Open the task" while it is open, and on a phone it is a bottom sheet the toast would
  // cover (its Close button sat under the toast): the toast waits for the dialog to close, and its 12 s start only once it is on screen.
  const toastShown = toast !== null && prefill === null;
  useEffect(() => { if (!toastShown) return; const id = setTimeout(() => setToast(null), 12000); return () => clearTimeout(id); }, [toastShown, toast]);

  const closeDialog = useCallback(() => setPrefill(null), []);
  const closePanel = useCallback(() => setPanel(null), []);
  const assigned = useCallback((result: AssignResponse, agent: { id: string; name: string }) => {
    setToast({ title: result.task.title, name: agent.name, taskId: result.task.id });
    onAssigned?.({ agentId: agent.id, taskId: result.task.id });
  }, [onAssigned]);
  const seated = prefill?.roomId && seatedAgentIds ? seatedAgentIds(prefill.roomId) : undefined;
  const key = panel ? `${panel.kind}:${panel.n}` : '';

  return <>
    {prefill && <Suspense fallback={null}><AssignDialog prefill={prefill} seatedIds={seated} onClose={closeDialog} onAssigned={assigned}
      onOpenTask={(taskId) => show({ kind: 'task', id: taskId }, toast?.name)} onOpenPanel={(p) => show(p)} /></Suspense>}
    {panel && <Suspense fallback={null}>
      {(panel.kind === 'task' || panel.kind === 'run') && <TaskPanel key={key} taskId={panel.kind === 'task' ? panel.id : null} runId={panel.kind === 'run' ? panel.id : null}
        agentName={panel.agentName} live={live} onClose={closePanel} onChanged={onChanged} />}
      {(panel.kind === 'approval' || panel.kind === 'approvals') && <ApprovalPanel key={key} panel={panel} live={live} onClose={closePanel} onChanged={onChanged} />}
      {panel.kind === 'meeting' && <MeetingPanel key={key} meetingId={panel.id} roomId={panel.roomId} onClose={closePanel} />}
      {panel.kind === 'agent' && <AgentWorkPanel key={key} agentId={panel.id} name={panel.name} live={live} onClose={closePanel} />}
    </Suspense>}
    {toast && toastShown && createPortal(<div className="hqAsToast" dir={locale === 'ar' ? 'rtl' : 'ltr'} role="status">
      <span dir="auto">{t('hqAssign.toast', { title: toast.title, name: toast.name })}</span>
      <button type="button" onClick={() => { show({ kind: 'task', id: toast.taskId }, toast.name); setToast(null); }}>{t('hqAssign.done.openTask')}</button>
      <button type="button" className="hqAsX" aria-label={t('hqAssign.toast.dismiss')} onClick={() => setToast(null)}><X size={14} aria-hidden="true" /></button>
    </div>, document.body)}
  </>;
}
