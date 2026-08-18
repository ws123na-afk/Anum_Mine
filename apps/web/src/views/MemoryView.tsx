import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import type { TenantContext } from '@anum/contracts';
import { Clock, Database, Plus, Search, Trash2 } from 'lucide-react';
import {
  ApiError,
  createMemory,
  deleteMemory,
  listMemories,
  type MemoryListFilters,
  type MemoryNote,
} from '../lib/api';

const CONTENT_TRUNCATE_LENGTH = 280;
const FILTER_DEBOUNCE_MS = 300;

function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

function isExpiredNote(note: MemoryNote): boolean {
  if (note.retention.kind !== 'expires_at' || !note.retention.expiresAt) return false;
  const expiry = new Date(note.retention.expiresAt).getTime();
  return Number.isFinite(expiry) && expiry < Date.now();
}

function retentionLabel(note: MemoryNote): string {
  if (note.retention.kind === 'expires_at') {
    return note.retention.expiresAt
      ? `Expires ${formatDateTime(note.retention.expiresAt)}`
      : 'Expires (no date set)';
  }
  if (note.retention.kind === 'task') return 'Retained until task completes';
  return 'Retained indefinitely';
}

export default function MemoryView({ tenantContext }: { tenantContext: TenantContext }) {
  // Filters
  const [taskIdInput, setTaskIdInput] = useState('');
  const [queryInput, setQueryInput] = useState('');
  const [includeExpired, setIncludeExpired] = useState(false);
  const [debouncedTaskId, setDebouncedTaskId] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');

  useEffect(() => {
    const handle = setTimeout(() => {
      setDebouncedTaskId(taskIdInput.trim());
      setDebouncedQuery(queryInput.trim());
    }, FILTER_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [taskIdInput, queryInput]);

  // List state
  const [notes, setNotes] = useState<MemoryNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  const fetchNotes = useCallback(() => {
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError(null);

    const filters: MemoryListFilters = {};
    if (debouncedTaskId) filters.taskId = debouncedTaskId;
    if (debouncedQuery) filters.query = debouncedQuery;
    if (includeExpired) filters.includeExpired = true;

    listMemories(filters)
      .then((result) => {
        if (requestIdRef.current !== requestId) return;
        setNotes(result);
        setLoading(false);
      })
      .catch((err) => {
        if (requestIdRef.current !== requestId) return;
        setError(err instanceof ApiError ? err.message : 'Failed to load memory notes.');
        setLoading(false);
      });
  }, [debouncedTaskId, debouncedQuery, includeExpired]);

  useEffect(() => {
    fetchNotes();
  }, [fetchNotes]);

  const filtersActive = Boolean(debouncedTaskId || debouncedQuery);

  // Expand / collapse long content
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  function toggleExpand(id: string) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  // Delete (two-click confirm)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  function handleDeleteClick(id: string) {
    if (pendingDeleteId === id) {
      void performDelete(id);
    } else {
      setPendingDeleteId(id);
    }
  }

  async function performDelete(id: string) {
    setDeletingId(id);
    setDeleteError(null);
    try {
      await deleteMemory(id);
      setNotes((prev) => prev.filter((note) => note.id !== id));
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : 'Failed to delete memory note.');
    } finally {
      setDeletingId(null);
      setPendingDeleteId(null);
    }
  }

  // Create form
  const [formTaskId, setFormTaskId] = useState('');
  const [formContent, setFormContent] = useState('');
  const [formSourceType, setFormSourceType] = useState('user_note');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const taskId = formTaskId.trim();
    const content = formContent.trim();
    if (!taskId || !content) return;

    setCreating(true);
    setCreateError(null);
    try {
      const note = await createMemory({
        taskId,
        content,
        sourceType: formSourceType.trim() || 'user_note',
      });
      setNotes((prev) => [note, ...prev]);
      setFormContent('');
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : 'Failed to create memory note.');
    } finally {
      setCreating(false);
    }
  }

  return (
    <div>
      <div className="viewHeader">
        <div>
          <p className="eyebrow">{tenantContext.workspaceId}</p>
          <h2>Task memory notes</h2>
        </div>
      </div>

      <section className="card" aria-label="Filter memory notes">
        <div className="filterBar">
          <label className="field">
            <span>Filter by task ID</span>
            <input
              type="text"
              value={taskIdInput}
              onChange={(event) => setTaskIdInput(event.target.value)}
              placeholder="e.g. task_8f2a1c"
            />
          </label>
          <label className="field">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-1)' }}>
              <Search size={14} aria-hidden="true" />
              Search content
            </span>
            <input
              type="search"
              value={queryInput}
              onChange={(event) => setQueryInput(event.target.value)}
              placeholder="Search note content…"
            />
          </label>
          <label
            style={{
              alignItems: 'center',
              color: 'var(--color-text-muted)',
              display: 'flex',
              fontSize: 'var(--text-sm)',
              fontWeight: 700,
              gap: 'var(--space-2)',
            }}
          >
            <input
              type="checkbox"
              checked={includeExpired}
              onChange={(event) => setIncludeExpired(event.target.checked)}
            />
            Include expired notes
          </label>
        </div>
      </section>

      <section className="card" aria-label="Create memory note">
        <div className="panelHeader">
          <h3>New memory note</h3>
        </div>
        <form className="taskComposer" onSubmit={handleCreate}>
          <label className="field">
            <span>Task ID</span>
            <input
              type="text"
              value={formTaskId}
              onChange={(event) => setFormTaskId(event.target.value)}
              placeholder="e.g. task_8f2a1c"
              required
            />
          </label>
          <label className="field">
            <span>Content</span>
            <textarea
              value={formContent}
              onChange={(event) => setFormContent(event.target.value)}
              placeholder="What should be remembered about this task?"
              required
            />
          </label>
          <label className="field">
            <span>Source type</span>
            <input
              type="text"
              value={formSourceType}
              onChange={(event) => setFormSourceType(event.target.value)}
              placeholder="user_note"
            />
          </label>
          {createError && <div className="errorNotice">{createError}</div>}
          <div className="actions">
            <button type="submit" disabled={creating}>
              <Plus size={18} aria-hidden="true" />
              {creating ? 'Saving…' : 'Add memory note'}
            </button>
          </div>
        </form>
      </section>

      <section aria-label="Memory notes list">
        {deleteError && <div className="errorNotice">{deleteError}</div>}

        {loading && (
          <div className="list">
            <div className="skeleton" style={{ height: 88 }} />
            <div className="skeleton" style={{ height: 88 }} />
            <div className="skeleton" style={{ height: 88 }} />
          </div>
        )}

        {!loading && error && (
          <div className="errorNotice">
            <p>{error}</p>
            <div className="actions">
              <button type="button" className="secondary" onClick={fetchNotes}>
                Retry
              </button>
            </div>
          </div>
        )}

        {!loading && !error && notes.length === 0 && (
          <div className="emptyState">
            <Database size={28} aria-hidden="true" />
            <p>
              {filtersActive
                ? 'No memory notes match the current filters.'
                : 'No memory notes have been recorded for this workspace yet.'}
            </p>
          </div>
        )}

        {!loading && !error && notes.length > 0 && (
          <div className="list">
            {notes.map((note) => {
              const expired = isExpiredNote(note);
              const expanded = expandedIds.has(note.id);
              const isLong = note.content.length > CONTENT_TRUNCATE_LENGTH;
              const displayContent =
                expanded || !isLong ? note.content : `${note.content.slice(0, CONTENT_TRUNCATE_LENGTH)}…`;
              const isPendingDelete = pendingDeleteId === note.id;
              const isDeleting = deletingId === note.id;

              return (
                <div
                  key={note.id}
                  className="listRow"
                  style={{ alignItems: 'stretch', flexDirection: 'column' }}
                >
                  <div
                    style={{
                      alignItems: 'center',
                      display: 'flex',
                      flexWrap: 'wrap',
                      gap: 'var(--space-3)',
                      justifyContent: 'space-between',
                    }}
                  >
                    <div style={{ alignItems: 'center', display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
                      <span className="badge">{note.provenance.sourceType}</span>
                      {expired && <span className="pill pill--warning">Expired</span>}
                    </div>
                    <div className="actions" style={{ marginTop: 0 }}>
                      {isPendingDelete && (
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => setPendingDeleteId(null)}
                          disabled={isDeleting}
                        >
                          Cancel
                        </button>
                      )}
                      <button
                        type="button"
                        className="danger"
                        onClick={() => handleDeleteClick(note.id)}
                        disabled={isDeleting}
                      >
                        <Trash2 size={16} aria-hidden="true" />
                        {isDeleting ? 'Deleting…' : isPendingDelete ? 'Confirm delete' : 'Delete'}
                      </button>
                    </div>
                  </div>

                  <p style={{ whiteSpace: 'pre-wrap' }}>{displayContent}</p>
                  {isLong && (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => toggleExpand(note.id)}
                      style={{ justifySelf: 'start', width: 'fit-content' }}
                    >
                      {expanded ? 'Show less' : 'Show more'}
                    </button>
                  )}

                  <div
                    style={{
                      color: 'var(--color-text-muted)',
                      display: 'flex',
                      flexWrap: 'wrap',
                      fontSize: 'var(--text-sm)',
                      gap: 'var(--space-4)',
                      marginTop: 'var(--space-1)',
                    }}
                  >
                    <span>Task: {note.taskId}</span>
                    <span>Created by: {note.provenance.createdByUserId}</span>
                    <span>Created: {formatDateTime(note.createdAt)}</span>
                    <span style={{ alignItems: 'center', display: 'inline-flex', gap: 'var(--space-1)' }}>
                      <Clock size={14} aria-hidden="true" />
                      {retentionLabel(note)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
