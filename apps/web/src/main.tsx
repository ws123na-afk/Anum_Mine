import { useMemo, useState, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, Database, ListChecks, Settings as SettingsIcon, ShieldCheck } from 'lucide-react';
import { currentTenantContext } from './lib/api';
import { initAuth } from './lib/auth';
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
  const tenantContext = useMemo(() => currentTenantContext(), []);
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

function SplashScreen({ children }: { children: ReactNode }) {
  return (
    <main className="shell">
      <aside className="sidebar" aria-label="Primary navigation">
        <div className="brand">ANUM</div>
      </aside>
      <section className="workspace">
        <div className="card" role="status">
          {children}
        </div>
      </section>
    </main>
  );
}

async function bootstrap() {
  const container = document.getElementById('root')!;
  const root = createRoot(container);

  // In OIDC mode, initAuth() may navigate the browser away to Keycloak's
  // login page entirely (onLoad: 'login-required') - this splash is what's
  // visible for the brief moment before that redirect, and again on the way
  // back while the adapter parses the returned tokens. In stub-header mode
  // (the default) initAuth() resolves immediately and this is never seen.
  root.render(<SplashScreen>Loading ANUM…</SplashScreen>);

  try {
    await initAuth();
  } catch (error) {
    root.render(
      <SplashScreen>
        <p className="errorNotice">
          Sign-in failed: {error instanceof Error ? error.message : 'Unknown error'}. Reload the
          page to try again.
        </p>
      </SplashScreen>,
    );
    return;
  }

  root.render(<App />);
}

void bootstrap();
