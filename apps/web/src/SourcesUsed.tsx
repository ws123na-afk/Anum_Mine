import { useEffect, useState } from 'react';
import { BookOpen, ChevronRight, CircleDot, FileText, Lock, MemoryStick, RefreshCw } from 'lucide-react';
import type { AgentRun } from '@anum/contracts';
import * as api from './lib/api';
import { describeError, isPermissionDenied } from './lib/errors';
import {
  groupSources, runSources, scoreLabel, sourceDisplay, sourceNameMap, sourceNamesPath, sourcesNote,
  type SourceGroup, type SourceName,
} from './lib/retrieval';
import { t } from './lib/i18n';

/** What the Memory view needs to find and highlight a memory a run used. */
export interface MemoryTarget { id: string; title: string | null }

type Names = { state: 'loading' } | { state: 'ready'; names: Map<string, SourceName> } | { state: 'error'; message: string } | { state: 'denied' };

/**
 * Which workspace memories and files the run's prompt used, from the run's `retrieval`
 * step (ids and scores only; the API never returns the retrieved text on a run).
 * Renders nothing for a run without a retrieval step.
 *
 * Names come from one bounded lookup (`GET /api/v1/retrieval/sources`) for all of the run's
 * sources. A found memory opens in the Memory view, a found file opens its detail; a source
 * that no longer exists (or the caller cannot see) shows its id and is not a link.
 */
export function SourcesUsed({ run, onOpenMemory, onOpenFile }: { run: AgentRun | null; onOpenMemory(target: MemoryTarget): void; onOpenFile(fileId: string): void }) {
  const record = runSources(run);
  const groups = record ? groupSources(record.sources) : [];
  // A string, so the lookup reruns only when the run's set of sources changes.
  const path = sourceNamesPath(groups);
  const [names, setNames] = useState<Names>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!path) return;
    let live = true;
    setNames({ state: 'loading' });
    api.getSourceNames(path).then(
      (list) => { if (live) setNames({ state: 'ready', names: sourceNameMap(list) }); },
      (error: unknown) => { if (live) setNames(isPermissionDenied(error) ? { state: 'denied' } : { state: 'error', message: describeError(error, t('common.unreachable')) }); },
    );
    return () => { live = false; };
  }, [path, attempt]);

  if (!record) return null;
  const note = sourcesNote(record);
  const resolved = names.state === 'ready' ? names.names : null;
  return (
    <section className="surface sourcesSurface" aria-labelledby="sources-used-heading">
      <div className="sectionHeader">
        <div>
          <p className="eyebrow">{t('sources.eyebrow')}</p>
          <h2 id="sources-used-heading">{t('sources.title')}</h2>
        </div>
        <span className={`statusBadge ${record.state === 'ok' ? 'success' : record.state === 'unavailable' ? 'warning' : ''}`}>
          {t('sources.count', { count: groups.length })}
        </span>
      </div>
      {groups.length > 0 && names.state === 'loading' && (
        <p className="notice sourcesStatus" role="status"><CircleDot aria-hidden="true" />{t('sources.lookingUp')}</p>
      )}
      {groups.length > 0 && names.state === 'error' && (
        <div className="notice errorNotice sourcesStatus" role="alert">
          <CircleDot aria-hidden="true" />
          <span>{t('sources.lookupFailed', { reason: names.message })}</span>
          <button type="button" className="secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw aria-hidden="true" />{t('common.retry')}</button>
        </div>
      )}
      {groups.length > 0 && names.state === 'denied' && (
        <p className="notice sourcesStatus" role="status">
          <Lock aria-hidden="true" />{t('sources.denied')}
        </p>
      )}
      {groups.length > 0 && (
        <ul className="sourcesList">
          {groups.map((group) => (
            <li key={`${group.sourceType}:${group.sourceId}`}>
              <SourceRow group={group} names={resolved} onOpenMemory={onOpenMemory} onOpenFile={onOpenFile} />
            </li>
          ))}
        </ul>
      )}
      {note && (
        <p className="muted">
          <BookOpen aria-hidden="true" size={14} /> {note}
        </p>
      )}
      {groups.length > 0 && (
        <p className="muted">{t('sources.untrusted')}</p>
      )}
    </section>
  );
}

function SourceRow({ group, names, onOpenMemory, onOpenFile }: { group: SourceGroup; names: Map<string, SourceName> | null; onOpenMemory(target: MemoryTarget): void; onOpenFile(fileId: string): void }) {
  const display = sourceDisplay(group, names);
  const memory = group.sourceType === 'memory';
  const passages = group.passages
    .map((passage) => t(passage.truncated ? 'sources.passageShortened' : 'sources.passage', { n: passage.chunkIndex + 1, score: scoreLabel(passage.score) }))
    .join(t('sources.passageJoin'));
  const body = (
    <>
      <span className="sourceIcon" aria-hidden="true">{memory ? <MemoryStick /> : <FileText />}</span>
      <span className="sourceText">
        <strong className="sourceTitle">{display.title}</strong>
        <small>{display.kind}{display.title !== group.sourceId && <> · <code dir="ltr">{group.sourceId}</code></>}</small>
        <small>{passages}</small>
      </span>
    </>
  );
  if (!display.found) return <div className="sourceRow">{body}</div>;
  const open = () => (memory ? onOpenMemory({ id: group.sourceId, title: display.title === group.sourceId ? null : display.title }) : onOpenFile(group.sourceId));
  return (
    <button type="button" className="sourceRow sourceLink" onClick={open} aria-label={t(memory ? 'sources.openMemory' : 'sources.openFile', { title: display.title, kind: display.kind, passages })}>
      {body}
      <ChevronRight className="sourceChevron flipRtl" aria-hidden="true" />
    </button>
  );
}
