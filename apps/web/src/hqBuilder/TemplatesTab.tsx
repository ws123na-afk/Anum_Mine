// The Templates tab: save the open floor or its building as an owner-named template, and make a new floor or
// building from one (desks come empty, no agent is ever copied). docs/ANUM_MINE_LAYOUT_HISTORY.md#templates.
import { useCallback, useEffect, useState } from 'react';
import type { OfficeLayoutTemplate, OfficeLayoutTemplateList } from '@anum/contracts';
import { formatDateTime, t } from '../lib/i18n.ts';
import { makeOfficeId } from './builderApi.ts';
import type { HistoryApi } from './historyApi.ts';
import { historyProblem, type HistoryProblem } from './historyModel.ts';
import type { BuilderController } from './useBuilderState.ts';

interface Props { b: BuilderController; api: HistoryApi; editable: boolean }
type Action = { kind: 'apply' | 'rename' | 'delete'; id: string } | null;

function stamp(iso: string): string {
  const at = Date.parse(iso);
  return Number.isNaN(at) ? iso : formatDateTime(at, { dateStyle: 'medium' });
}

export function TemplatesTab({ b, api, editable }: Props) {
  const building = b.data.buildings.find((x) => x.id === b.buildingId);
  const [list, setList] = useState<OfficeLayoutTemplateList | null>(null);
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState<HistoryProblem | null>(null);
  const [actionProblem, setActionProblem] = useState<HistoryProblem | null>(null);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [floorName, setFloorName] = useState('');
  const [buildingName, setBuildingName] = useState('');
  const [action, setAction] = useState<Action>(null);
  const [text, setText] = useState('');
  const [level, setLevel] = useState(1);
  const [zone, setZone] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setProblem(null);
    try { setList(await api.templates()); } catch (error) { setProblem(historyProblem(error)); } finally { setLoading(false); }
  }, [api]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setFloorName(b.floor?.name ?? ''); }, [b.floor?.id, b.floor?.name]);
  useEffect(() => { setBuildingName(building?.name ?? ''); setZone(building?.timezone ?? 'Asia/Dubai'); }, [building?.id, building?.name, building?.timezone]);

  const run = async (work: () => Promise<string>) => {
    setBusy(true);
    setActionProblem(null);
    setNotice('');
    try {
      const message = await work();
      setNotice(message);
      setAction(null);
      await load();
    } catch (error) {
      setActionProblem(historyProblem(error));
    } finally {
      setBusy(false);
    }
  };

  const manage = editable && (list?.can_manage ?? false);
  const used = new Set(b.data.floors.filter((f) => f.building_id === b.buildingId).map((f) => f.level));
  const nextLevel = () => { let n = 1; while (used.has(n)) n += 1; return n; };

  const begin = (kind: 'apply' | 'rename' | 'delete', tpl: OfficeLayoutTemplate) => {
    setActionProblem(null);
    setAction({ kind, id: tpl.id });
    setText(kind === 'rename' ? tpl.name : tpl.kind === 'floor' ? tpl.name : `${tpl.name} 2`);
    setLevel(nextLevel());
  };

  const apply = (tpl: OfficeLayoutTemplate) => run(async () => {
    if (tpl.kind === 'floor') {
      const made = await api.floorFromTemplate({ template_id: tpl.id, id: makeOfficeId('floor'), building_id: b.buildingId, level, name: text.trim() || undefined });
      await b.reload({ building: b.buildingId, floor: made.floor.id });
      return t('hqt.notice.floorMade', { name: made.floor.name, rooms: made.room_ids.length });
    }
    const made = await api.buildingFromTemplate({ template_id: tpl.id, id: makeOfficeId('building'), name: text.trim(), timezone: zone });
    await b.reload({ building: made.building.id });
    return t('hqt.notice.buildingMade', { name: made.building.name, floors: made.floor_ids.length });
  });

  return (
    <div className="hqhTab">
      {notice && <p className="hqbBanner" role="status" data-tone="info" data-testid="template-notice">{notice}</p>}
      {actionProblem && (
        <div className="hqbBanner" role="alert" data-tone="error"><div><strong>{actionProblem.kind === 'denied' ? t('hqt.err.denied') : actionProblem.text}</strong></div></div>
      )}

      {manage && (
        <section className="hqbSection" aria-labelledby="hqt-save">
          <h3 id="hqt-save">{t('hqt.save.title')}</h3>
          <form
            className="hqbRoomForm"
            onSubmit={(e) => { e.preventDefault(); if (floorName.trim()) void run(async () => {
              const saved = await api.saveFloorTemplate({ id: makeOfficeId('tpl'), floor_id: b.floorId, name: floorName.trim() });
              return t('hqt.notice.saved', { name: saved.name }) + (saved.skipped_unplaced_rooms ? ` ${t('hqt.notice.skipped', { count: saved.skipped_unplaced_rooms })}` : '');
            }); }}
          >
            <label className="hqbField"><span>{t('hqt.save.floorName')}</span><input value={floorName} maxLength={100} dir="auto" disabled={busy} onChange={(e) => setFloorName(e.target.value)} /></label>
            <button type="submit" disabled={busy || !floorName.trim()}>{t('hqt.save.floor')}</button>
          </form>
          <form
            className="hqbRoomForm"
            onSubmit={(e) => { e.preventDefault(); if (buildingName.trim()) void run(async () => {
              const saved = await api.saveBuildingTemplate({ id: makeOfficeId('tpl'), building_id: b.buildingId, name: buildingName.trim() });
              return t('hqt.notice.saved', { name: saved.name }) + (saved.skipped_unplaced_rooms ? ` ${t('hqt.notice.skipped', { count: saved.skipped_unplaced_rooms })}` : '');
            }); }}
          >
            <label className="hqbField"><span>{t('hqt.save.buildingName')}</span><input value={buildingName} maxLength={100} dir="auto" disabled={busy} onChange={(e) => setBuildingName(e.target.value)} /></label>
            <button type="submit" disabled={busy || !buildingName.trim()}>{t('hqt.save.building')}</button>
          </form>
          <p className="hqbHint">{t('hqt.save.note')}</p>
        </section>
      )}
      {!manage && !loading && <p className="hqbHint" role="note">{t('hqt.viewOnly')}</p>}

      <section className="hqbSection" aria-labelledby="hqt-list">
        <h3 id="hqt-list">{t('hqt.list.title')}</h3>
        {loading && <p className="hqbHint" role="status">{t('hqh.loading')}</p>}
        {problem && (
          <div className="hqbCallout" role="alert">
            <p>{problem.kind === 'denied' ? t('hqt.err.deniedList') : problem.text}</p>
            {problem.kind !== 'denied' && <button type="button" onClick={() => void load()}>{t('hqb.common.retry')}</button>}
          </div>
        )}
        {!loading && !problem && list && list.items.length === 0 && (
          <div className="hqbCallout" data-testid="templates-empty"><p>{manage ? t('hqt.list.emptyOwner') : t('hqt.list.emptyViewer')}</p></div>
        )}
        {list && list.items.length > 0 && (
          <ul className="hqhList" aria-label={t('hqt.list.title')}>
            {list.items.map((tpl) => (
              <li key={tpl.id} className="hqhTpl" data-testid="template-row">
                <div className="hqhTplHead">
                  <strong dir="auto">{tpl.name}</strong>
                  <span className="hqhBadge">{t(tpl.kind === 'floor' ? 'hqt.kind.floor' : 'hqt.kind.building')}</span>
                </div>
                <small>{t('hqt.list.counts', { floors: tpl.summary.floors, rooms: tpl.summary.rooms, desks: tpl.summary.desks })}</small>
                <small><bdi>{tpl.created_by}</bdi> {t('hqh.at')} {stamp(tpl.created_at)}</small>
                {manage && action?.id !== tpl.id && (
                  <div className="hqbRow">
                    <button type="button" className="hqbPrimary" disabled={busy || b.dirty} title={b.dirty ? t('hqt.draftBlocks') : undefined} onClick={() => begin('apply', tpl)}>{t(tpl.kind === 'floor' ? 'hqt.apply.floor' : 'hqt.apply.building')}</button>
                    <button type="button" disabled={busy} onClick={() => begin('rename', tpl)}>{t('hqt.rename')}</button>
                    <button type="button" className="hqbQuiet" disabled={busy} onClick={() => begin('delete', tpl)}>{t('hqt.delete')}</button>
                  </div>
                )}
                {manage && action?.id === tpl.id && action.kind === 'apply' && (
                  <form className="hqbRoomForm" onSubmit={(e) => { e.preventDefault(); void apply(tpl); }}>
                    <label className="hqbField"><span>{t(tpl.kind === 'floor' ? 'hqt.apply.floorName' : 'hqt.apply.buildingName')}</span><input value={text} maxLength={100} dir="auto" disabled={busy} onChange={(e) => setText(e.target.value)} /></label>
                    {tpl.kind === 'floor'
                      ? <label className="hqbField"><span>{t('hqb.floor.level')}</span><input type="number" min={-20} max={120} value={level} disabled={busy} onChange={(e) => setLevel(Math.round(Number(e.target.value)))} /></label>
                      : <label className="hqbField"><span>{t('hqt.apply.timezone')}</span><input value={zone} maxLength={80} dir="ltr" disabled={busy} onChange={(e) => setZone(e.target.value)} /></label>}
                    <p className="hqbHint">{t('hqt.apply.note')}</p>
                    <div className="hqbRow">
                      <button type="submit" className="hqbPrimary" disabled={busy || (tpl.kind === 'building' && !text.trim())}>{busy ? t('hqb.common.saving') : t('hqt.apply.create')}</button>
                      <button type="button" disabled={busy} onClick={() => setAction(null)}>{t('hqb.common.cancel')}</button>
                    </div>
                  </form>
                )}
                {manage && action?.id === tpl.id && action.kind === 'rename' && (
                  <form className="hqbRoomForm" onSubmit={(e) => { e.preventDefault(); if (text.trim()) void run(async () => { const r = await api.renameTemplate(tpl.id, text.trim(), tpl.revision); return t('hqt.notice.renamed', { name: r.name }); }); }}>
                    <label className="hqbField"><span>{t('hqt.rename')}</span><input value={text} maxLength={100} dir="auto" disabled={busy} onChange={(e) => setText(e.target.value)} /></label>
                    <div className="hqbRow">
                      <button type="submit" className="hqbPrimary" disabled={busy || !text.trim() || text.trim() === tpl.name}>{t('hqt.rename.save')}</button>
                      <button type="button" disabled={busy} onClick={() => setAction(null)}>{t('hqb.common.cancel')}</button>
                    </div>
                  </form>
                )}
                {manage && action?.id === tpl.id && action.kind === 'delete' && (
                  <div className="hqbConfirm" role="alertdialog" aria-label={t('hqt.delete')}>
                    <p>{t('hqt.delete.question', { name: tpl.name })}</p>
                    <div className="hqbRow">
                      <button type="button" className="hqbDanger" disabled={busy} onClick={() => void run(async () => { await api.deleteTemplate(tpl.id, tpl.revision); return t('hqt.notice.deleted', { name: tpl.name }); })}>{t('hqt.delete')}</button>
                      <button type="button" disabled={busy} onClick={() => setAction(null)}>{t('hqb.common.cancel')}</button>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
