import { useRef, useState } from 'react';
import { Mic, Send, Square } from 'lucide-react';
import {
  appendTranscript,
  completeVoiceSession,
  createPushToTalk,
  createVoiceSession,
  submitVoiceCommand,
  type TranscriptRetention,
  type VoiceSession,
} from './lib/voice';

interface PushToTalkController {
  supported: boolean;
  start: () => void;
  stop: () => void;
  cancel: () => void;
}

export function VoiceView() {
  const [session, setSession] = useState<VoiceSession | null>(null);
  const [transcript, setTranscript] = useState('');
  const [retention, setRetention] = useState<TranscriptRetention>('session');
  const [state, setState] = useState<'idle' | 'listening' | 'submitting'>('idle');
  const [status, setStatus] = useState('Ready for push-to-talk.');
  const controller = useRef<PushToTalkController | null>(null);
  const sequence = useRef(0);

  async function startListening() {
    try {
      let activeSession = session;
      if (!activeSession) {
        activeSession = await createVoiceSession(undefined, retention);
        setSession(activeSession);
      }
      controller.current = createPushToTalk(
        (text, isFinal) => {
          setTranscript(text);
          setStatus(isFinal ? 'Transcript ready to submit.' : 'Listening...');
        },
        setStatus,
        activeSession.locale,
      );
      if (!controller.current.supported) {
        setStatus('Speech recognition is unavailable. Type the command below instead.');
        return;
      }
      controller.current.start();
      setState('listening');
      setStatus('Listening...');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Unable to start voice session.');
    }
  }

  function stopListening() {
    controller.current?.stop();
    setState('idle');
    setStatus('Transcript ready to submit.');
  }

  async function submit() {
    if (!transcript.trim()) return;
    setState('submitting');
    try {
      const activeSession = session ?? await createVoiceSession(undefined, retention);
      setSession(activeSession);
      const segment = await appendTranscript(activeSession.id, transcript.trim(), sequence.current++);
      const result = await submitVoiceCommand(activeSession.id, segment.id);
      await completeVoiceSession(activeSession.id);
      setStatus(`Task created: ${result.task.title}`);
      setSession(null);
      setTranscript('');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Unable to submit voice command.');
    } finally {
      setState('idle');
    }
  }

  return <>
    <section className="moduleIntro">
      <div><p className="eyebrow">Hands-free capture</p><h2>Voice command</h2><p>Transcripts create normal governed tasks. Sensitive approvals remain visual.</p></div>
    </section>
    <section className="surface voiceSurface">
      <div className={state === 'listening' ? 'voiceIndicator listening' : 'voiceIndicator'}><Mic size={30} /></div>
      <div className="voiceControls">
        <label className="field"><span>Transcript retention</span><select value={retention} onChange={(event) => setRetention(event.target.value as TranscriptRetention)} disabled={Boolean(session)}><option value="session">Delete when session ends</option><option value="30_days">Keep for 30 days</option><option value="permanent">Keep until deleted</option></select></label>
        <label className="field"><span>Transcript</span><textarea value={transcript} onChange={(event) => setTranscript(event.target.value)} placeholder="Hold to speak or type a task instruction" rows={6} /></label>
        <div className="actions">{state === 'listening' ? <button type="button" className="danger" onClick={stopListening}><Square size={17} />Stop</button> : <button type="button" onClick={startListening} disabled={state === 'submitting'}><Mic size={17} />Start listening</button>}<button type="button" className="secondary" onClick={submit} disabled={!transcript.trim() || state !== 'idle'}><Send size={17} />Create task</button></div>
        <p className="notice" role="status">{status}</p>
      </div>
    </section>
  </>;
}
