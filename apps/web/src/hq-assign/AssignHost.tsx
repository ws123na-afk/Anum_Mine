/** Mounted once by the headquarters. Listens for "assign work" and "open task" requests from any card, desk, room,
 * team panel, flow dock item or the assistant, and shows the dialog, the confirmation toast and the task side panel
 * on the same screen. Nothing here navigates unless the person clicks a link to another page. */
import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import type { AssignResponse } from '@anum/contracts';
import { t } from '../lib/i18n';
import { ASSIGN_EVENT, setAssignHostMounted, TASK_PANEL_EVENT, type AssignPrefill } from '../lib/hqAssign';
import { useLocale } from '../useLocale';
import { AssignDialog } from './AssignDialog';
import { TaskPanel } from './TaskPanel';

export interface AssignHostProps {
  /** Agents seated in a room, for the dialog opened from that room. */
  seatedAgentIds?: (roomId: string) => ReadonlySet<string>;
  /** The live flow re-reads so the new task shows at once. */
  onAssigned?: () => void;
  onNavigate: (hash: string) => void;
}

export function AssignHost({ seatedAgentIds, onAssigned, onNavigate }: AssignHostProps) {
  const locale = useLocale();
  const [prefill, setPrefill] = useState<AssignPrefill | null>(null);
  const [panel, setPanel] = useState<{ taskId: string; agentName?: string } | null>(null);
  const [toast, setToast] = useState<{ title: string; name: string; taskId: string } | null>(null);

  useEffect(() => {
    setAssignHostMounted(true);
    const open = (e: Event) => setPrefill((e as CustomEvent<AssignPrefill>).detail ?? {});
    const task = (e: Event) => setPanel({ taskId: String((e as CustomEvent<string>).detail) });
    window.addEventListener(ASSIGN_EVENT, open); window.addEventListener(TASK_PANEL_EVENT, task);
    return () => { window.removeEventListener(ASSIGN_EVENT, open); window.removeEventListener(TASK_PANEL_EVENT, task); setAssignHostMounted(false); };
  }, []);
  useEffect(() => { if (!toast) return; const id = setTimeout(() => setToast(null), 12000); return () => clearTimeout(id); }, [toast]);

  const closeDialog = useCallback(() => setPrefill(null), []);
  const closePanel = useCallback(() => setPanel(null), []);
  const assigned = useCallback((result: AssignResponse, agent: { name: string }) => {
    setToast({ title: result.task.title, name: agent.name, taskId: result.task.id });
    onAssigned?.();
  }, [onAssigned]);
  const seated = prefill?.roomId && seatedAgentIds ? seatedAgentIds(prefill.roomId) : undefined;

  return <>
    {prefill && <AssignDialog prefill={prefill} seatedIds={seated} onClose={closeDialog} onAssigned={assigned}
      onOpenTask={(taskId) => setPanel({ taskId, agentName: toast?.name })} onNavigate={onNavigate} />}
    {panel && <TaskPanel taskId={panel.taskId} agentName={panel.agentName} onClose={closePanel} onNavigate={onNavigate} />}
    {toast && createPortal(<div className="hqAsToast" dir={locale === 'ar' ? 'rtl' : 'ltr'} role="status">
      <span dir="auto">{t('hqAssign.toast', { title: toast.title, name: toast.name })}</span>
      <button type="button" onClick={() => { setPanel({ taskId: toast.taskId, agentName: toast.name }); setToast(null); }}>{t('hqAssign.done.openTask')}</button>
      <button type="button" className="hqAsX" aria-label={t('hqAssign.toast.dismiss')} onClick={() => setToast(null)}><X size={14} aria-hidden="true" /></button>
    </div>, document.body)}
  </>;
}
