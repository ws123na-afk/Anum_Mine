import { useMemo, useState, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, Database, ListChecks, Settings as SettingsIcon, ShieldCheck } from 'lucide-react';
import type { TenantContext } from '@anum/contracts';
import { defaultTenantContext } from './lib/api';
import TasksView from './views/TasksView';
import ApprovalsView from './views/ApprovalsView';
import MemoryView from './views/MemoryView';
import ActivityView from './views/ActivityView';
import SettingsView from './views/SettingsView';
import './styles.css';

type ViewId = 'tasks' | 'activity' | 'approvals' | 'memory' | 'settings';

interface NavItem {
  id: ViewId;
  label: string;
  icon: ReactNode;
}

const NAV_ITEMS: NavItem[] = [
  { id: 'tasks', label: 'Tasks', icon: <ListChecks size={18} aria-hidden="true" /> },
  { id: 'activity', label: 'Agents', icon: <Activity size={18} aria-hidden="true" /> },
  { id: 'approvals', label: 'Approvals', icon: <ShieldCheck size={18} aria-hidden="true" /> },
  { id: 'memory', label: 'Memory', icon: <Database size={18} aria-hidden="true" /> },
  { id: 'settings', label: 'Settings', icon: <SettingsIcon size={18} aria-hidden="true" /> },
];

const VIEW_TITLES: Record<ViewId, { eyebrow: string; title: string }> = {
  tasks: { eyebrow: 'Workspace', title: 'Tasks' },
  activity: { eyebrow: 'Workspace', title: 'Agent activity' },
  approvals: { eyebrow: 'Workspace', title: 'Approvals' },
  memory: { eyebrow: 'Workspace', title: 'Memory' },
  settings: { eyebrow: 'Workspace', title: 'Settings' },
};

function App() {
  const [view, setView] = useState<ViewId>('tasks');
  const tenantContext: TenantContext = defaultTenantContext;
  const tenantLabel = useMemo(
    () => `${tenantContext.tenantId} / ${tenantContext.workspaceId}`,
    [tenantContext],
  );
  const { eyebrow, title } = VIEW_TITLES[view];

  return (
    <main className="shell">
      <aside className="sidebar" aria-label="Primary navigation">
        <div className="brand">ANUM</div>
        <nav>
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              type="button"
              className="navLink"
              aria-current={view === item.id ? 'page' : undefined}
              onClick={() => setView(item.id)}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">{eyebrow}</p>
            <h1>{title}</h1>
          </div>
          <span className="tenant">{tenantLabel}</span>
        </header>

        {view === 'tasks' && <TasksView tenantContext={tenantContext} />}
        {view === 'activity' && <ActivityView tenantContext={tenantContext} />}
        {view === 'approvals' && <ApprovalsView tenantContext={tenantContext} />}
        {view === 'memory' && <MemoryView tenantContext={tenantContext} />}
        {view === 'settings' && <SettingsView tenantContext={tenantContext} />}
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
