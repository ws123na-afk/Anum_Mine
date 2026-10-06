import { BookOpen, FileText, MemoryStick } from 'lucide-react';
import type { AgentRun } from '@anum/contracts';
import { groupSources, runSources, scoreLabel, sourceLabel, sourcesNote } from './lib/retrieval';

/**
 * Which workspace memories and files the run's prompt used, from the run's `retrieval`
 * step (ids and scores only; the API never returns the retrieved text on a run).
 * Renders nothing for a run without a retrieval step.
 */
export function SourcesUsed({ run }: { run: AgentRun | null }) {
  const record = runSources(run);
  if (!record) return null;
  const groups = groupSources(record.sources);
  const note = sourcesNote(record);
  return (
    <section className="surface sourcesSurface" aria-labelledby="sources-used-heading">
      <div className="sectionHeader">
        <div>
          <p className="eyebrow">Retrieval</p>
          <h2 id="sources-used-heading">Sources used</h2>
        </div>
        <span className={`statusBadge ${record.state === 'ok' ? 'success' : record.state === 'unavailable' ? 'warning' : ''}`}>
          {groups.length ? `${groups.length} ${groups.length === 1 ? 'source' : 'sources'}` : 'none'}
        </span>
      </div>
      {groups.length > 0 && (
        <ul className="compactList sourcesList">
          {groups.map((group) => (
            <li className="sourceRow" key={`${group.sourceType}:${group.sourceId}`}>
              <span className="sourceIcon" aria-hidden="true">{group.sourceType === 'memory' ? <MemoryStick /> : <FileText />}</span>
              <span>
                <strong>{sourceLabel(group.sourceType)}</strong> <code>{group.sourceId}</code>
                <small>
                  {group.passages
                    .map((passage) => `passage ${passage.chunkIndex + 1}, ${scoreLabel(passage.score)}${passage.truncated ? ', shortened' : ''}`)
                    .join('; ')}
                </small>
              </span>
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
        <p className="muted">Retrieved text is passed to the model as labeled, untrusted data; it cannot change tools or approvals.</p>
      )}
    </section>
  );
}
