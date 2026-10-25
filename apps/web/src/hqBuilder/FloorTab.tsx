import { useEffect, useState } from 'react';
import { t } from '../lib/i18n.ts';
import type { BuilderController } from './useBuilderState.ts';

interface Props { b: BuilderController; editable: boolean }

/** Floor outline and names, and deleting an empty floor or building (the server lists what blocks it). */
export function FloorTab({ b, editable }: Props) {
  const floor = b.floor;
  const building = b.data.buildings.find((x) => x.id === b.buildingId);
  const [buildingName, setBuildingName] = useState(building?.name ?? '');
  const [name, setName] = useState(floor?.name ?? '');
  const [level, setLevel] = useState(floor?.level ?? 0);
  const [width, setWidth] = useState(floor?.width_m ?? 60);
  const [depth, setDepth] = useState(floor?.depth_m ?? 40);
  const [confirm, setConfirm] = useState<'' | 'floor' | 'building'>('');
  useEffect(() => { setBuildingName(building?.name ?? ''); }, [building?.id, building?.revision]);
  useEffect(() => {
    setName(floor?.name ?? ''); setLevel(floor?.level ?? 0); setWidth(floor?.width_m ?? 60); setDepth(floor?.depth_m ?? 40); setConfirm('');
  }, [floor?.id, floor?.revision]);
  if (!floor || !building) return <p className="hqbHint">{t('hqb.floor.none')}</p>;
  const patch: { name?: string; level?: number; width_m?: number; depth_m?: number } = {};
  if (name.trim() && name.trim() !== floor.name) patch.name = name.trim();
  if (level !== floor.level) patch.level = level;
  if (width !== floor.width_m || depth !== floor.depth_m) { patch.width_m = width; patch.depth_m = depth; }
  const changed = Object.keys(patch).length > 0;
  const sizeValid = width >= 1 && width <= 1000 && depth >= 1 && depth <= 1000;
  const off = !editable || b.busy;
  return (
    <div className="hqbFloorTab">
      <form className="hqbSection" onSubmit={(e) => { e.preventDefault(); if (changed && sizeValid && name.trim()) void b.saveFloor(patch); }}>
        <h3>{t('hqb.floor.title')}</h3>
        {!b.bounds && <p className="hqbHint" role="status">{t('hqb.floor.unbounded')}</p>}
        <label className="hqbField"><span>{t('hqb.floor.name')}</span><input value={name} maxLength={100} dir="auto" disabled={off} onChange={(e) => setName(e.target.value)} /></label>
        <div className="hqbRow">
          <label className="hqbGrow"><span>{t('hqb.floor.level')}</span><input type="number" min={-20} max={120} value={level} disabled={off} onChange={(e) => setLevel(Math.round(Number(e.target.value)))} /></label>
          <label className="hqbGrow"><span>{t('hqb.floor.width')}</span><input type="number" min={1} max={1000} step="0.5" value={width} disabled={off} onChange={(e) => setWidth(Number(e.target.value))} /></label>
          <label className="hqbGrow"><span>{t('hqb.floor.depth')}</span><input type="number" min={1} max={1000} step="0.5" value={depth} disabled={off} onChange={(e) => setDepth(Number(e.target.value))} /></label>
        </div>
        {editable && <button type="submit" className="hqbPrimary" disabled={b.busy || !changed || !sizeValid || !name.trim()}>{t('hqb.floor.save')}</button>}
        {b.dirty && editable && <p className="hqbHint">{t('hqb.floor.draftNote')}</p>}
      </form>

      <form className="hqbSection" onSubmit={(e) => { e.preventDefault(); if (buildingName.trim() && buildingName.trim() !== building.name) void b.saveBuildingName(buildingName.trim()); }}>
        <h3>{t('hqb.building.title')}</h3>
        <label className="hqbField"><span>{t('hqb.building.name')}</span><input value={buildingName} maxLength={100} dir="auto" disabled={off} onChange={(e) => setBuildingName(e.target.value)} /></label>
        {editable && <button type="submit" disabled={b.busy || !buildingName.trim() || buildingName.trim() === building.name}>{t('hqb.building.save')}</button>}
      </form>

      {editable && (
        <section className="hqbSection">
          <h3>{t('hqb.floor.dangerTitle')}</h3>
          <p className="hqbHint">{t('hqb.floor.dangerHint')}</p>
          {confirm === '' && (
            <div className="hqbRow">
              <button type="button" className="hqbQuiet" onClick={() => setConfirm('floor')}>{t('hqb.floor.delete')}</button>
              <button type="button" className="hqbQuiet" onClick={() => setConfirm('building')}>{t('hqb.building.delete')}</button>
            </div>
          )}
          {confirm !== '' && (
            <div className="hqbConfirm" role="alertdialog" aria-label={t('hqb.floor.dangerTitle')}>
              <p>{confirm === 'floor' ? t('hqb.floor.deleteQuestion', { name: floor.name }) : t('hqb.building.deleteQuestion', { name: building.name })}</p>
              <div className="hqbRow">
                <button type="button" className="hqbDanger" disabled={b.busy} onClick={() => { void (confirm === 'floor' ? b.deleteFloor() : b.deleteBuilding()).then(() => setConfirm('')); }}>
                  {confirm === 'floor' ? t('hqb.floor.delete') : t('hqb.building.delete')}
                </button>
                <button type="button" onClick={() => setConfirm('')}>{t('hqb.common.cancel')}</button>
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
