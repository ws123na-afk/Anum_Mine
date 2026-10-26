import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { OfficeLayoutRevisionDetail } from '@anum/contracts';
import { t } from '../lib/i18n.ts';
import { changeCount, summaryText, type HistoryProblem } from './historyModel.ts';
import { MiniPlan } from './MiniPlan.tsx';

interface Props {
  detail: OfficeLayoutRevisionDetail;
  floorName: string;
  busy: boolean;
  problem: HistoryProblem | null;
  onRestore: (confirmUnassign: string[]) => void;
  onClose: () => void;
  onReload: () => void;
}

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), [href], select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** The confirmation of a restore: what changes, which desks lose an agent, and a click to go on. Typed or spoken answers cannot confirm. */
export function RestoreDialog({ detail, floorName, busy, problem, onRestore, onClose, onReload }: Props) {
  const [understood, setUnderstood] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const summary = detail.diff.summary;
  const nothing = changeCount(summary) === 0 && detail.unassign.length === 0;
  const changed = detail.diff.rooms.filter((r) => r.status !== 'same');
  const needsConfirm = detail.unassign.length > 0;
  const stale = problem?.kind === 'stale' || problem?.kind === 'unassign' || problem?.kind === 'gone';

  useEffect(() => {
    opener.current = document.activeElement as HTMLElement | null;
    box.current?.focus();
    return () => { opener.current?.focus?.(); };
  }, []);

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') { e.stopPropagation(); if (!busy) onClose(); return; }
    if (e.key !== 'Tab' || !box.current) return;
    const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === box.current)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  const host = document.querySelector('.hqb') ?? document.body;
  return createPortal(
    <div className="hqhOverlay" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div
        className="hqhDialog" role="dialog" aria-modal="true" aria-labelledby="hqh-dialog-title" aria-describedby="hqh-dialog-body"
        ref={box} tabIndex={-1} onKeyDown={onKeyDown} data-testid="restore-dialog"
      >
        <h3 id="hqh-dialog-title">{t('hqh.dialog.title', { no: detail.revision.revision_no, floor: floorName })}</h3>
        <div id="hqh-dialog-body" className="hqhDialogBody">
          <p>{nothing ? t('hqh.dialog.nothing') : t('hqh.dialog.intro', { summary: summaryText(summary) })}</p>
          <MiniPlan diff={detail.diff} floor={detail.floor} label={t('hqh.plan.restoreLabel', { no: detail.revision.revision_no })} />
          {changed.length > 0 && (
            <ul className="hqhChanges" aria-label={t('hqh.dialog.changes')}>
              {changed.slice(0, 12).map((r) => (
                <li key={r.id}><span className="hqhChip" data-status={r.status}>{t(`hqh.status.${r.status}` as 'hqh.status.added')}</span> <bdi>{r.from_name ? `${r.from_name} > ${r.name}` : r.name}</bdi></li>
              ))}
              {changed.length > 12 && <li>{t('hqb.err.more', { count: changed.length - 12 })}</li>}
            </ul>
          )}
          {needsConfirm && (
            <div className="hqbConfirm" role="group" aria-labelledby="hqh-unassign-title">
              <p id="hqh-unassign-title"><strong>{t('hqh.dialog.unassignTitle', { count: detail.unassign.length })}</strong></p>
              <ul className="hqhUnassign">
                {detail.unassign.map((u) => (
                  <li key={u.desk_id}><bdi>{u.label}</bdi> ({t('hqh.dialog.inRoom', { room: u.room })}){u.agent_hidden ? ` ${t('hqh.dialog.privateAgent')}` : u.agent_id ? ` ${u.agent_id}` : ''}</li>
                ))}
              </ul>
              <label className="hqbCheck">
                <input type="checkbox" checked={understood} disabled={busy} onChange={(e) => setUnderstood(e.target.checked)} />
                <span>{t('hqh.dialog.understand', { count: detail.unassign.length })}</span>
              </label>
            </div>
          )}
          <p className="hqbHint">{t('hqh.dialog.note')}</p>
          {problem && (
            <div className="hqbBanner" role="alert" data-tone="error">
              <div><strong>{problem.text}</strong></div>
              {stale && <button type="button" onClick={onReload}>{t('hqh.dialog.reload')}</button>}
            </div>
          )}
        </div>
        <div className="hqbRow hqhDialogActions">
          <button
            type="button" className="hqbPrimary" disabled={busy || nothing || (needsConfirm && !understood)}
            onClick={() => onRestore(detail.unassign.map((u) => u.desk_id))}
          >
            {busy ? t('hqh.dialog.restoring') : t('hqh.dialog.restore', { no: detail.revision.revision_no })}
          </button>
          <button type="button" disabled={busy} onClick={onClose}>{t('hqb.common.cancel')}</button>
        </div>
      </div>
    </div>,
    host,
  );
}
