import { useMemo, useState, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, CheckCircle2, Clock, Database, KeyRound, Play, ShieldCheck, Square, Workflow } from 'lucide-react';
import type { AgentRun, Approval, Task } from '@anum/contracts';
import { approveTask, cancelTask, createAndRunTask, defaultTenantContext } from './lib/api';
import { demoApproval, demoRun, demoTask } from './lib/demoData';
import './styles.css';

function App() {
  const [task, setTask] = useState<Task>(demoTask);
  const [run, setRun] = useState<AgentRun>(demoRun);
  const [approval, setApproval] = useState<Approval | null>(demoApproval);
  const [prompt, setPrompt] = useState('Send and publish the Phase 1 status update');
  const [statusText, setStatusText] = useState('Demo data loaded. Start the API to run live tasks.');
  const [isBusy, setIsBusy] = useState(false);

  const tenantLabel = useMemo(
    () => `${defaultTenantContext.tenantId} / ${defaultTenantContext.workspaceId}`,
    [],
  );

  async function handleRunTask() {
    setIsBusy(true);
    setStatusText('Creating task through ANUM API...');
    try {
      const result = await createAndRunTask(prompt);
      setTask(result.task);
      setRun(result.run);
      setApproval(result.approval);
      setStatusText(result.approval ? 'Task is waiting for approval.' : 'Task completed.');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : 'Unable to reach ANUM API.');
    } finally {
      setIsBusy(false);
    }
  }

  async function handleCancelTask() {
    setIsBusy(true);
    setStatusText('Cancelling task through ANUM API...');
    try {
      const cancelled = await cancelTask(task.id);
      setTask(cancelled);
      setApproval(null);
      setStatusText('Task cancelled.');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : 'Unable to cancel task.');
    } finally {
      setIsBusy(false);
    }
  }

  async function handleApprove() {
    if (!approval) {
      return;
    }
    setIsBusy(true);
    setStatusText('Approving task through ANUM API...');
    try {
      const result = await approveTask(approval.id);
      setTask(result.task);
      setRun(result.run ?? run);
      setApproval(result.approval);
      setStatusText('Approval accepted and runtime resumed.');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : 'Unable to approve task.');
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <main className="shell">
      <aside className="sidebar" aria-label="Primary navigation">
        <div className="brand">ANUM</div>
        <nav>
          {['Tasks', 'Agents', 'Approvals', 'Memory', 'Integrations', 'Settings'].map((item) => (
            <a href={`#${item.toLowerCase()}`} key={item}>{item}</a>
          ))}
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Workspace</p>
            <h1>Foundation Control Center</h1>
          </div>
          <span className="tenant">{tenantLabel}</span>
        </header>

        <section className="statusGrid" aria-label="System foundation status">
          <Metric icon={<ShieldCheck />} label="Tenant boundary" value="Header scoped" />
          <Metric icon={<KeyRound />} label="Identity" value="OIDC stub" />
          <Metric icon={<Workflow />} label="Runtime" value="Approval aware" />
          <Metric icon={<Database />} label="Persistence" value="Repository ready" />
        </section>

        <section className="panel" id="tasks">
          <div className="panelHeader">
            <div>
              <p className="eyebrow">Active task</p>
              <h2>{task.title}</h2>
            </div>
            <span className="badge">{task.status.replace('_', ' ')}</span>
          </div>
          <label className="taskComposer">
            <span>Task prompt</span>
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={3} />
          </label>
          <div className="actions">
            <button type="button" onClick={handleRunTask} disabled={isBusy || !prompt.trim()}>
              <Play size={18} /> Run task
            </button>
            <button type="button" className="secondary" onClick={handleCancelTask} disabled={isBusy || ['completed', 'failed', 'cancelled'].includes(task.status)}>
              <Square size={18} /> Cancel
            </button>
          </div>
          <p className="notice" role="status">{statusText}</p>
          <p className="prompt">{task.prompt}</p>
          <div className="timeline">
            {run.steps.map((step) => (
              <div className="step" key={step.id}>
                <Activity size={18} />
                <div>
                  <strong>{step.type.replace('_', ' ')}</strong>
                  <p>{step.summary}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="panel" id="approvals">
          <div className="panelHeader">
            <div>
              <p className="eyebrow">Approval gate</p>
              <h2>{approval?.action ?? 'No pending approval'}</h2>
            </div>
            {approval ? <span className="risk">{approval.riskLevel}</span> : <span className="badge">clear</span>}
          </div>
          <p className="prompt">{approval?.reason ?? 'The current task does not require approval.'}</p>
          <div className="actions">
            <button type="button" onClick={handleApprove} disabled={isBusy || !approval || approval.status !== 'pending'}>
              <CheckCircle2 size={18} /> Approve
            </button>
            <button type="button" className="secondary" disabled>
              <Clock size={18} /> {approval?.status ?? 'idle'}
            </button>
          </div>
        </section>
      </section>
    </main>
  );
}

function Metric({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <article className="metric">
      <div className="metricIcon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
