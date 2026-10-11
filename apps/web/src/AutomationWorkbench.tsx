import { useCallback, useEffect, useState } from 'react';
import { ArrowDown, ArrowUp, CalendarClock, ChevronDown, ChevronRight, Clock3, Copy, Pause, Play, Plus, RefreshCw, RotateCcw, Square, Trash2, Workflow, X } from 'lucide-react';
import { createAutomationSchedule, createAutomationWorkflow, deleteAutomationSchedule, getAutomationRuns, getAutomationSchedules, getAutomationWorkflows, startAutomationRun, toggleAutomationSchedule, transitionAutomationRun, type AutomationRun, type AutomationSchedule, type AutomationWorkflow } from './lib/api';
import { cronLabel, draftsFromSteps, intervalCron, moveStep, newStep, nextRunLabel, removeStep, SCHEDULE_PRESETS, STEP_ACTIONS, workflowBody, type StepDraft } from './lib/automation';
import './workbench.css';

const statusTone = (status: string) => status === 'completed' ? 'success' : status === 'failed' || status === 'cancelled' ? 'dangerTone' : 'warning';
const initialSteps = (): StepDraft[] => [{ id: 'execute', name: 'Execute work', action: 'task.execute', input: '{}', maxAttempts: 3 }];

export function AutomationWorkbench() {
  const [workflows, setWorkflows] = useState<AutomationWorkflow[]>([]);
  const [runs, setRuns] = useState<AutomationRun[]>([]);
  const [schedules, setSchedules] = useState<AutomationSchedule[]>([]);
  const [name, setName] = useState('Daily operations review');
  const [description, setDescription] = useState('');
  const [steps, setSteps] = useState<StepDraft[]>(initialSteps);
  const [basedOn, setBasedOn] = useState<AutomationWorkflow | null>(null);
  const [selected, setSelected] = useState('');
  const [scheduleName, setScheduleName] = useState('Weekday run');
  const [cronMode, setCronMode] = useState<'preset' | 'interval' | 'cron'>('preset');
  const [preset, setPreset] = useState<string>(SCHEDULE_PRESETS[3].value);
  const [interval, setIntervalMinutes] = useState(30);
  const [cron, setCron] = useState('0 9 * * 1-5');
  const [timezone, setTimezone] = useState('UTC');
  const [expanded, setExpanded] = useState('');
  const [message, setMessage] = useState('Loading durable automation state...');
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => { try { const [workflowItems, runItems, scheduleItems] = await Promise.all([getAutomationWorkflows(), getAutomationRuns(), getAutomationSchedules()]); setWorkflows(workflowItems); setRuns(runItems); setSchedules(scheduleItems); setSelected((current) => current || workflowItems[0]?.id || ''); setMessage(`${workflowItems.length} workflows, ${scheduleItems.length} schedules and ${runItems.length} runs loaded.`); } catch (error) { setMessage(error instanceof Error ? error.message : 'Unable to load automation state.'); } }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  async function perform(operation: () => Promise<unknown>, success: string) { setBusy(true); try { await operation(); await refresh(); setMessage(success); } catch (error) { setMessage(error instanceof Error ? error.message : 'Automation operation failed.'); } finally { setBusy(false); } }
  const updateStep = (index: number, patch: Partial<StepDraft>) => setSteps((current) => current.map((step, i) => i === index ? { ...step, ...patch } : step));
  function loadWorkflow(workflow: AutomationWorkflow) { setBasedOn(workflow); setName(`${workflow.name} (v${workflow.version + 1})`); setDescription(workflow.description); setSteps(draftsFromSteps(workflow.steps)); setMessage(`Editing a copy of ${workflow.name}. Workflows are immutable, so saving creates a new workflow.`); }
  function resetComposer() { setBasedOn(null); setName('Daily operations review'); setDescription(''); setSteps(initialSteps()); }
  function saveWorkflow() { let body: ReturnType<typeof workflowBody>; try { body = workflowBody(name, description, steps); } catch (error) { setMessage(error instanceof Error ? error.message : 'Invalid workflow.'); return; } void perform(async () => { const item = await createAutomationWorkflow(body); setSelected(item.id); resetComposer(); }, 'Workflow saved durably.'); }
  function scheduleCron(): string { return cronMode === 'preset' ? preset : cronMode === 'interval' ? intervalCron(interval) : cron.trim(); }
  function saveSchedule() { let expression: string; try { expression = scheduleCron(); } catch (error) { setMessage(error instanceof Error ? error.message : 'Invalid schedule.'); return; } void perform(() => createAutomationSchedule({ workflow_id: selected, name: scheduleName.trim(), cron: expression, timezone: timezone.trim() || 'UTC', enabled: true }), 'Schedule created.'); }
  // Run now launches the saved workflow, not the composer: block it while the composer holds unsaved edits.
  const unsaved = basedOn !== null || name !== 'Daily operations review' || description !== '' || JSON.stringify(steps) !== JSON.stringify(initialSteps());
  const workflowSchedules =schedules.filter((item) => !selected || item.workflow_id === selected);
  const workflowName = (id: string) => workflows.find((item) => item.id === id)?.name ?? id;
  return <>
    <section className="moduleIntro"><div><p className="eyebrow">Durable execution</p><h2>Automation workbench</h2><p>Compose, schedule, launch, pause, resume, and retry replay-safe agent workflows.</p></div><button type="button" onClick={() => void refresh()}><RefreshCw size={17} />Refresh</button></section>
    <section className="automationGrid">
      <section className="surface automationComposer"><div className="sectionHeader"><div><p className="eyebrow">Workflow definition</p><h2>{basedOn ? 'Edit as new workflow' : 'Create automation'}</h2></div><Workflow size={20} /></div>
        {basedOn && <p className="notice wbNotice">Based on <strong>{basedOn.name}</strong> v{basedOn.version}. Saving creates a new workflow; the original and its schedules are unchanged.</p>}
        <label className="field"><span>Name</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label className="field"><span>Description</span><input value={description} maxLength={1000} onChange={(event) => setDescription(event.target.value)} /></label>
        <ol className="wbSteps" aria-label="Workflow steps">{steps.map((step, index) => <li className="wbStep" key={index}>
          <div className="wbStepHead"><strong>Step {index + 1}</strong><div className="runActions"><button type="button" title="Move up" aria-label={`Move step ${index + 1} up`} disabled={index === 0} onClick={() => setSteps((current) => moveStep(current, index, -1))}><ArrowUp size={15} /></button><button type="button" title="Move down" aria-label={`Move step ${index + 1} down`} disabled={index === steps.length - 1} onClick={() => setSteps((current) => moveStep(current, index, 1))}><ArrowDown size={15} /></button><button type="button" title="Remove" aria-label={`Remove step ${index + 1}`} disabled={steps.length === 1} onClick={() => setSteps((current) => removeStep(current, index))}><X size={15} /></button></div></div>
          <div className="wbStepFields">
            <label className="field"><span>Step id</span><input value={step.id} maxLength={80} onChange={(event) => updateStep(index, { id: event.target.value })} /></label>
            <label className="field"><span>Step name</span><input value={step.name} maxLength={160} onChange={(event) => updateStep(index, { name: event.target.value })} /></label>
            <label className="field"><span>Action</span><select value={STEP_ACTIONS.some((item) => item.value === step.action) ? step.action : ''} onChange={(event) => updateStep(index, { action: event.target.value || step.action })}>{!STEP_ACTIONS.some((item) => item.value === step.action) && <option value="">{step.action}</option>}{STEP_ACTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
            <label className="field"><span>Max attempts</span><input type="number" min={1} max={10} value={step.maxAttempts} onChange={(event) => updateStep(index, { maxAttempts: Number(event.target.value) })} /></label>
          </div>
          <label className="field"><span>Input (JSON object{step.action === 'fail' ? ', e.g. {"message": "..."}' : ''})</span><textarea className="wbJson" rows={2} value={step.input} onChange={(event) => updateStep(index, { input: event.target.value })} /></label>
        </li>)}</ol>
        <div className="actions"><button type="button" className="secondary" disabled={steps.length >= 100} onClick={() => setSteps((current) => [...current, newStep(current)])}><Plus size={17} />Add step</button>{unsaved && <button type="button" className="secondary" onClick={resetComposer}><X size={17} />Discard</button>}<button type="button" disabled={busy || !name.trim()} onClick={saveWorkflow}><Plus size={17} />{basedOn ? 'Save as new' : 'Create'}</button></div>
      </section>
      <section className="surface automationComposer"><div className="sectionHeader"><div><p className="eyebrow">Replay-safe launch</p><h2>Start workflow</h2></div><Play size={20} /></div>
        <label className="field"><span>Workflow</span><select value={selected} onChange={(event) => setSelected(event.target.value)}><option value="">Select a workflow</option>{workflows.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        {selected && <p className="muted wbMeta">{workflows.find((item) => item.id === selected)?.steps.map((step) => step.name).join(' → ')}</p>}
        <div className="actions"><button type="button" className="secondary" disabled={!selected} onClick={() => { const item = workflows.find((workflow) => workflow.id === selected); if (item) loadWorkflow(item); }}><Copy size={17} />Edit copy</button><button type="button" disabled={busy || !selected || unsaved} aria-describedby={unsaved ? 'wbRunHint' : undefined} onClick={() => void perform(() => startAutomationRun(selected, crypto.randomUUID()), `Run of ${workflowName(selected)} started.`)}><Play size={17} />Run now</button></div>
        {unsaved && selected && <p className="muted" id="wbRunHint">Save or discard the composer's edits first: Run now starts the saved workflow selected above.</p>}
        <p className="notice" role="status"><Clock3 size={14} />{message}</p>
      </section>
    </section>
    <section className="surface runTable"><div className="sectionHeader"><div><p className="eyebrow">Recurring execution</p><h2>Schedules</h2></div><CalendarClock size={20} /></div>
      <div className="wbScheduleForm">
        <label className="field"><span>Schedule name</span><input value={scheduleName} maxLength={160} onChange={(event) => setScheduleName(event.target.value)} /></label>
        <label className="field"><span>Repeat</span><select value={cronMode} onChange={(event) => setCronMode(event.target.value as typeof cronMode)}><option value="preset">Common schedule</option><option value="interval">Every N minutes</option><option value="cron">Cron expression</option></select></label>
        {cronMode === 'preset' && <label className="field"><span>When</span><select value={preset} onChange={(event) => setPreset(event.target.value)}>{SCHEDULE_PRESETS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>}
        {cronMode === 'interval' && <label className="field"><span>Minutes</span><input type="number" min={1} max={1440} value={interval} onChange={(event) => setIntervalMinutes(Number(event.target.value))} /></label>}
        {cronMode === 'cron' && <label className="field"><span>Cron (min hour day month weekday)</span><input value={cron} maxLength={120} onChange={(event) => setCron(event.target.value)} /></label>}
        <label className="field"><span>Timezone</span><input value={timezone} maxLength={80} onChange={(event) => setTimezone(event.target.value)} /></label>
        <div className="actions"><button type="button" disabled={busy || !selected || !scheduleName.trim()} onClick={saveSchedule}><Plus size={17} />Add schedule</button></div>
      </div>
      {!selected && <p className="muted">Select a workflow above to schedule it.</p>}
      {workflowSchedules.length === 0 ? <div className="emptyState"><CalendarClock /><h3>No schedules</h3><p>Schedules fire only while the API scheduler is enabled.</p></div> : workflowSchedules.map((item) => <article className="runRow" key={item.id}>
        <div><strong>{item.name}</strong><code>{cronLabel(item.cron)} · {item.timezone}</code></div>
        <span className={`statusBadge ${item.enabled ? 'success' : 'warning'}`}>{item.enabled ? 'active' : 'paused'}</span>
        <span>Next: {nextRunLabel(item.next_run_at, item.enabled)}</span>
        <div className="runActions">{item.enabled ? <button title="Pause" aria-label={`Pause schedule ${item.name}`} disabled={busy} onClick={() => void perform(() => toggleAutomationSchedule(item.id, 'disable'), 'Schedule paused.')}><Pause size={16} /></button> : <button title="Resume" aria-label={`Resume schedule ${item.name}`} disabled={busy} onClick={() => void perform(() => toggleAutomationSchedule(item.id, 'enable'), 'Schedule resumed.')}><Play size={16} /></button>}<button title="Delete" aria-label={`Delete schedule ${item.name}`} disabled={busy} onClick={() => { if (window.confirm(`Delete schedule "${item.name}"?`)) void perform(() => deleteAutomationSchedule(item.id), 'Schedule deleted.'); }}><Trash2 size={16} /></button></div>
      </article>)}
    </section>
    <section className="surface runTable"><div className="sectionHeader"><div><p className="eyebrow">Execution history</p><h2>Workflow runs</h2></div><span className="muted">{runs.length} total</span></div>{runs.length === 0 ? <div className="emptyState"><Workflow /><h3>No workflow runs</h3><p>Create and launch an automation to begin.</p></div> : runs.map((run) => <article className="wbRun" key={run.id}><div className="runRow"><div><button type="button" className="wbToggle" aria-expanded={expanded === run.id} aria-label={`Show steps for run ${run.id}`} onClick={() => setExpanded((current) => current === run.id ? '' : run.id)}>{expanded === run.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}<strong>{workflowName(run.workflow_id)}</strong></button><code>{run.id}</code></div><span className={`statusBadge ${statusTone(run.status)}`}>{run.status}</span><span>{run.current_step} / {run.steps.length} steps</span><div className="runActions">{run.status === 'paused' && <button title="Resume" aria-label="Resume run" disabled={busy} onClick={() => void perform(() => transitionAutomationRun(run.id, 'resume'), 'Run resumed.')}><Play size={16} /></button>}{['failed', 'cancelled'].includes(run.status) && <button title="Retry" aria-label="Retry run" disabled={busy} onClick={() => void perform(() => transitionAutomationRun(run.id, 'retry'), 'Retry started.')}><RotateCcw size={16} /></button>}{!['completed', 'cancelled'].includes(run.status) && <button title="Cancel" aria-label="Cancel run" disabled={busy} onClick={() => void perform(() => transitionAutomationRun(run.id, 'cancel'), 'Run cancelled.')}><Square size={16} /></button>}{run.status === 'paused' && <Pause size={16} aria-label="Paused" />}</div></div>
      {expanded === run.id && <ol className="wbStepLog" aria-label="Run steps">{run.steps.map((step) => <li key={step.id}><div className="wbStepHead"><span><strong>{step.name}</strong> <code>{step.action}</code></span><span className={`statusBadge ${statusTone(step.status)}`}>{step.status}</span></div><span className="muted">Attempt {step.attempt}</span>{step.error && <p className="wbError">{step.error}</p>}{Object.keys(step.output).length > 0 && <pre className="wbJson">{JSON.stringify(step.output, null, 2)}</pre>}</li>)}</ol>}
    </article>)}</section>
  </>;
}
