import React from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, CheckCircle2, Clock, Database, KeyRound, ShieldCheck, Workflow } from 'lucide-react';
import type { AgentRun, Approval, Task } from '@anum/contracts';
import './styles.css';

const task: Task = {
  id: 'task_demo_phase_1',
  title: 'Prepare ANUM Phase 1 slice',
  prompt: 'Create the first governed agent task flow.',
  status: 'waiting_approval',
  tenantId: 'tenant_local',
  workspaceId: 'workspace_foundation',
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
};

const run: AgentRun = {
  id: 'run_demo_phase_1',
  taskId: task.id,
  status: 'waiting_approval',
  steps: [
    {
      id: 'step_model',
      type: 'model_call',
      summary: 'Mock model prepared a deterministic task plan.',
      createdAt: new Date().toISOString(),
    },
    {
      id: 'step_approval',
      type: 'approval_wait',
      summary: 'Runtime paused before a high-risk action.',
      createdAt: new Date().toISOString(),
    },
  ],
};

const approval: Approval = {
  id: 'approval_demo_phase_1',
  taskId: task.id,
  action: 'high_risk_tool_execution',
  riskLevel: 'high',
  status: 'pending',
  reason: 'Publishing or external side effects require explicit approval.',
  createdAt: new Date().toISOString(),
};

function App() {
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
          <span className="tenant">tenant_local / workspace_foundation</span>
        </header>

        <section className="statusGrid" aria-label="System foundation status">
          <Metric icon={<ShieldCheck />} label="Tenant boundary" value="Header scoped" />
          <Metric icon={<KeyRound />} label="Identity" value="OIDC stub" />
          <Metric icon={<Workflow />} label="Runtime" value="Approval aware" />
          <Metric icon={<Database />} label="Persistence" value="In-memory now" />
        </section>

        <section className="panel" id="tasks">
          <div className="panelHeader">
            <div>
              <p className="eyebrow">Active task</p>
              <h2>{task.title}</h2>
            </div>
            <span className="badge">{task.status.replace('_', ' ')}</span>
          </div>
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
              <h2>{approval.action}</h2>
            </div>
            <span className="risk">{approval.riskLevel}</span>
          </div>
          <p className="prompt">{approval.reason}</p>
          <div className="actions">
            <button type="button"><CheckCircle2 size={18} /> Approve</button>
            <button type="button" className="secondary"><Clock size={18} /> Keep waiting</button>
          </div>
        </section>
      </section>
    </main>
  );
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <article className="metric">
      <div className="metricIcon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
