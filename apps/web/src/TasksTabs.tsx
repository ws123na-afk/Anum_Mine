// The Tasks page head and its tabs (docs/tasks.md#web-tasks-view): My tasks and Team tasks (people's
// work items), Agent tasks (what ANUM runs) and Approvals. Approvals is its own route (#approvals),
// so it draws the same head; picking another tab there opens #tasks on that tab.
import type { ReactNode } from 'react';
import { FinancePageHead, FinanceTabs } from './FinanceShell';
import { t } from './lib/i18n';
import { viewLabel } from './lib/shell';
import './approvalsInbox.css';

export type TasksTab = 'mine' | 'team' | 'agent' | 'approvals';
export const TASKS_TABS: readonly TasksTab[] = ['mine', 'team', 'agent', 'approvals'];
export const TASKS_TAB_KEY = 'anum.tasks.tab';
export const TASKS_PANEL = 'tasks';

/** The tab the Tasks route opens on: the last one picked in this browser tab, else Agent tasks. */
export function readTasksTab(): Exclude<TasksTab, 'approvals'> {
  try { const saved = sessionStorage.getItem(TASKS_TAB_KEY); return saved === 'mine' || saved === 'team' ? saved : 'agent'; } catch { return 'agent'; }
}
export function rememberTasksTab(tab: TasksTab): void { try { sessionStorage.setItem(TASKS_TAB_KEY, tab); } catch { /* this visit only */ } }

export function TasksHead({ tab, onPick, actions, pending }: { tab: TasksTab; onPick(tab: TasksTab): void; actions?: ReactNode; pending?: number }) {
  const items = TASKS_TABS.map((id) => ({ id, label: t(`tasksTabs.${id}`), badge: id === 'approvals' && pending ? pending : undefined }));
  return <FinancePageHead title={tab === 'approvals' ? viewLabel('approvals') : viewLabel('tasks')} crumbs={tab === 'approvals' ? [{ label: viewLabel('tasks'), href: '#tasks' }] : undefined}
    subtitle={tab === 'approvals' ? t('tasksTabs.approvalsSubtitle') : t('tasksTabs.subtitle')} actions={actions}
    tabs={<FinanceTabs label={t('tasksTabs.label')} idPrefix={TASKS_PANEL} items={items} value={tab} onChange={(next) => { rememberTasksTab(next); onPick(next); }} />} />;
}
